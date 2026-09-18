from __future__ import annotations

from backend.app.normalization.teams import canonical_name_for, resolve_team_id


def test_known_alias_resolves_to_canonical_name():
    assert canonical_name_for("Man United") == "Manchester United"
    assert canonical_name_for("man utd") == "Manchester United"


def test_unknown_name_falls_back_to_itself():
    assert canonical_name_for("Some Random FC") == "Some Random FC"


def test_resolve_team_id_is_idempotent(db_session):
    id_first = resolve_team_id(db_session, "football_data_co_uk", "Man United")
    id_second = resolve_team_id(db_session, "football_data_co_uk", "Man United")
    assert id_first == id_second


def test_resolve_team_id_maps_different_aliases_to_same_team(db_session):
    id_alias_1 = resolve_team_id(db_session, "football_data_co_uk", "Man United")
    id_alias_2 = resolve_team_id(db_session, "another_source", "Manchester United")
    assert id_alias_1 == id_alias_2


def test_resolve_team_id_fuzzy_matches_accents_and_punctuation_from_new_source(db_session):
    """Regresion: una fuente NUEVA (p.ej. una casa de apuestas) que escriba
    un equipo ya conocido con tilde/guion/orden distinto no debe crear un
    Team fantasma con un id distinto - si no, esa cuota nunca casaria con
    el partido programado del mismo equipo aunque sea literalmente el
    mismo club."""
    id_original = resolve_team_id(db_session, "football_data_co_uk", "Atletico Madrid")
    id_accented = resolve_team_id(db_session, "the_odds_api", "Atlético Madrid")
    assert id_original == id_accented

    id_hyphenated = resolve_team_id(db_session, "football_data_co_uk", "Paris SG")
    id_spaced = resolve_team_id(db_session, "the_odds_api", "Paris Saint Germain")
    assert id_hyphenated == id_spaced
