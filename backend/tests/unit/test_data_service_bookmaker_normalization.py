"""Bug real reportado por el usuario ("las cuotas no tienen sentido"):
`api_football` y `the_odds_api` cubren en parte las MISMAS casas reales,
pero cada fuente manda un nombre distinto para la misma casa global
("1xBet" vs "onexbet", "William Hill" vs "williamhill"...). Sin normalizar
antes de guardar, el consenso de mercado contaba la MISMA casa real dos
veces (una por cada nombre), inflando `bookmakers_used`/`bookmakers_count`
y, si las dos fuentes traian el precio en momentos distintos, podian tirar
de la mediana en direcciones contradictorias."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import attach_odds_to_scheduled_matches


class _FakeOddsProvider:
    name = "test_odds_provider"

    def __init__(self, snapshots: list[FixtureOddsSnapshot]):
        self._snapshots = snapshots

    def is_available(self) -> bool:
        return True

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        return self._snapshots


def _make_scheduled_match(competition_code: str, home_name: str, away_name: str) -> tuple[int, dt.datetime]:
    COMPETITIONS.setdefault(competition_code, ("Bookmaker Norm Test League", "Testland"))
    with session_scope() as db:
        competition = Competition(code=competition_code, name="Bookmaker Norm Test League", country="Testland")
        db.add(competition)
        db.flush()
        season = Season(competition_id=competition.id, label="2025/26")
        db.add(season)
        db.flush()
        home = Team(canonical_name=home_name)
        away = Team(canonical_name=away_name)
        db.add_all([home, away])
        db.flush()
        kickoff = dt.datetime.utcnow() + dt.timedelta(days=2)
        match = Match(
            provider="test",
            provider_id=f"bookmaker-norm-test-{competition_code}",
            competition_id=competition.id,
            season_id=season.id,
            kickoff_utc=kickoff,
            home_team_id=home.id,
            away_team_id=away.id,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        return match.id, kickoff


def test_confirmed_same_bookmaker_from_different_sources_is_not_counted_twice():
    """"1xBet" (nombre que manda api_football) y "onexbet" (key tecnico
    que manda the_odds_api) son la MISMA casa real -- deben colapsar a una
    unica fila, igual que el bug ya arreglado de duplicados EXACTOS."""
    match_id, kickoff = _make_scheduled_match("bookmaker_norm_test_1", "NormHome1", "NormAway1")

    snapshot = FixtureOddsSnapshot(
        home_team_raw="NormHome1",
        away_team_raw="NormAway1",
        commence_time=kickoff.replace(tzinfo=dt.timezone.utc),
        odds=[
            RawOddsRecord("1xBet", "over_under_goals", 1.5, "over", 1.07),
            RawOddsRecord("onexbet", "over_under_goals", 1.5, "over", 4.90),  # misma casa real, otro nombre
            RawOddsRecord("William Hill", "over_under_goals", 1.5, "over", 1.10),
            RawOddsRecord("williamhill", "over_under_goals", 1.5, "over", 1.12),
        ],
    )

    with session_scope() as db:
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), "bookmaker_norm_test_1")

    with session_scope() as db:
        rows = db.query(MatchOdds).filter_by(match_id=match_id).all()
        bookmakers = sorted(r.bookmaker for r in rows)
        assert bookmakers == ["1xBet", "William Hill"]  # nunca 4 filas para 2 casas reales


def test_regional_variants_of_the_same_brand_are_never_merged():
    """Guarda de no-regresion: "unibet_fr"/"unibet_nl" son libros
    REGIONALES distintos de Unibet con cuotas propias -- fusionarlos
    perderia senhal real, no solo un duplicado. No hay alias para ellos."""
    match_id, kickoff = _make_scheduled_match("bookmaker_norm_test_2", "NormHome2", "NormAway2")

    snapshot = FixtureOddsSnapshot(
        home_team_raw="NormHome2",
        away_team_raw="NormAway2",
        commence_time=kickoff.replace(tzinfo=dt.timezone.utc),
        odds=[
            RawOddsRecord("unibet_fr", "match_result", None, "home", 1.80),
            RawOddsRecord("unibet_nl", "match_result", None, "home", 1.95),
        ],
    )

    with session_scope() as db:
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), "bookmaker_norm_test_2")

    with session_scope() as db:
        rows = db.query(MatchOdds).filter_by(match_id=match_id).all()
        bookmakers = sorted(r.bookmaker for r in rows)
        assert bookmakers == ["unibet_fr", "unibet_nl"]  # cada uno sobrevive por separado
