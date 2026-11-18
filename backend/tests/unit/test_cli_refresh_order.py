"""`cli.py::refresh()` orquesta 7 pasos en secuencia. Bug real reportado
por el usuario ("partidos con marcador ya visible seguian 'sin resolver'
en el Historico"): `evaluate` (liquida PredictionResult) iba ANTES de
`update_fixtures` -- que es quien trae el resultado/estado real de la
temporada en curso -- asi que un partido recien finalizado no se
liquidaba hasta el SIGUIENTE `refresh`. Este test fija el ORDEN real de
los pasos (nunca ejecuta nada real: cada paso se sustituye por un stub
que solo registra que se llamo), para que una regresion futura del orden
falle aqui en vez de descubrirse semanas despues en produccion."""

from __future__ import annotations

from backend.app import cli


def test_refresh_settles_predictions_only_after_fixtures_and_odds_are_fresh(monkeypatch):
    call_order: list[str] = []

    def _record(name):
        def _fake(*args, **kwargs):
            call_order.append(name)

        return _fake

    monkeypatch.setattr(cli, "update", _record("update"))
    monkeypatch.setattr(cli, "update_fixtures", _record("update_fixtures"))
    monkeypatch.setattr(cli, "update_odds", _record("update_odds"))
    monkeypatch.setattr(cli, "update_secondary_odds", _record("update_secondary_odds"))
    monkeypatch.setattr(cli, "evaluate", _record("evaluate"))
    monkeypatch.setattr(cli, "train", _record("train"))
    monkeypatch.setattr(cli, "predict_upcoming", _record("predict_upcoming"))

    cli.refresh(days=10, competition=None, skip_historical=False)

    assert call_order == [
        "update",
        "update_fixtures",
        "update_odds",
        "update_secondary_odds",
        "evaluate",
        "train",
        "predict_upcoming",
    ]
    # El orden concreto que importaba de verdad para el bug real: nunca
    # liquidar ANTES de tener los fixtures/estado reales de la temporada
    # en curso.
    assert call_order.index("update_fixtures") < call_order.index("evaluate")
    assert call_order.index("update_odds") < call_order.index("evaluate")


def test_refresh_skip_historical_still_evaluates_after_fixtures(monkeypatch):
    call_order: list[str] = []

    def _record(name):
        def _fake(*args, **kwargs):
            call_order.append(name)

        return _fake

    monkeypatch.setattr(cli, "update", _record("update"))
    monkeypatch.setattr(cli, "update_fixtures", _record("update_fixtures"))
    monkeypatch.setattr(cli, "update_odds", _record("update_odds"))
    monkeypatch.setattr(cli, "update_secondary_odds", _record("update_secondary_odds"))
    monkeypatch.setattr(cli, "evaluate", _record("evaluate"))
    monkeypatch.setattr(cli, "train", _record("train"))
    monkeypatch.setattr(cli, "predict_upcoming", _record("predict_upcoming"))

    cli.refresh(days=10, competition=None, skip_historical=True)

    assert "update" not in call_order
    assert call_order.index("update_fixtures") < call_order.index("evaluate")
