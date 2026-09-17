"""CLI `football-edge` (seccion 41 del brief).

Ejemplos:
    football-edge update --competition laliga --season 2023/24
    football-edge train --competition laliga
    football-edge predict --competition laliga --date 2026-09-20
    football-edge backtest --competition laliga --market over_2_5
    football-edge report --date 2026-09-20
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import typer

from backend.app.backtesting.engine import run_walk_forward_backtest, summarize_backtest
from backend.app.backtesting.reports import write_backtest_report
from backend.app.config.settings import REPO_ROOT
from backend.app.db.database import init_db, session_scope
from backend.app.db.models.core import Competition
from backend.app.features.goals import build_match_feature_table
from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider
from backend.app.ingestion.football_data.provider import (
    COMPETITION_DIV_CODES,
    FootballDataCoUkProvider,
)
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.services.data_service import ingest_matches
from backend.app.services.match_service import load_market_odds_column, load_matches_dataframe
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.app.utils.logging import get_logger

app = typer.Typer(help="Football Edge Detector CLI")
logger = get_logger(__name__)

ALL_COMPETITIONS = list(COMPETITION_DIV_CODES.keys())


def _seasons_since(start_year: int = 2018) -> list[str]:
    current_year = dt.date.today().year
    return [f"{y}/{str(y + 1)[-2:]}" for y in range(start_year, current_year + 1)]


@app.command()
def update(
    competition: str = typer.Option(None),
    season: str = typer.Option(None),
    source: str = typer.Option(
        "history_dataset",
        help="'history_dataset' (mirror en GitHub, recomendado) o 'football_data_co_uk' (fuente directa).",
    ),
) -> None:
    """Descarga e ingesta datos historicos reales (resultados + cuotas)."""
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    seasons = [season] if season else _seasons_since()
    provider = ClubFootballMatchDataProvider() if source == "history_dataset" else FootballDataCoUkProvider()

    with session_scope() as db:
        for comp in competitions:
            for s in seasons:
                try:
                    n = ingest_matches(db, provider, comp, s)
                    typer.echo(f"[update] {comp} {s}: {n} partidos")
                except Exception as exc:  # noqa: BLE001
                    typer.echo(f"[update] {comp} {s}: ERROR {exc}")


@app.command()
def train(competition: str = typer.Option(None)) -> None:
    """Entrena baseline + Dixon-Coles + ML classifier + ensemble por competicion."""
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    with session_scope() as db:
        for comp in competitions:
            result = train_competition_models(db, comp)
            typer.echo(f"[train] {json.dumps(result, default=str)}")


@app.command()
def predict(competition: str, date: str = typer.Option(None)) -> None:
    """Genera predicciones para partidos programados de una competicion."""
    init_db()
    target_date = dt.date.fromisoformat(date) if date else None
    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition, target_date)
        typer.echo(f"[predict] {len(predictions)} predicciones generadas para {competition}")


@app.command()
def backtest(competition: str, market: str = "over_2_5", output: str | None = None) -> None:
    """Ejecuta un backtest walk-forward y escribe un JSON en data/processed."""
    init_db()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition).one_or_none()
        if comp is None:
            typer.echo(f"Competicion no encontrada: {competition}")
            raise typer.Exit(1)
        matches = load_matches_dataframe(db, comp.id)
        table = build_match_feature_table(matches)

        odds_col = f"market_odds_{market}"
        odds_by_match = load_market_odds_column(db, table["match_id"].tolist(), market)
        table[odds_col] = table["match_id"].map(odds_by_match)
        has_market_odds = table[odds_col].notna().any()

        results = run_walk_forward_backtest(
            table, DixonColesModel, market, market_odds_col=odds_col if has_market_odds else None
        )
        summary = summarize_backtest(results)

    output_path = Path(output) if output else REPO_ROOT / "data" / "processed" / f"backtest_{competition}_{market}.json"
    write_backtest_report(summary, output_path)
    typer.echo(f"[backtest] resumen escrito en {output_path}")


@app.command()
def report(date: str = typer.Option(None)) -> None:
    """Genera un informe JSON del dia con predicciones de todas las competiciones."""
    init_db()
    target_date = dt.date.fromisoformat(date) if date else dt.date.today()
    report_data = {"date": target_date.isoformat(), "competitions": {}}

    with session_scope() as db:
        for comp in ALL_COMPETITIONS:
            predictions = generate_predictions_for_competition(db, comp, target_date)
            report_data["competitions"][comp] = [
                {
                    "match_id": p.match_id,
                    "market": p.market,
                    "model_probability": p.model_probability,
                    "market_probability": p.market_probability,
                    "edge": p.edge,
                    "fair_odds": p.fair_odds,
                    "confidence": p.confidence,
                    "data_quality": p.data_quality,
                }
                for p in predictions
            ]

    output_path = REPO_ROOT / "data" / "processed" / f"report_{target_date.isoformat()}.json"
    output_path.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")
    typer.echo(f"[report] escrito en {output_path}")


if __name__ == "__main__":
    app()
