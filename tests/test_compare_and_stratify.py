"""End-to-end comparison, stratification and failure analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wg_eval.compare import compare_policies
from wg_eval.config import config_from_mapping
from wg_eval.failures import summarize_failures
from wg_eval.stratify import (
    allocation_balance,
    allocation_table,
    compare_by_stratum,
    stratified_estimate,
    world_stratum_map,
)
from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment


def test_comparison_recovers_the_true_effect(records, config):
    result = compare_policies(records, config, metrics=["mean_loss"])
    c = result.get("mean_loss", "policy_b")
    # The generator's true shift is +0.8.
    assert c.difference.ci_low <= 0.8 <= c.difference.ci_high
    assert c.difference.n_clusters == records["world_id"].nunique()
    assert c.verdict.label in {"superior", "worse", "equivalent", "inconclusive",
                               "statistically_different_practically_equivalent"}


def test_comparison_is_deterministic_for_a_fixed_seed(records, config):
    first = compare_policies(records, config, metrics=["mean_loss"]).get("mean_loss", "policy_b")
    second = compare_policies(records, config, metrics=["mean_loss"]).get("mean_loss", "policy_b")
    assert first.difference.ci == second.difference.ci


def test_every_comparison_carries_provenance(records, config):
    result = compare_policies(records, config, metrics=["mean_loss"])
    prov = result.get("mean_loss", "policy_b").provenance
    payload = prov.as_dict()
    assert payload["bootstrap"]["seed"] == config.bootstrap.seed
    assert payload["confidence_level"] == config.bootstrap.confidence_level
    assert payload["metric"]["definition"]
    assert payload["unit_of_inference"] == "world"
    assert prov.fingerprint().startswith("sha256:")


def test_metrics_without_a_margin_say_so(records, config):
    result = compare_policies(records, config, metrics=["cvar90_loss"])
    c = result.get("cvar90_loss", "policy_b")
    assert c.equivalence is None
    assert any("No practical margin declared" in n for n in c.notes)


def test_non_inferiority_is_run_when_requested(records, config):
    result = compare_policies(records, config, metrics=["mean_loss"])
    c = result.get("mean_loss", "policy_b")
    assert c.non_inferiority_result is not None
    assert c.non_inferiority_result.kind == "non_inferiority"


def test_validation_errors_stop_the_comparison(records, config):
    doubled = pd.concat([records, records.iloc[:4]], ignore_index=True)
    with pytest.raises(ValueError, match="record validation failed"):
        compare_policies(doubled, config)


def test_metric_coarser_than_the_cluster_level_is_refused(config_mapping):
    bad = {
        **config_mapping,
        "unit_of_inference": "event",
        "metrics": [{"name": "world_loss", "column": "loss", "level": "world"}],
        "bootstrap": {**config_mapping["bootstrap"], "cluster_level": "event"},
        "equivalence": {},
    }
    cfg = config_from_mapping(bad)
    frame = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.5)],
        WorldModel(n_worlds=8, events_per_world=2, residents_per_event=5),
        seed=1,
    )
    with pytest.raises(ValueError, match="coarser than"):
        compare_policies(frame, cfg, validate=False)


def test_event_level_resampling_works_when_metrics_allow_it(config_mapping, records):
    cfg = config_from_mapping(
        {
            **config_mapping,
            "unit_of_inference": "event",
            "metrics": [{"name": "mean_loss", "column": "loss", "level": "event"}],
            "bootstrap": {**config_mapping["bootstrap"], "cluster_level": "event"},
            "equivalence": {},
        }
    )
    result = compare_policies(records, cfg, validate=False)
    c = result.get("mean_loss", "policy_b")
    assert c.difference.cluster_level == "event"
    assert c.difference.n_clusters == records["event_id"].nunique()


def test_unknown_baseline_is_reported_clearly(records, config_mapping):
    cfg = config_from_mapping(
        {**config_mapping, "comparison": {"baseline": "policy_zzz", "candidates": ["policy_b"]}}
    )
    with pytest.raises(ValueError, match="not present"):
        compare_policies(records, cfg, validate=False)


def test_tail_disagreement_is_detected():
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", loss_shift=-3.2, catastrophe_prob=0.04,
                            catastrophe_magnitude=60.0, interaction_sd=0.6),
            PolicyBehaviour("policy_b", interaction_sd=0.6),
        ],
        WorldModel(n_worlds=50, events_per_world=4, residents_per_event=15, world_sd=2.5),
        seed=20260919,
    )
    cfg = config_from_mapping(
        {
            "unit_of_inference": "world",
            "metrics": [
                {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event"},
                {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event",
                 "params": {"alpha": 0.9}},
            ],
            "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
            "bootstrap": {"n_resamples": 800, "seed": 3},
        }
    )
    result = compare_policies(frame, cfg, validate=False)
    kinds = {d["kind"] for d in result.disagreements}
    assert "central_vs_tail" in kinds
    assert result.get("mean_loss", "policy_b").verdict.favours == "policy_a"
    assert result.get("cvar90_loss", "policy_b").verdict.favours == "policy_b"


def test_world_stratum_map_assigns_one_stratum_per_world(records):
    mapping = world_stratum_map(records, "landscape")
    assert len(mapping) == records["world_id"].nunique()
    assert mapping["world_id"].is_unique


def test_world_stratum_map_uses_the_modal_value(records):
    noisy = records.copy()
    first = noisy["world_id"].iloc[0]
    idx = noisy.index[noisy["world_id"] == first][:1]
    noisy.loc[idx, "landscape"] = "moon"
    mapping = world_stratum_map(noisy, "landscape")
    assert mapping.loc[mapping["world_id"] == first, "stratum_value"].iloc[0] != "moon"


def test_allocation_table_and_balance_on_a_balanced_design(records):
    table = allocation_table(records, "landscape")
    assert set(table["policy_id"]) == {"policy_a", "policy_b"}
    balance = allocation_balance(records, "landscape")
    assert balance["checked"]
    assert balance["max_share_gap"] == pytest.approx(0.0, abs=1e-9)


def test_allocation_balance_detects_a_confounded_design():
    n = 40
    easy = [i for i in range(n) if i % 2 == 0]
    hard = [i for i in range(n) if i % 2 == 1]
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", world_subset=easy + hard[:4]),
            PolicyBehaviour("policy_b", loss_shift=-1.5, world_subset=hard + easy[:4]),
        ],
        WorldModel(
            n_worlds=n, events_per_world=2, residents_per_event=10, world_sd=1.0,
            strata={"landscape": ["flat", "steep"]},
            stratum_effects={"landscape": {"flat": -4.0, "steep": 4.0}},
        ),
        seed=5,
    )
    balance = allocation_balance(frame, "landscape")
    assert balance["max_share_gap"] > 0.5


def test_stratified_comparison_reports_per_stratum_and_skips_thin_strata(records, config):
    strat = compare_by_stratum(records, config, "landscape", min_clusters=5)
    assert set(strat.per_stratum) == {"flat", "steep"}
    table = strat.summary_table()
    assert "__all__" in set(table["landscape"])
    tiny = compare_by_stratum(records, config, "landscape", min_clusters=999, include_overall=False)
    assert not tiny.per_stratum
    assert all("min_clusters" in reason for reason in tiny.skipped.values())


def test_stratified_estimate_weights_by_shared_clusters(records, config):
    strat = compare_by_stratum(records, config, "landscape", min_clusters=5, include_overall=False)
    out = stratified_estimate(strat, "mean_loss", "policy_b")
    assert out["available"]
    assert np.isfinite(out["weighted_difference"])
    with pytest.raises(KeyError):
        stratified_estimate(strat, "mean_loss", "policy_b", weights={"flat": 1.0})


def test_failure_summary_buckets_unspecified_reasons(records):
    holed = records.copy()
    failures = holed.index[holed["mission_success"] == 0][:20]
    holed.loc[failures, "failure_reason"] = pd.NA
    summary = summarize_failures(holed, baseline="policy_a", candidate="policy_b")
    reasons = set(summary.table["failure_reason"])
    assert "unspecified" in reasons
    assert summary.table["count"].sum() == int((holed["mission_success"] == 0).sum())


def test_failure_shift_table_counts_only_failures(records):
    summary = summarize_failures(records, baseline="policy_a", candidate="policy_b")
    assert summary.shifts is not None
    # Successes are not a failure mode and must not appear as one.
    assert "unspecified" not in set(summary.shifts["failure_reason"])
    assert (summary.shifts["baseline_rate"] <= 1.0).all()
    # Per-observation failure-mode rates sum to the overall failure rate.
    total = float(summary.shifts["baseline_rate"].sum())
    expected = float(summary.totals.set_index("policy_id").loc["policy_a", "failure_rate"])
    assert total == pytest.approx(expected, abs=0.02)


def test_failure_summary_needs_the_column(records):
    with pytest.raises(KeyError):
        summarize_failures(records.drop(columns=["failure_reason"]))
