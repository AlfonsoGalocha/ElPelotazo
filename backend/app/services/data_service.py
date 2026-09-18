"""Orquesta ingestion -> normalizacion -> persistencia."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOdds, MatchStatistics
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
from backend.app.ingestion.base import DataProvider, FixtureOddsSnapshot, RawMatchRecord
from backend.app.ingestion.odds.provider import OddsApiProvider
from backend.app.normalization.competitions import resolve_competition_id, resolve_season_id
from backend.app.normalization.players import resolve_referee_id
from backend.app.normalization.teams import resolve_team_id
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


def ingest_matches(
    db: Session, provider: DataProvider, competition_code: str, season_label: str, force_refresh: bool = False
) -> int:
    """Descarga y persiste los partidos de una (competicion, temporada).

    Idempotente: usa (provider, provider_id) como clave unica, asi que
    re-ejecutar la ingesta actualiza en vez de duplicar.

    `force_refresh`: para proveedores con cache en disco (hoy solo
    `ClubFootballMatchDataProvider`, ver su docstring de
    `refresh_cache()`), fuerza una re-descarga real antes de leer.
    Necesario para recoger resultados REALES recien jugados -- sin esto,
    una vez descargado el CSV la primera vez, nunca se volvia a comprobar
    si habia partidos nuevos, por mucho que se re-ejecutara `update`. Los
    proveedores sin cache (o sin este metodo) ignoran el flag sin error.
    """
    if not provider.is_available():
        logger.warning(
            "ingestion.provider_unavailable",
            extra={"provider": provider.name, "competition": competition_code},
        )
        return 0

    if force_refresh and hasattr(provider, "refresh_cache"):
        provider.refresh_cache()

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


DUPLICATE_MATCH_DRIFT = dt.timedelta(days=3)  # tolerancia para reconocer que dos
# registros de fuentes DISTINTAS (p.ej. openfootball_fixtures y
# club_football_match_data) son el MISMO partido real, cuando la fecha
# exacta pueda variar unas horas/dias entre fuentes.


def _find_absorbable_duplicate(
    db: Session, competition_id: int, season_id: int, home_team_id: int, away_team_id: int, kickoff: dt.datetime
) -> Match | None:
    """Busca un `Match` ya existente de OTRA fuente que sea, con altisima
    probabilidad, el MISMO partido real: mismo equipo local/visitante,
    misma competicion/temporada, fecha cercana.

    Sin esto, un partido ingerido primero como "scheduled" via
    `openfootball_fixtures` (fixtures futuros) y luego, una vez jugado,
    ingerido de nuevo como "finished" via `club_football_match_data`
    (historico) con un `provider_id` DISTINTO, crearia una fila DUPLICADA:
    la original se quedaria "scheduled" para siempre (huerfana, nunca se
    marca como jugada), mientras la nueva fila "finished" coexiste con
    ella. Ademas de la duplicacion en si, esto rompe cualquier logica de
    "jornada actual" basada en `status == 'scheduled'`, y perderia el
    `matchday` que so lo trae el fixture original.
    """
    candidates = (
        db.query(Match)
        .filter(
            Match.competition_id == competition_id,
            Match.season_id == season_id,
            Match.home_team_id == home_team_id,
            Match.away_team_id == away_team_id,
        )
        .all()
    )
    for candidate in candidates:
        if abs(candidate.kickoff_utc - kickoff) <= DUPLICATE_MATCH_DRIFT:
            return candidate
    return None


def _upsert_match(db: Session, record: RawMatchRecord, competition_id: int, season_id: int) -> Match:
    home_team_id = resolve_team_id(db, record.provider, record.home_team_raw)
    away_team_id = resolve_team_id(db, record.provider, record.away_team_raw)
    referee_id = resolve_referee_id(db, record.referee_raw)
    kickoff = dt.datetime.fromisoformat(record.date)

    match = (
        db.query(Match)
        .filter_by(provider=record.provider, provider_id=record.provider_id)
        .one_or_none()
    )
    is_finished = record.home_goals is not None and record.away_goals is not None

    if match is None:
        match = _find_absorbable_duplicate(
            db, competition_id, season_id, home_team_id, away_team_id, kickoff
        )
        if match is not None:
            logger.info(
                "ingestion.duplicate_match_absorbed: match_id=%d old_provider=%s new_provider=%s",
                match.id,
                match.provider,
                record.provider,
            )
            # Se adopta la fuente NUEVA (normalmente mas completa: el
            # historico trae stats/goles que el fixture nunca tuvo), pero
            # se conserva el `id` (y por tanto predicciones/cuotas ya
            # asociadas) y el `matchday` si ya estaba puesto y la fuente
            # nueva no trae uno.
            match.provider = record.provider
            match.provider_id = record.provider_id
            match.kickoff_utc = kickoff  # la fuente nueva suele ser mas autorizada (resultado ya jugado)

    if match is None:
        match = Match(
            provider=record.provider,
            provider_id=record.provider_id,
            competition_id=competition_id,
            season_id=season_id,
            kickoff_utc=kickoff,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            referee_id=referee_id,
        )
        db.add(match)

    if record.matchday is not None:
        match.matchday = record.matchday

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

    # SOLO se tocan las cuotas si esta fuente concreta las trae. Bug real
    # detectado: `openfootball_fixtures` (el calendario) NUNCA trae cuotas
    # (`record.odds` siempre `[]`) y `refresh` reingiere el calendario en
    # CADA ejecucion (`update-fixtures` corre siempre, no solo con
    # `--skip-historical`). Si el borrado fuera incondicional, cada
    # `refresh` borraria las cuotas reales ya conseguidas por
    # `attach_odds_to_scheduled_matches` en una ejecucion anterior, y solo
    # sobrevivirian si el intento de re-casarlas con la API de cuotas
    # (justo despues, en el mismo `refresh`) tenia exito para ESE partido
    # exacto otra vez — cualquier fallo parcial (nombre que no casa esa
    # vez, la API sin ese partido en la respuesta, limite de requests...)
    # y la cuota desaparecia para siempre hasta el proximo acierto. Al
    # exigir `record.odds` no vacio, una fuente que NUNCA trae cuotas
    # simplemente nunca las toca, sean cuales sean.
    if record.odds:
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


def _attach_odds_snapshots(
    db: Session,
    snapshots: list[FixtureOddsSnapshot],
    provider_name: str,
    competition_id: int,
    log_prefix: str,
) -> OddsAttachResult:
    """Logica COMUN a cualquier fuente de cuotas (The Odds API, API-Football...):
    casar cada snapshot por equipo (normalizado al mismo team_id que el
    resto del sistema) + proximidad de fecha contra un `Match` YA
    EXISTENTE y programado. Nunca crea partidos nuevos: si no hay un
    `Match` que case, esa cuota se descarta (se devuelve como ejemplo en
    `unmatched_examples` en vez de perderse en silencio)."""
    scheduled = (
        db.query(Match)
        .filter(Match.competition_id == competition_id, Match.status == "scheduled")
        .all()
    )

    matched_match_ids: list[int] = []
    unmatched_examples: list[tuple[str, str]] = []
    for snapshot in snapshots:
        home_team_id = resolve_team_id(db, provider_name, snapshot.home_team_raw)
        away_team_id = resolve_team_id(db, provider_name, snapshot.away_team_raw)

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
                "%s.no_match_found: home=%s away=%s", log_prefix, snapshot.home_team_raw, snapshot.away_team_raw
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
                    # "pre_match", no "live": son cuotas tomadas ANTES del
                    # partido para un fixture programado, nunca cuotas en
                    # vivo durante el juego (esta fuente no las provee).
                    snapshot_type="pre_match",
                    recorded_at=dt.datetime.utcnow(),
                )
            )
        matched_match_ids.append(match.id)

    db.commit()
    logger.info(
        "%s.completed: fixtures_fetched=%d matched=%d", log_prefix, len(snapshots), len(matched_match_ids)
    )
    return OddsAttachResult(
        fixtures_fetched=len(snapshots),
        matched=len(matched_match_ids),
        matched_match_ids=matched_match_ids,
        unmatched_examples=unmatched_examples,
    )


def attach_odds_to_scheduled_matches(
    db: Session, provider: OddsApiProvider, competition_code: str
) -> OddsAttachResult:
    """Descarga cuotas REALES de goles/1X2 de partidos futuros (The Odds
    API) y las asocia a los `Match` ya existentes (creados por
    `update-fixtures`)."""
    if not provider.is_available():
        logger.warning("ingestion.odds_api.unavailable", extra={"competition": competition_code})
        return OddsAttachResult(fixtures_fetched=0, matched=0, matched_match_ids=[], unmatched_examples=[])

    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    snapshots = provider.fetch_odds(competition_code)
    return _attach_odds_snapshots(db, snapshots, provider.name, competition.id, "ingestion.odds_api")


def attach_secondary_odds_to_scheduled_matches(
    db: Session,
    provider: ApiFootballOddsProvider,
    competition_code: str,
    season_year: int,
    date_from: dt.date,
    date_to: dt.date,
) -> OddsAttachResult:
    """Descarga cuotas REALES de tarjetas/corners (API-Football) para los
    partidos entre `date_from` y `date_to` (pensado para la ventana de la
    jornada actual, ver services/round_service.py: pedir la temporada
    completa agotaria el cupo gratuito de 100 requests/dia) y las asocia a
    los `Match` ya existentes."""
    if not provider.is_available():
        logger.warning("ingestion.api_football.unavailable", extra={"competition": competition_code})
        return OddsAttachResult(fixtures_fetched=0, matched=0, matched_match_ids=[], unmatched_examples=[])

    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    snapshots = provider.fetch_secondary_odds(competition_code, season_year, date_from, date_to)
    return _attach_odds_snapshots(db, snapshots, provider.name, competition.id, "ingestion.api_football")
