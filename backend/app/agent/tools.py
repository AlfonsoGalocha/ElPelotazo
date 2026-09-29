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

from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition, Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import AgentAlert, Prediction
from backend.app.prediction.anomaly import best_prediction_per_match, compute_anomaly_flags
from backend.app.prediction.confidence import signal_tier
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.services.evaluation_service import real_performance_report
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


def _serialize_prediction_for_match(
    prediction: Prediction, siblings: list[Prediction]
) -> dict[str, Any]:
    """Version COMPLETA (no solo la mejor) para `analyze_match`: incluye
    los factores reales del modelo (`explanation.factors`, mismos que usa
    /predictions/{id}/detail) para que el agente pueda explicar el "por
    que" sin inventar nada que el modelo no pueda respaldar."""
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
        "fair_odds": round(prediction.fair_odds, 4),
        "edge_pp": round(prediction.edge * 100, 2) if prediction.edge is not None else None,
        "confidence": prediction.confidence,
        "data_quality": prediction.data_quality,
        "signal_tier": signal_tier(prediction.confidence, prediction.data_quality),
        "anomaly_flags": flags,
        # Factores REALES usados por el modelo (nunca inventados) -- mismo
        # campo que ya expone /predictions/{id}/detail.
        "factors": prediction.explanation.get("factors", []) if prediction.explanation else [],
    }


def _find_match(db: Session, query_text: str) -> Match | None:
    """Busca un partido por nombre de equipo(s), MISMA logica (substring,
    sin similitud difusa) que `GET /matches?search=` -- nunca fuzzy
    matching ambiguo (principio del proyecto, ver normalization/teams.py).
    Si la consulta trae "vs"/" - "/"contra", intenta casar un equipo a cada
    lado; si no, busca ese texto en cualquiera de los dos equipos. De entre
    los candidatos, el mas cercano a AHORA (pasado o futuro)."""
    query_text = query_text.strip()
    HomeTeam, AwayTeam = aliased(Team), aliased(Team)
    base = db.query(Match).join(HomeTeam, Match.home_team_id == HomeTeam.id).join(
        AwayTeam, Match.away_team_id == AwayTeam.id
    )

    split = None
    for sep in (" vs ", " vs. ", " - ", " contra "):
        if sep in query_text.lower():
            idx = query_text.lower().index(sep)
            split = (query_text[: idx].strip(), query_text[idx + len(sep) :].strip())
            break

    if split:
        team_a, team_b = f"%{split[0]}%", f"%{split[1]}%"
        candidates = base.filter(
            or_(
                (HomeTeam.canonical_name.ilike(team_a) & AwayTeam.canonical_name.ilike(team_b)),
                (HomeTeam.canonical_name.ilike(team_b) & AwayTeam.canonical_name.ilike(team_a)),
            )
        ).all()
    else:
        pattern = f"%{query_text}%"
        candidates = base.filter(
            or_(HomeTeam.canonical_name.ilike(pattern), AwayTeam.canonical_name.ilike(pattern))
        ).all()

    if not candidates:
        return None
    now = dt.datetime.utcnow()
    candidates.sort(key=lambda m: abs((m.kickoff_utc - now).total_seconds()))
    return candidates[0]


def analyze_match(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Analiza un partido concreto: TODAS las predicciones del sistema
    para el (resultado, goles, BTTS...), la mejor prediccion segun
    `signal_score`, y el resultado real si ya termino. Nunca inventa un
    partido que no exista en la base de datos."""
    query_text = str(params.get("query", "")).strip()
    if not query_text:
        return {"error": "Falta el parametro 'query' (ej. 'Barcelona' o 'Real Madrid vs Sevilla')."}

    match = _find_match(db, query_text)
    if match is None:
        return {
            "error": (
                f"No se encontro ningun partido para {query_text!r}. Puede que el equipo "
                "este mal escrito, no juegue en las 5 ligas cubiertas, o no se haya "
                "ingerido ese fixture todavia."
            )
        }

    predictions = db.query(Prediction).filter(Prediction.match_id == match.id).all()
    best = best_prediction_per_match({match.id: predictions}, get_settings()).get(match.id)

    return {
        "match_id": match.id,
        "competition": match.competition.name,
        "home_team": match.home_team.canonical_name,
        "away_team": match.away_team.canonical_name,
        "kickoff_utc": match.kickoff_utc.isoformat(),
        "status": match.status,
        "final_score": (
            f"{match.home_goals}-{match.away_goals}"
            if match.status == "finished" and match.home_goals is not None
            else None
        ),
        "predictions": [_serialize_prediction_for_match(p, predictions) for p in predictions],
        "best_prediction": _serialize_best_prediction(best, predictions),
    }


def get_model_performance(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Rendimiento REAL del modelo (accuracy, Brier score, log loss,
    calibracion, ROI) calculado SOLO sobre predicciones ya liquidadas
    contra el resultado real -- nunca un backtest sintetico. Usa esto para
    '¿como va nuestro modelo?', '¿que tal funciona en Premier?', '¿donde
    tenemos peor calibracion?'. Si no hay predicciones liquidadas
    todavia, lo dice explicitamente en vez de inventar numeros."""
    competition_code = params.get("competition_code") or None
    market = params.get("market") or None
    return real_performance_report(db, competition_code, market, None)


def get_active_alerts(db: Session, _params: dict[str, Any]) -> dict[str, Any]:
    """Alertas proactivas ya generadas (contradicciones, senhales de baja
    fiabilidad, outliers, senhales de valor) de la jornada actual --
    ver agent/alerts.py. Si `AGENT_ALERTS_ENABLED` esta desactivado o no
    se ha ejecutado todavia la generacion, la lista viene vacia (no es un
    error: se dice explicitamente por que)."""
    settings = get_settings()
    if not settings.agent_alerts_enabled:
        return {
            "enabled": False,
            "alerts": [],
            "note": "Las alertas proactivas estan desactivadas (AGENT_ALERTS_ENABLED=false).",
        }
    alerts = db.query(AgentAlert).order_by(AgentAlert.created_at.desc()).limit(30).all()
    return {
        "enabled": True,
        "alerts": [
            {
                "match_id": a.match_id,
                "home_team": a.match.home_team.canonical_name,
                "away_team": a.match.away_team.canonical_name,
                "alert_type": a.alert_type,
                "message": a.message,
                "created_at": a.created_at.isoformat(),
            }
            for a in alerts
        ],
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
    Tool(
        name="analyze_match",
        description=(
            "Analiza un partido concreto en profundidad: todas las predicciones del sistema "
            "(resultado, goles, BTTS...), la mejor prediccion, y el resultado real si ya "
            "termino. Usa esta tool cuando el usuario pregunte por un partido/equipo "
            "concreto, p.ej. 'analiza el Barcelona - Getafe' o '¿que dice el modelo del "
            "Real Madrid?'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Nombre de uno o los dos equipos, ej. 'Barcelona' o 'Barcelona vs Getafe'."
                    ),
                }
            },
            "required": ["query"],
        },
        handler=analyze_match,
    ),
    Tool(
        name="get_model_performance",
        description=(
            "Rendimiento REAL historico del modelo (accuracy, Brier score, calibracion, ROI), "
            "calculado solo sobre predicciones ya liquidadas contra el resultado real. Usa esta "
            "tool para '¿como va nuestro modelo?', '¿que tal en Premier?', '¿esta mejorando?'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "competition_code": {
                    "type": "string",
                    "description": (
                        "Filtra por competicion, ej. 'premier_league', 'laliga'. Omitir para todas."
                    ),
                },
                "market": {
                    "type": "string",
                    "description": "Filtra por mercado, ej. 'over_2_5', 'btts'. Omitir para todos.",
                },
            },
            "required": [],
        },
        handler=get_model_performance,
    ),
    Tool(
        name="get_active_alerts",
        description=(
            "Alertas proactivas ya detectadas por el sistema (contradicciones entre mercados, "
            "senhales de baja fiabilidad, cuotas outlier, senhales de valor) de la jornada "
            "actual. Usa esta tool para '¿hay alguna alerta?', '¿algo raro en las cuotas?'."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=get_active_alerts,
    ),
]

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}
