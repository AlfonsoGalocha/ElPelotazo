import datetime as dt
from types import SimpleNamespace

import pandas as pd

from backend.app.db.models import standings  # noqa: F401

from backend.app.services.continuous_learning import _scores, closed_round_results
from backend.app.services.model_service import rolling_training_split


def test_window_advances_and_dispatches_equal_kickoffs_together():
    table = pd.DataFrame({"match_id": range(130), "date": pd.date_range("2026-01-01", periods=130)})
    base, holdout = rolling_training_split(table)
    next_table = pd.concat([table, pd.DataFrame({"match_id": [130, 131], "date": [pd.Timestamp("2026-06-01")] * 2})])
    new_base, new_holdout = rolling_training_split(next_table)
    assert base["date"].max() < holdout["date"].min()
    assert new_base["date"].max() < new_holdout["date"].min()
    assert set(new_base.match_id) > set(base.match_id)
    assert set(new_base.match_id) - set(base.match_id) <= set(holdout.match_id)
    assert set(new_holdout.match_id) >= {130, 131}

    tied = table.copy()
    boundary = holdout["date"].min()
    tied.loc[base.index[-1], "date"] = boundary
    tied_base, tied_holdout = rolling_training_split(tied)
    assert tied_base["date"].max() < tied_holdout["date"].min()


def test_closed_round_excludes_in_progress_and_cancelled_labels():
    def fixture(id, round, status, goals=0):
        return SimpleNamespace(id=id, round_name=None, matchday=round, season_id=1,
                               status=status, home_goals=goals, away_goals=0)

    assert closed_round_results([
        fixture(1, 1, "finished"), fixture(2, 1, "cancelled"),
        fixture(3, 2, "finished"), fixture(4, 2, "scheduled"),
        fixture(5, 3, "finished", None),
    ]) == {1}


def test_paired_metrics_keep_binary_and_multiclass_separate():
    binary = [(1, (1, "1"), .5, .8, True), (2, (1, "2"), .5, .2, False), (3, (1, "3"), .5, .4, True)]
    result = _scores(binary)
    assert result["matches"] == 3 and result["rounds"] == 3
    assert result["delta"]["brier"] < 0 and result["delta"]["log_loss"] < 0
    assert result["delta"]["brier_interval"] is not None
    multi = _scores([(1, (1, "1"), [.4, .3, .3], [.5, .3, .2], [True, False, False])])
    assert multi["candidate"]["brier"] == .38
    assert multi["delta"]["brier_interval"] is None


def test_real_rolling_fit_dedup_and_prospective_capture(db_session, monkeypatch):
    from backend.app.db.models.core import Competition
    from backend.app.db.models.matches import Match
    from backend.app.db.models.modeling import ModelVersion
    from backend.app.db.models.publication import ModelComparisonCut
    from backend.app.normalization.competitions import COMPETITIONS
    from backend.app.services import continuous_learning as learning
    from backend.app.services.data_service import ingest_matches
    from backend.app.services.model_service import load_model_artifact, train_competition_models
    from backend.tests.fixtures.synthetic import generate_synthetic_matches
    from backend.tests.unit.test_multinomial_classifier import _FakeProvider

    db = db_session
    code = "continuous-fit-test"
    monkeypatch.setitem(COMPETITIONS, code, ("Learning test", "Test"))
    monkeypatch.setattr(learning, "COVERED", (code,))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=4, seed=82)
    provider = _FakeProvider(synthetic)
    for season in synthetic["season_label"].unique():
        ingest_matches(db, provider, code, season)
    competition = db.query(Competition).filter_by(code=code).one()
    matches = db.query(Match).filter_by(competition_id=competition.id).order_by(Match.kickoff_utc, Match.id).all()
    for index, match in enumerate(matches):
        match.matchday = index + 1
    db.flush()
    old = db.get(ModelVersion, train_competition_models(db, code)["model_version_id"])
    old.is_active = True
    db.commit()

    first = learning.train_due(db, (code,))[0]
    assert first["status"] == "trained"
    assert learning.train_due(db, (code,)) == []
    first_version = db.get(ModelVersion, first["model_version_id"])
    first_artifact = load_model_artifact(first_version)
    base = set(first_artifact["training_manifest"]["base_fit_match_ids"])
    holdout = set(first_artifact["training_manifest"]["holdout_match_ids"])

    previous_match = matches[-1]
    fixtures = []
    for index in range(8):
        new = Match(provider="test", provider_id=f"rolling-late-{index}", competition_id=competition.id,
                    season_id=previous_match.season_id, matchday=1000 + index,
                    kickoff_utc=previous_match.kickoff_utc + dt.timedelta(days=index + 1),
                    home_team_id=previous_match.home_team_id, away_team_id=previous_match.away_team_id,
                    status="finished", home_goals=2, away_goals=index % 3)
        db.add(new)
        fixtures.append(new)
    db.commit()
    second = learning.train_due(db, (code,))[0]
    second_version = db.get(ModelVersion, second["model_version_id"])
    manifest = second_version.hyperparameters["learning_manifest"]
    new_ids = set(manifest["new_base_fit_match_ids"])
    assert new_ids and new_ids <= holdout and not new_ids & base
    assert {match.id for match in fixtures} <= set(manifest["closed_round_result_ids"])
    assert second_version.is_active is False and old.is_active is True
    assert manifest["dataset_sha256"] != first_version.hyperparameters["learning_manifest"]["dataset_sha256"]
    assert learning.train_due(db, (code,)) == []

    cutoff = learning.utcnow()
    upcoming = Match(provider="test", provider_id="rolling-prospective", competition_id=competition.id,
                     season_id=previous_match.season_id, matchday=1010,
                     kickoff_utc=cutoff + dt.timedelta(minutes=20),
                     home_team_id=previous_match.home_team_id, away_team_id=previous_match.away_team_id,
                     status="scheduled")
    db.add(upcoming)
    db.flush()
    assert learning.capture_comparisons(db, cutoff=cutoff, clock=lambda: cutoff) == 1
    assert learning.capture_comparisons(db, cutoff=cutoff, clock=lambda: cutoff) == 0
    cut = db.get(ModelComparisonCut, upcoming.id)
    assert [row["id"] for row in cut.payload["models"]] == [old.id, second_version.id]
    assert all(len(row["sha256"]) == 64 for row in cut.payload["models"])
    assert cut.captured_at < upcoming.kickoff_utc
    assert cut.payload["feature_row"]["home_goals"] is None
    upcoming.status, upcoming.home_goals, upcoming.away_goals = "finished", 2, 0
    db.flush()
    before, = learning.comparison_report(db, as_of=cutoff)
    assert before["metrics"] == {}
    report, = learning.comparison_report(db, as_of=cutoff + dt.timedelta(hours=3))
    assert report["metrics"]["btts"]["matches"] == 1
    assert report["promotion_status"] == "blocked_uncertified_provenance"


def test_pinned_corners_freeze_with_no_rolling_pair_and_no_market(db_session, monkeypatch):
    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match, MatchStatistics
    from backend.app.db.models.publication import ModelComparisonCut
    from backend.app.services import continuous_learning as learning

    db = db_session
    cutoff = dt.datetime(2026, 12, 1, 10)
    competition = Competition(code="afc_champions_league", name="AFC test", country="AFC")
    home, away = Team(canonical_name="Corner test home"), Team(canonical_name="Corner test away")
    season = Season(competition=competition, label="2026/27")
    db.add_all([season, home, away])
    db.flush()
    fixture = Match(provider="test", provider_id="pinned-corners", competition_id=competition.id,
                    season_id=season.id, home_team_id=home.id, away_team_id=away.id,
                    status="scheduled", kickoff_utc=cutoff + dt.timedelta(minutes=20))
    db.add(fixture)
    db.flush()

    raw = pd.DataFrame({"match_id": [fixture.id], "date": [fixture.kickoff_utc], "status": ["scheduled"]})
    probabilities = {
        "corners_over_8_5": (.70, .65), "corners_over_9_5": (.60, .55),
        "corners_over_10_5": (.50, .45), "corners_under_9_5": (.40, .45),
    }
    monkeypatch.setattr(learning, "load_matches_dataframe", lambda *_: raw)
    monkeypatch.setattr(learning, "build_match_feature_table", lambda rows: rows)
    monkeypatch.setattr(learning, "tracked_artifact", lambda *_: {"corners_model": object()})
    monkeypatch.setattr(learning, "corner_probabilities",
                        lambda _, artifact: {fixture.id: probabilities} if artifact else {})

    assert learning.capture_comparisons(db, cutoff=cutoff, clock=lambda: fixture.kickoff_utc) == 0
    assert learning.capture_comparisons(db, cutoff=cutoff, clock=lambda: cutoff) == 1
    probabilities["corners_over_8_5"] = (.10, .10)
    assert learning.capture_comparisons(db, cutoff=cutoff, clock=lambda: cutoff) == 0
    cut = db.get(ModelComparisonCut, fixture.id)
    tracking = cut.payload["corner_tracking"]
    assert tracking["candidate"]["source_id"] == 264
    assert tracking["rows"][0]["candidate"] == .65
    assert all(row["reference"] is None for row in tracking["rows"])

    fixture.status = "finished"
    db.add(MatchStatistics(match_id=fixture.id, home_corners=5, away_corners=5))
    db.flush()
    report, = learning.comparison_report(db, as_of=cutoff + dt.timedelta(hours=3))
    track = report["corner_tracking"]
    assert track["captured"] == track["settled"] == 1
    assert track["coherence"]["candidate"] == {"checked": 1, "monotonicity": 0, "complement": 0}
    assert track["coverage"]["corners_over_8_5"] == dict(candidate=1, poisson=1, titular=0, reference=0)
    assert set(track["metrics"]["corners_over_8_5"]["comparisons"]) == {"poisson"}
