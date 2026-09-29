"""Generacion de alertas proactivas de Jarvis (Fase 6 del brief, seccion
13: "detectar nuevas cuotas, cambios importantes, predicciones con gran
diferencia respecto a mercado... y generar alertas").

Deliberadamente separado del motor de prediccion: esto NUNCA genera ni
modifica una `Prediction`, solo lee las que ya existen (via
`prediction/anomaly.py`, ya usado por /top-signals y el Home) y guarda
filas en `agent_alerts` cuando algo merece destacarse. Idempotente por la
UniqueConstraint(match_id, alert_type) del modelo: correr esto cada hora
(scheduler) nunca duplica alertas.

Entrega deliberadamente NO incluida todavia: envio real (email/Telegram/
push). No hay canal de notificacion configurado ni credenciales pedidas
por el usuario -- inventar uno seria fabricar infraestructura sin que nos
la hayan pedido. `get_active_alerts` (agent/tools.py) es, de momento, la
unica forma de "recibir" estas alertas: preguntandole a Jarvis.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent.tools import _market_display_name
from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import AgentAlert, Prediction
from backend.app.prediction.anomaly import (
    best_prediction_per_match,
    compute_anomaly_flags,
    is_strong_signal,
)
from backend.app.prediction.confidence import signal_tier
from backend.app.services.round_service import get_current_round

VALUE_SIGNAL = "SENAL_DE_VALOR"

_TIER_ORDER = {"LOW_DATA_SUPPORT": 0, "MEDIUM_DATA_SUPPORT": 1, "HIGH_DATA_SUPPORT": 2}

_ANOMALY_MESSAGES = {
    "CONTRADICCION": (
        "Contradicción detectada: dos mercados excluyentes del mismo partido "
        "tienen probabilidad de modelo > 50% a la vez (mercado {market}, modelo {model_pct}%)."
    ),
    "SENAL_BAJA_FIABILIDAD": (
        "Señal de baja fiabilidad en {market}: confianza {confidence} / calidad de datos "
        "{data_quality} (modelo {model_pct}%, mercado {market_pct})."
    ),
    "OUTLIER": (
        "Cuotas dispersas en {market}: consenso poco fiable pese a la cuota disponible "
        "(modelo {model_pct}%, mercado {market_pct})."
    ),
    "HIGH_PROBABILITY_LOW_VALUE": (
        "Alta probabilidad pero poco valor en {market}: modelo {model_pct}%, "
        "edge de solo {edge_pp}pp -- la cuota ya refleja lo obvio."
    ),
}


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}" if value is not None else "?"


def _build_anomaly_message(flag: str, prediction: Prediction) -> str:
    template = _ANOMALY_MESSAGES[flag]
    return template.format(
        market=_market_display_name(prediction.market),
        model_pct=_pct(prediction.model_probability),
        market_pct=_pct(prediction.market_probability),
        confidence=_pct(prediction.confidence),
        data_quality=_pct(prediction.data_quality),
        edge_pp=_pct(prediction.edge) if prediction.edge is not None else "?",
    )


def _upsert_alert(
    db: Session,
    existing_keys: set[tuple[int, str]],
    match_id: int,
    alert_type: str,
    prediction_id: int | None,
    message: str,
) -> bool:
    key = (match_id, alert_type)
    if key in existing_keys:
        return False
    db.add(
        AgentAlert(
            match_id=match_id, prediction_id=prediction_id, alert_type=alert_type, message=message
        )
    )
    existing_keys.add(key)
    return True


def generate_alerts_for_current_round(db: Session, settings: Settings | None = None) -> int:
    """Escanea la jornada actual de cada competicion vigilada y guarda
    alertas nuevas. Devuelve cuantas se crearon (0 si `agent_alerts_enabled`
    esta desactivado -- nunca genera nada sin que el usuario lo active)."""
    settings = settings or get_settings()
    if not settings.agent_alerts_enabled:
        return 0

    existing_keys = {(a.match_id, a.alert_type) for a in db.query(AgentAlert).all()}
    created = 0

    competitions = db.query(Competition).order_by(Competition.code).all()
    for competition in competitions:
        if settings.agent_alert_leagues and competition.code not in settings.agent_alert_leagues:
            continue
        round_info = get_current_round(db, competition.code)
        if round_info is None or not round_info.match_ids:
            continue

        predictions = (
            db.query(Prediction).filter(Prediction.match_id.in_(round_info.match_ids)).all()
        )
        by_match: dict[int, list[Prediction]] = {}
        for p in predictions:
            by_match.setdefault(p.match_id, []).append(p)
        best_by_match = best_prediction_per_match(by_match, settings)

        for match_id, match_predictions in by_match.items():
            for prediction in match_predictions:
                flags = compute_anomaly_flags(prediction, match_predictions, settings)
                for flag in flags:
                    # CONTRADICCION se reporta una sola vez por lado (si A
                    # contradice a B, procesar B tambien la detectaria de
                    # nuevo) -- el UniqueConstraint por (match, tipo) ya lo
                    # deduplica sin logica extra aqui.
                    message = _build_anomaly_message(flag, prediction)
                    if _upsert_alert(db, existing_keys, match_id, flag, prediction.id, message):
                        created += 1

            best = best_by_match.get(match_id)
            if best is None:
                continue
            flags = compute_anomaly_flags(best, match_predictions, settings)
            if not is_strong_signal(flags):
                continue
            edge_pp = (best.edge or 0.0) * 100
            tier = signal_tier(best.confidence, best.data_quality)
            if edge_pp >= settings.agent_alert_min_edge_pp and _TIER_ORDER.get(
                tier, 0
            ) >= _TIER_ORDER.get(settings.agent_alert_min_tier, 1):
                market_label = _market_display_name(best.market)
                message = (
                    f"Señal de valor en {market_label}: modelo {_pct(best.model_probability)}%, "
                    f"mercado {_pct(best.market_probability)}%, edge +{edge_pp:.1f}pp, "
                    f"confianza {tier.replace('_DATA_SUPPORT', '').title()}."
                )
                if _upsert_alert(db, existing_keys, match_id, VALUE_SIGNAL, best.id, message):
                    created += 1

    db.commit()
    return created
