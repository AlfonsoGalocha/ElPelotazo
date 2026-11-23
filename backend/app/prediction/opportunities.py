"""Price-specific pre-match qualification shared by live rankings and captures."""

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from backend.app.normalization.bookmakers import is_excluded_bookmaker
from backend.app.prediction.anomaly import CONTRADICTION

QUOTE_FIELDS = ("match_id", "bookmaker", "market", "line", "selection", "price", "snapshot_type",
                "provider_updated_at", "fetched_at", "recorded_at")


@dataclass(frozen=True)
class Opportunity:
    quote: object | None
    reference_quote: object | None
    max_age_minutes: int
    age_minutes: float | None
    probability: float | None
    break_even: float | None
    ev: float | None
    conservative_ev: float | None
    reasons: tuple[str, ...]
    confidence_reasons: tuple[str, ...]
    score: float | None

    @property
    def high_confidence(self):
        return not self.reasons and not self.confidence_reasons


def valid_probability(value):
    return value is not None and isfinite(value) and 0 <= value <= 1


# Review threshold in probability points. It does not adjust the prediction.
MARKET_DISAGREEMENT_THRESHOLD = .25


def market_probability_gap(probability, market_probability, source):
    if (source != "derived_double_chance" and valid_probability(probability)
            and valid_probability(market_probability)):
        return probability - market_probability
    return None


def quote_age(row, now):
    archived = row.archived_quote
    if not row.bookmaker or archived is None or any(
        getattr(row, field) != getattr(archived, field) for field in QUOTE_FIELDS
    ):
        return None
    origin, fetched, recorded = row.provider_updated_at, row.fetched_at, row.recorded_at
    if origin is None or fetched is None or recorded is None or not origin <= fetched <= recorded <= now:
        return None
    return (now - origin).total_seconds() / 60


def ranking_score(probability, ev, reliability, quality, freshness, interval_quality, *, weights=(.4, .3, .3)):
    # The market band ensures positive-EV candidates already have p > 1/1.90.
    # Saturate EV at 20% to limit sensitivity to extreme model estimates.
    normalized_ev = min(max(ev / .20, 0), 1)
    evidence = (reliability + quality + freshness + interval_quality) / 4
    return weights[0] * probability + weights[1] * normalized_ev + weights[2] * evidence


def assess_opportunity(prediction, odds, market_key, settings, now, reliability, flags):
    match = prediction.match
    price_floor, price_ceiling = settings.min_opportunity_odds, settings.max_opportunity_odds
    max_age = settings.max_odds_age_minutes or settings.opportunity_max_odds_age_minutes
    # The existing six-hour ceiling stays in place for the main view.
    # In the last hour the high-confidence view requires a quote within one hour.
    high_max_age = min(max_age, 60 if match.kickoff_utc - now <= timedelta(hours=1) else 180)
    exact = [
        row for row in odds
        if (row.match_id, row.market, row.line, row.selection, row.snapshot_type)
        == (prediction.match_id, *market_key, "pre_match")
        and not is_excluded_bookmaker(row.bookmaker)
        and isfinite(row.price) and row.price > 1
    ] if market_key else []
    in_band = [row for row in exact if price_floor <= row.price <= price_ceiling]
    dated = [(age, row) for row in in_band if (age := quote_age(row, now)) is not None]
    current = [(age, row) for age, row in dated if age <= max_age]
    age, quote = max(current, key=lambda pair: (pair[1].price, -pair[0], pair[1].bookmaker), default=(None, None))
    reference_pool = in_band or exact
    reference = quote or max(reference_pool, key=lambda row: (row.price, row.bookmaker), default=None)
    probability = prediction.calibrated_probability
    low, high = prediction.uncertainty_low, prediction.uncertainty_high
    has_interval = valid_probability(low) and valid_probability(high) and low <= high
    break_even = 1 / reference.price if reference else None
    ev = probability * reference.price - 1 if valid_probability(probability) and reference else None
    conservative = min(probability, low) * reference.price - 1 if has_interval and ev is not None else None

    reasons = []
    if match.status != "scheduled" or not prediction.created_at <= now < match.kickoff_utc:
        reasons.append("partido_iniciado_o_prediccion_fuera_de_fecha")
    if not valid_probability(prediction.model_probability):
        reasons.append("probabilidad_invalida")
    if not exact:
        reasons.append("sin_mercado")
    elif not in_band:
        reasons.append("cuota_fuera_de_banda")
    elif not dated:
        reasons.append("origen_cuota_no_verificado")
    elif not current:
        reasons.append("cuota_desactualizada")
    if probability is None:
        reasons.append("calibracion_no_disponible")
    elif not valid_probability(probability):
        reasons.append("probabilidad_invalida")
    elif probability in (0, 1):
        reasons.append("calibracion_en_extremo")
    has_local_evidence = (
        reliability is not None and reliability.n_observations >= 30
        and reliability.source_level == "competition_market"
    )
    # A large calibration correction needs corroborating evidence before ranking.
    if (valid_probability(probability) and probability not in (0, 1)
            and valid_probability(prediction.model_probability)
            and abs(probability - prediction.model_probability) > .15 and not has_local_evidence):
        reasons.append("ajuste_calibracion_sin_respaldo")
    gap = market_probability_gap(
        probability if probability is not None else prediction.model_probability,
        prediction.market_probability, prediction.market_probability_source,
    )
    if gap is not None and gap > MARKET_DISAGREEMENT_THRESHOLD:
        reasons.append("ventaja_extrema_frente_al_mercado")
    if ev is not None and ev <= 0:
        reasons.append("ev_estimado_no_positivo")
    if CONTRADICTION in flags:
        reasons.append("mercado_contradictorio")
    confidence = []
    if not has_interval or high - low > .30:
        confidence.append("incertidumbre_elevada_o_no_disponible")
    if conservative is None or conservative <= 0:
        confidence.append("ev_conservador_no_positivo")
    if not has_local_evidence:
        confidence.append("fiabilidad_historica_insuficiente")
    if age is None or age > high_max_age:
        confidence.append("cuota_sin_frescura_alta")
    score = None
    if not reasons:
        data_quality = prediction.data_quality if valid_probability(prediction.data_quality) else 0
        source_quality = min(len({row.bookmaker for _, row in current}) / settings.market_quality_medium_min_bookmakers, 1)
        score = ranking_score(
            probability, ev, reliability.value if reliability else 0,
            (data_quality + source_quality) / 2,
            1 - age / max_age if max_age else float(age == 0),
            1 - (high - low) if has_interval else 0,
        )
    return Opportunity(
        quote, reference, max_age, quote_age(reference, now) if reference else None,
        probability, break_even, ev, conservative, tuple(dict.fromkeys(reasons)), tuple(confidence), score,
    )


def forecast_priority(item):
    prediction, assessment = item
    return assessment.probability, assessment.score, -prediction.id


def select_ranked(candidates, limit):
    by_fixture = {}
    for prediction, assessment in sorted(candidates, key=forecast_priority, reverse=True):
        by_fixture.setdefault(prediction.match_id, (prediction, assessment))
    return sorted(
        by_fixture.values(), key=lambda item: (item[1].score, *forecast_priority(item)), reverse=True,
    )[:limit]
