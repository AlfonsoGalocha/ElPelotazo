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

from backend.app.backtesting.calibration import calibration_report_for_table
from backend.app.backtesting.engine import run_walk_forward_backtest, summarize_backtest
from backend.app.backtesting.reports import write_backtest_report
from backend.app.config.settings import REPO_ROOT
from backend.app.db.database import init_db, session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
from backend.app.ingestion.football_data.fixtures_provider import OpenFootballFixturesProvider
from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider
from backend.app.ingestion.football_data.provider import (
    COMPETITION_DIV_CODES,
    FootballDataCoUkProvider,
)
from backend.app.ingestion.odds.provider import OddsApiProvider
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.services.data_service import (
    attach_odds_to_scheduled_matches,
    attach_secondary_odds_to_scheduled_matches,
    ingest_matches,
)
from backend.app.services.match_service import load_market_odds_column, load_matches_dataframe
from backend.app.services.model_service import train_competition_models
from backend.app.services.evaluation_service import evaluate_settled_predictions, settle_finished_predictions
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.app.services.round_service import get_current_round
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
    refresh_cache: bool = typer.Option(
        True,
        help=(
            "Re-descarga el dataset historico aunque ya este cacheado en disco. BUG REAL corregido: "
            "sin esto por defecto, una vez descargado el CSV la primera vez, este comando nunca volvia "
            "a comprobar si habia partidos nuevos jugados, por mucho que se re-ejecutara -- exactamente "
            "lo necesario para recoger resultados reales tras acabar una jornada. Desactivalo "
            "(--no-refresh-cache) solo para pruebas repetidas el mismo dia sin gastar red."
        ),
    ),
) -> None:
    """Descarga e ingesta datos historicos reales (resultados + cuotas)."""
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    seasons = [season] if season else _seasons_since()
    provider = ClubFootballMatchDataProvider() if source == "history_dataset" else FootballDataCoUkProvider()

    # Una unica invalidacion de cache ANTES del bucle (no dentro, por
    # competicion/temporada): el CSV cubre TODAS las ligas/temporadas en
    # un unico archivo, asi que invalidar dentro del bucle forzaria una
    # redescarga completa por cada combinacion (hasta 25 veces) en vez de
    # una sola vez para toda la ejecucion del comando.
    if refresh_cache and hasattr(provider, "refresh_cache"):
        provider.refresh_cache()

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
def update_secondary_odds(competition: str = typer.Option(None)) -> None:
    """Descarga cuotas REALES de tarjetas/corners (API-Football) para los
    partidos de la JORNADA ACTUAL de cada competicion (nunca la temporada
    completa: el plan gratuito de API-Football son 100 requests/dia, y
    consultar toda la temporada lo agotaria de inmediato).

    Requiere API_FOOTBALL_ENABLED=true y API_FOOTBALL_KEY en `.env`
    (registro en https://www.api-football.com o via RapidAPI — en ese caso
    pon ademas API_FOOTBALL_USE_RAPIDAPI=true). Sin esto configurado, se
    salta sin error: tarjetas/corners siguen funcionando igual que hasta
    ahora, solo como "prediccion del modelo — sin mercado".

    IMPORTANTE: The Odds API (`update-odds`) NO cubre estos mercados en
    ningun plan (ver docs/data_sources.md) — por eso hace falta esta
    fuente aparte. Sin verificar end-to-end contra la API real desde este
    entorno (red restringida); si el matching de mercados falla, el propio
    comando imprime los nombres de mercado reales que SI vio, para
    ajustarlo en un vistazo.
    """
    init_db()
    provider = ApiFootballOddsProvider()
    if not provider.is_available():
        typer.echo(
            "[update-secondary-odds] API_FOOTBALL_KEY no configurada: sin cuotas de tarjetas/corners."
        )
        typer.echo(
            "[update-secondary-odds] Registrate en https://www.api-football.com (o via RapidAPI) y en tu .env pon:"
        )
        typer.echo("[update-secondary-odds]   API_FOOTBALL_ENABLED=true")
        typer.echo("[update-secondary-odds]   API_FOOTBALL_KEY=<tu-key>")
        typer.echo("[update-secondary-odds]   API_FOOTBALL_USE_RAPIDAPI=true  # solo si tu key es de RapidAPI")
        return

    competitions = [competition] if competition else ALL_COMPETITIONS
    with session_scope() as db:
        for comp in competitions:
            round_info = get_current_round(db, comp)
            if round_info is None or not round_info.match_ids:
                typer.echo(f"[update-secondary-odds] {comp}: sin partidos programados, se salta.")
                continue

            season = db.query(Season).filter_by(id=round_info.season_id).one()
            season_year = int(season.label.split("/")[0])
            window_start = (round_info.round_start or dt.datetime.utcnow()).date()
            window_end = (round_info.round_end or dt.datetime.utcnow()).date()

            try:
                result = attach_secondary_odds_to_scheduled_matches(
                    db, provider, comp, season_year, window_start, window_end
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-secondary-odds] {comp}: ERROR {exc}")
                continue

            typer.echo(
                f"[update-secondary-odds] {comp}: {result.fixtures_fetched} partidos consultados, "
                f"{result.matched} con cuotas de tarjetas/corners casadas."
            )
            if result.matched_match_ids:
                dates = sorted(
                    {m.kickoff_utc.date() for m in db.query(Match).filter(Match.id.in_(result.matched_match_ids))}
                )
                regenerated = 0
                for target_date in dates:
                    regenerated += len(generate_predictions_for_competition(db, comp, target_date))
                typer.echo(
                    f"[update-secondary-odds] {comp}: {regenerated} predicciones regeneradas con las cuotas nuevas."
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
    `update` + `update-fixtures` + `update-odds` + `update-secondary-odds` +
    `train` + `predict-upcoming` en secuencia.

    Pensado para no tener que acordarse de encadenar los comandos a mano cada
    vez que quieres refrescar el dashboard. Usa `--skip-historical` en
    ejecuciones repetidas del mismo dia (los resultados ya jugados no
    cambian cada pocas horas; los fixtures, las cuotas y las predicciones si
    conviene refrescarlos a menudo). Las cuotas se descargan ANTES de
    generar las predicciones para que estas ya incluyan `market_probability`
    y `edge` cuando haya cuotas disponibles (requieren ODDS_API_KEY /
    API_FOOTBALL_KEY respectivamente; si no estan configuradas, cada paso
    se salta solo sin romper el resto del pipeline).

    Paso 2 (`evaluate`): en cuanto llegan resultados reales nuevos (paso 1),
    compara las predicciones que YA estaban guardadas de esos partidos
    contra el resultado real -- asi queda un historial de "como de bien
    predijo el modelo" ANTES de que el reentrenamiento del paso 6 cambie
    nada. Nunca falla el `refresh` completo si no hay nada nuevo que
    evaluar (p.ej. la primera vez que se ejecuta).
    """
    typer.echo("=== [1/7] Resultados historicos ===")
    if skip_historical:
        typer.echo("(saltado por --skip-historical)")
    else:
        update(competition=competition, season=None, source="history_dataset")

    typer.echo("=== [2/7] Evaluar predicciones de partidos ya finalizados ===")
    evaluate(competition=competition)

    typer.echo("=== [3/7] Fixtures reales (temporada en curso) ===")
    update_fixtures(competition=competition)

    typer.echo("=== [4/7] Cuotas de mercado reales: goles/1X2 (The Odds API) ===")
    update_odds(competition=competition)

    typer.echo("=== [5/7] Cuotas de mercado reales: tarjetas/corners (API-Football) ===")
    update_secondary_odds(competition=competition)

    typer.echo("=== [6/7] Entrenamiento de modelos ===")
    train(competition=competition)

    typer.echo("=== [7/7] Predicciones para partidos programados ===")
    predict_upcoming(days=days, competition=competition)

    typer.echo("\nListo. Arranca (o recarga) la API y el dashboard para verlo.")


@app.command()
def evaluate(competition: str = typer.Option(None), output: str | None = None) -> None:
    """Compara las predicciones YA HECHAS contra el resultado REAL de los
    partidos que ya se jugaron ("como de bien acerto el modelo la jornada
    pasada"), sin volver a entrenar nada -- ver
    services/evaluation_service.py. Pensado para ejecutarse DESPUES de
    `football-edge update` (que trae los resultados reales) y ANTES o
    DESPUES de `train`, da igual: usa las predicciones que YA estaban
    guardadas de antes del partido, nunca predicciones nuevas.

    Escribe un JSON con Brier score / log loss / calibracion por mercado
    (siempre) y ROI hipotetico (solo si habia cuota de mercado real).
    """
    init_db()
    with session_scope() as db:
        settled_count = settle_finished_predictions(db, competition_code=competition)
        report_data = evaluate_settled_predictions(db, competition_code=competition)

    if settled_count:
        typer.echo(
            f"[evaluate] {settled_count} predicciones liquidadas "
            "(PredictionResult creado, nunca sobreescrito)."
        )

    if not report_data["competitions"]:
        typer.echo(
            "[evaluate] Sin partidos finalizados con predicciones guardadas todavia. "
            "Ejecuta 'football-edge update' para traer resultados reales, luego reintenta."
        )
        return

    for comp_code, markets in report_data["competitions"].items():
        typer.echo(f"--- {comp_code} ---")
        for market, market_report in markets.items():
            quality = market_report["model_quality"]
            strategy = market_report["market_strategy"] or {}
            roi = strategy.get("roi")
            suffix = f" | ROI={roi:.3f} (n_apuestas={strategy.get('n_bets', 0)})" if roi is not None else ""
            typer.echo(
                f"  {market}: n={market_report['n_predictions']} "
                f"brier={quality['brier_score']:.4f} log_loss={quality['log_loss']:.4f} "
                f"accuracy={quality['accuracy_secondary_only']:.3f}" + suffix
            )

    output_path = Path(output) if output else REPO_ROOT / "data" / "processed" / "evaluation_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report_data, indent=2, default=str))
    typer.echo(f"[evaluate] informe completo escrito en {output_path}")


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
def calibration_report(
    competition: str, market: str = "over_2_5", method: str = "isotonic", output: str | None = None
) -> None:
    """Compara probabilidad CRUDA del modelo vs CALIBRADA (isotonic/platt)
    sobre las mismas predicciones out-of-fold del backtest walk-forward
    (seccion 19 del pedido de revision integral: "no introduzcas
    calibracion automaticamente sin evaluar primero"). Las predicciones en
    produccion (`prediction_service.py`) usan SIEMPRE la probabilidad
    CRUDA para el edge -- este comando es solo diagnostico, no cambia nada
    del pipeline. Si demuestra una mejora real y consistente, seria la
    evidencia necesaria para plantear activar calibracion en produccion.
    """
    init_db()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition).one_or_none()
        if comp is None:
            typer.echo(f"Competicion no encontrada: {competition}")
            raise typer.Exit(1)
        matches = load_matches_dataframe(db, comp.id)
        table = build_match_feature_table(matches)
        report_data = calibration_report_for_table(table, DixonColesModel, market, method=method)

    if "error" in report_data:
        typer.echo(f"[calibration-report] {report_data['error']}")
        return

    typer.echo(
        f"[calibration-report] {competition}/{market} ({method}, n_test={report_data['n_test']}): "
        f"brier {report_data['before']['brier_score']:.4f} -> {report_data['after']['brier_score']:.4f} | "
        f"log_loss {report_data['before']['log_loss']:.4f} -> {report_data['after']['log_loss']:.4f} | "
        f"ECE {report_data['before']['expected_calibration_error']:.4f} -> "
        f"{report_data['after']['expected_calibration_error']:.4f}"
    )
    output_path = (
        Path(output) if output else REPO_ROOT / "data" / "processed" / f"calibration_{competition}_{market}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report_data, indent=2, default=str))
    typer.echo(f"[calibration-report] informe completo escrito en {output_path}")


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
