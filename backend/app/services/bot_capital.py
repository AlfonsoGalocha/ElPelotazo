"""Capital policy of the virtual bot: how much to stake, and on which combination of positions.

Predictions are inputs only. This module never changes a probability, a model or
the opportunity ranking; it decides how much fictitious money the existing bot
commits, with fractional Kelly and expected log growth used as references that
are always capped by explicit exposure limits.
"""

from dataclasses import asdict, dataclass
from functools import lru_cache
from itertools import combinations, product
from math import isfinite, sqrt

import numpy as np
from scipy.optimize import minimize

from backend.app.prediction.opportunities import market_probability_gap, valid_probability

CAPITAL_POLICY_VERSION = "capital-v1-portfolio-kelly"
MIN_STAKE_CENTS = 200
STAKE_STEP_CENTS = 100
# Quotes older than this cannot size a position, even if the ranking still shows them.
MAX_STAKE_QUOTE_AGE_MINUTES = 90
COMBO_LEG_ODDS = (1.05, 1.60)
COMBO_PRICE_RANGE = (1.25, 3.20)
COMBO_MARKETS = {
    "over_1_5", "over_2_5", "under_2_5", "over_3_5", "btts",
    "double_chance_1x", "double_chance_x2", "double_chance_12",
    "home_team_over_0_5", "away_team_over_0_5", "home_win", "away_win",
}
# Dependence between two different fixtures is unknown; these bounds are stress
# values, not estimates. Fixtures of one competition on one day share more context.
RHO_SAME_COMPETITION_DAY = .15
RHO_DEFAULT = .05
MIN_LOG_GROWTH = .0005  # 0.05 % of equity per decision; below that the bot abstains.


@dataclass(frozen=True)
class RiskProfile:
    name: str
    label: str
    kelly_fraction: float
    max_single_fraction: float  # one position, over equity
    max_single_cents: int
    max_double_fraction: float
    max_exposure_fraction: float  # pending + new stakes, over equity
    min_reserve_fraction: float  # available cash that must remain untouched
    max_scenario_loss_fraction: float  # worst joint outcome of the new decision
    experimental: bool = False


PROFILES = {
    "prudente": RiskProfile("prudente", "Prudente", .25, .08, 3_000, .04, .20, .70, .15),
    "moderado": RiskProfile("moderado", "Moderado", .35, .20, 7_500, .10, .40, .50, .30),
    "agresivo_experimental": RiskProfile(
        "agresivo_experimental", "Agresivo experimental", .50, .50, 15_000, .35, .60, .35, .50, experimental=True,
    ),
}
DEFAULT_PROFILE = "moderado"


def active_profile(name, aggressive_authorized):
    """Experimental limits apply only with an explicit, separate authorisation."""
    profile = PROFILES.get(name) or PROFILES[DEFAULT_PROFILE]
    if profile.experimental and not aggressive_authorized:
        return PROFILES[DEFAULT_PROFILE], f"{profile.name}_sin_autorizacion"
    return profile, None


def _clip(value, low=0., high=1.):
    return min(max(value, low), high)


def confirmed_age_minutes(item, now):
    """Minutes since the provider last re-served this exact price (origin age is a separate signal)."""
    fetched = getattr(item.quote, "fetched_at", None) if item.quote is not None else None
    if now is None or fetched is None:
        return item.age_minutes
    return max((now - fetched).total_seconds() / 60, 0)


def statistical_confidence(prediction, item, reliability, now=None):
    """How much the estimate can be trusted, independently of how high it is."""
    low, high = prediction.uncertainty_low, prediction.uncertainty_high
    has_interval = valid_probability(low) and valid_probability(high) and low <= high
    n = reliability.n_observations if reliability else 0
    local = reliability is not None and reliability.source_level == "competition_market"
    model = prediction.model_probability
    if not valid_probability(item.probability):
        return dict(score=0., components={}, gates=["probabilidad_no_disponible"], calibration_error=.05)
    confirmed = confirmed_age_minutes(item, now)
    gap = market_probability_gap(item.probability, prediction.market_probability, prediction.market_probability_source)
    components = dict(
        intervalo=_clip(1 - (high - low) / .30) if has_interval else 0.,
        muestra=_clip(n / 200) * (1 if local else .6),
        historico_mercado=_clip(reliability.value) if reliability else 0.,
        calibracion=(.5 if item.probability_source == "original" else 1.) * (
            _clip(1 - abs(item.probability - model) / .30) if valid_probability(model) else .5),
        acuerdo_mercado=_clip(1 - abs(gap) / .25) if gap is not None else .5,
        frescura=(_clip(1 - confirmed / MAX_STAKE_QUOTE_AGE_MINUTES) + _clip(1 - item.age_minutes / item.max_age_minutes)) / 2
        if confirmed is not None and item.age_minutes is not None and item.max_age_minutes else 0.,
    )
    weights = dict(intervalo=.25, muestra=.20, historico_mercado=.15, calibracion=.15, acuerdo_mercado=.15, frescura=.10)
    score = sum(weights[key] * value for key, value in components.items())
    gates = []
    if not has_interval:
        gates.append("sin_intervalo_de_incertidumbre")
    if confirmed is None or confirmed > MAX_STAKE_QUOTE_AGE_MINUTES:
        gates.append("cuota_desactualizada_para_stake")
    return dict(score=round(score, 4) if not gates else 0., components={k: round(v, 4) for k, v in components.items()},
                gates=gates, calibration_error=round(max((high - low) / 4, .02) if has_interval else .05, 4))


def effective_probability(probability, low, price, confidence):
    """Shrink the estimate toward break-even: no confidence means no edge at all."""
    if not (valid_probability(probability) and isfinite(price) and price > 1):
        return None
    conservative = (probability + low) / 2 if valid_probability(low) and low <= probability else probability - .05
    break_even = 1 / price
    return break_even + confidence * (conservative - break_even)


def kelly_fraction(p, price):
    return (p * price - 1) / (price - 1) if price > 1 else 0.


def leg_from_candidate(leg, prediction, item, reliability, now=None):
    """Frozen pre-kickoff description of one selection, sufficient to replay sizing."""
    confidence = statistical_confidence(prediction, item, reliability, now)
    p_eff = effective_probability(item.probability, prediction.uncertainty_low, item.quote.price, confidence["score"])
    return dict(
        leg, confidence=confidence["score"], confidence_components=confidence["components"],
        confidence_gates=confidence["gates"], calibration_error=confidence["calibration_error"],
        uncertainty=[prediction.uncertainty_low, prediction.uncertainty_high],
        effective_probability=round(p_eff, 6) if p_eff is not None else None,
        reliability=dict(source=reliability.source_level, n=reliability.n_observations, value=reliability.value)
        if reliability else None,
    )


def leg_key(leg):
    return leg["match_id"], leg["market"], leg.get("line"), leg.get("selection")


def joint_probability(p1, p2, rho):
    """P(both) for two Bernoulli events with correlation rho, kept inside Fréchet bounds."""
    joint = p1 * p2 + rho * sqrt(p1 * (1 - p1) * p2 * (1 - p2))
    return _clip(joint, max(0., p1 + p2 - 1), min(p1, p2))


def pair_rho(a, b):
    same = a["competition"] == b["competition"] and a["kickoff"][:10] == b["kickoff"][:10]
    return RHO_SAME_COMPETITION_DAY if same else RHO_DEFAULT


HIGH_PROBABILITY_LEG = .80
HIGH_PROBABILITY_MAX_PRICE = 1.40


def assess_high_probability_double(a, b):
    """User-requested double of two very likely selections, sized on the model's own probabilities.

    It skips the confidence shrinkage on purpose, so it is recorded as its own strategy and
    evaluated separately; it still needs a positive expected value under adverse dependence.
    """
    if a["match_id"] == b["match_id"] or any(
        leg["probability"] < HIGH_PROBABILITY_LEG or leg["price"] > HIGH_PROBABILITY_MAX_PRICE for leg in (a, b)
    ):
        return None
    a, b = (dict(leg, effective_probability=leg["probability"]) for leg in (a, b))
    price = round(a["price"] * b["price"], 4)
    rho = pair_rho(a, b)
    sensitivity = []
    for label, shift, r in (("independencia", 0, 0.), ("dependencia_positiva", 0, rho),
                            ("dependencia_negativa", 0, -rho), ("error_calibracion_adicional", 1, -rho)):
        joint = joint_probability(_clip(a["probability"] - shift * a["calibration_error"]),
                                  _clip(b["probability"] - shift * b["calibration_error"]), r)
        sensitivity.append(dict(scenario=label, rho=r, probability=round(joint, 4), ev=round(joint * price - 1, 4)))
    robust = min(row["ev"] for row in sensitivity[:3])
    reasons = [] if robust > 0 else ["ev_negativo_con_dependencia_adversa"]
    if not COMBO_PRICE_RANGE[0] <= price <= COMBO_PRICE_RANGE[1]:
        reasons.append("cuota_combinada_fuera_de_rango")
    model_joint = a["probability"] * b["probability"]
    return dict(
        legs=[a, b], price=price, model_probability=round(model_joint, 4), model_ev=round(model_joint * price - 1, 4),
        rho_stress=rho, sensitivity=sensitivity, effective_probability=sensitivity[2]["probability"],
        robust_ev=robust, synthetic_price=True, accepted=not reasons, reasons=reasons, strategy="alta_probabilidad",
    )


def assess_double(a, b):
    """Joint estimate of a two-fixture double, with dependence and calibration stress."""
    reasons = []
    if a["match_id"] == b["match_id"]:
        reasons.append("mismo_partido")
    for leg in (a, b):
        if leg["effective_probability"] is None or leg["confidence_gates"]:
            reasons.append("componente_sin_confianza_estadistica")
        elif leg["effective_probability"] * leg["price"] <= 1:
            reasons.append("componente_sin_ventaja_tras_confianza")
    price = round(a["price"] * b["price"], 4)
    if not COMBO_PRICE_RANGE[0] <= price <= COMBO_PRICE_RANGE[1]:
        reasons.append("cuota_combinada_fuera_de_rango")
    rho = pair_rho(a, b)
    sensitivity = []
    if not reasons:
        # Effective probabilities already price calibration error; the gate stresses dependence.
        # The extra calibration scenario is reported for transparency only.
        for label, shift, r in (("independencia", 0, 0.), ("dependencia_positiva", 0, rho),
                                ("dependencia_negativa", 0, -rho), ("error_calibracion_adicional", 1, -rho)):
            p1 = _clip(a["effective_probability"] - shift * a["calibration_error"])
            p2 = _clip(b["effective_probability"] - shift * b["calibration_error"])
            joint = joint_probability(p1, p2, r)
            sensitivity.append(dict(scenario=label, rho=r, probability=round(joint, 4), ev=round(joint * price - 1, 4)))
        if min(row["ev"] for row in sensitivity[:3]) <= 0:
            reasons.append("ev_negativo_con_dependencia_adversa")
    model_joint = a["probability"] * b["probability"]
    return dict(
        legs=[a, b], price=price, model_probability=round(model_joint, 4),
        model_ev=round(model_joint * price - 1, 4), rho_stress=rho, sensitivity=sensitivity,
        effective_probability=sensitivity[0]["probability"] if sensitivity else None,
        robust_ev=min(row["ev"] for row in sensitivity[:3]) if sensitivity else None,
        synthetic_price=True, accepted=not reasons, reasons=reasons, strategy="ventaja_confirmada",
    )


def _scenario_table(positions, rho_sign=0, shift=0):
    """Joint outcomes of the distinct selections involved, as (probabilities, outcome matrix)."""
    legs = list(dict.fromkeys(leg_key(leg) for pos in positions for leg in pos["legs"]))
    info = {leg_key(leg): leg for pos in positions for leg in pos["legs"]}
    p = np.array([_clip(info[k]["effective_probability"] - shift * info[k]["calibration_error"], 1e-6, 1 - 1e-6) for k in legs])
    outcomes = np.array(list(product((1, 0), repeat=len(legs))), dtype=float)
    probs = np.prod(np.where(outcomes == 1, p, 1 - p), axis=1)
    if rho_sign and len(legs) > 1:
        # First-order Bahadur expansion: pairwise dependence of the stress size between selections.
        z = (outcomes - p) / np.sqrt(p * (1 - p))
        adjust = np.ones(len(outcomes))
        for i, j in combinations(range(len(legs)), 2):
            adjust += rho_sign * pair_rho(info[legs[i]], info[legs[j]]) * z[:, i] * z[:, j]
        probs = np.clip(probs * adjust, 0, None)
        probs /= probs.sum()
    index = {k: i for i, k in enumerate(legs)}
    wins = np.array([[all(row[index[leg_key(leg)]] for leg in pos["legs"]) for pos in positions] for row in outcomes])
    return legs, outcomes, probs, wins


def _growth(stakes, prices, probs, wins, equity):
    pnl = np.where(wins, stakes * (prices - 1), -stakes).sum(axis=1)
    return float(probs @ np.log1p(pnl / equity)), pnl


def evaluate_alternative(positions, profile, equity, available, reserved, multiplier, min_stake_cents=MIN_STAKE_CENTS):
    """Size one alternative (a set of positions) and describe its joint risk.

    Stakes follow fractional Kelly, raised to `min_stake_cents` when the user sets a floor.
    The floor never breaks the exposure, reserve or scenario-loss limits: an alternative that
    cannot fund every position at the floor within those limits is not viable.
    """
    prices = np.array([pos["price"] for pos in positions])
    # Sizing stresses dependence only: calibration error is already priced into the effective
    # probability (interval midpoint + confidence shrinkage); subtracting it again would count it twice.
    tables = [_scenario_table(positions, sign, 0) for sign in (0, -1, 1)]
    calibration_table = _scenario_table(positions, -1, 1)
    _, outcomes, base_probs, wins = tables[0]
    cash = available - profile.min_reserve_fraction * equity
    bounds = []
    for pos in positions:
        cap = (profile.max_double_fraction if len(pos["legs"]) > 1 else profile.max_single_fraction) * equity
        bounds.append((0, max(0., min(cap, profile.max_single_cents, cash))))
    if not positions or all(high <= 0 for _, high in bounds):
        return None

    # Robust full-Kelly reference: maximise the worst expected log growth across dependence stresses.
    # Optimised in fractions of equity: cents would hide the gradient below the solver tolerance.
    def objective(x):
        return -min(_growth(x * equity, prices, probs, wins, equity)[0] for _, _, probs, _ in tables)

    # The full-Kelly reference is bounded only by solvency; caps apply after the Kelly fraction.
    unit_bounds = [(0, .99 * available / equity) if high > 0 else (0, 0) for _, high in bounds]
    full = minimize(objective, np.array([high / 4 for _, high in unit_bounds]), bounds=unit_bounds,
                    method="L-BFGS-B", options=dict(ftol=1e-12, gtol=1e-10)).x * equity
    scaled = full * profile.kelly_fraction * multiplier
    caps = np.array([high for _, high in bounds])
    stakes = np.minimum(scaled, caps)
    floored = min_stake_cents > MIN_STAKE_CENTS
    if floored:
        if (full < STAKE_STEP_CENTS).any() or (caps < min_stake_cents).any():
            return dict(feasible=False, reason="sin_ventaja_o_sin_margen_para_el_stake_minimo", binding_limits=[])
        stakes = np.maximum(stakes, min_stake_cents)
    # Joint limits: worst joint outcome, total exposure including pending tickets, liquidity reserve.
    limits = dict(
        perdida_maxima_escenario=profile.max_scenario_loss_fraction * equity,
        exposicion_total=profile.max_exposure_fraction * equity - reserved,
        reserva_liquidez=cash,
    )
    binding = ["tope_por_posicion"] if (scaled > stakes + 1).any() else []
    for label, limit in limits.items():
        total = stakes.sum()
        if total > limit:
            stakes *= max(limit, 0) / total
            binding.append(label)
    stakes = np.floor(stakes / STAKE_STEP_CENTS) * STAKE_STEP_CENTS
    if not (stakes >= min_stake_cents).all():
        return dict(feasible=False, reason="stake_inferior_al_minimo", binding_limits=binding)
    growth = [_growth(stakes, prices, probs, wins, equity)[0] for _, _, probs, _ in tables]
    calibration_growth = _growth(stakes, prices, calibration_table[2], calibration_table[3], equity)[0]
    _, pnl = _growth(stakes, prices, base_probs, wins, equity)
    model_probs = _model_probabilities(positions, outcomes)
    scenarios = [
        dict(outcome={k[0]: bool(v) for k, v in zip(tables[0][0], row)}, probability=round(float(pr), 4),
             profit_cents=int(round(pl)))
        for row, pr, pl in zip(outcomes, base_probs, pnl)
    ]
    mean = float(base_probs @ pnl)
    robust_profit = min(float(probs @ _growth(stakes, prices, probs, w, equity)[1]) for _, _, probs, w in tables)
    return dict(
        robust_expected_profit_cents=int(round(robust_profit)),
        feasible=True, stakes_cents=[int(s) for s in stakes],
        full_kelly_cents=[int(round(f)) for f in full],
        committed_cents=int(stakes.sum()), committed_share=round(float(stakes.sum()) / equity, 4),
        expected_profit_cents=int(round(mean)),
        model_expected_profit_cents=int(round(float(model_probs @ pnl))),
        expected_log_growth=round(growth[0], 6), robust_log_growth=round(min(growth), 6),
        calibration_stress_log_growth=round(calibration_growth, 6),
        loss_probability=round(float(base_probs[pnl < 0].sum()), 4),
        volatility_cents=int(round(sqrt(float(base_probs @ (pnl - mean) ** 2)))),
        max_loss_cents=int(-pnl.min()), max_loss_share=round(float(-pnl.min()) / equity, 4),
        scenarios=scenarios, binding_limits=binding,
    )


def _model_probabilities(positions, outcomes):
    info = {leg_key(leg): leg for pos in positions for leg in pos["legs"]}
    p = np.array([info[k]["probability"] for k in dict.fromkeys(leg_key(leg) for pos in positions for leg in pos["legs"])])
    return np.prod(np.where(outcomes == 1, p, 1 - p), axis=1)


def _describe(positions):
    def name(pos):
        if len(pos["legs"]) == 1:
            return f"Individual {pos['legs'][0]['match']}"
        return "Combinada " + " / ".join(f"{leg['match']} ({leg['market']})" for leg in pos["legs"])
    return " + ".join(name(pos) for pos in positions) or "No apostar"


def drawdown_multiplier(equity, peak):
    """Lower the Kelly fraction in drawdowns; it never rises above 1 after losses."""
    drawdown = 1 - equity / peak if peak > 0 else 0
    if drawdown <= .10:
        return 1.
    return max(.25, 1 - (drawdown - .10) * 2.5)


def choose_portfolio(singles, doubles, profile, *, equity, available, reserved, peak, max_positions=6,
                     min_stake_cents=MIN_STAKE_CENTS):
    """Compare every compatible alternative as a portfolio; abstaining is always one of them.

    With a user stake floor, an alternative qualifies when its expected profit stays positive under
    adverse dependence; otherwise it must also add expected log growth. Robust log growth ranks them.
    """
    multiplier = drawdown_multiplier(equity, peak)
    pool = [dict(kind="auto_single", legs=[leg], price=leg["price"]) for leg in singles
            if leg["effective_probability"] is not None and not leg["confidence_gates"]
            and leg["effective_probability"] * leg["price"] > 1]
    pool += [dict(kind="auto_double", legs=d["legs"], price=d["price"], double=d,
                  strategy=d.get("strategy", "ventaja_confirmada")) for d in doubles if d["accepted"]]
    pool = sorted(pool, key=lambda pos: _edge(pos), reverse=True)[:max_positions]
    alternatives = [dict(description="No apostar", positions=[], feasible=True, expected_log_growth=0.,
                         robust_log_growth=0., committed_cents=0, max_loss_cents=0, loss_probability=0.,
                         expected_profit_cents=0)]
    for size in range(1, len(pool) + 1):
        for subset in combinations(pool, size):
            # Two positions on one fixture must share the selection (and its probability), so the joint
            # scenarios stay exact.
            chosen = {}
            if any(chosen.setdefault(leg["match_id"], (leg_key(leg), leg["effective_probability"]))
                   != (leg_key(leg), leg["effective_probability"]) for pos in subset for leg in pos["legs"]):
                continue
            result = evaluate_alternative(list(subset), profile, equity, available, reserved, multiplier, min_stake_cents)
            if result is None:
                continue
            alternatives.append(dict(result, description=_describe(subset), positions=list(subset)))
    floored = min_stake_cents > MIN_STAKE_CENTS
    feasible = [alt for alt in alternatives[1:] if alt["feasible"] and (
        alt["robust_log_growth"] > MIN_LOG_GROWTH or (floored and alt["robust_expected_profit_cents"] > 0))]
    best = max(feasible, key=lambda alt: (alt["robust_log_growth"], -alt["committed_cents"]), default=alternatives[0])
    for alt in alternatives:
        alt["chosen"] = alt is best
        alt["verdict"] = _verdict(alt, best)
    tickets = []
    for pos, stake in zip(best["positions"], best.get("stakes_cents", [])):
        p_eff = pos["double"]["effective_probability"] if pos["kind"] == "auto_double" else pos["legs"][0]["effective_probability"]
        model_p = pos["double"]["model_probability"] if pos["kind"] == "auto_double" else pos["legs"][0]["probability"]
        tickets.append(dict(
            kind=pos["kind"], strategy=pos.get("strategy", "ventaja_confirmada"), legs=pos["legs"],
            stake_cents=stake, price=pos["price"],
            probability=model_p, effective_probability=p_eff,
            confidence=min(leg["confidence"] for leg in pos["legs"]),
            ev=round(model_p * pos["price"] - 1, 4), effective_ev=round(p_eff * pos["price"] - 1, 4),
            bankroll_share=round(stake / equity, 4), max_loss_cents=stake,
            **({"double": {k: v for k, v in pos["double"].items() if k != "legs"}} if pos["kind"] == "auto_double" else {}),
            reason=_ticket_reason(pos, stake, best),
        ))
    return dict(
        tickets=tickets, chosen=best["description"], multiplier=round(multiplier, 4),
        reason=_decision_reason(best, alternatives),
        alternatives=[_public_alternative(alt) for alt in sorted(
            alternatives, key=lambda a: (not a["chosen"], -(a.get("robust_log_growth") or -1)))][:12],
        evaluated_alternatives=len(alternatives),
        positions_considered=[_public_position(pos) for pos in pool],
    )


def _edge(pos):
    p = pos["double"]["effective_probability"] if pos["kind"] == "auto_double" else pos["legs"][0]["effective_probability"]
    return p * pos["price"] - 1


def _verdict(alt, best):
    if alt["chosen"]:
        return "elegida"
    if not alt["feasible"]:
        return alt.get("reason", "no_viable")
    if not alt["positions"]:
        return "descartada_por_existir_alternativa_con_crecimiento_robusto_positivo"
    if alt["robust_log_growth"] <= MIN_LOG_GROWTH:
        return "crecimiento_robusto_insuficiente"
    return "menor_crecimiento_robusto_que_la_elegida"


def _decision_reason(best, alternatives):
    if not best["positions"]:
        viable = [a for a in alternatives[1:] if a["feasible"]]
        if any(a.get("reason") == "sin_ventaja_o_sin_margen_para_el_stake_minimo" for a in alternatives[1:]) and not viable:
            return "Abstención: ninguna posición tiene ventaja positiva o margen de riesgo para el stake mínimo."
        if not viable:
            return "Abstención: ninguna posición supera los filtros de confianza, ventaja y stake mínimo."
        return "Abstención: ninguna alternativa ofrece crecimiento logarítmico suficiente en el peor escenario de dependencia."
    return (f"{best['description']}: mayor crecimiento logarítmico robusto ({best['robust_log_growth']:.3%} de la banca) "
            f"de {len(alternatives)} alternativas; compromete {best['committed_share']:.1%} de la banca, "
            f"pérdida máxima {best['max_loss_cents'] / 100:.2f} € y probabilidad de pérdida {best['loss_probability']:.1%}."
            + (f" Límites activos: {', '.join(best['binding_limits'])}." if best["binding_limits"] else ""))


def _ticket_reason(pos, stake, best):
    leg = pos["legs"][0]
    if pos.get("strategy") == "alta_probabilidad":
        d = pos["double"]
        return (f"Combinada de alta probabilidad {pos['price']:.2f}: dos selecciones ≥{HIGH_PROBABILITY_LEG:.0%} según el modelo, "
                f"probabilidad conjunta {d['effective_probability']:.1%} con dependencia adversa, EV {d['robust_ev']:+.1%}. "
                "Usa la probabilidad del modelo sin rebaja por confianza.")
    if pos["kind"] == "auto_double":
        d = pos["double"]
        return (f"Combinada sintética {pos['price']:.2f}: probabilidad conjunta efectiva {d['effective_probability']:.1%}, "
                f"EV con dependencia adversa {d['robust_ev']:+.1%} (ρ={-d['rho_stress']:+.2f}).")
    return (f"Probabilidad {leg['probability']:.1%} con confianza {leg['confidence']:.2f} → efectiva "
            f"{leg['effective_probability']:.1%} frente al equilibrio {1 / leg['price']:.1%}.")


def _public_position(pos):
    return dict(kind=pos["kind"], price=pos["price"], matches=[leg["match"] for leg in pos["legs"]],
                markets=[leg["market"] for leg in pos["legs"]], edge=round(_edge(pos), 4))


def _public_alternative(alt):
    keys = ("description", "feasible", "chosen", "verdict", "stakes_cents", "committed_cents", "committed_share",
            "expected_profit_cents", "model_expected_profit_cents", "expected_log_growth", "robust_log_growth",
            "calibration_stress_log_growth",
            "loss_probability", "volatility_cents", "max_loss_cents", "max_loss_share", "scenarios", "binding_limits")
    return {k: alt[k] for k in keys if k in alt}


def profile_summary(profile, equity):
    return dict(asdict(profile), max_single_at_equity_cents=int(min(profile.max_single_fraction * equity, profile.max_single_cents)))


@lru_cache(maxsize=4)
def simulate_profiles(n_paths=2000, n_bets=150, initial_cents=30_000, seed=7):
    """Monte Carlo of each profile on synthetic single opportunities.

    Three worlds: probabilities right; the model overconfident by three points; and an
    exceptional-evidence world (large estimated edges, high confidence, still three points
    overconfident) where 100-150 EUR stakes can appear. Opportunities are drawn
    independently: no real odds are used and nothing is presented as an offer.
    """
    rng = np.random.default_rng(seed)
    price = rng.uniform(1.25, 1.95, (n_paths, n_bets))
    uniforms = rng.random((n_paths, n_bets))
    base_edge, base_confidence = rng.random((n_paths, n_bets)), rng.random((n_paths, n_bets))
    results = {}
    # (world, overconfidence bias, estimated edge range, confidence range)
    worlds = (("calibrado", 0., (0, .06), (.3, .95)), ("sobreconfianza_3pp", .03, (0, .06), (.3, .95)),
              ("evidencia_alta_sobreconfianza_3pp", .03, (.06, .14), (.8, 1.)))
    for world, bias, (e_low, e_high), (c_low, c_high) in worlds:
        edge = e_low + base_edge * (e_high - e_low)
        confidence = c_low + base_confidence * (c_high - c_low)
        estimated = np.clip(1 / price + edge, 0, .97)
        true_p = np.clip(estimated - bias, 0, 1)
        won = uniforms < true_p
        rows = {}
        for name, profile in {**PROFILES, "plano_10": None}.items():
            bank = np.full(n_paths, float(initial_cents))
            peak = bank.copy()
            worst_drawdown = np.zeros(n_paths)
            stakes_seen = []
            for t in range(n_bets):
                if profile is None:
                    stake = np.minimum(1000., bank)
                else:
                    p_eff = 1 / price[:, t] + confidence[:, t] * (estimated[:, t] - .5 * bias - 1 / price[:, t])
                    f = np.clip((p_eff * price[:, t] - 1) / (price[:, t] - 1), 0, None)
                    dd = 1 - bank / peak
                    mult = np.where(dd <= .10, 1, np.maximum(.25, 1 - (dd - .10) * 2.5))
                    cap = np.minimum.reduce([
                        np.full(n_paths, float(profile.max_single_cents)), profile.max_single_fraction * bank,
                        profile.max_scenario_loss_fraction * bank, (1 - profile.min_reserve_fraction) * bank,
                    ])
                    stake = np.floor(np.minimum(f * profile.kelly_fraction * mult * bank, cap) / 100) * 100
                    stake = np.where(stake >= MIN_STAKE_CENTS, stake, 0)
                stakes_seen.append(stake)
                bank = bank + np.where(won[:, t], stake * (price[:, t] - 1), -stake)
                peak = np.maximum(peak, bank)
                worst_drawdown = np.maximum(worst_drawdown, 1 - bank / peak)
            stakes = np.concatenate(stakes_seen)
            placed = stakes[stakes > 0]
            rows[name] = dict(
                median_final_cents=int(np.median(bank)), p5_final_cents=int(np.percentile(bank, 5)),
                p95_final_cents=int(np.percentile(bank, 95)), loss_probability=round(float((bank < initial_cents).mean()), 4),
                halved_probability=round(float((bank < initial_cents / 2).mean()), 4),
                median_max_drawdown=round(float(np.median(worst_drawdown)), 4),
                p95_max_drawdown=round(float(np.percentile(worst_drawdown, 95)), 4),
                median_stake_cents=int(np.median(placed)) if placed.size else 0,
                p99_stake_cents=int(np.percentile(placed, 99)) if placed.size else 0,
                share_stakes_100_plus=round(float((placed >= 10_000).mean()), 4) if placed.size else 0.,
                bets_placed_share=round(float(placed.size / stakes.size), 4),
            )
        results[world] = rows
    return dict(paths=n_paths, bets=n_bets, initial_cents=initial_cents, worlds=results,
                assumptions="Oportunidades sintéticas independientes con cuota 1,25–1,95. Calibrado y sobreconfianza: ventaja "
                            "estimada 0–6 pp y confianza 0,30–0,95 (la sobreconfianza resta 3 pp a la probabilidad real). "
                            "Evidencia alta: ventaja estimada 6–14 pp, confianza 0,80–1,00 y también 3 pp de sobreconfianza. "
                            "Sin cuotas reales; sirve para comparar límites, no para prever beneficios.")

