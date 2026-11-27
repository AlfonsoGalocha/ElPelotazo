"""Rebuild a no-vig reference from a complete, timestamped pre-match snapshot."""

from collections import defaultdict
from math import isfinite

from backend.app.market.consensus import compute_market_consensus
from backend.app.normalization.bookmakers import is_excluded_bookmaker


def snapshot_markets(quotes, cutoff):
    latest = {}
    markets = defaultdict(list)
    for quote in quotes:
        if (quote.snapshot_type != "pre_match" or is_excluded_bookmaker(quote.bookmaker)
                or not quote.provider_updated_at or not quote.fetched_at or not quote.recorded_at
                or not quote.provider_updated_at <= quote.fetched_at <= quote.recorded_at <= cutoff):
            continue
        key = quote.market, quote.line
        batch = quote.recorded_at, quote.fetched_at
        if key not in latest or batch > latest[key]:
            latest[key] = batch
            markets[key].clear()
        if batch == latest[key]:
            markets[key].append(quote)
    return markets


def no_vig_reference(markets, key):
    market, line, selection = key
    required = {
        "match_result": {"home", "draw", "away"},
        "btts": {"yes", "no"},
    }.get(market, {"over", "under"})
    if selection not in required:
        return None
    by_book = defaultdict(list)
    for quote in markets.get((market, line), ()):
        if quote.selection in required and isfinite(quote.price) and quote.price > 1:
            by_book[quote.bookmaker].append(quote)
    complete = [
        quote for rows in by_book.values()
        if len(rows) == len(required) and {row.selection for row in rows} == required
        for quote in rows
    ]
    return compute_market_consensus(complete, market, line, selection).market_probability
