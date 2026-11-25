"""Motor de evidencia estadistica por mercado (Fase B de la revision v2.8).

Gap real identificado en la auditoria: ningun sitio del codigo expone, para
UNA prediccion concreta, la frecuencia historica REAL con la que su mercado
se cumplio en los partidos recientes de los dos equipos implicados (p.ej.
"en cuantos de los ultimos 10 partidos de estos dos equipos el total de
corners supero 9.5"). `features/form.py` solo guarda MEDIAS continuas
(goals_for_avg_last5, etc.) para entrenar modelos; nunca una frecuencia de
cumplir un UMBRAL concreto, ni pensado para consultarse por partido suelto.

Este modulo NO reentrena ni recalcula nada del pipeline de entrenamiento:
consulta directamente `Match`/`MatchStatistics`/`CornerObservation` para los
dos equipos del partido de la prediccion, igual que hace
`prediction/eligibility.py` para verificar corners. Disciplina anti-leakage
identica al resto del repo (`features/base.py`): toda fila historica debe
tener `kickoff_utc` ESTRICTAMENTE anterior al partido evaluado.

Diseño de las ventanas (documentado aqui, no disperso):
- "last5"/"last10"/"last20": forma reciente, SIN restriccion de temporada
  (igual convencion que `features/base.py::WINDOWS`, que tampoco corta por
  temporada) -- pooled entre los equipos relevantes (ver
  `_population_team_ids`).
- "season": todo lo disponible de la temporada EN CURSO del partido
  evaluado (`Match.season_id`), sin limite de partidos.
- "home"/"away": contexto de localia, independiente de temporada (todo el
  historial disponible) -- partidos en los que el equipo relevante jugo
  como local ("home") o como visitante ("away"). Para mercados ya
  restringidos a un lado (home_win, double_chance_1x, team_over local...)
  esto puede coincidir en parte con la poblacion ya usada en las demas
  ventanas; no es un error, es la misma pregunta vista sin recorte de
  temporada ni de recencia.

Que poblacion de equipo(s) alimenta cada mercado (ver `_population_team_ids`
y `_stat_value`): mercados simetricos (over/under de goles, corners, btts,
draw, double_chance_12) agregan el historial de AMBOS equipos; mercados de
un lado (home_win/away_win, double_chance_1x/x2, team_over local/visitante)
usan solo el historial del equipo relevante, y el propio valor ya filtra
las filas donde ese equipo no jugo en el lado que pide el mercado (ver
`_stat_value`: devuelve `None`, nunca 0, cuando la fila no aplica -- se
excluye del denominador en vez de contar como fallo).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.db.models.matches import CornerObservation, Match, MatchStatistics
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS, MARKET_DEFINITIONS
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS

RECENCY_WINDOWS: dict[str, int] = {"last5": 5, "last10": 10, "last20": 20}
# Tope de filas traidas para las ventanas "home"/"away" (todo el historial
# disponible, sin recorte de temporada, pero con un limite de cordura para
# no traer decadas de partidos de una sola consulta).
HOME_AWAY_FETCH_LIMIT = 60


@dataclass(frozen=True)
class WindowStat:
    mean: float | None
    median: float | None
    std: float | None
    n: int


@dataclass(frozen=True)
class MarketEvidence:
    market: str
    line: float | None
    windows: dict[str, WindowStat] = field(default_factory=dict)
    line_hit_frequency: dict[str, float | None] = field(default_factory=dict)
    effective_sample_size: float = 0.0
    data_coverage: float = 0.0


@dataclass(frozen=True)
class _TeamRow:
    match_id: int
    kickoff_utc: dt.datetime
    is_home: bool
    home_goals: int | None
    away_goals: int | None
    corners_total: int | None


def _latest_corner_total_subquery():
    return (
        select(CornerObservation.total)
        .where(CornerObservation.match_id == Match.id)
        .order_by(CornerObservation.id.desc())
        .limit(1)
        .correlate(Match)
        .scalar_subquery()
    )


def _fetch_team_rows(
    db: Session,
    team_id: int,
    before_kickoff: dt.datetime,
    *,
    season_id: int | None = None,
    limit: int | None = None,
) -> list[_TeamRow]:
    """Partidos FINALIZADOS de `team_id` con `kickoff_utc < before_kickoff`
    (anti-leakage: nunca `<=`, nunca info del propio partido evaluado ni de
    partidos futuros respecto a el)."""
    stmt = (
        select(
            Match.id,
            Match.kickoff_utc,
            Match.home_team_id,
            Match.home_goals,
            Match.away_goals,
            MatchStatistics.home_corners,
            MatchStatistics.away_corners,
            _latest_corner_total_subquery().label("observed_corner_total"),
        )
        .outerjoin(MatchStatistics, MatchStatistics.match_id == Match.id)
        .where(
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            Match.status == "finished",
            Match.kickoff_utc < before_kickoff,
        )
        .order_by(Match.kickoff_utc.desc())
    )
    if season_id is not None:
        stmt = stmt.where(Match.season_id == season_id)
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = db.execute(stmt).all()
    out: list[_TeamRow] = []
    for match_id, kickoff_utc, home_team_id, home_goals, away_goals, home_corners, away_corners, observed_total in rows:
        corners_total = observed_total
        if corners_total is None and home_corners is not None and away_corners is not None:
            corners_total = home_corners + away_corners
        out.append(
            _TeamRow(
                match_id=match_id,
                kickoff_utc=kickoff_utc,
                is_home=(home_team_id == team_id),
                home_goals=home_goals,
                away_goals=away_goals,
                corners_total=corners_total,
            )
        )
    return out


def _dedupe_by_match(rows: list[_TeamRow]) -> list[_TeamRow]:
    """Cuando la poblacion simetrica agrega dos equipos que ya se
    enfrentaron entre si en el pasado, ese partido aparece en el historial
    de AMBOS -- sin esto contaria doble (una vez por cada lado), inflando
    artificialmente su peso y el tamanho de muestra. Se queda la primera
    aparicion (el valor del mercado es el mismo independientemente de que
    equipo aporto la fila para los mercados simetricos donde esto se
    invoca -- ver docstring de `_stat_value`)."""
    seen: set[int] = set()
    out: list[_TeamRow] = []
    for row in rows:
        if row.match_id in seen:
            continue
        seen.add(row.match_id)
        out.append(row)
    return out


def _population_team_ids(market: str, match: Match) -> list[int]:
    if market in ("home_win", "double_chance_1x", "home_team_over_0_5", "home_team_over_1_5"):
        return [match.home_team_id]
    if market in ("away_win", "double_chance_x2", "away_team_over_0_5", "away_team_over_1_5"):
        return [match.away_team_id]
    # Simetricos: draw, double_chance_12, over/under de goles, btts, corners.
    return [match.home_team_id, match.away_team_id]


def _stat_value(market: str, row: _TeamRow) -> float | None:
    """Valor crudo relevante de ESTA fila historica para `market`, desde el
    punto de vista del equipo al que pertenece la fila (`row.is_home` dice
    si ESE equipo jugo de local en ese partido pasado). `None` (nunca 0)
    cuando la fila no aporta dato (estadistica ausente, o el mercado exige
    un lado que esta fila no cumple) -- se excluye del denominador, no se
    cuenta como fallo."""
    goals_spec = MARKET_DEFINITIONS.get(market)
    if goals_spec is not None:
        if goals_spec.kind in ("over", "under"):
            if row.home_goals is None or row.away_goals is None:
                return None
            return float(row.home_goals + row.away_goals)
        if goals_spec.kind == "btts":
            if row.home_goals is None or row.away_goals is None:
                return None
            return 1.0 if (row.home_goals > 0 and row.away_goals > 0) else 0.0
        if goals_spec.kind == "team_over":
            goals_for = row.home_goals if row.is_home else row.away_goals
            if goals_for is None:
                return None
            return float(goals_for)
        if goals_spec.kind == "match_result":
            if row.home_goals is None or row.away_goals is None:
                return None
            if market == "draw":
                return 1.0 if row.home_goals == row.away_goals else 0.0
            if market == "home_win":
                if not row.is_home:
                    return None
                return 1.0 if row.home_goals > row.away_goals else 0.0
            if market == "away_win":
                if row.is_home:
                    return None
                return 1.0 if row.away_goals > row.home_goals else 0.0
        if goals_spec.kind == "double_chance":
            if market not in DOUBLE_CHANCE_COMPONENTS:
                return None
            if row.home_goals is None or row.away_goals is None:
                return None
            if market == "double_chance_1x":
                if not row.is_home:
                    return None
                return 1.0 if row.home_goals >= row.away_goals else 0.0
            if market == "double_chance_x2":
                if row.is_home:
                    return None
                return 1.0 if row.away_goals >= row.home_goals else 0.0
            if market == "double_chance_12":
                return 1.0 if row.home_goals != row.away_goals else 0.0
        return None

    corners_spec = SECONDARY_MARKET_DEFINITIONS.get(market)
    if corners_spec is not None:
        if row.corners_total is None:
            return None
        return float(row.corners_total)

    raise ValueError(f"Mercado desconocido para el motor de evidencia: {market}")


def _line_satisfied(market: str, value: float, line: float | None) -> bool | None:
    """Si el valor crudo cumple la LINEA de la prediccion (`prediction.line`,
    nunca la del `MarketSpec` por defecto -- la cuota real liquidada puede
    diferir de la linea "canonica" del mercado)."""
    goals_spec = MARKET_DEFINITIONS.get(market)
    corners_spec = SECONDARY_MARKET_DEFINITIONS.get(market)
    spec = goals_spec or corners_spec
    if spec is None:
        return None
    if spec.kind not in ("over", "under", "team_over"):
        # btts/match_result/double_chance: el "valor crudo" YA es el
        # indicador 0/1 de si se cumplio -- no hay linea que comparar.
        return value >= 0.5
    effective_line = line if line is not None else spec.line
    if effective_line is None:
        return None
    return value < effective_line if spec.kind == "under" else value > effective_line


def _window_stat(values: list[float]) -> WindowStat:
    n = len(values)
    if n == 0:
        return WindowStat(mean=None, median=None, std=None, n=0)
    mean = sum(values) / n
    sorted_values = sorted(values)
    mid = n // 2
    median = (
        sorted_values[mid]
        if n % 2 == 1
        else (sorted_values[mid - 1] + sorted_values[mid]) / 2.0
    )
    std = None
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = variance ** 0.5
    return WindowStat(mean=mean, median=median, std=std, n=n)


def _hit_frequency(market: str, values: list[float], line: float | None) -> float | None:
    hits = [1.0 if _line_satisfied(market, v, line) else 0.0 for v in values]
    if not hits:
        return None
    return sum(hits) / len(hits)


def build_market_evidence(
    db: Session, prediction: Prediction, match: Match | None = None
) -> MarketEvidence:
    """Evidencia historica real para `prediction.market` (seccion 5/18 del
    brief v2.8). Nunca usa filas con `kickoff_utc >= match.kickoff_utc`."""
    if match is None:
        match = db.get(Match, prediction.match_id)
    if match is None:
        raise ValueError(f"No existe el partido {prediction.match_id} de la prediccion {prediction.id}")

    team_ids = _population_team_ids(prediction.market, match)

    windows: dict[str, WindowStat] = {}
    line_hit_frequency: dict[str, float | None] = {}

    fetched_total = 0
    usable_total = 0

    for window_name, n in RECENCY_WINDOWS.items():
        pooled_rows: list[_TeamRow] = []
        for team_id in team_ids:
            pooled_rows.extend(
                _fetch_team_rows(db, team_id, match.kickoff_utc, limit=n)
            )
        pooled_rows = _dedupe_by_match(pooled_rows)
        fetched_total += len(pooled_rows)
        values = [v for row in pooled_rows if (v := _stat_value(prediction.market, row)) is not None]
        usable_total += len(values)
        windows[window_name] = _window_stat(values)
        line_hit_frequency[window_name] = _hit_frequency(prediction.market, values, prediction.line)

    season_rows: list[_TeamRow] = []
    for team_id in team_ids:
        season_rows.extend(
            _fetch_team_rows(db, team_id, match.kickoff_utc, season_id=match.season_id)
        )
    season_rows = _dedupe_by_match(season_rows)
    fetched_total += len(season_rows)
    season_values = [v for row in season_rows if (v := _stat_value(prediction.market, row)) is not None]
    usable_total += len(season_values)
    windows["season"] = _window_stat(season_values)
    line_hit_frequency["season"] = _hit_frequency(prediction.market, season_values, prediction.line)

    all_time_rows: dict[int, list[_TeamRow]] = {
        team_id: _fetch_team_rows(db, team_id, match.kickoff_utc, limit=HOME_AWAY_FETCH_LIMIT)
        for team_id in team_ids
    }
    home_rows = [row for rows in all_time_rows.values() for row in rows if row.is_home]
    away_rows = [row for rows in all_time_rows.values() for row in rows if not row.is_home]
    fetched_total += len(home_rows) + len(away_rows)

    home_values = [v for row in home_rows if (v := _stat_value(prediction.market, row)) is not None]
    away_values = [v for row in away_rows if (v := _stat_value(prediction.market, row)) is not None]
    usable_total += len(home_values) + len(away_values)
    windows["home"] = _window_stat(home_values)
    windows["away"] = _window_stat(away_values)
    line_hit_frequency["home"] = _hit_frequency(prediction.market, home_values, prediction.line)
    line_hit_frequency["away"] = _hit_frequency(prediction.market, away_values, prediction.line)

    effective_sample_size = float(windows["season"].n or windows["last20"].n)
    data_coverage = (usable_total / fetched_total) if fetched_total > 0 else 0.0

    return MarketEvidence(
        market=prediction.market,
        line=prediction.line,
        windows=windows,
        line_hit_frequency=line_hit_frequency,
        effective_sample_size=effective_sample_size,
        data_coverage=data_coverage,
    )
