"""Calidad del MERCADO (evidencia detras de una cuota), separada a proposito
de `confidence` (prediction/confidence.py, que mide fiabilidad del MODELO).

Mezclar ambos conceptos en un unico numero escondia informacion util: una
senhal respaldada por 1 sola casa y otra por 20 pueden tener el MISMO
`confidence` si el resto de factores (calibracion, tamanho de muestra,
acuerdo entre modelos) coinciden, porque `market_coverage` es solo uno de
sus 5 componentes ponderados. `market_quality_tier` aisla especificamente
"cuanta evidencia de mercado real hay detras de esta cuota", visible por
si sola en la UI (ver seccion 11 de la revision de arquitectura).

Clasificacion:
    HIGH:   >= `market_quality_high_min_bookmakers` casas Y dispersion
            relativa <= `market_quality_max_dispersion_ratio` (muchas
            casas de acuerdo entre si, no solo muchas casas).
    MEDIUM: >= `market_quality_medium_min_bookmakers` casas (o HIGH en
            numero de casas pero con demasiada dispersion para confiar).
    LOW:    menos casas que el minimo de MEDIUM, o sin cuota en absoluto.

Dispersion relativa = (cuota_max - cuota_min) / cuota_mediana -- reutiliza
los campos que `market/consensus.py` ya calcula (min/max/median), sin
necesitar una columna nueva en la BD.
"""

from __future__ import annotations

from backend.app.config.settings import Settings, get_settings


def dispersion_ratio(odds_min: float | None, odds_max: float | None, odds_median: float | None) -> float | None:
    if odds_min is None or odds_max is None or odds_median is None or odds_median <= 0:
        return None
    return (odds_max - odds_min) / odds_median


def market_quality_tier(
    bookmakers_used: int | None,
    odds_min: float | None,
    odds_max: float | None,
    odds_median: float | None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    if not bookmakers_used or bookmakers_used <= 0:
        return "LOW"

    if bookmakers_used >= settings.market_quality_high_min_bookmakers:
        ratio = dispersion_ratio(odds_min, odds_max, odds_median)
        # Sin dispersion calculable (p.ej. una sola linea sin min/max
        # distintos), no se penaliza por falta de dato: se confia en el
        # numero de casas.
        if ratio is None or ratio <= settings.market_quality_max_dispersion_ratio:
            return "HIGH"
        return "MEDIUM"

    if bookmakers_used >= settings.market_quality_medium_min_bookmakers:
        return "MEDIUM"

    return "LOW"
