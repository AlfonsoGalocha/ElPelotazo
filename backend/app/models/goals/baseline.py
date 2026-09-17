"""Baseline extremadamente simple: Poisson con lambda = media de goles de la
COMPETICION (ignora por completo quien juega). Sirve como listón minimo: si
un modelo mas complejo no lo supera en Brier/Log Loss out-of-sample, no
aporta valor (seccion 52 del brief)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.models.base import GoalsModel, poisson_pmf_vector


class LeagueAverageBaseline(GoalsModel):
    name = "league_average_baseline"

    def __init__(self) -> None:
        self.home_lambda_: float = 1.5
        self.away_lambda_: float = 1.2

    def fit(self, table: pd.DataFrame) -> LeagueAverageBaseline:
        finished = table.dropna(subset=["home_goals", "away_goals"])
        if len(finished) == 0:
            return self
        self.home_lambda_ = float(finished["home_goals"].mean())
        self.away_lambda_ = float(finished["away_goals"].mean())
        return self

    def predict_goal_distribution(self, table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        n = len(table)
        home_lam = np.full(n, self.home_lambda_)
        away_lam = np.full(n, self.away_lambda_)
        return poisson_pmf_vector(home_lam), poisson_pmf_vector(away_lam)
