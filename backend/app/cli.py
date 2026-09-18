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
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.ingestion.football_data.fixtures_provider import OpenFootballFixturesProvider
from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider
from backend.app.ingestion.football_data.provider import (
    COMPETITION_DIV_CODES,
    FootballDataCoUkProvider,
)
from backend.app.ingestion.odds.provider import OddsApiProvider
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.services.data_service import attach_odds_to_scheduled_matches, ingest_matches
from backend.app.services.match_service import load_market_odds_column, load_matches_dataframe
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.app.utils.dates import season_label as season_label_from_date
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
def update_fixtures(competition: str = typer.Option(None)) -> None:
    """Descarga el CALENDARIO real de la temporada en curso (partidos aun no
    jugados, con fecha real) via openfootball/football.json. Complementa
    `update` (que solo trae partidos YA jugados): sin esto, no hay ningun
    partido futuro sobre el que generar predicciones "de hoy".
    """
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    season_label = season_label_from_date(dt.date.today())
    provider = OpenFootballFixturesProvider()

    with session_scope() as db:
        for comp in competitions:
            try:
                n = ingest_matches(db, provider, comp, season_label)
                typer.echo(f"[update-fixtures] {comp} {season_label}: {n} partidos programados")
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-fixtures] {comp} {season_label}: ERROR {exc}")


@app.command()
def update_odds(competition: str = typer.Option(None)) -> None:
    """Descarga cuotas REALES de partidos futuros (The Odds API) y las asocia
    a los partidos ya programados (creados por `update-fixtures`).

    Requiere ODDS_API_ENABLED=true y ODDS_API_KEY configurados en `.env`
    (registro gratuito en https://the-odds-api.com, plan free = 500
    requests/mes). Sin esto configurado, se salta sin error: el resto del
    sistema sigue funcionando igual, simplemente sin `market_probability`
    ni `edge` para partidos futuros (los partidos ya jugados si tienen
    cuotas historicas via `update`).
    """
    init_db()
    provider = OddsApiProvider()
    if not provider.is_available():
        env_path = REPO_ROOT / ".env"
        typer.echo("[update-odds] ODDS_API_KEY no configurada: sin cuotas de mercado para partidos futuros.")
        typer.echo(f"[update-odds] Fichero de configuracion esperado: {env_path}")
        if not env_path.exists():
            typer.echo(
                "[update-odds] ESE FICHERO NO EXISTE. Si solo has editado '.env.example', "
                "eso NO es suficiente: copialo primero con 'cp .env.example .env' y luego "
                "edita el .env nuevo (no el .example)."
            )
        typer.echo("[update-odds] Registrate gratis en https://the-odds-api.com y en tu .env pon:")
        typer.echo("[update-odds]   ODDS_API_ENABLED=true")
        typer.echo("[update-odds]   ODDS_API_KEY=<tu-key>")
        return

    competitions = [competition] if competition else ALL_COMPETITIONS
    with session_scope() as db:
        for comp in competitions:
            try:
                result = attach_odds_to_scheduled_matches(db, provider, comp)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-odds] {comp}: ERROR {exc}")
                continue

            typer.echo(
                f"[update-odds] {comp}: la API devolvio {result.fixtures_fetched} partidos futuros, "
                f"{result.matched} casaron con partidos ya programados en nuestra BD."
            )
            if result.fixtures_fetched == 0:
                typer.echo(
                    f"[update-odds] {comp}: la API no tiene ahora mismo ningun partido futuro para "
                    "esta liga (puede que no haya jornada en los proximos dias, o que el plan "
                    "gratuito tenga cobertura limitada para esta competicion)."
                )
            elif result.matched == 0:
                typer.echo(
                    f"[update-odds] {comp}: la API SI devolvio partidos pero NINGUNO caso por "
                    "nombre de equipo o fecha. Ejemplos de partidos de la API sin casar "
                    "(nombre_local, nombre_visitante):"
                )
                for home, away in result.unmatched_examples:
                    typer.echo(f"[update-odds]   - '{home}' vs '{away}'")
                typer.echo(
                    "[update-odds]   Si esos nombres no coinciden con los que usa el resto del "
                    "sistema, hay que anadir un alias en backend/app/normalization/teams.py."
                )

            # Sin esto, las cuotas quedan guardadas en MatchOdds pero las
            # Prediction ya generadas (o las que se generen mas tarde sin
            # volver a tocar este comando) seguirian sin `market_probability`/
            # `edge`: una Prediction es una foto fija en el tiempo, no se
            # recalcula sola cuando llegan cuotas nuevas. Se regeneran aqui
            # mismo las predicciones de los partidos que SI consiguieron
            # cuota, para que un unico comando deje todo consistente sin
            # tener que acordarse de correr `predict`/`predict-upcoming`
            # despues a mano.
            if result.matched_match_ids:
                dates = sorted(
                    {
                        m.kickoff_utc.date()
                        for m in db.query(Match).filter(Match.id.in_(result.matched_match_ids))
                    }
                )
                regenerated = 0
                for target_date in dates:
                    regenerated += len(generate_predictions_for_competition(db, comp, target_date))
                typer.echo(
                    f"[update-odds] {comp}: {regenerated} predicciones regeneradas con las cuotas nuevas."
                )


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
def predict_upcoming(
    days: int = typer.Option(10, help="Genera predicciones para partidos programados en los proximos N dias"),
    competition: str = typer.Option(None),
) -> None:
    """Genera predicciones para TODOS los partidos programados (todas las
    competiciones por defecto) dentro de una ventana de dias.

    Es el comando que faltaba para no tener que llamar a `predict` fecha por
    fecha y competicion por competicion: recorre cada (competicion, fecha
    con partidos programados) y llama a `generate_predictions_for_competition`
    para cada una. Requiere haber corrido antes `update-fixtures` y `train`.
    """
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    horizon = dt.datetime.utcnow() + dt.timedelta(days=days)

    with session_scope() as db:
        total = 0
        for comp_code in competitions:
            comp = db.query(Competition).filter_by(code=comp_code).one_or_none()
            if comp is None:
                typer.echo(f"[predict-upcoming] {comp_code}: sin datos, ejecuta antes 'update'")
                continue
            dates = sorted(
                {
                    m.kickoff_utc.date()
                    for m in db.query(Match).filter(
                        Match.competition_id == comp.id,
                        Match.status == "scheduled",
                        Match.kickoff_utc <= horizon,
                    )
                }
            )
            if not dates:
                typer.echo(f"[predict-upcoming] {comp_code}: sin partidos programados en {days} dias")
                continue
            comp_total = 0
            for target_date in dates:
                comp_total += len(generate_predictions_for_competition(db, comp_code, target_date))
            typer.echo(f"[predict-upcoming] {comp_code}: {comp_total} predicciones ({len(dates)} fechas)")
            total += comp_total
        typer.echo(f"[predict-upcoming] TOTAL: {total} predicciones generadas")


@app.command()
def reset_scheduled(competition: str = typer.Option(None)) -> None:
    """Borra TODOS los partidos con status='scheduled' (y sus predicciones y
    cuotas asociadas) para una o todas las competiciones. NO toca partidos
    'finished' (el historico real usado para entrenar no se ve afectado).

    Pensado como limpieza puntual si aparecen en el dashboard partidos que
    no tienen sentido (equipos que ya no juegan en esa liga, fechas
    antiguas, duplicados...): normalmente son restos de pruebas de una fase
    anterior del desarrollo que quedaron guardados en la base de datos
    local (el fichero SQLite persiste entre ejecuciones aunque el codigo
    cambie). Tras limpiar, hay que volver a traer fixtures/cuotas/predicciones
    reales:

        football-edge reset-scheduled
        football-edge refresh --skip-historical
    """
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    with session_scope() as db:
        total_matches = 0
        total_predictions = 0
        for comp_code in competitions:
            comp = db.query(Competition).filter_by(code=comp_code).one_or_none()
            if comp is None:
                continue
            scheduled = (
                db.query(Match)
                .filter(Match.competition_id == comp.id, Match.status == "scheduled")
                .all()
            )
            for match in scheduled:
                total_predictions += (
                    db.query(Prediction).filter(Prediction.match_id == match.id).delete()
                )
                db.delete(match)  # cascade borra match_statistics/match_odds (ver models/matches.py)
            total_matches += len(scheduled)
        db.commit()
    typer.echo(
        f"[reset-scheduled] Borrados {total_matches} partidos programados y "
        f"{total_predictions} predicciones asociadas."
    )
    typer.echo("[reset-scheduled] Ejecuta ahora 'football-edge refresh --skip-historical' para regenerar todo.")


@app.command()
def refresh(
    days: int = typer.Option(10, help="Ventana de dias para fixtures/predicciones"),
    competition: str = typer.Option(None),
    skip_historical: bool = typer.Option(
        False, help="Salta la descarga de resultados historicos (rapido si ya la corriste antes)"
    ),
) -> None:
    """Un unico comando que deja el sistema listo para ver predicciones: hace
    `update` + `update-fixtures` + `update-odds` + `train` + `predict-upcoming`
    en secuencia.

    Pensado para no tener que acordarse de encadenar los comandos a mano cada
    vez que quieres refrescar el dashboard. Usa `--skip-historical` en
    ejecuciones repetidas del mismo dia (los resultados ya jugados no
    cambian cada pocas horas; los fixtures, las cuotas y las predicciones si
    conviene refrescarlos a menudo). `update-odds` se ejecuta ANTES de
    generar las predicciones para que estas ya incluyan `market_probability`
    y `edge` cuando haya cuotas disponibles (requiere ODDS_API_KEY; si no
    esta configurada, se salta sola sin romper el resto del pipeline).
    """
    typer.echo("=== [1/5] Resultados historicos ===")
    if skip_historical:
        typer.echo("(saltado por --skip-historical)")
    else:
        update(competition=competition, season=None, source="history_dataset")

    typer.echo("=== [2/5] Fixtures reales (temporada en curso) ===")
    update_fixtures(competition=competition)

    typer.echo("=== [3/5] Cuotas de mercado reales (partidos futuros) ===")
    update_odds(competition=competition)

    typer.echo("=== [4/5] Entrenamiento de modelos ===")
    train(competition=competition)

    typer.echo("=== [5/5] Predicciones para partidos programados ===")
    predict_upcoming(days=days, competition=competition)

    typer.echo("\nListo. Arranca (o recarga) la API y el dashboard para verlo.")


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
