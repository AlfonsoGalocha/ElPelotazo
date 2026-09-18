"""Consenso de mercado multi-casa (sustituye a `market.odds.best_available_quote`).

Por que existia un problema: `best_available_quote` elegia una UNICA casa
(Pinnacle si estaba disponible; si no, la primera casa que apareciera en el
diccionario, en un orden practicamente arbitrario segun como respondiera la
API) y la trataba como "la probabilidad de mercado". Una sola cuota no es
un mercado: puede venir de una casa con un error de tipeo, una linea mal
identificada, o simplemente ruido de una casa poco liquida. Un caso real
detectado: una cuota aislada muy alta de una unica casa (p.ej. 4.40 cuando
el resto cotiza ~1.80) puede convertir una prediccion normal en un "edge
enorme" que en realidad es un artefacto de datos, no una oportunidad real.

Este modulo:
1. Nunca mezcla snapshot_type distintos (pre_match/closing/opening) en el
   mismo calculo: usa el grupo mas informativo disponible, con preferencia
   `closing > pre_match > opening` (closing es el precio final antes del
   partido, mas informativo que uno tomado dias antes).
2. Para cada bookmaker con AMBAS selecciones del mercado, calcula la
   probabilidad implicita SIN vig (metodo proporcional, ver market/vig.py).
   Si un bookmaker solo cotiza una selección, se usa su probabilidad
   implicita CRUDA (con vig) solo como ultimo recurso.
3. Descarta bookmakers outlier antes de agregar: cualquier probabilidad
   implicita que se aleje de la mediana del grupo mas de
   `OUTLIER_MAD_MULTIPLIER` desviaciones absolutas medianas (MAD), un
   metodo robusto estandar para detectar outliers sin asumir normalidad.
   Solo se aplica con >= `MIN_BOOKS_FOR_OUTLIER_FILTER` casas (con menos,
   no hay base estadistica para decidir cual es el outlier).
4. Agrega las probabilidades supervivientes con la MEDIANA (mas robusta
   que la media ante outliers residuales).
5. Devuelve evidencia de mercado completa (cuantas casas cotizaron, cuantas
   sobrevivieron al filtro, min/max/mediana/media de la cuota CRUDA de
   todas las casas) para que el resto del sistema pueda razonar sobre la
   FIABILIDAD del consenso, no solo su valor.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from backend.app.db.models.matches import MatchOdds
from backend.app.market.implied_probability import implied_probability
from backend.app.market.vig import no_vig_probabilities

# Preferencia de snapshot: closing (precio final pre-partido) es mas
# informativo que uno tomado dias antes (pre_match); "live" nunca deberia
# mezclarse con ninguno de los dos (ver seccion 14 de la revision: un
# partido programado nunca deberia tener cuotas "live" hasta que empiece).
SNAPSHOT_PREFERENCE = ["closing", "pre_match", "opening", "live"]

OUTLIER_MAD_MULTIPLIER = 3.5  # umbral estandar para deteccion robusta de outliers
MIN_BOOKS_FOR_OUTLIER_FILTER = 3


@dataclass
class MarketConsensus:
    market_probability: float | None
    market_probability_source: str | None
    # "consensus_no_vig" | "consensus_raw" | "single_book_no_vig" | "single_book_raw"
    market_odds: float | None  # cuota representativa (mediana cruda) para EV/fair-odds display
    bookmakers_count: int  # cuantas casas cotizaban esta seleccion (antes de filtrar outliers)
    bookmakers_used: int  # cuantas sobrevivieron al filtro de outliers y entraron en el consenso
    min_odds: float | None
    max_odds: float | None
    median_odds: float | None
    average_odds: float | None
    outliers_removed: list[str]  # nombres de bookmaker descartados por outlier, para logging/debug


def _median_absolute_deviation(values: list[float], median: float) -> float:
    deviations = [abs(v - median) for v in values]
    return statistics.median(deviations) if deviations else 0.0


def _drop_outliers(probabilities: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    if len(probabilities) < MIN_BOOKS_FOR_OUTLIER_FILTER:
        return probabilities, []

    values = list(probabilities.values())
    median = statistics.median(values)
    mad = _median_absolute_deviation(values, median)
    if mad == 0:
        # Todas las casas cotizan (casi) lo mismo: no hay outliers que detectar.
        return probabilities, []

    kept: dict[str, float] = {}
    removed: list[str] = []
    for bookmaker, prob in probabilities.items():
        # 0.6745 normaliza el MAD para que sea comparable a una desviacion
        # estandar bajo normalidad (constante estandar de la literatura de
        # deteccion de outliers robusta, p.ej. Iglewicz & Hoaglin).
        modified_z_score = 0.6745 * (prob - median) / mad
        if abs(modified_z_score) > OUTLIER_MAD_MULTIPLIER:
            removed.append(bookmaker)
        else:
            kept[bookmaker] = prob
    # Nunca se descartan TODAS las casas: si el filtro dejaria el consenso
    # vacio (degenerado), se prefiere no filtrar antes que quedarse sin dato.
    if not kept:
        return probabilities, []
    return kept, removed


def compute_market_consensus(
    odds_rows: list[MatchOdds], market: str, line: float | None, selection: str
) -> MarketConsensus:
    """Calcula el consenso de mercado para (market, line, selection) a
    partir de TODAS las cuotas disponibles de TODOS los bookmakers, en vez
    de confiar en una unica casa."""
    candidates = [o for o in odds_rows if o.market == market and o.line == line]
    if not candidates:
        return MarketConsensus(None, None, None, 0, 0, None, None, None, None, [])

    snapshot_types_present = {o.snapshot_type for o in candidates}
    chosen_snapshot = next((s for s in SNAPSHOT_PREFERENCE if s in snapshot_types_present), None)
    if chosen_snapshot is not None:
        candidates = [o for o in candidates if o.snapshot_type == chosen_snapshot]

    by_bookmaker: dict[str, dict[str, float]] = {}
    for row in candidates:
        by_bookmaker.setdefault(row.bookmaker, {})[row.selection] = row.price

    selection_odds = {
        bookmaker: sels[selection] for bookmaker, sels in by_bookmaker.items() if selection in sels
    }
    if not selection_odds:
        return MarketConsensus(None, None, None, 0, 0, None, None, None, None, [])

    raw_prices = list(selection_odds.values())
    bookmakers_count = len(raw_prices)
    min_odds = min(raw_prices)
    max_odds = max(raw_prices)
    median_odds = statistics.median(raw_prices)
    average_odds = statistics.fmean(raw_prices)

    # Probabilidad por bookmaker: sin vig cuando cotiza ambas selecciones,
    # cruda (documentada como tal) si solo cotiza esta.
    dual_sided: dict[str, float] = {}
    single_sided: dict[str, float] = {}
    for bookmaker, sels in by_bookmaker.items():
        if selection not in sels:
            continue
        if len(sels) >= 2:
            dual_sided[bookmaker] = no_vig_probabilities(sels)[selection]
        else:
            single_sided[bookmaker] = implied_probability(sels[selection])

    if dual_sided:
        kept, removed = _drop_outliers(dual_sided)
        market_probability = statistics.median(kept.values())
        source = "consensus_no_vig" if len(kept) > 1 else "single_book_no_vig"
    elif single_sided:
        kept, removed = _drop_outliers(single_sided)
        market_probability = statistics.median(kept.values())
        source = "consensus_raw" if len(kept) > 1 else "single_book_raw"
    else:
        kept, removed, market_probability, source = {}, [], None, None

    return MarketConsensus(
        market_probability=market_probability,
        market_probability_source=source,
        market_odds=median_odds,
        bookmakers_count=bookmakers_count,
        bookmakers_used=len(kept),
        min_odds=min_odds,
        max_odds=max_odds,
        median_odds=median_odds,
        average_odds=average_odds,
        outliers_removed=removed,
    )
