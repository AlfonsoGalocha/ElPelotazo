"""Orquesta ingestion -> normalizacion -> persistencia."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.matches import Match, MatchOdds, MatchStatistics
from backend.app.ingestion.base import DataProvider, RawMatchRecord
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
