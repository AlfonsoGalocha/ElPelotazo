from __future__ import annotations

import statistics

from backend.app.market.consensus import compute_market_consensus


class _FakeOddsRow:
    def __init__(self, bookmaker: str, market: str, line: float | None, selection: str, price: float,
                 snapshot_type: str = "pre_match"):
        self.bookmaker = bookmaker
        self.market = market
        self.line = line
        self.selection = selection
        self.price = price
        self.snapshot_type = snapshot_type


def _dual_sided_rows(prices: dict[str, tuple[float, float]]) -> list[_FakeOddsRow]:
    """prices: {bookmaker: (over_price, under_price)}"""
    rows = []
    for bookmaker, (over, under) in prices.items():
        rows.append(_FakeOddsRow(bookmaker, "over_under_goals", 2.5, "over", over))
        rows.append(_FakeOddsRow(bookmaker, "over_under_goals", 2.5, "under", under))
    return rows


def test_no_rows_returns_empty_consensus():
    result = compute_market_consensus([], "over_under_goals", 2.5, "over")
    assert result.market_probability is None
    assert result.bookmakers_count == 0


def test_invalid_odds_are_discarded_instead_of_crashing():
    """Bug real detectado con mercados 'additional' de The Odds API
    (alternate_totals/btts, menos fiables que h2h/totals): una casa puede
    devolver price=1.0 como placeholder de mercado suspendido/sin
    liquidez. Sin filtrar esto antes de implied_probability(), una unica
    cuota basura tumbaba TODO el calculo de consenso (ValueError) y con
    el, el comando de refresco entero. La cuota invalida se descarta como
    si esa casa no hubiera cotizado, nunca se inventa ni se corrige."""
    rows = _dual_sided_rows({"bet365": (1.90, 1.95)})
    rows.append(_FakeOddsRow("suspended_book", "over_under_goals", 2.5, "over", 1.0))
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert result.market_probability is not None
    assert result.bookmakers_count == 1  # la casa con price=1.0 no cuenta


def test_single_bookmaker_dual_sided_removes_vig():
    rows = _dual_sided_rows({"bet365": (1.90, 1.95)})
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert result.market_probability_source == "single_book_no_vig"
    assert result.bookmakers_count == 1
    assert result.bookmakers_used == 1
    assert 0.0 < result.market_probability < 1.0


def test_multiple_agreeing_bookmakers_produce_consensus():
    rows = _dual_sided_rows(
        {
            "bet365": (1.80, 2.05),
            "pinnacle": (1.82, 2.02),
            "betfair_exchange": (1.79, 2.06),
            "william_hill": (1.81, 2.03),
        }
    )
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert result.market_probability_source == "consensus_no_vig"
    assert result.bookmakers_count == 4
    assert result.bookmakers_used == 4
    assert result.min_odds == 1.79
    assert result.max_odds == 1.82
    assert result.median_odds == statistics.median([1.80, 1.82, 1.79, 1.81])


def test_outlier_bookmaker_is_excluded_from_consensus():
    """Caso real reportado: la mayoria de casas cotiza ~1.80-1.85, pero una
    unica casa cotiza 4.40 (una linea distinta mal identificada, un error de
    tipeo, o simplemente ruido). El consenso NO debe dejarse arrastrar por
    esa unica cuota atipica."""
    rows = _dual_sided_rows(
        {
            "bet365": (1.80, 2.05),
            "pinnacle": (1.82, 2.02),
            "betfair_exchange": (1.79, 2.06),
            "william_hill": (1.81, 2.03),
            "outlier_book": (4.40, 1.22),
        }
    )
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert "outlier_book" in result.outliers_removed
    assert result.bookmakers_used == 4
    # El consenso de probabilidad debe seguir cerca de ~0.55 (cuota ~1.80),
    # NO cerca de 1/4.40 ~= 0.227 (lo que pasaria si el outlier dominase).
    assert result.market_probability > 0.45


def test_few_bookmakers_never_triggers_outlier_filter():
    """Con menos de MIN_BOOKS_FOR_OUTLIER_FILTER casas no hay base
    estadistica para decidir cual es el "outlier": no se descarta nada."""
    rows = _dual_sided_rows({"bet365": (1.80, 2.05), "outlier_book": (4.40, 1.22)})
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert result.outliers_removed == []
    assert result.bookmakers_used == 2


def test_single_sided_bookmaker_used_only_as_fallback():
    """Un bookmaker que solo cotiza UNA seleccion (no la otra) no puede
    quitarsele el vig: se usa su probabilidad implicita CRUDA, y solo como
    ultimo recurso si no hay ningun bookmaker con ambas selecciones."""
    rows = [_FakeOddsRow("bet365", "over_under_goals", 2.5, "over", 1.90)]
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    assert result.market_probability_source == "single_book_raw"


def test_different_snapshot_types_never_mixed():
    """No se debe mezclar una cuota 'closing' (historica) con una 'pre_match'
    (futura) o 'live' del mismo partido en el mismo calculo."""
    rows = [
        _FakeOddsRow("bet365", "over_under_goals", 2.5, "over", 1.50, snapshot_type="closing"),
        _FakeOddsRow("bet365", "over_under_goals", 2.5, "under", 2.80, snapshot_type="closing"),
        _FakeOddsRow("the_odds_api", "over_under_goals", 2.5, "over", 5.00, snapshot_type="live"),
        _FakeOddsRow("the_odds_api", "over_under_goals", 2.5, "under", 1.10, snapshot_type="live"),
    ]
    result = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    # Preferencia closing > pre_match > opening > live: debe usar SOLO closing.
    assert result.bookmakers_count == 1
    assert result.market_odds == 1.50


def test_different_lines_never_mixed():
    """Over 2.5 y Over 3.5 son mercados distintos: nunca deben mezclarse en
    el mismo consenso aunque compartan 'market' y 'selection'."""
    rows = [
        _FakeOddsRow("bet365", "over_under_goals", 2.5, "over", 1.80),
        _FakeOddsRow("bet365", "over_under_goals", 2.5, "under", 2.05),
        _FakeOddsRow("bet365", "over_under_goals", 3.5, "over", 3.50),
        _FakeOddsRow("bet365", "over_under_goals", 3.5, "under", 1.30),
    ]
    result_2_5 = compute_market_consensus(rows, "over_under_goals", 2.5, "over")
    result_3_5 = compute_market_consensus(rows, "over_under_goals", 3.5, "over")
    assert result_2_5.market_odds == 1.80
    assert result_3_5.market_odds == 3.50
