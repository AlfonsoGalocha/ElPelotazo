from types import SimpleNamespace

import pytest

from backend.app.services.bot_bankroll import settle_ticket


@pytest.mark.parametrize(
    ("second_status", "second_home", "outcome"),
    [
        ("scheduled", None, ("pending", 0)),
        ("finished", 0, ("lost", 0)),
        ("finished", 2, ("won", 1440)),
        ("cancelled", None, ("won", 900)),
    ],
)
def test_double_settlement(second_status, second_home, outcome):
    ticket = {
        "stake_cents": 600,
        "legs": [
            {"match_id": 1, "market": "home_win", "price": 1.50},
            {"match_id": 2, "market": "home_win", "price": 1.60},
        ],
    }
    fixtures = {
        1: (SimpleNamespace(status="finished", home_goals=2, away_goals=1), None),
        2: (SimpleNamespace(status=second_status, home_goals=second_home, away_goals=1), None),
    }
    assert settle_ticket(ticket, fixtures) == outcome


def test_corners_await_verified_result():
    ticket = {"stake_cents": 600, "legs": [{"match_id": 1, "market": "corners_over_9_5", "price": 1.50}]}
    match = SimpleNamespace(status="finished", home_goals=2, away_goals=1)
    assert settle_ticket(ticket, {1: (match, None)}) == ("pending", 0)
    assert settle_ticket(ticket, {1: (match, 10)}) == ("won", 900)
    assert settle_ticket(ticket, {1: (match, 9)}) == ("lost", 0)


def _pick(market, p):
    return SimpleNamespace(market=market, id=1), SimpleNamespace(probability=p, score=0.5)


def test_user_market_order_and_scoring_evidence():
    from backend.app.services.bot_match_context import choose_match_pick

    over = _pick("over_2_5", 0.70)
    both = _pick("btts", 0.75)
    cover = _pick("double_chance_1x", 0.83)
    away_cover = _pick("double_chance_x2", 0.82)
    context = {
        "favorite": "home",
        "h2h": {"matches": 4, "both_scored": 3},
        "home_form": {"matches": 5, "scored": 4},
        "away_form": {"matches": 5, "scored": 3},
    }

    assert choose_match_pick([over, both, cover], context) == (over, "goles")
    assert choose_match_pick([both, cover], context) == (both, "ambos_marcan")
    context["away_form"]["scored"] = 2
    assert choose_match_pick([both, cover], context) == (cover, "doble_oportunidad")
    context["favorite"] = "away"
    assert choose_match_pick([both, cover, away_cover], context) == (away_cover, "doble_oportunidad")
    assert choose_match_pick([both, cover], context) == (None, "pasar")


def test_full_available_balance_and_original_half_weight(monkeypatch):
    from backend.app.services import bot_bankroll

    monkeypatch.setattr(bot_bankroll, "candidate_leg", lambda p, a: {"match_id": p.match_id})
    best = [
        (SimpleNamespace(match_id=1), SimpleNamespace(probability_source="original", high_confidence=False)),
        (SimpleNamespace(match_id=2), SimpleNamespace(probability_source="calibrated", high_confidence=False)),
    ]
    assert [ticket["stake_cents"] for ticket in bot_bankroll.make_tickets(best, 30_001)] == [10_000, 20_001]
    assert bot_bankroll.make_tickets([], 30_000) == []
    assert bot_bankroll.make_tickets(best, 0) == []


def test_double_can_use_available_balance_without_repeating_legs(monkeypatch):
    from backend.app.services import bot_bankroll

    monkeypatch.setattr(bot_bankroll, "candidate_leg", lambda p, a: {"match_id": p.match_id})
    assessment = SimpleNamespace(high_confidence=True, conservative_ev=0.1, quote=SimpleNamespace(bookmaker="same"))
    best = [(SimpleNamespace(match_id=1), assessment), (SimpleNamespace(match_id=2), assessment)]
    tickets = bot_bankroll.make_tickets(best, 30_000)
    assert len(tickets) == 1
    assert tickets[0]["stake_cents"] == 30_000
    assert tickets[0]["legs"] == [{"match_id": 1}, {"match_id": 2}]
