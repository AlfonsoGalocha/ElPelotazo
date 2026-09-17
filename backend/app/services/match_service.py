"""Carga partidos desde la BD a un DataFrame ancho listo para features/goals.py."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from backend.app.db.models.core import Season
from backend.app.db.models.matches import Match, MatchOdds, MatchStatistics
from backend.app.market.odds import best_available_quote


def load_matches_dataframe(db: Session, competition_id: int | None = None) -> pd.DataFrame:
    query = db.query(Match, MatchStatistics, Season).join(
        MatchStatistics, MatchStatistics.match_id == Match.id, isouter=True
    ).join(Season, Season.id == Match.season_id)

    if competition_id is not None:
        query = query.filter(Match.competition_id == competition_id)

    rows = []
    for match, stats, season in query.all():
        rows.append(
            {
                "match_id": match.id,
                "competition_id": match.competition_id,
                "season_id": match.season_id,
                "season_label": season.label,
                "date": match.kickoff_utc,
                "home_team_id": match.home_team_id,
                "away_team_id": match.away_team_id,
                "referee_id": match.referee_id,
                "home_goals": match.home_goals,
                "away_goals": match.away_goals,
                "home_shots": stats.home_shots if stats else None,
                "away_shots": stats.away_shots if stats else None,
                "home_shots_on_target": stats.home_shots_on_target if stats else None,
                "away_shots_on_target": stats.away_shots_on_target if stats else None,
                "home_corners": stats.home_corners if stats else None,
                "away_corners": stats.away_corners if stats else None,
                "home_fouls": stats.home_fouls if stats else None,
                "away_fouls": stats.away_fouls if stats else None,
                "home_yellow_cards": stats.home_yellow_cards if stats else None,
                "away_yellow_cards": stats.away_yellow_cards if stats else None,
                "home_red_cards": stats.home_red_cards if stats else None,
                "away_red_cards": stats.away_red_cards if stats else None,
                "home_xg": stats.home_xg if stats else None,
                "away_xg": stats.away_xg if stats else None,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def load_market_odds_column(db: Session, match_ids: list[int], market_key: str) -> dict[int, float]:
    """Cuota de MERCADO (no sin vig) por match_id para un mercado del MVP,
    usada SOLO en backtesting para simular una estrategia hipotetica
    (`market_strategy_metrics`), nunca como feature de entrenamiento (evitaria
    "closing line leakage": la cuota de cierre no se conoce antes del partido).
    """
    from backend.app.services.prediction_service import MARKET_TO_ODDS_LOOKUP

    if market_key not in MARKET_TO_ODDS_LOOKUP or not match_ids:
        return {}
    market, line, selection = MARKET_TO_ODDS_LOOKUP[market_key]

    odds_by_match = (
        db.query(MatchOdds)
        .filter(MatchOdds.match_id.in_(match_ids), MatchOdds.market == market, MatchOdds.line == line)
        .all()
    )
    grouped: dict[int, list[MatchOdds]] = {}
    for row in odds_by_match:
        grouped.setdefault(row.match_id, []).append(row)

    result = {}
    for match_id, rows in grouped.items():
        quote = best_available_quote(rows, market, line, selection)
        if quote:
            result[match_id] = quote.market_odds
    return result
