"""End-to-end comparison, stratification and failure analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wg_eval.compare import compare_policies
from wg_eval.config import ConfigError, config_from_mapping
from wg_eval.hierarchy import InferenceStructureError
from wg_eval.failures import summarize_failures
from wg_eval.stratify import (
    allocation_balance,
    allocation_ledger,
    allocation_table,
    compare_by_stratum,
    stratified_estimate,
    stratum_redundancy,
    unit_stratum_map,
)
from wg_eval.synth.generators import (
    DEFAULT_STRATA,
    PolicyBehaviour,
    WorldModel,
    generate_experiment,
    mark_status,
)


def test_comparison_recovers_the_true_effect(records, config):
    result = compare_policies(records, config, metrics=["mean_loss"])
    c = result.get("mean_loss", "policy_b")
    # The generator's true shift is +0.8.
    assert c.difference.ci_low <= 0.8 <= c.difference.ci_high
    assert c.difference.n_clusters == records["world_id"].nunique()
    assert c.verdict.label in {"superior", "worse", "statistically_equivalent",
                               "inconclusive", "statistically_different_within_margin"}


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
    assert payload["unit_of_inference"] == "world_id"
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


def test_metric_coarser_than_the_resampling_unit_is_refused(config_mapping):
    """A world-level value cannot be assigned to one of several events."""
    bad = {
        **config_mapping,
        "inference": {"primary_unit": "event_id", "nested_units": ["resident_id"]},
        "metrics": [{"name": "world_loss", "column": "loss", "level": "world_id"}],
        "equivalence": {},
    }
    with pytest.raises(ConfigError, match="not a declared inference level"):
        config_from_mapping(bad)


def test_declaring_a_nested_level_as_independent_is_refused(config_mapping, records):
    """Events nest inside units here, so events are not replicates."""
    cfg = config_from_mapping(
        {
            **config_mapping,
            "inference": {"primary_unit": "event_id", "nested_units": ["resident_id"]},
            "metrics": [{"name": "mean_loss", "column": "loss", "level": "event_id",
                         "role": "primary"}],
            "aggregation": {"resident_id->event_id": {"loss": "mean"}},
            "equivalence": {},
        }
    )
    with pytest.raises(InferenceStructureError, match="coarser grouping"):
        compare_policies(records, cfg, validate=False)


def test_event_level_inference_is_accepted_when_events_contain_units(config_mapping):
    """The same declaration is correct when the records really are shaped that way."""
    frame = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.5)],
        WorldModel(n_units=40, events_per_unit=1, observations_per_event=6,
                   units_per_shared_event=4, shared_event_sd=2.0),
        seed=3,
    )
    cfg = config_from_mapping(
        {
            **config_mapping,
            "inference": {"primary_unit": "event_id", "nested_units": ["world_id", "resident_id"]},
            "metrics": [{"name": "mean_loss", "column": "loss", "level": "world_id",
                         "role": "primary"}],
            "aggregation": {"resident_id->world_id": {"loss": "mean"},
                            "world_id->event_id": {"loss": "mean"}},
            "equivalence": {},
            "strata": [],
        }
    )
    result = compare_policies(frame, cfg, validate=False)
    c = result.get("mean_loss", "policy_b")
    assert c.difference.cluster_level == "event_id"
    assert c.difference.n_clusters == frame["event_id"].nunique()


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
                            event_catastrophe_prob=0.0, catastrophe_magnitude=60.0, interaction_sd=0.6),
            PolicyBehaviour("policy_b", interaction_sd=0.6),
        ],
        WorldModel(n_units=50, events_per_unit=4, observations_per_event=15, unit_sd=2.5),
        seed=20260919,
    )
    cfg = config_from_mapping(
        {
            "unit_of_inference": "world",
            "metrics": [
                {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
                 "role": "primary"},
                {"name": "cvar90_loss", "column": "loss", "estimator": "cvar",
                 "level": "event_id", "params": {"alpha": 0.9}},
            ],
            "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
            "bootstrap": {"n_resamples": 800, "seed": 3},
            "aggregation": {"resident_id->event_id": {"loss": "mean"}},
        }
    )
    result = compare_policies(frame, cfg, validate=False)
    kinds = {d["kind"] for d in result.disagreements}
    assert "central_vs_tail" in kinds
    assert result.get("mean_loss", "policy_b").verdict.favours == "policy_a"
    assert result.get("cvar90_loss", "policy_b").verdict.favours == "policy_b"


def test_unit_stratum_map_assigns_one_stratum_per_world(records):
    mapping = unit_stratum_map(records, "difficulty")
    assert len(mapping) == records["world_id"].nunique()
    assert mapping["world_id"].is_unique


def test_unit_stratum_map_uses_the_modal_value(records):
    noisy = records.copy()
    first = noisy["world_id"].iloc[0]
    idx = noisy.index[noisy["world_id"] == first][:1]
    noisy.loc[idx, "difficulty"] = "moon"
    mapping = unit_stratum_map(noisy, "difficulty")
    assert mapping.loc[mapping["world_id"] == first, "stratum_value"].iloc[0] != "moon"


def test_allocation_table_and_balance_on_a_balanced_design(records):
    table = allocation_table(records, "difficulty")
    assert set(table["policy_id"]) == {"policy_a", "policy_b"}
    balance = allocation_balance(records, "difficulty")
    assert balance["checked"]
    assert balance["max_share_gap"] == pytest.approx(0.0, abs=1e-9)


def test_allocation_balance_detects_a_confounded_design():
    n = 40
    easy = [i for i in range(n) if i % 2 == 0]
    hard = [i for i in range(n) if i % 2 == 1]
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", unit_subset=easy + hard[:4]),
            PolicyBehaviour("policy_b", loss_shift=-1.5, unit_subset=hard + easy[:4]),
        ],
        WorldModel(
            n_units=n, events_per_unit=2, observations_per_event=10, unit_sd=1.0,
            strata={"difficulty": ["low", "high"]},
            stratum_effects={"difficulty": {"low": -4.0, "high": 4.0}},
        ),
        seed=5,
    )
    balance = allocation_balance(frame, "difficulty")
    assert balance["max_share_gap"] > 0.5


def test_stratified_comparison_reports_per_stratum_and_skips_thin_strata(records, config):
    strat = compare_by_stratum(records, config, "difficulty", min_units=5)
    assert set(strat.per_stratum) == {"low", "high"}
    table = strat.summary_table()
    assert "__all__" in set(table["difficulty"])
    tiny = compare_by_stratum(records, config, "difficulty", min_units=999, include_overall=False)
    assert not tiny.per_stratum
    assert all("min_units" in reason for reason in tiny.skipped.values())


def test_stratified_estimate_weights_by_shared_clusters(records, config):
    strat = compare_by_stratum(records, config, "difficulty", min_units=5, include_overall=False)
    out = stratified_estimate(strat, "mean_loss", "policy_b")
    assert out["available"]
    assert np.isfinite(out["weighted_difference"])
    with pytest.raises(KeyError):
        stratified_estimate(strat, "mean_loss", "policy_b", weights={"low": 1.0})


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


def test_allocation_ledger_accounts_for_everything_each_policy_was_given(records, config):
    ledger = allocation_ledger(records, config, columns=["difficulty"])
    assert ledger["unit"] == "world_id"
    assert ledger["n_units_shared"] == records["world_id"].nunique()
    per_policy = {r["policy_id"]: r for r in ledger["per_policy"]}
    assert set(per_policy) == {"policy_a", "policy_b"}
    for row in per_policy.values():
        assert {"n_units", "n_shared_units", "n_unique_units", "n_rows"} <= set(row)
        assert row["n_event_id"] > 0
    block = ledger["strata"]["difficulty"]
    assert set(block["target_composition_all_units"]) == {"low", "high"}
    # Allocation is balanced here, so the shared-unit mix matches the full mix.
    assert all(abs(v) < 1e-9 for v in block["composition_shift"].values())
    assert "not corrected" in ledger["note"]


def test_allocation_ledger_counts_run_statuses(records, config):
    crashed_units = sorted(records["world_id"].unique())[:4]
    holed = mark_status(records, policy="policy_b", units=crashed_units, status="crashed")
    ledger = allocation_ledger(holed, config)
    assert ledger["run_status_counts"]["policy_b"]["crashed"] > 0
    assert "crashed" not in ledger["run_status_counts"].get("policy_a", {})


def test_identical_strata_are_reported_as_one_dimension(records):
    doubled = records.assign(difficulty_copy=records["difficulty"])
    findings = stratum_redundancy(doubled, ["difficulty", "difficulty_copy"])
    assert any(f["code"] == "identical_strata" for f in findings)


def test_deterministically_nested_strata_are_reported(records):
    fine = records["difficulty"].astype(str) + "_" + records["scale"].astype(str)
    nested = records.assign(fine_grained=fine)
    findings = stratum_redundancy(nested, ["difficulty", "fine_grained"])
    nesting = [f for f in findings if f["code"] == "nested_strata"]
    assert nesting and nesting[0]["detail"]["coarse"] == "difficulty"


def test_a_stratum_confounded_with_policy_is_reported():
    """Each stratum level ran under one policy only, so no contrast exists inside it."""
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", unit_subset=list(range(0, 24, 2))),
            PolicyBehaviour("policy_b", unit_subset=list(range(1, 24, 2))),
        ],
        WorldModel(n_units=24, events_per_unit=1, observations_per_event=4,
                   strata={"difficulty": ["low", "high"]}),
        seed=7,
    )
    findings = stratum_redundancy(frame, ["difficulty"])
    confounded = [f for f in findings if f["code"] == "stratum_confounded_with_policy"]
    assert len(confounded) == 2
    assert {f["detail"]["level"] for f in confounded} == {"low", "high"}


def test_single_level_stratum_is_reported(records):
    flat = records.assign(constant="same")
    findings = stratum_redundancy(flat, ["constant"])
    assert any(f["code"] == "single_level_stratum" for f in findings)


def test_default_strata_are_domain_neutral():
    assert set(DEFAULT_STRATA) == {"difficulty", "scale", "regime", "capacity"}
