"""Read-only sports forecasts and price qualification for the Today calendar."""

from collections import defaultdict
from math import isclose, isfinite

from backend.app.config.settings import get_settings
from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS, CHAMPIONS_LEAGUE
from backend.app.normalization.bookmakers import is_excluded_bookmaker

from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.prediction.opportunities import QUOTE_FIELDS, quote_age, ranking_score, valid_probability
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP, rank_champions_preview, score_api_candidate, score_champions_candidate
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


def informative_pick(rows, competition, reliability, model_version_id, now, *, odds, diagnostics):
    settings = get_settings()
    max_age = settings.max_odds_age_minutes or settings.opportunity_max_odds_age_minutes
    candidates = []
    by_market = {row.market: row for row in rows}
    incoherent = set()
    for group in (("home_win", "draw", "away_win"), ("over_2_5", "under_2_5"),
                  ("corners_over_9_5", "corners_under_9_5")):
        values = [by_market[key].calibrated_probability for key in group if key in by_market]
        if len(values) == len(group) and all(valid_probability(p) for p in values):
            if not isclose(sum(values), 1, abs_tol=1e-6):
                incoherent.update(group)
    corners = ("corners_over_8_5", "corners_over_9_5", "corners_over_10_5")
    corner_values = [by_market[key].calibrated_probability for key in corners if key in by_market]
    if len(corner_values) == len(corners) and all(valid_probability(p) for p in corner_values):
        if not corner_values[0] >= corner_values[1] >= corner_values[2]:
            incoherent.update((*corners, "corners_under_9_5"))
    for row in rows:
        calibrated = row.calibrated_probability if row.market not in incoherent else None
        probability = calibrated if calibrated is not None else row.model_probability
        if not valid_probability(probability) or probability in (0, 1):
            continue
        evidence = reliability.get((competition, row.market))
        supported = bool(evidence and evidence.source_level == "competition_market" and evidence.n_observations >= 30)
        low, high = row.uncertainty_low, row.uncertainty_high
        interval = valid_probability(low) and valid_probability(high) and low <= probability <= high
        key = ALL_MARKET_TO_ODDS_LOOKUP.get(row.market)
        if key is None or probability < .60:
            continue
        prices = []
        for quote in odds:
            if ((quote.match_id, quote.market, quote.line, quote.selection, quote.snapshot_type)
                    != (row.match_id, *key, "pre_match") or not isfinite(quote.price)
                    or not 1.30 <= quote.price <= 1.90 or is_excluded_bookmaker(quote.bookmaker)):
                continue
            archived = quote.archived_quote
            if (not quote.bookmaker or archived is None
                    or any(getattr(quote, field) != getattr(archived, field) for field in QUOTE_FIELDS)
                    or quote.fetched_at is None or quote.recorded_at is None
                    or not quote.fetched_at <= quote.recorded_at <= now):
                continue
            age = quote_age(quote, now)
            if age is None and quote.provider_updated_at is not None:
                continue
            state = "current" if age is not None and age <= max_age else "stale" if age is not None else "unverified"
            prices.append((state, quote, age))
        if not prices:
            continue
        order = {"current": 2, "stale": 1, "unverified": 0}
        state, quote, age = max(prices, key=lambda item: (order[item[0]], item[1].price, item[1].recorded_at, item[1].bookmaker))
        ev = probability * quote.price - 1 if state == "current" else None
        details = diagnostics.get(row.market, {})
        flags = list(details.get("reasons", ()))
        if quote.price > 1.80 and "cuota_fuera_de_banda" not in flags:
            flags.append("cuota_fuera_de_banda")
        if probability < .65 and "probabilidad_inferior_al_65" not in flags:
            flags.append("probabilidad_inferior_al_65")
        disagreement = "ventaja_extrema_frente_al_mercado" in flags
        source_quality = min(len({price.bookmaker for status, price, _ in prices if status == state})
                             / settings.market_quality_medium_min_bookmakers, 1)
        score = ranking_score(probability, ev or 0, evidence.value if evidence else 0,
                              (row.data_quality + source_quality) / 2,
                              max(0, 1 - age / max_age) if age is not None else 0,
                              1 - (high - low) if interval else 0)
        priority = (order[state], not disagreement, calibrated is not None or supported, score, row.market)
        candidates.append((priority, {
            "market": row.market,
            "odds": quote.price, "bookmaker": quote.bookmaker, "quote_status": state,
            "quote_age_minutes": age, "ev": ev,
            "strict_reasons": flags,
            "probability": probability,
            "probability_source": "calibrated" if calibrated is not None else "original",
            "calibration_source": getattr(row, "calibration_source", None) if calibrated is not None else None,
            "model_version_id": getattr(row, "model_version_id", model_version_id),
            "generated_at": getattr(row, "created_at", now).isoformat() + "Z",
            "observations": evidence.n_observations if evidence else None,
            "evidence_source": evidence.source_level if evidence else None,
            "interval": [low, high] if interval else None,
            "limited_evidence": not (supported and calibrated is not None and interval),
        }))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def build_today_previews(db, fixtures, now):
    if not fixtures:
        return {}
    groups = defaultdict(list)
    for match in fixtures:
        groups[match.competition.code].append(match)
    domestic_ids = [match.id for match in fixtures if match.competition.code not in API_CANDIDATE_COMPETITIONS]
    domestic = defaultdict(dict)
    if domestic_ids:
        saved = (db.query(Prediction).join(ModelVersion)
                 .filter(Prediction.match_id.in_(domestic_ids), Prediction.created_at <= now,
                         ModelVersion.created_at <= now, ModelVersion.is_active.isnot(False))
                 .order_by(Prediction.created_at.desc(), Prediction.id.desc()))
        for row in saved:
            domestic[row.match_id].setdefault(row.market, row)
    active_competitions = {
        tag.split(":", 1)[0] for (tag,) in db.query(ModelVersion.dataset_version)
        .filter(ModelVersion.created_at <= now, ModelVersion.is_active.isnot(False)) if tag
    }
    reliability = market_track_records_with_provenance(db, as_of=now)
    result = {}
    for code, matches in groups.items():
        version_id, technical_failure = None, False
        if code in API_CANDIDATE_COMPETITIONS:
            try:
                if code == CHAMPIONS_LEAGUE:
                    version, rows, _ = score_champions_candidate(db, matches, now, preview=True)
                else:
                    version, rows, _ = score_api_candidate(db, matches, now, code, preview=True)
                version_id = version.id if version else None
            except Exception:
                logger.exception("today_preview.champions_failed")
                rows, technical_failure = [], True
        else:
            rows = [row for match in matches for row in domestic[match.id].values()]
        picks, diagnostics = rank_champions_preview(matches, rows, version_id, now, reliability)
        lower, _ = rank_champions_preview(matches, rows, version_id, now, reliability, minimum=.60)
        wider, _ = rank_champions_preview(matches, rows, version_id, now, reliability, band=(1.25, 1.90))
        extended, _ = rank_champions_preview(matches, rows, version_id, now, reliability, band=(1.30, 1.90))
        updated, _ = rank_champions_preview(matches, rows, version_id, now, reliability, fresh_only=True)
        market_rows, market_diagnostics = defaultdict(list), defaultdict(dict)
        for row in rows:
            market_rows[row.match_id].append(row)
        for (match_id, market), details in diagnostics.items():
            market_diagnostics[match_id][market] = details
        for match in matches:
            match_rows = market_rows[match.id]
            details = market_diagnostics[match.id]
            checks = list(details.values())
            info = informative_pick(match_rows, code, reliability, version_id, now, odds=match.odds, diagnostics=details)
            failure = None
            if info is None:
                has_model = version_id is not None if code in API_CANDIDATE_COMPETITIONS else code in active_competitions
                failure = ("error_tecnico" if technical_failure else
                           "sin_candidato_informativo" if match_rows else
                           "sin_prediccion_disponible" if has_model else "sin_modelo_valido")
            result[match.id] = {
                "forecast": info, "opportunity": picks.get(match.id), "forecast_unavailable_reason": failure,
                "counts": {
                    "evaluated": len(checks),
                    "with_price": sum("sin_mercado" not in c["reasons"] for c in checks),
                    "minimum_probability": sum(bool(valid_probability(c["probability"]) and c["probability"] >= .65) for c in checks),
                    "price_in_band": sum(c["quote_in_band"] for c in checks),
                    "current_price_in_band": sum(c["quote_current"] for c in checks),
                    "positive_ev": sum(bool(c["ev"] is not None and c["ev"] > 0) for c in checks),
                    "positive_ev_current_price": sum(bool(c["quote_current"] and c["ev"] is not None and c["ev"] > 0) for c in checks),
                    "local_evidence": sum(c["evidence_sufficient"] for c in checks),
                    "eligible": sum(not c["reasons"] for c in checks),
                },
                "markets": details,
                "sensitivity": {"current": picks.get(match.id), "minimum_60": lower.get(match.id),
                                "wider_band": wider.get(match.id), "extended_band": extended.get(match.id), "current_verified_quotes": updated.get(match.id),
                                "informative": info},
            }
    return result
