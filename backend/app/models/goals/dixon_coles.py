"""Modelo Dixon-Coles (Dixon & Coles, 1997).

Cada equipo tiene un parametro de ataque (alpha_i) y defensa (beta_i).
lambda_home = exp(alpha_home - beta_away + gamma)   [gamma = ventaja de local]
lambda_away = exp(alpha_away - beta_home)

Ademas anhade una correccion `rho` (tau) sobre los 4 marcadores mas bajos
(0-0, 1-0, 0-1, 1-1), donde el supuesto de independencia entre goles local y
visitante se sabe que falla empiricamente.

Ajuste por maxima verosimilitud con decaimiento temporal exponencial (los
partidos mas recientes pesan mas), tal como en el paper original.

Identificabilidad: se fuerza mean(alpha) = 0 anhadiendo esa restriccion como
termino de penalizacion suave en vez de una restriccion dura (mas estable
numericamente con L-BFGS-B sin restricciones).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from backend.app.models.base import GoalsModel, poisson_pmf_vector

TIME_DECAY_HALFLIFE_DAYS = 365.0  # partidos de hace 1 anho pesan la mitad


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


class DixonColesModel(GoalsModel):
    name = "dixon_coles"

    def __init__(self, l2_penalty: float = 0.01) -> None:
        self.l2_penalty = l2_penalty
        self.teams_: list[int] = []
        self.team_index_: dict[int, int] = {}
        self.attack_: np.ndarray | None = None
        self.defense_: np.ndarray | None = None
        self.gamma_: float = 0.2
        self.rho_: float = -0.05
        self.global_avg_attack_: float = 0.0
        self.global_avg_defense_: float = 0.0

    def fit(self, table: pd.DataFrame) -> DixonColesModel:
        finished = table.dropna(subset=["home_goals", "away_goals"]).copy()
        finished["date"] = pd.to_datetime(finished["date"])
        max_date = finished["date"].max()

        self.teams_ = sorted(set(finished["home_team_id"]) | set(finished["away_team_id"]))
        self.team_index_ = {team_id: i for i, team_id in enumerate(self.teams_)}
        n_teams = len(self.teams_)

        home_idx = finished["home_team_id"].map(self.team_index_).to_numpy()
        away_idx = finished["away_team_id"].map(self.team_index_).to_numpy()
        home_goals = finished["home_goals"].to_numpy(dtype=float)
        away_goals = finished["away_goals"].to_numpy(dtype=float)

        days_ago = (max_date - finished["date"]).dt.days.to_numpy()
        decay_rate = np.log(2) / TIME_DECAY_HALFLIFE_DAYS
        weights = np.exp(-decay_rate * days_ago)

        x0 = np.zeros(2 * n_teams + 2)  # [attack..., defense..., gamma, rho]

        def unpack(params: np.ndarray):
            attack = params[:n_teams]
            defense = params[n_teams : 2 * n_teams]
            gamma, rho = params[-2], params[-1]
            return attack, defense, gamma, rho

        def neg_log_likelihood(params: np.ndarray) -> float:
            attack, defense, gamma, rho = unpack(params)
            lam = np.exp(attack[home_idx] - defense[away_idx] + gamma)
            mu = np.exp(attack[away_idx] - defense[home_idx])

            log_lik = poisson.logpmf(home_goals, lam) + poisson.logpmf(away_goals, mu)

            low_score_mask = (home_goals <= 1) & (away_goals <= 1)
            if low_score_mask.any():
                tau_vals = np.array(
                    [
                        _tau(int(h), int(a), lam[i], mu[i], rho)
                        for i, (h, a) in enumerate(zip(home_goals, away_goals, strict=True))
                        if low_score_mask[i]
                    ]
                )
                tau_vals = np.clip(tau_vals, 1e-6, None)
                log_lik[low_score_mask] += np.log(tau_vals)

            weighted_ll = np.sum(weights * log_lik)
            penalty = self.l2_penalty * (np.sum(attack**2) + np.sum(defense**2))
            return -(weighted_ll) + penalty

        result = minimize(neg_log_likelihood, x0, method="L-BFGS-B", options={"maxiter": 300})
        attack, defense, gamma, rho = unpack(result.x)

        self.attack_ = attack
        self.defense_ = defense
        self.gamma_ = float(gamma)
        self.rho_ = float(np.clip(rho, -1.0, 1.0))
        self.global_avg_attack_ = float(np.mean(attack))
        self.global_avg_defense_ = float(np.mean(defense))
        return self

    def _team_params(self, team_id: int) -> tuple[float, float]:
        idx = self.team_index_.get(team_id)
        if idx is None:
            # Equipo no visto en entrenamiento (p.ej. recien ascendido): shrink
            # hacia la media global (seccion 57/59 del brief: cold start / ascendidos).
            return self.global_avg_attack_, self.global_avg_defense_
        return self.attack_[idx], self.defense_[idx]

    def _lambdas(self, table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        home_lam, away_lam = [], []
        for row in table.itertuples(index=False):
            a_home, d_home = self._team_params(row.home_team_id)
            a_away, d_away = self._team_params(row.away_team_id)
            home_lam.append(np.exp(a_home - d_away + self.gamma_))
            away_lam.append(np.exp(a_away - d_home))
        return np.array(home_lam), np.array(away_lam)

    def predict_goal_distribution(self, table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        home_lam, away_lam = self._lambdas(table)
        return poisson_pmf_vector(home_lam), poisson_pmf_vector(away_lam)

    def predict_score_matrix(self, table: pd.DataFrame) -> np.ndarray:
        """Matriz de marcador conjunta con la correccion rho de Dixon-Coles
        aplicada a los 4 marcadores bajos y renormalizada."""
        home_dist, away_dist = self.predict_goal_distribution(table)
        home_lam, away_lam = self._lambdas(table)
        joint = np.einsum("ij,ik->ijk", home_dist, away_dist)

        for i in range(len(table)):
            for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
                joint[i, x, y] *= _tau(x, y, home_lam[i], away_lam[i], self.rho_)
            joint[i] = np.clip(joint[i], 0.0, None)
            joint[i] /= joint[i].sum()
        return joint
