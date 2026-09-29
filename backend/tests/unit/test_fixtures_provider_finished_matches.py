"""Tests de OpenFootballFixturesProvider.parse_payload -- regresion real
reportada por un usuario: 'history_dataset.py' (el mirror historico) puede
tardar semanas en reflejar la temporada en curso, dejando Historico
parado. Ahora esta fuente (que SI se actualiza a diario) tambien trae el
marcador de partidos ya jugados como tapagujero, sin duplicar partidos."""

from __future__ import annotations

import datetime as dt

from backend.app.ingestion.football_data.fixtures_provider import OpenFootballFixturesProvider


def _payload(*matches: dict) -> dict:
    return {"matches": list(matches)}


def test_finished_match_with_valid_score_is_included_regardless_of_date():
    payload = _payload(
        {
            "date": "2020-01-01",  # muy en el pasado a proposito
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 5",
            "score": {"ft": [2, 1]},
        }
    )
    records = OpenFootballFixturesProvider.parse_payload(payload, "laliga", "2025/26")
    assert len(records) == 1
    record = records[0]
    assert record.home_goals == 2
    assert record.away_goals == 1
    assert record.matchday == 5


def test_unscored_past_match_is_still_excluded_as_stale():
    """Sin score y con fecha pasada: sigue siendo descartado (dato de la
    fuente desactualizado), NO se muestra como "programado"."""
    payload = _payload(
        {
            "date": (dt.date.today() - dt.timedelta(days=3)).isoformat(),
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 5",
        }
    )
    records = OpenFootballFixturesProvider.parse_payload(payload, "laliga", "2025/26")
    assert records == []


def test_unscored_future_match_is_included_as_scheduled():
    payload = _payload(
        {
            "date": (dt.date.today() + dt.timedelta(days=3)).isoformat(),
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 6",
        }
    )
    records = OpenFootballFixturesProvider.parse_payload(payload, "laliga", "2025/26")
    assert len(records) == 1
    assert records[0].home_goals is None
    assert records[0].away_goals is None


def test_same_match_keeps_same_provider_id_before_and_after_being_played():
    """Clave para no duplicar: _upsert_match localiza el Match existente
    por (provider, provider_id) -- si el id cambiase al pasar de
    'programado' a 'jugado', se crearia una fila nueva en vez de
    actualizar la existente."""
    scheduled_payload = _payload(
        {
            "date": "2025-10-01",
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 7",
        }
    )
    finished_payload = _payload(
        {
            "date": "2025-10-01",
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 7",
            "score": {"ft": [3, 0]},
        }
    )
    scheduled = OpenFootballFixturesProvider.parse_payload(
        scheduled_payload, "laliga", "2025/26", min_date=dt.date(2020, 1, 1)
    )
    finished = OpenFootballFixturesProvider.parse_payload(
        finished_payload, "laliga", "2025/26", min_date=dt.date(2020, 1, 1)
    )
    assert scheduled[0].provider_id == finished[0].provider_id


def test_malformed_score_never_invents_a_result():
    payload = _payload(
        {
            "date": "2020-01-01",
            "team1": "FC Barcelona",
            "team2": "Real Getafe CF",
            "round": "Matchday 5",
            "score": {"ft": "no-deberia-ser-un-string"},
        }
    )
    records = OpenFootballFixturesProvider.parse_payload(payload, "laliga", "2025/26")
    # Score malformado + fecha pasada -> se descarta (no se inventa un
    # marcador, y sin marcador valido cuenta como "no jugado desactualizado").
    assert records == []
