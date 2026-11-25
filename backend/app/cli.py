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
import time
from pathlib import Path

import typer

from backend.app.agent.alerts import generate_alerts_for_current_round
from backend.app.backtesting.calibration import (
    calibration_report_for_table,
    ensemble_calibration_report,
)
from backend.app.backtesting.engine import run_walk_forward_backtest, summarize_backtest
from backend.app.backtesting.ranking_comparison import (
    compare_ranking_strategies,
    compare_ranking_strategies_walk_forward,
    load_settled_predictions,
    load_settled_predictions_with_kickoff,
)
from backend.app.backtesting.reports import write_backtest_report
from backend.app.config.settings import REPO_ROOT, get_settings
from backend.app.db.database import init_db, session_scope
from backend.app.db.models.core import Competition, Season, Team, TeamNameMapping
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.features.goals import build_match_feature_table, feature_columns
from backend.app.ingestion.api_football.client import INTERNATIONAL_COMPETITION_EDITIONS
from backend.app.ingestion.api_football.fixtures_provider import ApiFootballMatchProvider
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
from backend.app.ingestion.football_data.fixtures_provider import OpenFootballFixturesProvider
from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider
from backend.app.ingestion.football_data.provider import (
    COMPETITION_DIV_CODES,
    FootballDataCoUkProvider,
)
from backend.app.ingestion.odds.provider import OddsApiProvider
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.monitoring.drift import feature_drift_report
from backend.app.normalization.teams import canonical_name_for
from backend.app.services.data_service import (
    attach_odds_to_scheduled_matches,
    attach_secondary_odds_to_scheduled_matches,
    competition_season_has_matches,
    ingest_matches,
)
from backend.app.services.evaluation_service import (
    evaluate_settled_predictions,
    settle_finished_predictions,
    void_cancelled_predictions,
)
from backend.app.services.match_service import load_market_odds_column, load_matches_dataframe
from backend.app.services.model_service import train_competition_models
from backend.app.services.odds_diagnostics_service import record_ingestion_run
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.app.services.promotion_service import evaluate_and_promote
from backend.app.services.round_service import get_current_round
from backend.app.utils.dates import season_label as season_label_from_date
from backend.app.utils.logging import get_logger

app = typer.Typer(help="Football Edge Detector CLI")
logger = get_logger(__name__)

from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS, CHAMPIONS_LEAGUE

ALL_COMPETITIONS = [*COMPETITION_DIV_CODES, *API_CANDIDATE_COMPETITIONS]


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
    force_closed_seasons: bool = typer.Option(
        False,
        "--force-closed-seasons",
        help=(
            "Reingiere tambien temporadas YA CERRADAS que ya tengan partidos en la BD (lento: "
            "~9 temporadas x 5 ligas). Por defecto NO -- una temporada cerrada no cambia nunca, "
            "asi que reprocesarla en cada `refresh` es trabajo repetido sin sentido (bug real "
            "reportado por el usuario: 'refresh' tardaba 10 min TODAS las veces). Sin esta opcion, "
            "solo se reingiere la temporada EN CURSO (la unica que puede traer partidos nuevos) y, "
            "para una (competicion, temporada) nunca vista, su primera ingesta -- igual de completa "
            "que antes, solo que una vez, no en cada ejecucion."
        ),
    ),
) -> None:
    """Descarga e ingesta datos historicos reales (resultados + cuotas)."""
    init_db()
    if competition in API_CANDIDATE_COMPETITIONS:
        from backend.app.ingestion.api_football.client import LEAGUE_IDS
        provider = ApiFootballMatchProvider()
        if not provider.is_available():
            raise typer.Exit(1)
        if season:
            seasons = [season]
        else:
            metadata = provider._get("/leagues", {"id": LEAGUE_IDS[competition]})
            years = [r["year"] for r in metadata[0]["seasons"]] if metadata else []
            seasons = [f"{year}/{(year+1)%100:02d}" for year in years]
            provider.last_raw_responses.clear()
        with session_scope() as db:
            for label in seasons:
                if not force_closed_seasons and label != season_label_from_date(dt.date.today()) and competition_season_has_matches(db, competition, label):
                    continue
                count = ingest_matches(db, provider, competition, label, operation=f"update_{competition.removesuffix('_league')}_history")
                typer.echo(f"[update] {competition} {label}: {count} partidos")
        return
    competitions = [competition] if competition else list(COMPETITION_DIV_CODES)
    seasons = [season] if season else _seasons_since()
    provider = ClubFootballMatchDataProvider() if source == "history_dataset" else FootballDataCoUkProvider()
    current_season_label = season_label_from_date(dt.date.today())

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
                if (
                    not force_closed_seasons
                    and s != current_season_label
                    and competition_season_has_matches(db, comp, s)
                ):
                    typer.echo(f"[update] {comp} {s}: omitido (temporada cerrada ya en BD)")
                    continue
                try:
                    n = ingest_matches(db, provider, comp, s)
                    typer.echo(f"[update] {comp} {s}: {n} partidos")
                except Exception as exc:  # noqa: BLE001
                    typer.echo(f"[update] {comp} {s}: ERROR {exc}")
                    # Bug real reportado por el usuario: sin este rollback, un
                    # fallo real de conexion a mitad de esta iteracion (p.ej.
                    # Postgres cerrando la conexion) deja la sesion en estado
                    # invalido -- TODAS las iteraciones siguientes (de esta
                    # competicion Y de las demas) fallaban en cascada con
                    # "PendingRollbackError", ocultando el error real detras
                    # de decenas de lineas identicas e inutiles.
                    db.rollback()


@app.command()
def update_fixtures(competition: str = typer.Option(None)) -> None:
    """Descarga el CALENDARIO real de la temporada en curso (partidos aun no
    jugados, con fecha real) -- sin esto, no hay ningun partido futuro
    sobre el que generar predicciones "de hoy".

    Tambien trae, de la MISMA fuente, el marcador de los partidos YA
    jugados de la temporada en curso (sin estadisticas, que siguen
    viniendo de `update`) -- para que Historico/settlement no se queden
    parados esperando el mirror historico de `update` (que puede tardar
    semanas en reflejar la temporada en curso, bug real reportado por un
    usuario: histórico parado meses atras aunque ya se hubieran jugado
    muchas mas jornadas).

    Con API_FOOTBALL_ENABLED=true (ver `update-odds`), usa API-Football en
    vez de openfootball/football.json: fuente de pago, actualizada en
    tiempo real, en vez de un dataset mantenido por voluntarios que puede
    quedarse semanas sin actualizar. Sin esa config, sigue usando
    openfootball/football.json (gratis) exactamente como antes -- nunca
    rompe a quien no haya configurado API-Football.
    """
    init_db()
    settings = get_settings()
    competitions = [competition] if competition else ALL_COMPETITIONS
    season_label = season_label_from_date(dt.date.today())
    use_api_football = settings.api_football_enabled and bool(settings.api_football_key)
    provider = ApiFootballMatchProvider() if use_api_football else OpenFootballFixturesProvider()
    source_label = "API-Football" if use_api_football else "openfootball/football.json"

    with session_scope() as db:
        for comp in competitions:
            if comp in API_CANDIDATE_COMPETITIONS and not use_api_football:
                continue
            try:
                n = ingest_matches(db, provider, comp, season_label, operation="update_fixtures")
                typer.echo(
                    f"[update-fixtures] {comp} {season_label} ({source_label}): "
                    f"{n} partidos actualizados"
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-fixtures] {comp} {season_label}: ERROR {exc}")
                db.rollback()  # ver comentario equivalente en `update()`


@app.command("update-champions-statistics")
def update_champions_statistics(
    batches: int = typer.Option(1, min=1, max=50),
    competition: str = typer.Option(CHAMPIONS_LEAGUE),
) -> None:
    """Fill verified regulation-time statistics using batched API-Football fixtures."""
    from backend.app.services.champions_statistics import refresh_champions_statistics

    with session_scope() as db:
        from backend.app.services.champions_statistics import DOMESTIC_LEAGUES

        if competition not in (*API_CANDIDATE_COMPETITIONS, *DOMESTIC_LEAGUES):
            raise typer.BadParameter("Competición no soportada")
        count = refresh_champions_statistics(db, ApiFootballMatchProvider(), batches, competition)
    typer.echo(f"[update-champions-statistics] {count} partidos enriquecidos")


@app.command("update-international")
def update_international(
    competition: str = typer.Option(
        None,
        help=(
            "Codigo de competicion de selecciones (ver INTERNATIONAL_COMPETITION_EDITIONS). "
            "Sin especificar, ingiere TODAS las registradas."
        ),
    ),
) -> None:
    """Ingiere TODO el historico real de competiciones de SELECCIONES
    (seccion "Selecciones", pedido real del usuario, 2026-11) via
    API-Football -- la UNICA fuente real para estas competiciones: a
    diferencia de las 5 ligas de club, no existe un mirror historico tipo
    football-data.co.uk/openfootball para selecciones, asi que `update`
    (pensado para ese mirror) no sirve aqui.

    Recorre TODAS las ediciones conocidas de cada competicion
    (`INTERNATIONAL_COMPETITION_EDITIONS`, p.ej. Nations League es BIENAL:
    2018/2020/2022/2024/2026; el Mundial/la Eurocopa cada 4 anhos) en una
    sola ejecucion -- a diferencia de `update` para clubes, el volumen
    aqui es pequenho (unas pocas decenas de partidos por edicion), no hace
    falta la logica de "solo reingerir la temporada en curso" para evitar
    un `refresh` lento.

    Cada edicion se etiqueta con el MISMO `Season.label` que
    `update-fixtures`/`update-odds` ya usarian para esa fecha (via
    `season_label_from_date`, nunca un anho suelto) -- para que la edicion
    EN CURSO comparta fila de `Season` con lo que esos comandos vayan
    trayendo en vivo, en vez de duplicarla.

    Pausa 1 segundo entre cada peticion a `/fixtures` (bug real visto en
    vivo: con ~15 competiciones x varias ediciones cada una, sin pausa se
    agota el limite de peticiones por minuto de la API -- `_get` ya
    reintenta un `rateLimit` con backoff, ver
    `ApiFootballRateLimitError`, pero evitarlo de entrada es mas barato
    que depender solo del reintento).
    """
    init_db()
    if competition is not None and competition not in INTERNATIONAL_COMPETITION_EDITIONS:
        typer.echo(
            f"[update-international] Competicion no soportada: {competition!r}. "
            f"Opciones: {sorted(INTERNATIONAL_COMPETITION_EDITIONS)}"
        )
        raise typer.Exit(code=1)
    competitions = [competition] if competition else sorted(INTERNATIONAL_COMPETITION_EDITIONS)

    provider = ApiFootballMatchProvider()
    with session_scope() as db:
        for comp_code in competitions:
            for year in INTERNATIONAL_COMPETITION_EDITIONS[comp_code]:
                season_label = season_label_from_date(dt.date(year, 9, 1))
                try:
                    n = ingest_matches(db, provider, comp_code, season_label, operation="update_international")
                    typer.echo(
                        f"[update-international] {comp_code} {season_label} ({year}): {n} partidos"
                    )
                except Exception as exc:  # noqa: BLE001
                    typer.echo(
                        f"[update-international] {comp_code} {season_label} ({year}): ERROR {exc}"
                    )
                    db.rollback()
                time.sleep(1)


@app.command()
def update_odds(competition: str = typer.Option(None)) -> None:
    """Descarga cuotas REALES de partidos futuros y las asocia a los
    partidos ya programados (creados por `update-fixtures`).

    Con API_FOOTBALL_ENABLED=true (y API_FOOTBALL_KEY configurada), usa
    API-Football: en UN UNICO fetch por partido trae 1X2, goles Over/Under,
    BTTS Y corners (nunca pide `/odds` dos veces para el mismo
    partido -- ver `update-secondary-odds`, que se salta solo si detecta
    que esta config ya lo cubrio). Sin esa config, sigue usando The Odds
    API exactamente como antes (ODDS_API_ENABLED=true / ODDS_API_KEY en
    `.env`, registro gratuito en https://the-odds-api.com, plan free = 500
    requests/mes) -- esa fuente NUNCA cubre corners en ningun
    plan, solo 1X2/goles.

    Sin ninguna de las dos fuentes configurada, se salta sin error: el
    resto del sistema sigue funcionando igual, simplemente sin
    `market_probability` ni `edge` para partidos futuros (los partidos ya
    jugados si tienen cuotas historicas via `update`).
    """
    init_db()
    settings = get_settings()
    use_api_football = settings.api_football_enabled and bool(settings.api_football_key)
    provider = ApiFootballOddsProvider() if use_api_football else OddsApiProvider()
    source_label = "API-Football" if use_api_football else "The Odds API"
    if not provider.is_available():
        env_path = REPO_ROOT / ".env"
        typer.echo(
            "[update-odds] Ninguna fuente de cuotas configurada: sin cuotas de mercado "
            "para partidos futuros."
        )
        typer.echo(f"[update-odds] Fichero de configuracion esperado: {env_path}")
        if not env_path.exists():
            typer.echo(
                "[update-odds] ESE FICHERO NO EXISTE. Si solo has editado '.env.example', "
                "eso NO es suficiente: copialo primero con 'cp .env.example .env' y luego "
                "edita el .env nuevo (no el .example)."
            )
        typer.echo(
            "[update-odds] Opcion recomendada (cubre 1X2/goles/BTTS/corners de una vez):"
        )
        typer.echo(
            "[update-odds]   Registrate en https://www.api-football.com "
            "(o via RapidAPI) y en tu .env pon:"
        )
        typer.echo("[update-odds]   API_FOOTBALL_ENABLED=true")
        typer.echo("[update-odds]   API_FOOTBALL_KEY=<tu-key>")
        typer.echo("[update-odds] Alternativa gratis (solo 1X2/goles, NUNCA corners):")
        typer.echo(
            "[update-odds]   Registrate gratis en https://the-odds-api.com y en tu .env pon:"
        )
        typer.echo("[update-odds]   ODDS_API_ENABLED=true")
        typer.echo("[update-odds]   ODDS_API_KEY=<tu-key>")
        return

    provider_key = "api_football" if use_api_football else "the_odds_api"
    competitions = [competition] if competition else ALL_COMPETITIONS
    with session_scope() as db:
        if use_api_football and not competition:
            # Matches in the next capture window already sort first by kickoff.
            from sqlalchemy import func

            next_kickoffs = dict(
                db.query(Competition.code, func.min(Match.kickoff_utc)).join(Match)
                .filter(Competition.code.in_(competitions), Match.status == "scheduled",
                        Match.kickoff_utc > dt.datetime.utcnow()).group_by(Competition.code)
            )
            competitions = sorted(competitions, key=lambda code: (next_kickoffs.get(code, dt.datetime.max), code))
        for comp in competitions:
            if comp in API_CANDIDATE_COMPETITIONS and not use_api_football:
                continue
            started_at = dt.datetime.utcnow()
            try:
                result = attach_odds_to_scheduled_matches(db, provider, comp)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-odds] {comp}: ERROR {exc}")
                db.rollback()  # ver comentario equivalente en `update()`
                # Panel de diagnostico (Configuracion): registrar el
                # intento FALLIDO tambien, en su propia transaccion --
                # el rollback de arriba ya descarto la sesion anterior.
                with session_scope() as diag_db:
                    record_ingestion_run(
                        diag_db, provider_key, "update_odds", comp, started_at, dt.datetime.utcnow(), error=str(exc)
                    )
                continue

            failures = getattr(provider, "odds_fetch_failures", [])
            record_ingestion_run(
                db,
                provider_key,
                "update_odds",
                comp,
                started_at,
                dt.datetime.utcnow(),
                fixtures_fetched=result.fixtures_fetched,
                matched=result.matched,
                error="; ".join(failures) if failures else None,
            )

            typer.echo(
                f"[update-odds] {comp} ({source_label}): la API devolvio "
                f"{result.fixtures_fetched} partidos futuros, {result.matched} casaron "
                "con partidos ya programados en nuestra BD."
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
    """Descarga cuotas REALES de corners (API-Football) para los
    partidos de la JORNADA ACTUAL de cada competicion (nunca la temporada
    completa: el plan gratuito de API-Football son 100 requests/dia, y
    consultar toda la temporada lo agotaria de inmediato).

    Requiere API_FOOTBALL_ENABLED=true y API_FOOTBALL_KEY en `.env`
    (registro en https://www.api-football.com o via RapidAPI — en ese caso
    pon ademas API_FOOTBALL_USE_RAPIDAPI=true). Sin esto configurado, se
    salta sin error: corners siguen funcionando igual que hasta
    ahora, solo como "prediccion del modelo — sin mercado".

    IMPORTANTE: The Odds API (`update-odds`) NO cubre este mercado en
    ningun plan (ver docs/data_sources.md) — por eso hace falta esta
    fuente aparte. Sin verificar end-to-end contra la API real desde este
    entorno (red restringida); si el matching de mercados falla, el propio
    comando imprime los nombres de mercado reales que SI vio, para
    ajustarlo en un vistazo.

    Se SALTA sin hacer nada si `update-odds` ya esta usando API-Football
    como fuente primaria (API_FOOTBALL_ENABLED=true): ese comando ya trae
    corners en el MISMO fetch de `/odds`, pedirlo aqui otra vez
    seria duplicar la llamada por partido sin ninguna ganancia.
    """
    init_db()
    settings = get_settings()
    if settings.api_football_enabled and settings.api_football_key:
        typer.echo(
            "[update-secondary-odds] API-Football ya es la fuente primaria de cuotas "
            "(ver 'update-odds'): corners se trajeron en ese mismo paso, "
            "no hay nada que hacer aqui."
        )
        return

    provider = ApiFootballOddsProvider()
    if not provider.is_available():
        typer.echo(
            "[update-secondary-odds] API_FOOTBALL_KEY no configurada: sin cuotas de corners."
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

            started_at = dt.datetime.utcnow()
            try:
                result = attach_secondary_odds_to_scheduled_matches(
                    db, provider, comp, season_year, window_start, window_end
                )
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"[update-secondary-odds] {comp}: ERROR {exc}")
                db.rollback()  # ver comentario equivalente en `update()`
                with session_scope() as diag_db:
                    record_ingestion_run(
                        diag_db,
                        "api_football",
                        "update_secondary_odds",
                        comp,
                        started_at,
                        dt.datetime.utcnow(),
                        error=str(exc),
                    )
                continue

            record_ingestion_run(
                db,
                "api_football",
                "update_secondary_odds",
                comp,
                started_at,
                dt.datetime.utcnow(),
                fixtures_fetched=result.fixtures_fetched,
                matched=result.matched,
            )

            typer.echo(
                f"[update-secondary-odds] {comp}: {result.fixtures_fetched} partidos consultados, "
                f"{result.matched} con cuotas de corners casadas."
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
    """Entrena baseline + Dixon-Coles + ML classifier + ensemble por competicion.

    Tras entrenar, decide si la ModelVersion nueva SUSTITUYE a la que ya
    esta en produccion (`services/promotion_service.py::evaluate_and_promote`):
    solo si demuestra mejor Brier score en partidos reales POSTERIORES a su
    propio entrenamiento. Si no mejora (o no hay suficientes partidos
    nuevos con los que comparar todavia), la version de produccion
    anterior sigue siendo la que usa `predict`/`predict-upcoming` -- la
    nueva se guarda igual, nunca se descarta, solo no se activa.
    """
    init_db()
    competitions = [competition] if competition else ALL_COMPETITIONS
    from backend.app.services.continuous_learning import training_slot

    with session_scope() as db, training_slot(db) as acquired:
        if not acquired:
            typer.echo("[train] Ya hay otro entrenamiento en curso")
            return
        for comp in competitions:
            result = train_competition_models(db, comp)
            typer.echo(f"[train] {json.dumps(result, default=str)}")
            if result.get("status") != "trained":
                continue
            model_version = db.get(ModelVersion, result["model_version_id"])
            decision = evaluate_and_promote(db, comp, model_version)
            typer.echo(f"[train] promocion: {json.dumps(decision, default=str)}")


@app.command("learning-train")
def learning_train() -> None:
    from backend.app.services.continuous_learning import train_due

    with session_scope() as db:
        for report in train_due(db):
            typer.echo(json.dumps({key: value for key, value in report.items() if key != "metrics"}, default=str))


@app.command("learning-capture")
def learning_capture() -> None:
    from backend.app.services.continuous_learning import capture_comparisons

    from sqlalchemy import text

    with session_scope() as db:
        cutoff = None
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            cutoff = db.execute(text("SELECT timezone('UTC', statement_timestamp())")).scalar_one()
        typer.echo(f"[learning-capture] {capture_comparisons(db, cutoff=cutoff)} comparaciones registradas")


@app.command("publish-upcoming")
def publish_upcoming() -> None:
    """Capture the official forecast of fixtures entering the final hour."""
    from backend.app.services.publication_service import capture_due_publications

    with session_scope() as db:
        checked = capture_due_publications(db)
    typer.echo(f"[publish-upcoming] {checked} partidos comprobados")


@app.command("capture-strategies")
def capture_strategies() -> None:
    """Archive prospective selections for fixtures entering the final hour."""
    from backend.app.services.strategy_operations import run_strategy_scan

    count = run_strategy_scan()
    typer.echo(f"[capture-strategies] {count} cortes registrados")


@app.command("capture-selector-decisions")
def capture_selector_decisions_cmd() -> None:
    """Fase E (revision v2.8): congela el universo completo de candidatas/
    excluidas de "Mejores oportunidades" para los partidos de las
    proximas 48h -- ver services/selector_decision_service.py."""
    from backend.app.services.selector_decision_service import capture_selector_decisions

    with session_scope() as db:
        count = capture_selector_decisions(db)
    typer.echo(f"[capture-selector-decisions] {count} snapshots nuevos registrados")


@app.command("bot-decision")
def bot_decision(
    early: bool = typer.Option(False, help="Fija la decisión de hoy antes de la ventana automática."),
    dry_run: bool = typer.Option(False, help="Consulta las opciones actuales sin registrar la decisión."),
    trial: bool = typer.Option(False, help="Usa una banca de prueba independiente de 300 euros."),
    continuous: bool = typer.Option(False, help="Continúa la banca virtual de prueba con cada corte."),
    refresh_odds: bool = typer.Option(False, help="Actualiza antes las cuotas de los partidos de la próxima hora."),
) -> None:
    from backend.app.services.bot_bankroll import capture_bot_decision, refresh_imminent_odds

    if refresh_odds:
        with session_scope() as db:
            refreshed = refresh_imminent_odds(db)
        if refreshed:
            typer.echo(f"[bot-decision] cuotas actualizadas: {refreshed}", err=True)
    with session_scope() as db:
        decision = capture_bot_decision(db, early=early or dry_run, dry_run=dry_run, trial=trial, continuous=continuous)
    typer.echo(json.dumps(decision, ensure_ascii=False))


@app.command("archive-forecasts")
def archive_forecasts() -> None:
    """Guarda la previsión prepartido de los partidos de las próximas 3 horas (se congela al empezar)."""
    from backend.app.services.forecast_archive import archive_pre_match_forecasts

    init_db()
    with session_scope() as db:
        stored = archive_pre_match_forecasts(db)
    typer.echo(f"[archive-forecasts] {stored} previsiones guardadas")


@app.command("refresh-recent-results")
def refresh_recent_results_command() -> None:
    """Marca como terminados (con resultado) los partidos de las últimas 5 horas sin esperar al ciclo horario."""
    from backend.app.services.recent_results import refresh_recent_results

    with session_scope() as db:
        closed = refresh_recent_results(db)
    typer.echo(f"[refresh-recent-results] {closed} partidos cerrados")


@app.command("bot-manual-ticket")
def bot_manual_ticket(
    quote_id: int = typer.Option(..., help="Cuota prepartido archivada (match_odds_history.id)."),
    stake_eur: float = typer.Option(..., min=0.01, help="Stake virtual en euros, sujeto a los límites de riesgo."),
) -> None:
    """Registra una apuesta virtual manual, separada de las decisiones automáticas."""
    from backend.app.services.bot_bankroll import record_manual_ticket

    with session_scope() as db:
        payload = record_manual_ticket(db, quote_id, round(stake_eur * 100))
    typer.echo(json.dumps(payload, ensure_ascii=False))


@app.command("bot-simulate")
def bot_simulate(
    paths: int = typer.Option(2000, min=100), bets: int = typer.Option(150, min=10),
) -> None:
    """Simula los perfiles de riesgo del bot sobre oportunidades sintéticas, sin tocar ninguna banca."""
    from backend.app.services.bot_capital import simulate_profiles

    typer.echo(json.dumps(simulate_profiles(paths, bets), ensure_ascii=False, indent=2))


@app.command("reconcile-published-corners", hidden=True)
@app.command("reconcile-corners")
def reconcile_corners(
    limit: int = typer.Option(5, min=1, max=20),
    dry_run: bool = typer.Option(False, help="Muestra los partidos pendientes sin llamar al proveedor."),
) -> None:
    """Recover missing verified corner results for finished API-Football fixtures."""
    import httpx
    from sqlalchemy import or_

    from backend.app.db.models.core import ProviderResponse
    from backend.app.db.models.matches import CornerObservation, MatchStatistics
    from backend.app.db.models.publication import FixturePublication
    from backend.app.ingestion.api_football.client import ApiFootballClient
    from backend.app.services.corner_reconciliation import reconcile
    from backend.app.services.publication_service import latest_model_picks

    now = dt.datetime.utcnow()
    with session_scope() as db:
        last_attempt = {}
        for response in db.query(ProviderResponse.params, ProviderResponse.fetched_at).filter(
            ProviderResponse.operation == "reconcile_corners",
        ).order_by(ProviderResponse.fetched_at):
            last_attempt[str(response.params.get("fixture"))] = response.fetched_at

        published = db.query(FixturePublication.match_id).filter(FixturePublication.match_id == Match.id).exists()
        observed = db.query(CornerObservation.id).filter(CornerObservation.match_id == Match.id).exists()
        rows = db.query(Match.id, Match.provider_id, published.label("published")).outerjoin(
            MatchStatistics, MatchStatistics.match_id == Match.id
        ).filter(
            Match.status == "finished", Match.provider == "api_football", Match.kickoff_utc < now,
            or_(Match.round_name.is_(None), Match.provider_status == "FT"),
            ~observed,
            or_(MatchStatistics.home_corners.is_(None), MatchStatistics.home_corners < 0,
                MatchStatistics.away_corners.is_(None), MatchStatistics.away_corners < 0),
        ).order_by(published.desc(), Match.kickoff_utc.desc(), Match.id).all()

        recent_ids = [
            match_id for match_id, in db.query(Match.id).filter(
                Match.status == "finished", Match.kickoff_utc >= now - dt.timedelta(days=14),
            )
        ]
        corner_picks = {
            match_id for match_id, pick in latest_model_picks(db, recent_ids).items()
            if pick.market.startswith("corners_")
        }
        rows.sort(key=lambda row: row.id not in corner_picks)

        unattempted = []
        retries = []
        for match_id, provider_id, _ in rows:
            prefix = "api-football-"
            fixture = provider_id.removeprefix(prefix)
            if fixture == provider_id or not fixture.isascii() or not fixture.isdigit():
                continue
            attempted = last_attempt.get(fixture)
            if attempted is None:
                unattempted.append(match_id)
            elif attempted <= now - dt.timedelta(days=7):
                retries.append(match_id)
        ids = (unattempted + retries)[:limit]

    if dry_run:
        typer.echo(f"[reconcile-corners] Sin consultar: {len(unattempted)} · Reintentos: {len(retries)} · Lote: {ids}")
        return

    with httpx.Client(timeout=30) as transport:
        client = ApiFootballClient(transport)
        if not client.is_available():
            return
        verified = 0
        for match_id in ids:
            with session_scope() as db:
                total = reconcile(db, client, match_id, persist=True)
                verified += total is not None
        typer.echo(f"[reconcile-corners] Verificados {verified} de {len(ids)} partidos")


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


def _merge_team(db, duplicate: Team, canonical: Team) -> tuple[int, int]:
    """Funde `duplicate` en `canonical`: repunta sus TeamNameMapping y sus
    Match (home/away), fusionando cualquier Match que quede duplicado tras
    el repunte (mismo local/visitante/competicion/temporada, antes
    repartidos entre los dos ids de equipo) -- conservando el que tenga
    `status == 'finished'` cuando hay conflicto, que es siempre el dato
    mas completo/autoritativo. Devuelve (matches_repuntados, matches_fundidos)."""
    db.query(TeamNameMapping).filter_by(team_id=duplicate.id).update({"team_id": canonical.id})

    repointed = 0
    merged = 0
    for field in ("home_team_id", "away_team_id"):
        matches = db.query(Match).filter_by(**{field: duplicate.id}).all()
        for match in matches:
            setattr(match, field, canonical.id)
            db.flush()
            sibling = (
                db.query(Match)
                .filter(
                    Match.id != match.id,
                    Match.competition_id == match.competition_id,
                    Match.season_id == match.season_id,
                    Match.home_team_id == match.home_team_id,
                    Match.away_team_id == match.away_team_id,
                )
                .one_or_none()
            )
            repointed += 1
            if sibling is None:
                continue
            # Dos filas para el mismo partido real: nos quedamos con la
            # "finished" (dato autoritativo, ya jugado) y borramos la otra
            # -- igual patron de borrado seguro que `reset_scheduled` (las
            # predicciones no las borra el cascade de Match, hay que
            # borrarlas explicitamente antes).
            keep, drop = (match, sibling) if match.status == "finished" else (sibling, match)
            if keep.status != "finished" and drop.status == "finished":
                keep, drop = drop, keep
            # Bug real reportado por el usuario: si `drop` es un duplicado de
            # una temporada YA LIQUIDADA (ambas filas del mismo partido real
            # llegaron a tener sus propias predicciones generadas Y
            # liquidadas antes de que existiera el alias que las reconoce
            # como el mismo equipo -- no solo el caso de la jornada actual,
            # donde `drop` sigue 'scheduled' y nunca llega a liquidarse),
            # borrar `Prediction` sin antes borrar su `PredictionResult`
            # revienta con ForeignKeyViolation en Postgres (produccion;
            # SQLite, usado en tests, no fuerza la FK por defecto y deja
            # una fila huerfana en silencio en vez de romper). Al no estar
            # en una sesion independiente, ese fallo deshacia TODA la
            # fusion de este comando (de cualquier equipo, no solo el
            # problematico) al no llegar nunca al `db.commit()` final.
            drop_prediction_ids = [
                p.id for p in db.query(Prediction.id).filter(Prediction.match_id == drop.id)
            ]
            if drop_prediction_ids:
                db.query(PredictionResult).filter(
                    PredictionResult.prediction_id.in_(drop_prediction_ids)
                ).delete(synchronize_session=False)
            db.query(Prediction).filter(Prediction.match_id == drop.id).delete()
            db.delete(drop)
            db.flush()
            merged += 1

    db.delete(duplicate)
    return repointed, merged


@app.command()
def merge_duplicate_teams() -> None:
    """Limpieza puntual (seccion "jornada actual" rota solo en algunas
    ligas, bug real reportado por el usuario): antes de anhadir el alias
    que faltaba en normalization/teams.py, una fuente nueva que mandara un
    nombre de equipo con un prefijo distinto al ya conocido (p.ej.
    `api_football` mandando "SV Elversberg" cuando ya existia "Elversberg"
    via openfootball) creaba un Team FANTASMA con su propio id -- el
    resultado real de esos partidos se escribia contra el fantasma, nunca
    contra el original, que se quedaba 'scheduled' para siempre.

    Este comando NO depende de conocer de antemano que equipos se vieron
    afectados: recorre TODOS los Team ya guardados y, para cualquiera cuyo
    nombre canonico ya resuelva (via alias) al nombre de OTRO Team
    existente, funde ambos -- asi repara tambien casos que no se hayan
    detectado todavia. Idempotente: en una base de datos ya limpia no
    encuentra nada que fundir.
    """
    init_db()
    with session_scope() as db:
        duplicates_found = 0
        matches_repointed = 0
        matches_merged = 0
        for team in db.query(Team).all():
            resolved_name = canonical_name_for(team.canonical_name)
            if resolved_name == team.canonical_name:
                continue
            canonical = db.query(Team).filter_by(canonical_name=resolved_name).one_or_none()
            if canonical is None or canonical.id == team.id:
                continue
            typer.echo(
                f"[merge-duplicate-teams] Fundiendo '{team.canonical_name}' (id={team.id}) "
                f"-> '{canonical.canonical_name}' (id={canonical.id})"
            )
            repointed, merged = _merge_team(db, duplicate=team, canonical=canonical)
            duplicates_found += 1
            matches_repointed += repointed
            matches_merged += merged
        db.commit()
    typer.echo(
        f"[merge-duplicate-teams] {duplicates_found} equipo(s) fantasma fundido(s), "
        f"{matches_repointed} partido(s) repuntados, {matches_merged} duplicado(s) resuelto(s)."
    )


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

    Paso 5 (`evaluate`): en cuanto llegan resultados reales nuevos (pasos
    1 y 3), compara las predicciones que YA estaban guardadas de esos
    partidos contra el resultado real -- asi queda un historial de "como
    de bien predijo el modelo" ANTES de que el reentrenamiento del paso 6
    cambie nada. Nunca falla el `refresh` completo si no hay nada nuevo
    que evaluar (p.ej. la primera vez que se ejecuta).

    Bug real corregido (reportado por el usuario: partidos con marcador ya
    visible seguian "sin resolver" en el Historico): `evaluate` iba en el
    paso 2, ANTES de `update_fixtures` (paso 3) -- es `update_fixtures`
    quien trae el resultado/estado real de los partidos de la temporada en
    curso desde API-Football, asi que un partido recien finalizado
    quedaba con `Match.status` todavia "scheduled" cuando `evaluate` leia
    la BD en ESTE `refresh`, y no se liquidaba hasta el SIGUIENTE. Mover
    `evaluate` a justo antes de `train` (ya con fixtures/odds frescos)
    liquida los partidos recien finalizados en el mismo `refresh` en el
    que se descubren.
    """
    typer.echo("=== [1/7] Resultados historicos ===")
    if skip_historical:
        typer.echo("(saltado por --skip-historical)")
    else:
        update(competition=competition, season=None, source="history_dataset")

    typer.echo("=== [2/7] Fixtures reales (temporada en curso) ===")
    update_fixtures(competition=competition)

    typer.echo("=== [3/7] Cuotas de mercado reales: goles/1X2 (The Odds API) ===")
    update_odds(competition=competition)

    typer.echo("=== [4/7] Cuotas de mercado reales: corners (API-Football) ===")
    update_secondary_odds(competition=competition)

    typer.echo("=== [5/7] Evaluar predicciones de partidos ya finalizados ===")
    evaluate(competition=competition)

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
        voided_count = void_cancelled_predictions(db, competition_code=competition)
        report_data = evaluate_settled_predictions(db, competition_code=competition)
        # Alertas proactivas de Jarvis (Fase 6, agent/alerts.py) -- misma
        # cadencia que el settlement (cada hora via scheduler), no-op si
        # AGENT_ALERTS_ENABLED=false. Aqui y no en un paso aparte del
        # scheduler para no tener que tocar docker-compose.yml cada vez
        # que se anhada un nuevo tipo de alerta proactiva.
        new_alerts = generate_alerts_for_current_round(db)

    if settled_count:
        typer.echo(
            f"[evaluate] {settled_count} predicciones liquidadas "
            "(PredictionResult creado, nunca sobreescrito)."
        )
    if voided_count:
        typer.echo(
            f"[evaluate] {voided_count} predicciones anuladas (partido cancelado, stake devuelto)."
        )
    if new_alerts:
        typer.echo(f"[evaluate] {new_alerts} alertas nuevas de Jarvis generadas.")

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


@app.command("ensemble-calibration-report")
def ensemble_calibration_report_cmd(
    competition: str, market: str = "home_win", output: str | None = None
) -> None:
    """Igual que `calibration-report`, pero sobre el ENSEMBLE REAL (Dixon-Coles
    + clasificador ML + pesos de mezcla aprendidos), no sobre Dixon-Coles
    aislado -- ver backtesting/ensemble_engine.py.

    `calibration-report` solo puede responder "¿calibrar Dixon-Coles solo
    mejora algo?" (ya demostrado que NO, en LaLiga, para home_win/draw/
    double_chance_1x). Este comando responde la pregunta que de verdad
    importa: "¿calibrar lo que REALMENTE se sirve en produccion mejora
    algo, y con que metodo (isotonic o Platt)?" -- evaluado walk-forward,
    con el ganador ML y los pesos de ensemble reaprendidos en cada fold
    sobre un holdout de seleccion separado del propio fold de test (nunca
    leakage). Sigue siendo solo DIAGNOSTICO: no cambia nada del pipeline
    de produccion por si solo.
    """
    init_db()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition).one_or_none()
        if comp is None:
            typer.echo(f"Competicion no encontrada: {competition}")
            raise typer.Exit(1)
        matches = load_matches_dataframe(db, comp.id)
        table = build_match_feature_table(matches)

        def _progress(fold_idx: int, total_folds: int, test_season: str) -> None:
            # Cada fold reentrena Dixon-Coles + 3 candidatos ML sobre datos
            # reales (mucho mas lento que el dataset sintetico de los
            # tests) -- sin esto el comando parece colgado durante varios
            # minutos (bug real reportado por el usuario: Ctrl+C antes de
            # que terminara).
            typer.echo(
                f"[ensemble-calibration-report] fold {fold_idx}/{total_folds} "
                f"(test={test_season})..."
            )

        report_data = ensemble_calibration_report(table, market, on_fold_start=_progress)

    if "error" in report_data:
        typer.echo(f"[ensemble-calibration-report] {report_data['error']}")
        return

    def _fmt(block: dict) -> str:
        if "error" in block:
            return block["error"]
        return (
            f"brier={block['brier_score']:.4f} log_loss={block['log_loss']:.4f} "
            f"ECE={block['expected_calibration_error']:.4f} (n={block['n_test']})"
        )

    typer.echo(
        f"[ensemble-calibration-report] {competition}/{market} "
        f"(n_folds={report_data['n_folds']}, n_test={report_data['n_test']}):"
    )
    typer.echo(f"  raw      : {_fmt(report_data['raw'])}")
    typer.echo(f"  isotonic : {_fmt(report_data['isotonic'])}")
    typer.echo(f"  platt    : {_fmt(report_data['platt'])}")

    output_path = (
        Path(output)
        if output
        else REPO_ROOT / "data" / "processed" / f"ensemble_calibration_{competition}_{market}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report_data, indent=2, default=str))
    typer.echo(f"[ensemble-calibration-report] informe completo escrito en {output_path}")


@app.command()
def detect_drift(
    competition: str, current_window: int = 50, output: str | None = None
) -> None:
    """Deteccion de cambios de distribucion (PSI, ver monitoring/drift.py)
    en las features de entrada: compara los ultimos `current_window`
    partidos JUGADOS contra el resto del historico. PURAMENTE INFORMATIVO
    -- nunca bloquea `refresh`/`train`, solo da visibilidad de que algo se
    esta moviendo en los datos para decidir con datos si hace falta
    reentrenar antes de lo habitual.
    """
    init_db()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition).one_or_none()
        if comp is None:
            typer.echo(f"Competicion no encontrada: {competition}")
            raise typer.Exit(1)
        matches = load_matches_dataframe(db, comp.id)
        table = build_match_feature_table(matches)

    report_data = feature_drift_report(table, feature_columns(table), current_window=current_window)
    if report_data["status"] != "ok":
        typer.echo(
            f"[detect-drift] {competition}: datos insuficientes "
            f"({report_data['n_matches']} partidos, se necesitan {report_data['required']})"
        )
        return

    significant = report_data["features_with_significant_drift"]
    moderate = report_data["features_with_moderate_drift"]
    typer.echo(
        f"[detect-drift] {competition}: {len(report_data['features'])} features evaluadas "
        f"(referencia={report_data['n_reference']}, actual={report_data['n_current']}) -- "
        f"{len(significant)} con drift significativo, {len(moderate)} moderado"
    )
    for name in significant:
        typer.echo(f"  [SIGNIFICATIVO] {name}: PSI={report_data['features'][name]['psi']:.3f}")
    for name in moderate:
        typer.echo(f"  [moderado] {name}: PSI={report_data['features'][name]['psi']:.3f}")

    output_path = (
        Path(output) if output else REPO_ROOT / "data" / "processed" / f"drift_{competition}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report_data, indent=2, default=str))
    typer.echo(f"[detect-drift] informe completo escrito en {output_path}")


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


@app.command("compare-ranking-strategies")
def compare_ranking_strategies_cmd(
    competition: str = typer.Option(None),
    market: str = typer.Option(None),
    output: str | None = None,
) -> None:
    """Revision "motor profesional 2.0", Fase 7: compara `signal_score` (v1,
    el de produccion) contra `signal_score_v2` (probabilidad calibrada +
    EV ajustado al riesgo + incertidumbre) sobre predicciones YA
    LIQUIDADAS, restringido al TOP-N de cada estrategia -- la evidencia
    real que decide si v2 deberia activarse en produccion, nunca una
    suposicion. Ver backtesting/ranking_comparison.py para la metodologia
    completa (nunca hay riesgo de leakage: ambos scores se calculan sobre
    campos ya congelados en el momento de generar la prediccion, antes de
    conocerse el resultado real).

    Sin --competition, compara sobre TODAS las predicciones liquidadas del
    sistema (de cualquier competicion/mercado).
    """
    init_db()
    with session_scope() as db:
        settled = load_settled_predictions(db, competition_code=competition, market=market)
        report_data = compare_ranking_strategies(settled)

    scope = f"{competition or 'todas las competiciones'}/{market or 'todos los mercados'}"
    if report_data.n_settled_with_market == 0:
        typer.echo(
            f"[compare-ranking-strategies] {scope}: sin predicciones liquidadas con cuota de "
            "mercado todavia -- nada que comparar."
        )
        return

    typer.echo(
        f"[compare-ranking-strategies] {scope} "
        f"(n_liquidadas_con_mercado={report_data.n_settled_with_market}):"
    )
    for s in report_data.slices:
        roi_str = f"{s.roi:+.3f}" if s.roi is not None else "—"
        win_rate_str = f"{s.win_rate:.1%}" if s.win_rate is not None else "—"
        typer.echo(
            f"  {s.strategy:3s} top-{s.top_n:>4s}: n={s.n_bets:4d}  roi={roi_str}  "
            f"win_rate={win_rate_str}  pnl={s.total_pnl:+.2f}  "
            f"max_drawdown={s.max_drawdown:.2f}  brier={s.brier_score:.4f}"
            if s.brier_score is not None
            else f"  {s.strategy:3s} top-{s.top_n:>4s}: n=0"
        )

    output_path = (
        Path(output)
        if output
        else REPO_ROOT / "data" / "processed" / f"ranking_comparison_{competition or 'all'}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "n_settled_with_market": report_data.n_settled_with_market,
                "slices": [vars(s) for s in report_data.slices],
            },
            indent=2,
            default=str,
        )
    )
    typer.echo(f"[compare-ranking-strategies] informe completo escrito en {output_path}")


@app.command("compare-ranking-strategies-walk-forward")
def compare_ranking_strategies_walk_forward_cmd(
    competition: str = typer.Option(None),
    market: str = typer.Option(None),
    min_train_n: int = typer.Option(30),
    output: str | None = None,
) -> None:
    """Seccion 15 de docs/architecture_audit.md -- pedido explicito del
    usuario tras el rollback de emergencia del techo de cuota ("quiero
    que el modelo sea realista tambien con las predicciones, no solo
    [que mida] mucha diferencia de edge"): a diferencia de
    `compare-ranking-strategies` (valido para v1/v2 porque esas formulas
    no tienen ningun parametro ajustado), las estrategias con una curva
    (`v2_edge_cap` = produccion actual; `v2_edge_cap_longshot` =
    candidata que recalibra la probabilidad en la cola de cuota alta,
    NUNCA activada todavia) necesitan walk-forward real: cada
    liquidacion se puntua SOLO con una curva ajustada con liquidaciones
    ANTERIORES cronologicamente, nunca con el conjunto completo (eso
    seria leakage de validacion). Ver
    backtesting/ranking_comparison.py::compare_ranking_strategies_walk_forward
    para la metodologia completa.

    Esta es la evidencia real que decide si `v2_edge_cap_longshot`
    deberia activarse en produccion -- nunca una suposicion. Si no
    mejora ROI/win-rate/Brier out-of-sample sobre `v2_edge_cap` (el
    estado actual) con muestra suficiente y drawdown estable, NO se
    activa.

    Incluye tambien `v3_probability_first` ("Mejores oportunidades",
    cambio de filosofia del ranking, 2026-11): banda de cuotas 1.35-1.90
    + orden de prioridad por probabilidad calibrada (ya ACTIVADA en
    produccion, ver seccion correspondiente de architecture_audit.md --
    son reglas deterministas, no una correccion estadistica nueva sin
    validar). Su candidate pool es mas pequenho a proposito (pasa por su
    propio filtro: EV estimado y conservador positivos, fiabilidad
    historica real exigida) -- corre esto contra tu BD real para
    confirmar con datos de verdad que mejora ROI/win-rate/Brier frente a
    v2.
    """
    init_db()
    with session_scope() as db:
        settled = load_settled_predictions_with_kickoff(
            db, competition_code=competition, market=market
        )
        report_data = compare_ranking_strategies_walk_forward(
            settled, min_train_n=min_train_n, db=db
        )

    scope = f"{competition or 'todas las competiciones'}/{market or 'todos los mercados'}"
    if not report_data.slices:
        typer.echo(
            f"[compare-ranking-strategies-walk-forward] {scope}: menos de "
            f"{min_train_n} liquidaciones con cuota de mercado -- nada que evaluar "
            "out-of-sample todavia."
        )
        return

    typer.echo(
        f"[compare-ranking-strategies-walk-forward] {scope} "
        f"(n_total_con_mercado={report_data.n_settled_with_market}, "
        f"min_train_n={min_train_n}):"
    )
    for s in report_data.slices:
        roi_str = f"{s.roi:+.3f}" if s.roi is not None else "—"
        win_rate_str = f"{s.win_rate:.1%}" if s.win_rate is not None else "—"
        typer.echo(
            f"  {s.strategy:22s} top-{s.top_n:>4s}: n={s.n_bets:4d}  roi={roi_str}  "
            f"win_rate={win_rate_str}  pnl={s.total_pnl:+.2f}  "
            f"max_drawdown={s.max_drawdown:.2f}  brier={s.brier_score:.4f}"
            if s.brier_score is not None
            else f"  {s.strategy:22s} top-{s.top_n:>4s}: n=0"
        )

    output_path = (
        Path(output)
        if output
        else REPO_ROOT
        / "data"
        / "processed"
        / f"ranking_comparison_walk_forward_{competition or 'all'}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "n_settled_with_market": report_data.n_settled_with_market,
                "slices": [vars(s) for s in report_data.slices],
            },
            indent=2,
            default=str,
        )
    )
    typer.echo(f"[compare-ranking-strategies-walk-forward] informe escrito en {output_path}")


if __name__ == "__main__":
    app()
