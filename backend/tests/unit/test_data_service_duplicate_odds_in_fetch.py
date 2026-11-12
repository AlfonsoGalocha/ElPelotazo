"""Bug real reportado por el usuario (confirmado con datos reales de su
BD: el mismo bookmaker, mercado y linea repetido hasta 10 veces con
precios muy distintos): si una unica respuesta de la API trae el mismo
(bookmaker, market, line, selection) mas de una vez, `Query.delete()`
borra contra la BD de verdad pero las filas anhadidas con `db.add()` en
esa MISMA pasada no se han volcado todavia -- cada entrada repetida se
insertaba sin borrar a las anteriores, acumulando duplicados con precios
distintos en vez de quedarse con uno solo."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.services.data_service import attach_odds_to_scheduled_matches


class _FakeOddsProvider:
    name = "test_odds_provider"

    def __init__(self, snapshots: list[FixtureOddsSnapshot]):
        self._snapshots = snapshots

    def is_available(self) -> bool:
        return True

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        return self._snapshots


def test_duplicate_bookmaker_odds_in_a_single_fetch_do_not_accumulate():
    competition_code = "duplicate_odds_test_league"
    from backend.app.normalization.competitions import COMPETITIONS

    COMPETITIONS.setdefault(competition_code, ("Duplicate Odds Test League", "Testland"))

    with session_scope() as db:
        competition = Competition(code=competition_code, name="Duplicate Odds Test League", country="Testland")
        db.add(competition)
        db.flush()
        season = Season(competition_id=competition.id, label="2025/26")
        db.add(season)
        db.flush()
        home = Team(canonical_name="DuplicateOddsHome")
        away = Team(canonical_name="DuplicateOddsAway")
        db.add_all([home, away])
        db.flush()
        kickoff = dt.datetime.utcnow() + dt.timedelta(days=2)
        match = Match(
            provider="test",
            provider_id="duplicate-odds-test-1",
            competition_id=competition.id,
            season_id=season.id,
            kickoff_utc=kickoff,
            home_team_id=home.id,
            away_team_id=away.id,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        match_id = match.id

    # Misma (bookmaker, market, line, selection) tres veces en UNA sola
    # respuesta -- exactamente el patron real visto en produccion (ahi
    # eran 10 veces para "Marathonbet" a linea 1.5).
    snapshot = FixtureOddsSnapshot(
        home_team_raw="DuplicateOddsHome",
        away_team_raw="DuplicateOddsAway",
        commence_time=kickoff.replace(tzinfo=dt.timezone.utc),
        odds=[
            RawOddsRecord("Marathonbet", "over_under_goals", 1.5, "over", 1.09),
            RawOddsRecord("Marathonbet", "over_under_goals", 1.5, "over", 1.33),
            RawOddsRecord("Marathonbet", "over_under_goals", 1.5, "over", 11.75),  # la ultima debe ganar
            RawOddsRecord("Marathonbet", "over_under_goals", 1.5, "under", 5.5),  # selection distinta, no duplicado
        ],
    )

    with session_scope() as db:
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), competition_code)

    with session_scope() as db:
        rows = db.query(MatchOdds).filter_by(match_id=match_id).all()
        over_rows = [r for r in rows if r.selection == "over"]
        under_rows = [r for r in rows if r.selection == "under"]
        assert len(over_rows) == 1  # nunca 3
        assert over_rows[0].price == 11.75  # se queda con la ultima vista, no mezclada/promediada
        assert len(under_rows) == 1

        history_over_rows = [
            r
            for r in db.query(MatchOddsHistory).filter_by(match_id=match_id).all()
            if r.selection == "over"
        ]
        assert len(history_over_rows) == 1  # tampoco se duplica en el historico append-only
