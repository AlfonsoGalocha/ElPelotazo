"""Definicion unica de los mercados de goles del MVP (Fase 1).

Un solo lugar define, para cada mercado, tanto (a) como etiquetar el
resultado real de un partido finalizado (para entrenar/evaluar) como (b) como
leer la probabilidad correspondiente de una matriz de marcador conjunta
(para cualquier modelo). Evita que ambos calculos diverjan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MarketStatus = str  # "CORE" | "FUTURE" -- ver seccion 26/CompetitionConfig


@dataclass(frozen=True)
class MarketSpec:
    display_name: str
    kind: str  # "over" | "under" | "btts" | "match_result" | "double_chance" | "team_over"
    line: float | None = None
    team: str | None = None  # solo para "team_over": "home" | "away"


MARKET_DEFINITIONS: dict[str, MarketSpec] = {
    "over_1_5": MarketSpec("Over 1.5 Goals", "over", 1.5),
    "over_2_5": MarketSpec("Over 2.5 Goals", "over", 2.5),
    "under_2_5": MarketSpec("Under 2.5 Goals", "under", 2.5),
    "over_3_5": MarketSpec("Over 3.5 Goals", "over", 3.5),
    "btts": MarketSpec("Both Teams To Score", "btts", None),
    # 1X2: tres mercados BINARIOS independientes ("¿gana el local?", "¿empata?",
    # "¿gana el visitante?"), no uno multiclase -- encaja con la arquitectura
    # existente (un clasificador binario por mercado en ml_classifier.py, un
    # score_matrix -> probabilidad por mercado en probability.py) sin tocar
    # nada de esa infraestructura. `line` no aplica (None, igual que btts).
    "home_win": MarketSpec("Victoria Local (1)", "match_result", None),
    "draw": MarketSpec("Empate (X)", "match_result", None),
    "away_win": MarketSpec("Victoria Visitante (2)", "match_result", None),
    # Doble oportunidad: union de dos de los tres resultados de 1X2 (ver
    # DOUBLE_CHANCE_COMPONENTS). El modelo la calcula igual que cualquier
    # otro mercado (mismo score_matrix); la cuota de MERCADO no se ingesta
    # aparte (ningun proveedor actual la trae) sino que se DERIVA sumando
    # las probabilidades sin vig ya calculadas para sus dos componentes de
    # h2h -- ver services/prediction_service.py::_add_derived_double_chance_quotes.
    # Por eso siempre hay cuota de mercado si la hay para h2h (garantizado
    # a diferencia de btts/alternate_totals, que dependen de que una casa
    # concreta ofrezca ese mercado exacto).
    "double_chance_1x": MarketSpec("Doble Oportunidad 1X (Local o Empate)", "double_chance", None),
    "double_chance_x2": MarketSpec("Doble Oportunidad X2 (Empate o Visitante)", "double_chance", None),
    "double_chance_12": MarketSpec("Doble Oportunidad 12 (Local o Visitante)", "double_chance", None),
    # Goles de un equipo: NINGUNA fuente actual trae esta cuota (The Odds
    # API no la ofrece en el plan usado, ver docs/data_sources.md), asi que
    # queda siempre como "prediccion del modelo -- sin mercado"
    # (`/predictions/model-only`) hasta que se integre una fuente que si la
    # traiga -- nunca se inventa `market_probability`/`market_odds`.
    "home_team_over_0_5": MarketSpec("Local marca (Over 0.5)", "team_over", 0.5, "home"),
    "home_team_over_1_5": MarketSpec("Local Over 1.5 goles", "team_over", 1.5, "home"),
    "away_team_over_0_5": MarketSpec("Visitante marca (Over 0.5)", "team_over", 0.5, "away"),
    "away_team_over_1_5": MarketSpec("Visitante Over 1.5 goles", "team_over", 1.5, "away"),
}

# Selection en MatchOdds/RawOddsRecord (ver ingestion/odds/provider.py y
# history_dataset.py: mismo "match_result" market, selecciones
# "home"/"draw"/"away") para cada market_key de 1X2 -- unico punto que
# traduce entre el nombre interno del mercado y la selection real del
# proveedor de cuotas, para que no diverjan si se toca uno sin el otro.
MATCH_RESULT_SELECTIONS: dict[str, str] = {"home_win": "home", "draw": "draw", "away_win": "away"}

# Doble oportunidad = union de estos dos mercados de 1X2 (mismo orden que
# el nombre: "1X" = home_win U draw, etc). Unico punto que define la
# composicion, usado tanto para etiquetar/derivar probabilidad del modelo
# aqui como para derivar la cuota de mercado en prediction_service.py.
DOUBLE_CHANCE_COMPONENTS: dict[str, tuple[str, str]] = {
    "double_chance_1x": ("home_win", "draw"),
    "double_chance_x2": ("draw", "away_win"),
    "double_chance_12": ("home_win", "away_win"),
}

# Grupos de mercados MUTUAMENTE EXCLUYENTES y EXHAUSTIVOS (sus probabilidades
# reales suman exactamente 1): usados para (a) el test de consistencia y
# (b) `renormalize_mutually_exclusive_groups` mas abajo.
#
# RAIZ DEL PROBLEMA (seccion 2 de la revision de arquitectura, verificada
# leyendo el codigo, no asumida): `probability_from_score_matrix` (mas abajo)
# es internamente consistente por construccion -- todas las probabilidades
# de un grupo salen de particionar LA MISMA matriz conjunta de marcador, asi
# que su suma es 1 por definicion matematica. Pero `MarketClassifierModel`
# (models/goals/ml_classifier.py) entrena un `LogisticRegression` INDEPENDIENTE
# por cada mercado (uno para "home_win", otro para "draw", otro para
# "away_win", otro para "over_2_5", otro para "under_2_5"...), cada uno
# optimizando SU PROPIO log loss sin ninguna restriccion conjunta. El
# ensemble (`models/ensemble/ensemble.py`) mezcla esa probabilidad ML con la
# estadistica usando un peso `w` APRENDIDO POR MERCADO por separado
# (`learn_ensemble_weight`, en `services/model_service.py`): nada garantiza
# que los pesos de "home_win"/"draw"/"away_win" (o de "over_2_5"/"under_2_5")
# sean iguales entre si, asi que la mezcla final puede (y en la practica
# ocurre) dejar de sumar 1 exactamente en cuanto el peso ML es > 0 para
# alguno de los mercados del grupo.
#
# Esto NO es un caso raro de floating point: es estructural, y crece con la
# discrepancia entre el modelo estadistico y el clasificador ML.
#
# FIX (no es "esconder uno de los dos", es forzar la restriccion matematica
# real -- ver `renormalize_mutually_exclusive_groups`): proyectar el vector
# de probabilidades del grupo sobre el simplex (sum=1) dividiendo cada una
# por la suma del grupo, PROPORCIONALMENTE. Esto conserva toda la
# informacion relativa que aporta el ensemble (que seleccion es mas probable
# que otra, y por cuanto) mientras restaura la restriccion que el modelo
# conjunto de marcador ya cumplia. La solucion arquitectonica completa
# (un clasificador multinomial conjunto restringido al simplex, en vez de
# clasificadores binarios independientes) queda documentada como trabajo
# futuro en docs/architecture_audit.md: es la correccion definitiva, pero
# esta renormalizacion es matematicamente principiada (no un parche
# cosmetico) y corrige el sintoma con el mismo mecanismo causante
# identificado arriba.
MUTUALLY_EXCLUSIVE_GROUPS: list[tuple[str, ...]] = [
    ("home_win", "draw", "away_win"),
    ("over_2_5", "under_2_5"),
]


def renormalize_mutually_exclusive_groups(
    probabilities: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Proyecta cada grupo de `MUTUALLY_EXCLUSIVE_GROUPS` presente por
    completo en `probabilities` sobre el simplex (suma=1), fila a fila.

    Deja intactos los mercados que no forman parte de ningun grupo (btts,
    double_chance derivado, team_over, over_1_5/3_5 sin under complementario
    todavia en el MVP). Si algun grupo no esta completo en el dict (p.ej.
    llamando esto sobre un subconjunto de mercados) se ignora sin fallar.
    """
    out = dict(probabilities)
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        if not all(key in out for key in group):
            continue
        stacked = np.stack([np.asarray(out[key], dtype=float) for key in group], axis=0)
        group_sum = stacked.sum(axis=0)
        # group_sum == 0 solo puede pasar si todas las probabilidades del
        # grupo son 0 para esa fila (degenerado); en ese caso se reparte
        # uniformemente en vez de dividir por cero.
        safe_sum = np.where(group_sum > 0, group_sum, 1.0)
        normalized = stacked / safe_sum
        normalized = np.where(group_sum > 0, normalized, 1.0 / len(group))
        for idx, key in enumerate(group):
            out[key] = normalized[idx]
    return out


def label_for_market(table: pd.DataFrame, market_key: str) -> pd.Series:
    """Etiqueta booleana real (True = la seleccion se cumplio) para un partido finalizado."""
    spec = MARKET_DEFINITIONS[market_key]

    if spec.kind == "match_result":
        if market_key == "home_win":
            return table["home_goals"] > table["away_goals"]
        if market_key == "draw":
            return table["home_goals"] == table["away_goals"]
        if market_key == "away_win":
            return table["home_goals"] < table["away_goals"]
        raise ValueError(f"Mercado 1X2 desconocido: {market_key}")

    if spec.kind == "double_chance":
        component_a, component_b = DOUBLE_CHANCE_COMPONENTS[market_key]
        return label_for_market(table, component_a) | label_for_market(table, component_b)

    if spec.kind == "team_over":
        goals = table["home_goals"] if spec.team == "home" else table["away_goals"]
        return goals > spec.line

    total_goals = table["home_goals"] + table["away_goals"]
    if spec.kind == "over":
        return total_goals > spec.line
    if spec.kind == "under":
        return total_goals < spec.line
    if spec.kind == "btts":
        return (table["home_goals"] > 0) & (table["away_goals"] > 0)
    raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")


def _match_result_mask(home_grid: np.ndarray, away_grid: np.ndarray, market_key: str) -> np.ndarray:
    if market_key == "home_win":
        return home_grid > away_grid
    if market_key == "draw":
        return home_grid == away_grid
    if market_key == "away_win":
        return home_grid < away_grid
    raise ValueError(f"Mercado 1X2 desconocido: {market_key}")


def probability_from_score_matrix(score_matrix: np.ndarray, market_key: str) -> np.ndarray:
    """`score_matrix`: shape (n_rows, K+1, K+1) con score_matrix[i, h, a] =
    P(home=h, away=a) para el partido i (h,a en 0..K, K = ultima celda = "K+").

    Devuelve un array (n_rows,) con la probabilidad del mercado pedido.
    """
    spec = MARKET_DEFINITIONS[market_key]
    n, k_plus_1, _ = score_matrix.shape

    home_grid, away_grid = np.meshgrid(np.arange(k_plus_1), np.arange(k_plus_1), indexing="ij")
    # La celda "max_k" representa "max_k o mas": para sumas de goles totales,
    # aproximamos su valor puntual como max_k (conservador para lineas .5,
    # ya que max_k es un entero exacto y las lineas del MVP son todas .5).
    total_goals_grid = home_grid + away_grid

    if spec.kind == "over":
        mask = total_goals_grid > spec.line
    elif spec.kind == "under":
        mask = total_goals_grid < spec.line
    elif spec.kind == "btts":
        mask = (home_grid > 0) & (away_grid > 0)
    elif spec.kind == "team_over":
        team_grid = home_grid if spec.team == "home" else away_grid
        mask = team_grid > spec.line
    elif spec.kind == "match_result":
        mask = _match_result_mask(home_grid, away_grid, market_key)
    elif spec.kind == "double_chance":
        component_a, component_b = DOUBLE_CHANCE_COMPONENTS[market_key]
        # Los 3 resultados de 1X2 son mutuamente excluyentes: la union de
        # dos de ellos es simplemente el OR de sus masks, sin solapamiento.
        mask = _match_result_mask(home_grid, away_grid, component_a) | _match_result_mask(
            home_grid, away_grid, component_b
        )
    else:
        raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")

    return np.array([score_matrix[i][mask].sum() for i in range(n)])
