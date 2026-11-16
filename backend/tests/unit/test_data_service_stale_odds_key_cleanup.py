"""Bug real reportado por el usuario: las cuotas de corners seguian
saliendo corruptas incluso DESPUES de arreglar el filtrado de mercados en
`odds_provider.py` (que ya no clasifica "Away Corners Over/Under"/
"Multicorners"/etc. como `corners_total`) y de reconstruir/reingerir.
Causa: `_attach_odds_snapshots` borraba por CLAVE EXACTA (bookmaker,
market, line, selection) antes de insertar -- una fila vieja que vino de
un mercado hoy excluido (p.ej. "Multicorners", linea 20.5) generaba una
clave que YA NUNCA vuelve a aparecer en el fetch (el mercado real,
"Corners Over Under", no usa esa linea), asi que el borrado nunca la
alcanzaba y sobrevivia para siempre mezclada en el consenso, aunque el
propio mercado `corners_total` SI siguiera reingeriendose con normalidad
desde la fuente correcta. Debe borrarse todo lo existente para los
mercados que trae el snapshot ANTES de insertar, no clave a clave."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds
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


def test_a_stale_key_no_longer_produced_by_a_later_fetch_is_deleted_not_kept():
    competition_code = "stale_odds_key_test_league"
    from backend.app.normalization.competitions import COMPETITIONS

    COMPETITIONS.setdefault(competition_code, ("Stale Odds Key Test League", "Testland"))

    with session_scope() as db:
        competition = Competition(
            code=competition_code, name="Stale Odds Key Test League", country="Testland"
        )
        db.add(competition)
        db.flush()
        season = Season(competition_id=competition.id, label="2025/26")
        db.add(season)
        db.flush()
        home = Team(canonical_name="StaleOddsHome")
        away = Team(canonical_name="StaleOddsAway")
        db.add_all([home, away])
        db.flush()
        kickoff = dt.datetime.utcnow() + dt.timedelta(days=2)
        match = Match(
            provider="test",
            provider_id="stale-odds-key-test-1",
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

    # Primera pasada: simula el proveedor ANTES del arreglo -- "Corners
    # Over Under" (el real, linea 8.5) MEZCLADO con claves que venian de
    # mercados hoy excluidos: "Away Corners Over/Under" (misma linea
    # nominal 8.5, precio de un solo equipo, mucho mas caro) y
    # "Multicorners" (linea 20.5, que nunca va a volver a aparecer).
    stale_snapshot = FixtureOddsSnapshot(
        home_team_raw="StaleOddsHome",
        away_team_raw="StaleOddsAway",
        commence_time=kickoff.replace(tzinfo=dt.timezone.utc),
        odds=[
            RawOddsRecord("1xBet", "corners_total", 8.5, "over", 7.80),  # el valor corrupto
            RawOddsRecord("1xBet", "corners_total", 20.5, "over", 3.10),  # linea huerfana (Multicorners)
            RawOddsRecord("1xBet", "match_result", None, "home", 1.80),  # otro mercado, no debe tocarse
        ],
    )
    with session_scope() as db:
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([stale_snapshot]), competition_code)

    # Segunda pasada: el proveedor YA ARREGLADO sigue trayendo
    # `corners_total` (el mercado SIGUE existiendo, solo que ahora viene
    # SOLO del mercado real "Corners Over Under") -- pero con el precio
    # correcto en la linea 8.5 y SIN la linea 20.5, que no vuelve a
    # aparecer nunca mas porque venia de un mercado hoy excluido.
    fixed_snapshot = FixtureOddsSnapshot(
        home_team_raw="StaleOddsHome",
        away_team_raw="StaleOddsAway",
        commence_time=kickoff.replace(tzinfo=dt.timezone.utc),
        odds=[
            RawOddsRecord("1xBet", "corners_total", 8.5, "over", 1.48),  # el valor real
            RawOddsRecord("1xBet", "match_result", None, "home", 1.85),  # se actualiza con normalidad
        ],
    )
    with session_scope() as db:
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([fixed_snapshot]), competition_code)

    with session_scope() as db:
        rows = db.query(MatchOdds).filter_by(match_id=match_id).all()
        corners_rows = [r for r in rows if r.market == "corners_total"]
        match_result_rows = [r for r in rows if r.market == "match_result"]
        # Solo debe sobrevivir la linea 8.5 con el precio real -- ni el
        # precio corrupto ni la linea huerfana 20.5 deben quedarse.
        assert [(r.line, r.price) for r in corners_rows] == [(8.5, 1.48)]
        assert len(match_result_rows) == 1
        assert match_result_rows[0].price == 1.85  # el otro mercado si se actualiza con normalidad
