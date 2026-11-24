"""Adapter de cuotas REALES (API-Football / api-sports.io).

Cubre TODOS los mercados con fuente real que usa este proyecto en un
UNICO fetch por partido (una sola llamada a `/odds` por `fixture_id`,
nunca dos): 1X2 (match_result), doble oportunidad (double_chance_1x/x2/12
-- bug real corregido, ver DOUBLE_CHANCE_VALUE_MAP: se asumia sin
confirmar que ningun proveedor la traia), goles Over/Under
(over_under_goals), BTTS, y corners (corners_total) -- este ultimo porque
se comprobo que The Odds API no lo ofrece para futbol en NINGUN plan (su
catalogo se limita a resultado y totales de goles), no es una cuestion de
configuracion.

Tarjetas (cards) NO se cubre (pedido real del usuario, tras confirmar
contra una respuesta real de /odds): de ~120 mercados distintos
devueltos para un partido, ninguno menciona "card" -- ninguna casa de
las que agrega API-Football ofrece ese mercado para estas 5 ligas, asi
que intentar reconocerlo solo produciria predicciones "sin mercado"
permanentes. Si algun dia una casa empieza a ofrecerlo, re-confirmar con
`scripts/debug_match_odds.py` antes de re-añadir el hint, no asumir.

Fuente: https://www.api-football.com (o via RapidAPI, mismos datos,
distinta autenticacion -- ver `Settings.api_football_use_rapidapi`). Pide
cuotas para una VENTANA de fechas (nunca la temporada entera de golpe):
1 request de fixtures + 1 request de odds POR PARTIDO de esa ventana,
asi que el coste escala con el numero de partidos consultados, no con
los dias de la ventana en si.

IMPORTANTE -- sin verificar end-to-end: el entorno donde se desarrollo
este adapter tiene el egress de red restringido a un allowlist que NO
incluye api-football.com/api-sports.io, asi que este codigo se escribio
siguiendo la documentacion publica de la API pero no pudo probarse contra
la API real. Por eso el matching de mercados busca por SUBCADENA en el
nombre del mercado (insensible a mayusculas) en vez de un nombre exacto,
y el valor de cada seleccion se interpreta con reglas genericas (numero
tras "over/under", "home"/"draw"/"away" o "1"/"x"/"2", "yes"/"no") en vez
de asumir un formato exacto: si el nombre/valor real difiere ligeramente
de lo esperado, sigue funcionando; si para una liga entera no se
reconoce NADA de un mercado esperado, se loguea con nivel WARNING la
lista completa de nombres de mercado vistos, para diagnosticar en un
vistazo en vez de fallar en silencio (misma leccion aprendida del bug del
sport_key de Bundesliga en The Odds API).
"""

from __future__ import annotations

import datetime as dt
import re
import time

import httpx

from backend.app.config.settings import get_settings
from backend.app.ingestion.api_football.client import LEAGUE_IDS, ApiFootballClient, ApiFootballRateLimitError
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.normalization.competitions import competition_team_name
from backend.app.utils.dates import season_label as season_label_from_date
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Se busca por SUBCADENA (insensible a mayusculas), no por nombre exacto
# -- ver docstring del modulo. El mas especifico (corner) va ANTES que
# "over/under" generico: el nombre real de ese mercado en API-Football
# tambien contiene la palabra "over/under" ("Corners Over Under"), asi
# que si se comprobara el hint generico primero se clasificaria mal.
# IDs de mercados prepartido confirmados en respuestas de /odds.
# Si falta el ID, solo se aceptan nombres completos conocidos.
MARKET_SPECS = {
    "1": ("match_result", "match_result", ("match winner", "1x2", "full time result", "fulltime result")),
    "5": ("over_under_goals", "over_under", ("goals over/under", "goals over under")),
    "8": ("btts", "yes_no", ("both teams score", "both teams to score", "btts")),
    "12": ("double_chance", "double_chance", ("double chance",)),
    "43": ("home_team_total_goals", "team_scores", ("home team score a goal",)),
    "44": ("away_team_total_goals", "team_scores", ("away team score a goal",)),
    "45": ("corners_total", "over_under", ("corners over under", "corners over/under", "total corners")),
}
MARKET_SPECS_BY_NAME = {
    name: (market, value_kind, names)
    for market, value_kind, names in MARKET_SPECS.values() for name in names
}

# "Double Chance" NO es un mercado de 2 selecciones complementarias como
# over/under o btts (cuyas probabilidades reales suman 1 y por eso se
# puede quitar el vig con el metodo proporcional estandar, ver
# market/vig.py) -- sus 3 "selecciones" (1X, X2, 12) se SOLAPAN (cada una
# cubre 2 de los 3 resultados de 1X2; sus probabilidades reales suman 2,
# no 1). Pasar las 3 juntas a `no_vig_probabilities` normalizaria mal
# (aproximadamente a la mitad de la probabilidad real de cada una). Por
# eso cada una se guarda como su PROPIO mercado independiente
# ("double_chance_1x"/"double_chance_x2"/"double_chance_12", selection fija
# "yes") en vez de 3 selecciones de un unico mercado "double_chance" --
# `compute_market_consensus` las trata entonces como cotizacion de una
# unica cara (misma logica, honesta, ya usada cuando una casa solo cotiza
# un lado de btts/over-under: probabilidad implicita CRUDA, con vig, nunca
# se inventa un "no vig" sin la selecion complementaria real).
DOUBLE_CHANCE_VALUE_MAP = {
    "home/draw": "double_chance_1x",
    "homedraw": "double_chance_1x",
    "1x": "double_chance_1x",
    "draw/away": "double_chance_x2",
    "drawaway": "double_chance_x2",
    "x2": "double_chance_x2",
    "home/away": "double_chance_12",
    "homeaway": "double_chance_12",
    "12": "double_chance_12",
}

# "Over 3.5" / "Under 9.5" / "Over3.5" -> (selection, line)
OVER_UNDER_VALUE_RE = re.compile(r"(over|under)\s*([\d.]+)", re.IGNORECASE)

MATCH_RESULT_VALUE_MAP = {
    "home": "home",
    "1": "home",
    "draw": "draw",
    "x": "draw",
    "away": "away",
    "2": "away",
}


class ApiFootballOddsProvider(ApiFootballClient):
    """No implementa `DataProvider` (esa interfaz es para ingesta de
    PARTIDOS -- ver fixtures_provider.py; esta clase solo trae cuotas
    para partidos que YA existen en la BD, igual que `OddsApiProvider`)."""

    name = "api_football"

    # Bug real investigado por el usuario (cuotas desactualizadas): la
    # documentacion oficial de API-Football dice que las cuotas
    # prepartido solo estan disponibles entre 1 y 14 dias antes del
    # encuentro -- con una ventana de 45 dias (el valor de antes), CADA
    # `update-odds` pedia `/odds?fixture=X` para partidos de entre 15 y
    # 45 dias vista que NUNCA van a tener cuota todavia, desperdiciando
    # peticiones (y cupo del limite por minuto) que hacian falta para los
    # partidos que si tienen cuota real que traer. 15 dias (no 14 exacto)
    # como margen de seguridad por husos horarios/redondeo de fecha.

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        """Interfaz esperada por `data_service.attach_odds_to_scheduled_matches`
        (mismo contrato que `OddsApiProvider.fetch_odds`): cuotas de TODOS
        los mercados con fuente real de los proximos partidos programados.
        Temporada/ventana de fecha calculadas aqui -- The Odds API no
        necesita esto (devuelve "lo que tenga ahora"), API-Football si."""
        today = dt.date.today()
        season_year = int(season_label_from_date(today).split("/")[0])
        return self.fetch_odds_for_window(
            competition_code,
            season_year,
            today,
            today + dt.timedelta(days=get_settings().api_football_odds_window_days),
        )

    def fetch_odds_for_window(
        self, competition_code: str, season_year: int, date_from: dt.date, date_to: dt.date
    ) -> list[FixtureOddsSnapshot]:
        """Cuotas (todos los mercados) de los partidos de una liga entre
        `date_from` y `date_to`. Un unico fetch de `/odds` por partido --
        nunca se pide dos veces el mismo `fixture_id` para separar
        mercados "primarios" de "secundarios" (ver `fetch_secondary_odds`,
        que reusa este mismo resultado en vez de volver a pedirlo)."""
        league_id = LEAGUE_IDS.get(competition_code)
        if league_id is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")

        fixtures = self._get(
            "/fixtures",
            {
                "league": league_id,
                "season": season_year,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            },
        )

        self.odds_fetch_failures: list[str] = []
        snapshots: list[FixtureOddsSnapshot] = []
        market_names_seen: set[str] = set()
        # Provider pages can repeat fixtures. Keep the kickoff ordering and
        # only spend one odds request per fixture in this refresh.
        unique_fixtures = {row["fixture"]["id"]: row for row in fixtures}
        ordered = sorted(unique_fixtures.values(), key=lambda row: dt.datetime.fromisoformat(row["fixture"]["date"]))
        scheduled = [
            row for row in ordered
            if row["fixture"].get("status", {}).get("short") in {None, "NS"}
        ]
        for i, fixture_payload in enumerate(scheduled):
            if i > 0:
                time.sleep(1)
            fixture_id = fixture_payload["fixture"]["id"]
            home_name = competition_team_name(fixture_payload["teams"]["home"]["name"], competition_code)
            away_name = competition_team_name(fixture_payload["teams"]["away"]["name"], competition_code)
            commence_time = dt.datetime.fromisoformat(fixture_payload["fixture"]["date"])

            try:
                odds_response = self._get("/odds", {"fixture": fixture_id})
            except (httpx.HTTPError, ApiFootballRateLimitError) as exc:
                self.odds_fetch_failures.append(f"{fixture_id}: {type(exc).__name__}")
                logger.warning("ingestion.api_football.odds_fixture_failed: fixture=%s error=%s", fixture_id, type(exc).__name__)
                limited = isinstance(exc, ApiFootballRateLimitError) or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
                )
                if limited:
                    # Backoff has already been exhausted by the HTTP client.
                    # Preserve successes and defer the rest until the next run.
                    self.odds_fetch_failures.append(f"deferred={len(scheduled) - i - 1}")
                    break
                continue
            odds_response = [
                entry for entry in odds_response
                if entry.get("fixture", {}).get("id", fixture_id) == fixture_id
            ]
            suspended_markets: set[str] = set()
            odds_records, seen = self._parse_odds_response(odds_response, suspended_markets=suspended_markets)
            market_names_seen |= seen
            if odds_records or suspended_markets:
                snapshots.append(FixtureOddsSnapshot(
                    home_name, away_name, commence_time, odds_records, f"api-football-{fixture_id}",
                    frozenset(suspended_markets),
                ))

        if fixtures and not any(s.odds for s in snapshots):
            logger.warning(
                "ingestion.api_football.no_market_found: "
                "competition=%s fixtures_checked=%d market_names_seen=%s",
                competition_code,
                len(fixtures),
                sorted(market_names_seen),
            )
        return snapshots

    def fetch_secondary_odds(
        self, competition_code: str, season_year: int, date_from: dt.date, date_to: dt.date
    ) -> list[FixtureOddsSnapshot]:
        """Compatibilidad con `update-secondary-odds`: SOLO corners,
        filtrando el mismo resultado de `fetch_odds_for_window` en vez de
        volver a pedir `/odds` -- nunca duplica la llamada por partido."""
        snapshots = self.fetch_odds_for_window(competition_code, season_year, date_from, date_to)
        secondary = [
            FixtureOddsSnapshot(
                s.home_team_raw,
                s.away_team_raw,
                s.commence_time,
                [r for r in s.odds if r.market == "corners_total"],
                s.provider_fixture_id,
                s.suspended_markets & {"corners_total"},
            )
            for s in snapshots
        ]
        return [s for s in secondary if s.odds or s.suspended_markets]

    @staticmethod
    def _parse_odds_response(
        odds_response: list[dict], *, suspended_markets: set[str] | None = None,
    ) -> tuple[list[RawOddsRecord], set[str]]:
        records: list[RawOddsRecord] = []
        names_seen: set[str] = set()
        fetched_at = dt.datetime.utcnow()
        for entry in odds_response:
            updated_at = None
            if isinstance(entry.get("update"), str):
                try:
                    parsed = dt.datetime.fromisoformat(entry["update"].replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        updated_at = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
                except ValueError:
                    pass
            for bookmaker in entry.get("bookmakers", []):
                bookmaker_key = bookmaker.get("name", "unknown")
                for bet in bookmaker.get("bets", []):
                    bet_name = bet.get("name", "")
                    names_seen.add(bet_name)
                    bet_id = bet.get("id")
                    spec = (MARKET_SPECS.get(str(bet_id)) if bet_id is not None
                            else MARKET_SPECS_BY_NAME.get(bet_name.strip().lower()))
                    if spec is None:
                        continue
                    market_key, value_kind, _ = spec
                    if bet.get("suspended") is True or bet.get("blocked") is True:
                        if suspended_markets is not None:
                            suspended_markets.add(market_key)
                        continue
                    for value in bet.get("values", []):
                        if suspended_markets is not None and (
                            value.get("suspended") is True or value.get("blocked") is True
                        ):
                            suspended_markets.add(market_key)
                        record = ApiFootballOddsProvider._parse_value(
                            bookmaker_key, market_key, value_kind, value
                        )
                        if record is not None:
                            record.provider_updated_at = updated_at
                            record.fetched_at = fetched_at
                            records.append(record)
        return records, names_seen

    @staticmethod
    def _parse_value(
        bookmaker: str, market_key: str, value_kind: str, value: dict
    ) -> RawOddsRecord | None:
        if value.get("suspended") is True or value.get("blocked") is True:
            return None
        raw_value = str(value.get("value", ""))
        try:
            price = float(value.get("odd"))
        except (TypeError, ValueError):
            return None

        if value_kind == "over_under":
            match = OVER_UNDER_VALUE_RE.search(raw_value)
            if not match:
                return None
            selection, line = match.group(1).lower(), float(match.group(2))
            return RawOddsRecord(bookmaker, market_key, line, selection, price)

        if value_kind == "match_result":
            selection = MATCH_RESULT_VALUE_MAP.get(raw_value.strip().lower())
            if selection is None:
                return None
            return RawOddsRecord(bookmaker, market_key, None, selection, price)

        if value_kind in ("yes_no", "team_scores"):
            normalized = raw_value.strip().lower()
            if normalized not in ("yes", "no"):
                return None
            if value_kind == "team_scores":
                return RawOddsRecord(bookmaker, market_key, 0.5, "over" if normalized == "yes" else "under", price)
            return RawOddsRecord(bookmaker, market_key, None, normalized, price)

        if value_kind == "double_chance":
            # `market_key` aqui es solo el hint generico ("double_chance")
            # que disparo el match -- el mercado REAL a guardar depende del
            # VALOR de esta seleccion concreta (1X/X2/12), no del nombre
            # del bet (ver DOUBLE_CHANCE_VALUE_MAP arriba). "yes" fija a
            # proposito: cada variante es su propio mercado de una sola
            # cara (nunca 3 selecciones del mismo "double_chance").
            normalized = raw_value.strip().lower().replace(" ", "")
            resolved_market = DOUBLE_CHANCE_VALUE_MAP.get(normalized)
            if resolved_market is None:
                return None
            return RawOddsRecord(bookmaker, resolved_market, None, "yes", price)

        return None
