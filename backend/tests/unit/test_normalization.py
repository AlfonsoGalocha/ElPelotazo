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
