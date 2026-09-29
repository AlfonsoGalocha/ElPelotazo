"""Registro de herramientas ("tools") que el agente puede invocar.

Cada tool es un wrapper FINO sobre servicios que YA EXISTEN (round_service,
prediction/anomaly.py, etc): el agente nunca reimplementa logica de
negocio, solo la expone al LLM en un formato estructurado y compacto. Si
una tool no puede responder con datos reales, dice explicitamente que no
hay datos -- nunca rellena el hueco con una suposicion.

MVP (Fase 2): una sola tool, `get_matches_today`, que cubre el caso de uso
elegido como primer entregable ("que partidos hay hoy"). Anhadir una tool
nueva es: (1) escribir su handler aqui, (2) anhadirla a TOOLS. El
orquestador (`orchestrator.py`) no necesita cambios.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import best_prediction_per_match, compute_anomaly_flags
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.services.round_service import get_current_round


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # JSON Schema de los parametros, formato que espera la API de tool-use
    # de Anthropic (y, con el mismo shape, la de OpenAI/Gemini si algun dia
    # se anhade otro proveedor -- ver agent/llm.py).
    parameters: dict[str, Any]
    handler: Callable[[Session, dict[str, Any]], dict[str, Any]]


def _market_display_name(market_key: str) -> str:
    spec = MARKET_DEFINITIONS.get(market_key)
    return spec.display_name if spec else market_key


def _serialize_best_prediction(
    prediction: Prediction | None, siblings: list[Prediction]
) -> dict[str, Any] | None:
    if prediction is None:
        return None
    flags = compute_anomaly_flags(prediction, siblings, get_settings())
    market_probability = None
    if prediction.market_probability is not None:
        market_probability = round(prediction.market_probability, 4)
    return {
        "market": prediction.market,
        "market_display_name": _market_display_name(prediction.market),
        "selection": prediction.selection,
        "model_probability": round(prediction.model_probability, 4),
        "market_probability": market_probability,
        "market_odds": prediction.market_odds,
        "edge_pp": round(prediction.edge * 100, 2) if prediction.edge is not None else None,
        "signal_score": prediction.signal_score,
        "confidence": prediction.confidence,
        "anomaly_flags": flags,
    }


def get_matches_today(db: Session, _params: dict[str, Any]) -> dict[str, Any]:
    """Partidos de la jornada actual de cada competicion CORE (Premier,
    LaLiga, Bundesliga, Serie A, Ligue 1) cuyo kickoff todavia no ha
    pasado (misma regla que el Home: `round_service.get_current_round` ya
    excluye partidos con fecha pasada). Para cada uno, la MEJOR PREDICCION
    ya calculada por el sistema (`best_prediction_per_match`, el mismo
    criterio de `signal_score` que usa el resto de la app) -- nunca "la
    probabilidad mas alta" sin mas.
    """
    settings = get_settings()
    competitions = db.query(Competition).order_by(Competition.name).all()
    matches_out: list[dict[str, Any]] = []

    for competition in competitions:
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

        for match_id in round_info.match_ids:
            match_predictions = by_match.get(match_id, [])
            match = match_predictions[0].match if match_predictions else None
            if match is None:
                # Partido programado sin ninguna prediccion generada
                # todavia (p.ej. "predict-upcoming" no se ha corrido para
                # el desde que se ingirio el fixture) -- se reporta igual,
                # honestamente, sin prediccion.
                continue
            matches_out.append(
                {
                    "match_id": match_id,
                    "competition": competition.name,
                    "competition_code": competition.code,
                    "home_team": match.home_team.canonical_name,
                    "away_team": match.away_team.canonical_name,
                    "kickoff_utc": match.kickoff_utc.isoformat(),
                    "best_prediction": _serialize_best_prediction(
                        best_by_match.get(match_id), match_predictions
                    ),
                }
            )

    matches_out.sort(key=lambda m: m["kickoff_utc"])
    return {
        "date_checked_utc": dt.datetime.utcnow().isoformat(),
        "total_matches": len(matches_out),
        "matches": matches_out,
    }


TOOLS: list[Tool] = [
    Tool(
        name="get_matches_today",
        description=(
            "Devuelve los partidos de la jornada actual (proximos, nunca ya jugados) de las "
            "5 grandes ligas europeas, con la mejor prediccion del sistema para cada uno "
            "(si existe). Usa esta tool para responder preguntas como '¿que partidos hay "
            "hoy?', '¿que tenemos esta jornada?' o '¿hay algo interesante hoy?'."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=get_matches_today,
    ),
]

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}
