"""Orquesta ingestion -> normalizacion -> persistencia."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOdds, MatchStatistics
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.ingestion.odds.provider import OddsApiProvider
from backend.app.normalization.competitions import resolve_competition_id, resolve_season_id
from backend.app.normalization.players import resolve_referee_id
from backend.app.normalization.teams import resolve_team_id
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


def ingest_matches(db: Session, provider: DataProvider, competition_code: str, season_label: str) -> int:
    """Descarga y persiste los partidos de una (competicion, temporada).

    Idempotente: usa (provider, provider_id) como clave unica, asi que
    re-ejecutar la ingesta actualiza en vez de duplicar.
    """
    if not provider.is_available():
        logger.warning(
            "ingestion.provider_unavailable",
            extra={"provider": provider.name, "competition": competition_code},
        )
        return 0

    records = provider.fetch_matches(competition_code, season_label)
    competition_id = resolve_competition_id(db, competition_code)
    season_id = resolve_season_id(db, competition_id, season_label)

    count = 0
    for record in records:
        _upsert_match(db, record, competition_id, season_id)
        count += 1
    db.commit()
    logger.info(
        "ingestion.completed",
        extra={"provider": provider.name, "competition": competition_code, "season": season_label, "count": count},
    )
    return count


def _upsert_match(db: Session, record: RawMatchRecord, competition_id: int, season_id: int) -> Match:
    home_team_id = resolve_team_id(db, record.provider, record.home_team_raw)
    away_team_id = resolve_team_id(db, record.provider, record.away_team_raw)
    referee_id = resolve_referee_id(db, record.referee_raw)

    match = (
        db.query(Match)
        .filter_by(provider=record.provider, provider_id=record.provider_id)
        .one_or_none()
    )
    is_finished = record.home_goals is not None and record.away_goals is not None
    if match is None:
        match = Match(
            provider=record.provider,
            provider_id=record.provider_id,
            competition_id=competition_id,
            season_id=season_id,
            kickoff_utc=dt.datetime.fromisoformat(record.date),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            referee_id=referee_id,
        )
        db.add(match)

    match.home_goals = record.home_goals
    match.away_goals = record.away_goals
    match.home_goals_ht = record.home_goals_ht
    match.away_goals_ht = record.away_goals_ht
    match.status = "finished" if is_finished else "scheduled"
    db.flush()

    if match.statistics is None:
        match.statistics = MatchStatistics(match_id=match.id)
    stats = match.statistics
    stats.home_shots = record.home_shots
    stats.away_shots = record.away_shots
    stats.home_shots_on_target = record.home_shots_on_target
    stats.away_shots_on_target = record.away_shots_on_target
    stats.home_corners = record.home_corners
    stats.away_corners = record.away_corners
    stats.home_fouls = record.home_fouls
    stats.away_fouls = record.away_fouls
    stats.home_yellow_cards = record.home_yellow_cards
    stats.away_yellow_cards = record.away_yellow_cards
    stats.home_red_cards = record.home_red_cards
    stats.away_red_cards = record.away_red_cards

    db.query(MatchOdds).filter_by(match_id=match.id).delete()
    for odds in record.odds:
        db.add(
            MatchOdds(
                match_id=match.id,
                bookmaker=odds.bookmaker,
                market=odds.market,
                line=odds.line,
                selection=odds.selection,
                price=odds.price,
                snapshot_type=odds.snapshot_type,
                recorded_at=match.kickoff_utc,
            )
        )
    db.flush()
    return match


MAX_KICKOFF_DRIFT = dt.timedelta(days=2)  # tolerancia entre la fecha del fixture
# ingerido (openfootball) y la que reporta la API de cuotas (pueden diferir en
# horas por huso horario, o en un dia si una fuente aun no reflejo un
# aplazamiento) al intentar casar ambas por equipo+fecha.


@dataclass
class OddsAttachResult:
    """Resultado detallado de `attach_odds_to_scheduled_matches`, pensado
    para poder diagnosticar en la terminal (no solo en logs que pueden pasar
    desapercibidos) POR QUE no se encontraron cuotas si es el caso: cuenta
    distinto "la API no devolvio partidos para esta liga ahora mismo" de
    "la API devolvio partidos pero ninguno caso por nombre de equipo/fecha".
    """

    fixtures_fetched: int
    matched: int
    matched_match_ids: list[int]
    unmatched_examples: list[tuple[str, str]]  # (home_raw, away_raw), maximo 5


def attach_odds_to_scheduled_matches(
    db: Session, provider: OddsApiProvider, competition_code: str
) -> OddsAttachResult:
    """Descarga cuotas REALES de partidos futuros y las asocia a los
    `Match` ya existentes (creados por `update-fixtures`), casando por
    equipo (normalizado al mismo team_id que el resto del sistema) y
    proximidad de fecha. No crea partidos nuevos: si no hay un `Match`
    programado que case, esa cuota se descarta (se devuelve como ejemplo
    en `unmatched_examples`, nunca se inventa un partido para colocarla).
    """
    if not provider.is_available():
        logger.warning("ingestion.odds_api.unavailable", extra={"competition": competition_code})
        return OddsAttachResult(fixtures_fetched=0, matched=0, matched_match_ids=[], unmatched_examples=[])

    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    snapshots = provider.fetch_odds(competition_code)
    scheduled = (
        db.query(Match)
        .filter(Match.competition_id == competition.id, Match.status == "scheduled")
        .all()
    )

    matched_match_ids: list[int] = []
    unmatched_examples: list[tuple[str, str]] = []
    for snapshot in snapshots:
        home_team_id = resolve_team_id(db, provider.name, snapshot.home_team_raw)
        away_team_id = resolve_team_id(db, provider.name, snapshot.away_team_raw)

        match = next(
            (
                m
                for m in scheduled
                if m.home_team_id == home_team_id
                and m.away_team_id == away_team_id
                and abs(m.kickoff_utc - snapshot.commence_time.replace(tzinfo=None)) <= MAX_KICKOFF_DRIFT
            ),
            None,
        )
        if match is None:
            logger.info(
                "ingestion.odds_api.no_match_found: home=%s away=%s",
                snapshot.home_team_raw,
                snapshot.away_team_raw,
            )
            if len(unmatched_examples) < 5:
                unmatched_examples.append((snapshot.home_team_raw, snapshot.away_team_raw))
            continue

        for odds in snapshot.odds:
            db.query(MatchOdds).filter_by(
                match_id=match.id, bookmaker=odds.bookmaker, market=odds.market, line=odds.line, selection=odds.selection
            ).delete()
            db.add(
                MatchOdds(
                    match_id=match.id,
                    bookmaker=odds.bookmaker,
                    market=odds.market,
                    line=odds.line,
                    selection=odds.selection,
                    price=odds.price,
                    snapshot_type="live",
                    recorded_at=dt.datetime.utcnow(),
                )
            )
        matched_match_ids.append(match.id)

    db.commit()
    logger.info(
        "ingestion.odds_api.completed: competition=%s fixtures_fetched=%d matched=%d",
        competition_code,
        len(snapshots),
        len(matched_match_ids),
    )
    return OddsAttachResult(
        fixtures_fetched=len(snapshots),
        matched=len(matched_match_ids),
        matched_match_ids=matched_match_ids,
        unmatched_examples=unmatched_examples,
    )
