"""Cuota de una UNICA casa para un partido/mercado/linea concretos.

USO RESTRINGIDO: solo para backtesting sobre el dataset HISTORICO
(`services/match_service.py::load_market_odds_column`), donde ademas no hay
otra opcion — el dataset historico (football-data.co.uk / xgabora) solo
trae cuotas de Bet365, nunca de varias casas a la vez, asi que no existe
"consenso" posible que calcular ahi.

Para PREDICCIONES EN VIVO (partidos futuros con cuotas de varias casas via
The Odds API), usar `market/consensus.py::compute_market_consensus` en su
lugar: una unica cuota (aunque sea la de la casa "preferida") no es un
mercado, y tratarla como tal fue la causa de senhales con edge inflado por
una casa outlier (ver revision de arquitectura, seccion 5/6)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.db.models.matches import MatchOdds
from backend.app.market.vig import no_vig_probabilities, overround


@dataclass
class MarketQuote:
    bookmaker: str
    market_probability: float  # sin vig cuando hay ambas selecciones, si no, implicita bruta
    market_odds: float  # cuota de la seleccion pedida
    overround: float | None
    vig_removed: bool


def best_available_quote(
    odds_rows: list[MatchOdds], market: str, line: float | None, selection: str
) -> MarketQuote | None:
    """Elige la cuota mas representativa disponible para (market, line, selection).

    Preferencia: si hay cuotas de ambas selecciones del mismo bookmaker,
    quita el vig. Si no, usa la probabilidad implicita bruta (documentado
    explicitamente via `vig_removed=False`, nunca se presenta como "sin vig").
    """
    candidates = [
        o for o in odds_rows if o.market == market and o.line == line
    ]
    if not candidates:
        return None

    by_bookmaker: dict[str, dict[str, float]] = {}
    for row in candidates:
        by_bookmaker.setdefault(row.bookmaker, {})[row.selection] = row.price

    # Preferimos Pinnacle si esta disponible: mercado reconocido por tener
    # margenes bajos y ser una referencia habitual de "sharp" pricing.
    preferred_order = ["pinnacle", "bet365", "betfair_exchange", "william_hill"]
    bookmakers = sorted(by_bookmaker, key=lambda b: preferred_order.index(b) if b in preferred_order else 99)

    for bookmaker in bookmakers:
        selections = by_bookmaker[bookmaker]
        if selection not in selections:
            continue
        if len(selections) >= 2:
            no_vig = no_vig_probabilities(selections)
            return MarketQuote(
                bookmaker=bookmaker,
                market_probability=no_vig[selection],
                market_odds=selections[selection],
                overround=overround(selections),
                vig_removed=True,
            )
        return MarketQuote(
            bookmaker=bookmaker,
            market_probability=1.0 / selections[selection],
            market_odds=selections[selection],
            overround=None,
            vig_removed=False,
        )
    return None
