"""Definicion unica de los mercados de goles del MVP (Fase 1).

Un solo lugar define, para cada mercado, tanto (a) como etiquetar el
resultado real de un partido finalizado (para entrenar/evaluar) como (b) como
leer la probabilidad correspondiente de una matriz de marcador conjunta
(para cualquier modelo). Evita que ambos calculos diverjan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketSpec:
    display_name: str
    kind: str  # "over" | "under" | "btts" | "match_result" | "double_chance" | "team_over"
    line: float | None = None
    team: str | None = None  # solo para "team_over": "home" | "away"


MARKET_DEFINITIONS: dict[str, MarketSpec] = {
    "over_1_5": MarketSpec("Over 1.5 Goals", "over", 1.5),
    "over_2_5": MarketSpec("Over 2.5 Goals", "over", 2.5),
    "under_2_5": MarketSpec("Under 2.5 Goals", "under", 2.5),
    "over_3_5": MarketSpec("Over 3.5 Goals", "over", 3.5),
    "btts": MarketSpec("Both Teams To Score", "btts", None),
    # 1X2: tres mercados BINARIOS independientes ("¿gana el local?", "¿empata?",
    # "¿gana el visitante?"), no uno multiclase -- encaja con la arquitectura
    # existente (un clasificador binario por mercado en ml_classifier.py, un
    # score_matrix -> probabilidad por mercado en probability.py) sin tocar
    # nada de esa infraestructura. `line` no aplica (None, igual que btts).
    "home_win": MarketSpec("Victoria Local (1)", "match_result", None),
    "draw": MarketSpec("Empate (X)", "match_result", None),
    "away_win": MarketSpec("Victoria Visitante (2)", "match_result", None),
    # Doble oportunidad: union de dos de los tres resultados de 1X2 (ver
    # DOUBLE_CHANCE_COMPONENTS). El modelo la calcula igual que cualquier
    # otro mercado (mismo score_matrix); la cuota de MERCADO no se ingesta
    # aparte (ningun proveedor actual la trae) sino que se DERIVA sumando
    # las probabilidades sin vig ya calculadas para sus dos componentes de
    # h2h -- ver services/prediction_service.py::_add_derived_double_chance_quotes.
    # Por eso siempre hay cuota de mercado si la hay para h2h (garantizado
    # a diferencia de btts/alternate_totals, que dependen de que una casa
    # concreta ofrezca ese mercado exacto).
    "double_chance_1x": MarketSpec("Doble Oportunidad 1X (Local o Empate)", "double_chance", None),
    "double_chance_x2": MarketSpec("Doble Oportunidad X2 (Empate o Visitante)", "double_chance", None),
    "double_chance_12": MarketSpec("Doble Oportunidad 12 (Local o Visitante)", "double_chance", None),
    # Goles de un equipo: NINGUNA fuente actual trae esta cuota (The Odds
    # API no la ofrece en el plan usado, ver docs/data_sources.md), asi que
    # queda siempre como "prediccion del modelo -- sin mercado"
    # (`/predictions/model-only`) hasta que se integre una fuente que si la
    # traiga -- nunca se inventa `market_probability`/`market_odds`.
    "home_team_over_0_5": MarketSpec("Local marca (Over 0.5)", "team_over", 0.5, "home"),
    "home_team_over_1_5": MarketSpec("Local Over 1.5 goles", "team_over", 1.5, "home"),
    "away_team_over_0_5": MarketSpec("Visitante marca (Over 0.5)", "team_over", 0.5, "away"),
    "away_team_over_1_5": MarketSpec("Visitante Over 1.5 goles", "team_over", 1.5, "away"),
}

# Selection en MatchOdds/RawOddsRecord (ver ingestion/odds/provider.py y
# history_dataset.py: mismo "match_result" market, selecciones
# "home"/"draw"/"away") para cada market_key de 1X2 -- unico punto que
# traduce entre el nombre interno del mercado y la selection real del
# proveedor de cuotas, para que no diverjan si se toca uno sin el otro.
MATCH_RESULT_SELECTIONS: dict[str, str] = {"home_win": "home", "draw": "draw", "away_win": "away"}

# Doble oportunidad = union de estos dos mercados de 1X2 (mismo orden que
# el nombre: "1X" = home_win U draw, etc). Unico punto que define la
# composicion, usado tanto para etiquetar/derivar probabilidad del modelo
# aqui como para derivar la cuota de mercado en prediction_service.py.
DOUBLE_CHANCE_COMPONENTS: dict[str, tuple[str, str]] = {
    "double_chance_1x": ("home_win", "draw"),
    "double_chance_x2": ("draw", "away_win"),
    "double_chance_12": ("home_win", "away_win"),
}


def label_for_market(table: pd.DataFrame, market_key: str) -> pd.Series:
    """Etiqueta booleana real (True = la seleccion se cumplio) para un partido finalizado."""
    spec = MARKET_DEFINITIONS[market_key]

    if spec.kind == "match_result":
        if market_key == "home_win":
            return table["home_goals"] > table["away_goals"]
        if market_key == "draw":
            return table["home_goals"] == table["away_goals"]
        if market_key == "away_win":
            return table["home_goals"] < table["away_goals"]
        raise ValueError(f"Mercado 1X2 desconocido: {market_key}")

    if spec.kind == "double_chance":
        component_a, component_b = DOUBLE_CHANCE_COMPONENTS[market_key]
        return label_for_market(table, component_a) | label_for_market(table, component_b)

    if spec.kind == "team_over":
        goals = table["home_goals"] if spec.team == "home" else table["away_goals"]
        return goals > spec.line

    total_goals = table["home_goals"] + table["away_goals"]
    if spec.kind == "over":
        return total_goals > spec.line
    if spec.kind == "under":
        return total_goals < spec.line
    if spec.kind == "btts":
        return (table["home_goals"] > 0) & (table["away_goals"] > 0)
    raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")


def _match_result_mask(home_grid: np.ndarray, away_grid: np.ndarray, market_key: str) -> np.ndarray:
    if market_key == "home_win":
        return home_grid > away_grid
    if market_key == "draw":
        return home_grid == away_grid
    if market_key == "away_win":
        return home_grid < away_grid
    raise ValueError(f"Mercado 1X2 desconocido: {market_key}")


def probability_from_score_matrix(score_matrix: np.ndarray, market_key: str) -> np.ndarray:
    """`score_matrix`: shape (n_rows, K+1, K+1) con score_matrix[i, h, a] =
    P(home=h, away=a) para el partido i (h,a en 0..K, K = ultima celda = "K+").

    Devuelve un array (n_rows,) con la probabilidad del mercado pedido.
    """
    spec = MARKET_DEFINITIONS[market_key]
    n, k_plus_1, _ = score_matrix.shape

    home_grid, away_grid = np.meshgrid(np.arange(k_plus_1), np.arange(k_plus_1), indexing="ij")
    # La celda "max_k" representa "max_k o mas": para sumas de goles totales,
    # aproximamos su valor puntual como max_k (conservador para lineas .5,
    # ya que max_k es un entero exacto y las lineas del MVP son todas .5).
    total_goals_grid = home_grid + away_grid

    if spec.kind == "over":
        mask = total_goals_grid > spec.line
    elif spec.kind == "under":
        mask = total_goals_grid < spec.line
    elif spec.kind == "btts":
        mask = (home_grid > 0) & (away_grid > 0)
    elif spec.kind == "team_over":
        team_grid = home_grid if spec.team == "home" else away_grid
        mask = team_grid > spec.line
    elif spec.kind == "match_result":
        mask = _match_result_mask(home_grid, away_grid, market_key)
    elif spec.kind == "double_chance":
        component_a, component_b = DOUBLE_CHANCE_COMPONENTS[market_key]
        # Los 3 resultados de 1X2 son mutuamente excluyentes: la union de
        # dos de ellos es simplemente el OR de sus masks, sin solapamiento.
        mask = _match_result_mask(home_grid, away_grid, component_a) | _match_result_mask(
            home_grid, away_grid, component_b
        )
    else:
        raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")

    return np.array([score_matrix[i][mask].sum() for i in range(n)])
