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

from backend.app.ingestion.api_football.client import LEAGUE_IDS, ApiFootballClient
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.utils.dates import season_label as season_label_from_date
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Se busca por SUBCADENA (insensible a mayusculas), no por nombre exacto
# -- ver docstring del modulo. El mas especifico (corner) va ANTES que
# "over/under" generico: el nombre real de ese mercado en API-Football
# tambien contiene la palabra "over/under" ("Corners Over Under"), asi
# que si se comprobara el hint generico primero se clasificaria mal.
CORNERS_EXCLUDE = (
    "half",
    "halves",
    "team",
    "result",
    # Bug real reportado por el usuario (cuota de "mas de 8.5 corners" sin
    # sentido -- no monotona respecto a "mas de 9.5", imposible en la
    # realidad -- confirmada contra la respuesta real de la API):
    # API-Football tambien manda "Home Corners Over/Under" y "Away Corners
    # Over/Under" (corners de UN SOLO equipo, no del partido), que NO
    # contienen la palabra "team" (a diferencia de los mercados de goles
    # equivalentes, "Home/Away TEAM Total Goals", donde el exclude de
    # arriba ya bastaba) -- se colaban mezclados con el valor real bajo la
    # misma linea (8.5/9.5/10.5...), corrompiendo el consenso. Tambien
    # confirmados en la misma respuesta real: "Multicorners" (linea 20.5,
    # un mercado distinto con ese nombre), "Corners. total between 0 and
    # 10m" (solo los primeros 10 minutos) y "Corners. Total (Range)" (apuesta
    # por rangos tipo "6 - 8": sus dos valores extremos, "Under 6"/"Over 14",
    # SI encajan con la regex over/under generica y se colaban tambien).
    "home",
    "away",
    "multicorners",
    "between",
    "range",
)
MARKET_NAME_HINTS: list[tuple[str, tuple[str, ...], str, tuple[str, ...]]] = [
    # (market_key interno, hints, tipo_de_valor, excluye)
    ("corners_total", ("corner",), "over_under", CORNERS_EXCLUDE),
    # Bug real reportado por el usuario (cuota de "ambos marcan" sin
    # sentido, confirmado contra la respuesta real): API-Football devuelve
    # "Both Teams Score" (partido completo, el unico correcto), PERO
    # TAMBIEN "Both Teams Score - First Half", "Both Teams To Score -
    # Second Half" y "Both Teams To Score in Both Halves" (esta variante
    # ni siquiera usa "half" en singular, de ahi el "halves" en el
    # excluye) -- las cuatro contienen "both teams score"/"both teams to
    # score" como subcadena, mismo bug que en goles (ver mas abajo). NO se
    # excluye "team" aqui (a diferencia de los demas mercados): el propio
    # hint correcto "both teams score"/"both teams to score" ya contiene
    # "team", excluirlo autoexcluiria el caso bueno.
    (
        "btts",
        ("both teams score", "both teams to score", "btts"),
        "yes_no",
        ("half", "halves", "result"),
    ),
    (
        "match_result",
        ("match winner", "1x2", "full time result", "fulltime result"),
        "match_result",
        (),
    ),
    # Bug real reportado por el usuario (cuota de "Más de 1.5 goles" no
    # coincidia con la real del todo): API-Football devuelve "Goals
    # Over/Under" (partido completo, el unico correcto aqui), PERO TAMBIEN
    # "Goals Over/Under First Half", "Goals Over/Under - Second Half" y
    # "Home/Away Team Total Goals(1st/2nd Half)" -- todos contienen
    # "over/under"/"total goals" como subcadena, asi que antes del
    # `excluye` de abajo se clasificaban TAMBIEN como `over_under_goals`
    # del partido completo. Como varios bets del MISMO bookmaker acababan
    # compitiendo por la misma clave (bookmaker, linea, seleccion), el que
    # se procesara ULTIMO ganaba en silencio -- en produccion, el valor
    # guardado para "Over 1.5 goles" de Bet365 era en realidad el de la
    # SEGUNDA PARTE (1.57), no el del partido completo (1.10 real).
    # "Result/Total Goals" (p.ej. "Home/Over 3.5", un mercado compuesto
    # resultado+goles) tambien colaba via "total goals"; se excluye por
    # contener "result".
    (
        "over_under_goals",
        ("over/under", "goals over", "total goals"),
        "over_under",
        # "card": tarjetas se decidio NO soportar (ver docstring del
        # modulo) -- pero si alguna casa algun dia nombra ese mercado
        # "Cards Over/Under" (nunca confirmado, solo por si acaso), SIN
        # este exclude caeria aqui por contener "over/under" y se
        # guardaria como si fuera una linea de GOLES, mezclando dos
        # estadisticas sin relacion. Mejor que un bet de tarjetas se
        # ignore del todo (comportamiento ya correcto hoy, porque no hay
        # ningun hint para "card") a que se clasifique mal.
        ("half", "halves", "team", "result", "card"),
    ),
    # Bug real reportado por el usuario (comparo contra una app de cuotas
    # real: Sevilla-Betis "Doble Oportunidad 1X" a 1.57-1.61 segun la casa,
    # la app mostraba 1.70): el comentario anterior de este modulo (y de
    # prediction_service.py) decia "ningun proveedor actual trae doble
    # oportunidad como mercado propio" -- FALSO, nunca confirmado contra
    # una respuesta real, solo asumido. API-Football SI devuelve "Double
    # Chance" como mercado propio (agregando las mismas casas reales:
    # bet365, Sportium, 1xBet, Betway, bwin...), con margen real de cada
    # casa -- mientras tanto, la app mostraba un precio SIN margen (1 /
    # P(1)+P(X) derivado del consenso de 1X2), que por construccion
    # matematica siempre sale MAS ALTO que cualquier cuota real con margen.
    # Ahora se ingesta el mercado real; `_add_derived_double_chance_quotes`
    # (prediction_service.py) sigue derivandolo SOLO si esta fuente no trae
    # nada para un partido concreto (nunca lo sobreescribe si ya hay dato
    # real, logica que ya existia sin usarse).
    ("double_chance", ("double chance",), "double_chance", ()),
]

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
    DEFAULT_WINDOW_DAYS = 15

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
            today + dt.timedelta(days=self.DEFAULT_WINDOW_DAYS),
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

        snapshots: list[FixtureOddsSnapshot] = []
        market_names_seen: set[str] = set()
        # Bug real reportado por el usuario (cuota real movida a 1.35,
        # la app seguia mostrando 1.70): este bucle hace UNA peticion HTTP
        # por partido de la ventana (hasta 45 dias, puede ser 10-15
        # partidos por liga) sin ninguna pausa -- si el limite de
        # peticiones por minuto de API-Football se agota a mitad de la
        # lista, `_get` agota sus 3 reintentos (ver ApiFootballRateLimitError
        # en client.py) y la excepcion se propaga, cancelando TODOS los
        # partidos restantes de esa liga para este ciclo, no solo el que
        # la disparo -- asi se queda una cuota vieja varios ciclos. Mismo
        # remedio ya usado en `cli.py::update_international` para el mismo
        # problema real (ahi entre competiciones, aqui entre partidos).
        for i, fixture_payload in enumerate(fixtures):
            if i > 0:
                time.sleep(1)
            fixture_id = fixture_payload["fixture"]["id"]
            home_name = fixture_payload["teams"]["home"]["name"]
            away_name = fixture_payload["teams"]["away"]["name"]
            commence_time = dt.datetime.fromisoformat(fixture_payload["fixture"]["date"])

            odds_response = self._get("/odds", {"fixture": fixture_id})
            odds_records, seen = self._parse_odds_response(odds_response)
            market_names_seen |= seen
            if odds_records:
                snapshots.append(
                    FixtureOddsSnapshot(home_name, away_name, commence_time, odds_records)
                )

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
            )
            for s in snapshots
        ]
        return [s for s in secondary if s.odds]

    @staticmethod
    def _parse_odds_response(odds_response: list[dict]) -> tuple[list[RawOddsRecord], set[str]]:
        records: list[RawOddsRecord] = []
        names_seen: set[str] = set()
        for entry in odds_response:
            for bookmaker in entry.get("bookmakers", []):
                bookmaker_key = bookmaker.get("name", "unknown")
                for bet in bookmaker.get("bets", []):
                    bet_name = bet.get("name", "")
                    names_seen.add(bet_name)
                    bet_name_lower = bet_name.lower()
                    spec = next(
                        (
                            (market_key, value_kind)
                            for market_key, hints, value_kind, excludes in MARKET_NAME_HINTS
                            if any(hint in bet_name_lower for hint in hints)
                            and not any(exclude in bet_name_lower for exclude in excludes)
                        ),
                        None,
                    )
                    if spec is None:
                        continue
                    market_key, value_kind = spec
                    for value in bet.get("values", []):
                        record = ApiFootballOddsProvider._parse_value(
                            bookmaker_key, market_key, value_kind, value
                        )
                        if record is not None:
                            records.append(record)
        return records, names_seen

    @staticmethod
    def _parse_value(
        bookmaker: str, market_key: str, value_kind: str, value: dict
    ) -> RawOddsRecord | None:
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

        if value_kind == "yes_no":
            normalized = raw_value.strip().lower()
            if normalized not in ("yes", "no"):
                return None
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
