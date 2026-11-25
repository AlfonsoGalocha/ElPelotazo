"""Fase C de la revision v2.8: detector de contradicciones graduado.

`classify_contradiction` es pura (recibe una `Prediction` y una
`MarketEvidence` ya construidas, sin BD propia) -- estos tests construyen
ambas directamente, sin fixtures de base de datos, igual que
`test_ranking_v3.py` hace con `Prediction` sueltas.
"""

from __future__ import annotations

import datetime as dt

from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import (
    MIN_DATA_COVERAGE_FOR_CONTRADICTION_EVIDENCE,
    MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE,
    ContradictionLevel,
    classify_contradiction,
)
from backend.app.prediction.evidence import MarketEvidence, WindowStat


def _prediction(**overrides) -> Prediction:
    defaults = dict(
        id=1, match_id=1, market="over_2_5", line=2.5, selection="over",
        model_version_id=1, model_probability=0.6, calibrated_probability=None,
        fair_odds=1.6, confidence=0.7, data_quality=0.7,
        explanation={}, features_used={}, created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def _evidence(n: int, frequency: float | None, data_coverage: float = 1.0, window: str = "last10") -> MarketEvidence:
    """Una `MarketEvidence` sintetica con una sola ventana poblada (la que
    el test necesita); el resto queda vacia (n=0), tal y como
    `build_market_evidence` las deja cuando no hay datos reales."""
    empty = WindowStat(mean=None, median=None, std=None, n=0)
    windows = {name: empty for name in ("last5", "last10", "last20", "season", "home", "away")}
    freq = {name: None for name in windows}
    windows[window] = WindowStat(mean=frequency, median=frequency, std=0.1, n=n)
    freq[window] = frequency
    return MarketEvidence(
        market="over_2_5", line=2.5, windows=windows, line_hit_frequency=freq,
        effective_sample_size=float(n), data_coverage=data_coverage,
    )


def test_sin_contradiccion_cuando_la_probabilidad_calibrada_coincide_con_la_frecuencia_historica():
    prediction = _prediction(calibrated_probability=0.60)
    evidence = _evidence(n=20, frequency=0.60)

    assert classify_contradiction(prediction, evidence) == ContradictionLevel.SIN_CONTRADICCION


def test_evidencia_insuficiente_si_ninguna_ventana_llega_al_minimo_de_muestra():
    prediction = _prediction(calibrated_probability=0.60)
    evidence = _evidence(n=MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE - 1, frequency=0.10)

    assert classify_contradiction(prediction, evidence) == ContradictionLevel.EVIDENCIA_INSUFICIENTE


def test_evidencia_insuficiente_si_la_cobertura_de_datos_es_baja_aunque_n_sea_grande():
    """Caso real que motivo la revision: un mercado nuevo (corners en una
    liga que casi nunca trae esa estadistica) puede acumular muchos
    partidos INTENTADOS pero con el dato ausente la mayoria de las veces
    -- `n` ya filtrado parece suficiente, pero no hay que confiar en el."""
    prediction = _prediction(calibrated_probability=0.60)
    evidence = _evidence(
        n=MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE + 5,
        frequency=0.10,
        data_coverage=MIN_DATA_COVERAGE_FOR_CONTRADICTION_EVIDENCE - 0.1,
    )

    assert classify_contradiction(prediction, evidence) == ContradictionLevel.EVIDENCIA_INSUFICIENTE


def test_contradiccion_fuerte_para_el_caso_real_que_motivo_la_revision():
    """Over 9.5 corners (o cualquier over) con probabilidad calibrada alta
    pero frecuencia historica real muy baja, sobre una muestra que ya es
    estadisticamente informativa -- el caso exacto que el brief pedia
    dejar de tratar como "oportunidad"."""
    prediction = _prediction(market="corners_over_9_5", line=9.5, calibrated_probability=0.75)
    evidence = _evidence(n=20, frequency=0.15)

    assert classify_contradiction(prediction, evidence) == ContradictionLevel.FUERTE


def test_doble_oportunidad_con_buena_cuota_pero_baja_probabilidad_real_se_marca():
    """Doble oportunidad elegida por cuota atractiva pese a que el equipo
    casi nunca gana/empata en ese contexto -- segundo caso real citado en
    el brief (seccion 19)."""
    prediction = _prediction(market="double_chance_1x", line=None, calibrated_probability=0.70)
    evidence = _evidence(n=15, frequency=0.20)

    level = classify_contradiction(prediction, evidence)
    assert level in (ContradictionLevel.MODERADA, ContradictionLevel.FUERTE)


def test_una_muestra_grande_con_hueco_pequenio_pesa_mas_que_una_muestra_chica_con_hueco_grande():
    """Pedido real explicito de la revision: la MISMA diferencia relativa
    entre probabilidad y frecuencia historica debe considerarse mas grave
    cuanto mayor sea la muestra que la respalda (menos margen para ser
    puro ruido de muestra chica)."""
    prediction_small_n = _prediction(calibrated_probability=0.70)
    evidence_small_n = _evidence(n=MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE, frequency=0.50)

    prediction_large_n = _prediction(calibrated_probability=0.70)
    evidence_large_n = _evidence(n=200, frequency=0.50)

    level_small_n = classify_contradiction(prediction_small_n, evidence_small_n)
    level_large_n = classify_contradiction(prediction_large_n, evidence_large_n)

    severity_order = [
        ContradictionLevel.SIN_CONTRADICCION,
        ContradictionLevel.LEVE,
        ContradictionLevel.MODERADA,
        ContradictionLevel.FUERTE,
    ]
    assert severity_order.index(level_large_n) >= severity_order.index(level_small_n)


def test_gol_con_menos_ev_pero_mas_respaldo_historico_sale_sin_contradiccion():
    """Tercer caso real citado (seccion 19): una senhal de goles con EV mas
    modesto pero con la frecuencia historica real respaldandola por
    completo no debe marcarse como contradictoria."""
    prediction = _prediction(market="over_2_5", calibrated_probability=0.55)
    evidence = _evidence(n=40, frequency=0.56)

    assert classify_contradiction(prediction, evidence) == ContradictionLevel.SIN_CONTRADICCION


def test_elige_la_ventana_con_mayor_n_entre_las_que_cumplen_el_minimo():
    prediction = _prediction(calibrated_probability=0.90)
    empty = WindowStat(mean=None, median=None, std=None, n=0)
    windows = {name: empty for name in ("last5", "last10", "last20", "season", "home", "away")}
    freq = {name: None for name in windows}
    # "last5" es insuficiente (n < minimo); "season" es la mas fiable (mayor n).
    windows["last5"] = WindowStat(mean=0.90, median=0.90, std=0.1, n=2)
    freq["last5"] = 0.90
    windows["last10"] = WindowStat(mean=0.10, median=0.10, std=0.1, n=10)
    freq["last10"] = 0.10
    windows["season"] = WindowStat(mean=0.10, median=0.10, std=0.1, n=30)
    freq["season"] = 0.10
    evidence = MarketEvidence(
        market="over_2_5", line=2.5, windows=windows, line_hit_frequency=freq,
        effective_sample_size=30.0, data_coverage=1.0,
    )

    # Si usara "last5" (coincide con la probabilidad) saldria SIN_CONTRADICCION;
    # si usa la ventana mas fiable real ("season", 10% de frecuencia contra
    # 90% de probabilidad) debe salir una contradiccion fuerte.
    assert classify_contradiction(prediction, evidence) == ContradictionLevel.FUERTE


def test_nunca_excluye_devuelve_siempre_un_nivel_nunca_una_excepcion_o_none():
    """Documentado en `ContradictionLevel`: esto es POST-HOC, nunca excluye
    nada por si solo -- siempre devuelve una etiqueta utilizable."""
    prediction = _prediction(calibrated_probability=0.5)
    evidence = _evidence(n=0, frequency=None)

    level = classify_contradiction(prediction, evidence)
    assert isinstance(level, ContradictionLevel)
