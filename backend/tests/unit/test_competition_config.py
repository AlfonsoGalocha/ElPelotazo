from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.main import app

client = TestClient(app)


def test_disabled_competition_is_hidden_from_the_public_listing():
    with session_scope() as db:
        db.add(
            Competition(
                code="hidden-comp-test", name="Hidden Comp", country="Testland", enabled=False
            )
        )
        db.add(
            Competition(
                code="visible-comp-test", name="Visible Comp", country="Testland", enabled=True
            )
        )

    response = client.get("/competitions")
    assert response.status_code == 200
    codes = {c["code"] for c in response.json()}
    assert "visible-comp-test" in codes
    assert "hidden-comp-test" not in codes


def test_legacy_competition_without_enabled_set_stays_visible():
    """Una competicion creada ANTES de anhadir esta columna tendria
    `enabled=None` tras la migracion (ver _sync_missing_columns) -- nunca
    debe tratarse como deshabilitada por falta de dato."""
    with session_scope() as db:
        db.add(Competition(code="legacy-comp-test", name="Legacy Comp", country="Testland"))

    response = client.get("/competitions")
    assert response.status_code == 200
    codes = {c["code"] for c in response.json()}
    assert "legacy-comp-test" in codes


def test_listing_orders_by_priority_then_name():
    with session_scope() as db:
        db.add(
            Competition(code="priority-low-test", name="Z League", country="Testland", priority=0)
        )
        db.add(
            Competition(code="priority-high-test", name="A League", country="Testland", priority=10)
        )

    response = client.get("/competitions")
    codes_in_order = [c["code"] for c in response.json()]
    assert codes_in_order.index("priority-high-test") < codes_in_order.index("priority-low-test")
