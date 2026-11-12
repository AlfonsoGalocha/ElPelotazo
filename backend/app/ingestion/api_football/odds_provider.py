"""Adapter de cuotas REALES (API-Football / api-sports.io).

Cubre TODOS los mercados con fuente real que usa este proyecto en un
UNICO fetch por partido (una sola llamada a `/odds` por `fixture_id`,
nunca dos): 1X2 (match_result), goles Over/Under (over_under_goals),
BTTS, y tarjetas/corners (cards_total/corners_total) -- estos dos
ultimos porque se comprobo que The Odds API no los ofrece para futbol en
NINGUN plan (su catalogo se limita a resultado y totales de goles), no es
una cuestion de configuracion.

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

from backend.app.ingestion.api_football.client import LEAGUE_IDS, ApiFootballClient
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.utils.dates import season_label as season_label_from_date
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Se busca por SUBCADENA (insensible a mayusculas), no por nombre exacto
# -- ver docstring del modulo. Los mas especificos (card/corner) van
# ANTES que "over/under" generico: el nombre real de esos mercados en
# API-Football tambien contiene la palabra "over/under"
# ("Cards Over/Under"), asi que si se comprobara el hint generico primero
# se clasificarian mal.
MARKET_NAME_HINTS: list[tuple[str, tuple[str, ...], str, tuple[str, ...]]] = [
    # (market_key interno, hints, tipo_de_valor, excluye)
    ("cards_total", ("card",), "over_under", ("half", "team", "result")),
    ("corners_total", ("corner",), "over_under", ("half", "team", "result")),
    ("btts", ("both teams score", "both teams to score", "btts"), "yes_no", ()),
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
        ("half", "team", "result"),
    ),
]

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

    DEFAULT_WINDOW_DAYS = 45  # cuanto hacia el futuro se piden cuotas por defecto en fetch_odds()

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
        for fixture_payload in fixtures:
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
        """Compatibilidad con `update-secondary-odds`: SOLO tarjetas/corners,
        filtrando el mismo resultado de `fetch_odds_for_window` en vez de
        volver a pedir `/odds` -- nunca duplica la llamada por partido."""
        snapshots = self.fetch_odds_for_window(competition_code, season_year, date_from, date_to)
        secondary = [
            FixtureOddsSnapshot(
                s.home_team_raw,
                s.away_team_raw,
                s.commence_time,
                [r for r in s.odds if r.market in ("cards_total", "corners_total")],
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

        return None
