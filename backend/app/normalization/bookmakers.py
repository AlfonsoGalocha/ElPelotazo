"""Resolucion de nombre de casa de apuestas a un identificador canonico.

Por que existe (bug real reportado por el usuario, "las cuotas no tienen
sentido"): `api_football` y `the_odds_api` cubren en parte las MISMAS casas
reales, pero cada fuente usa su propio formato de nombre -- api_football
manda el nombre "bonito" de la casa ("1xBet", "Bet365", "Marathonbet",
"Pinnacle", "William Hill"), the_odds_api manda su `key` tecnico en
minusculas sin espacio ("onexbet", "bet365", "marathonbet", "pinnacle",
"williamhill"). Sin normalizar esto, la MISMA casa real se contaba DOS
VECES en el consenso de mercado (`bookmakers_used`/`bookmakers_count`
inflados) y, como las dos fuentes pueden traer el precio en momentos
distintos, podian arrastrar la mediana del consenso en direcciones
contradictorias -- confirmado contra una respuesta real donde "1xBet" y
"onexbet" cotizaban el mismo partido con precios opuestos.

IMPORTANTE: solo se fusionan pares CONFIRMADOS como la misma casa GLOBAL
(misma marca, mismo libro) contra datos reales de produccion -- nunca
variantes regionales de una marca ("unibet_fr"/"unibet_nl"/"unibet_se" son
libros REGIONALES distintos de Unibet, con cuotas propias por regulacion/
liquidez local; fusionarlos perderia senhal real en vez de solo quitar un
duplicado). Sin un alias confirmado, el nombre se devuelve tal cual vino
-- nunca se inventa una fusion sin evidencia.
"""

from __future__ import annotations

import re
import unicodedata

# (nombre normalizado, de CUALQUIER fuente) -> nombre canonico a mostrar y
# a usar como clave de conteo de casas unicas. Confirmado contra:
# - api_football (odds_provider.py): nombre "bonito" de la casa.
# - the_odds_api (ingestion/odds/provider.py): `key` tecnico de la casa.
KNOWN_ALIASES: dict[str, str] = {
    "1xbet": "1xBet",
    "onexbet": "1xBet",
    "bet365": "Bet365",
    "marathonbet": "Marathonbet",
    "pinnacle": "Pinnacle",
    "williamhill": "William Hill",
}

# Pedido real del usuario ("quiero fuera" a Betano, confirmado por el
# usuario como la unica casa que mete cuotas sin sentido): casas EXCLUIDAS
# del todo -- ni se guardan en MatchOdds/MatchOddsHistory, ni cuentan en
# el consenso de mercado (bookmakers_used/bookmakers_count), en NINGUN
# mercado ni fuente. Se comprueba sobre la clave normalizada (minusculas,
# sin acentos/espacios), igual que KNOWN_ALIASES, para que no dependa de
# que mayusculas/espacios exactos coincidan con los de la fuente.
EXCLUDED_BOOKMAKERS: frozenset[str] = frozenset({"betano"})


def is_excluded_bookmaker(raw_name: str) -> bool:
    """True si esta casa debe descartarse por completo (ver
    EXCLUDED_BOOKMAKERS) -- se comprueba ANTES de guardar nada, nunca
    despues, para que ni aparezca en bookmakers_used/bookmakers_count."""
    return _normalize_key(raw_name) in EXCLUDED_BOOKMAKERS


def _normalize_key(name: str) -> str:
    key = name.strip().lower()
    key = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode("ascii")
    # A diferencia de `normalization/teams.py` (que colapsa espacios a uno
    # solo): las claves de bookmaker de the_odds_api nunca llevan espacio
    # ("williamhill", no "william hill"), asi que aqui se quita el espacio
    # por completo para que "William Hill" (api_football) normalice igual
    # que "williamhill" (the_odds_api) sin necesitar un alias para cada uno.
    key = re.sub(r"\s+", "", key)
    return key


def canonical_bookmaker(raw_name: str) -> str:
    """Nombre canonico para mostrar y para contar casas UNICAS en el
    consenso de mercado. Sin alias conocido, se devuelve el nombre tal
    cual vino (nunca se inventa uno)."""
    return KNOWN_ALIASES.get(_normalize_key(raw_name), raw_name.strip())
