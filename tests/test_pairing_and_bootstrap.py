"""Pairing, cluster resampling, and the pseudoreplication guard."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wg_eval.aggregate import aggregate_to_level
from wg_eval.bootstrap import (
    cluster_bootstrap,
    design_effect,
    naive_iid_bootstrap,
    paired_cluster_bootstrap,
)
from wg_eval.config import AggregationSpec, MetricSpec
from wg_eval.hierarchy import InferenceSpec
from wg_eval.pairing import (
    ClusteredValues,
    build_paired_panel,
    imbalance_report,
    per_cluster_statistics,
    single_policy_values,
)

METRIC = MetricSpec(name="mean_loss", column="loss", estimator="mean", level="event_id")
INFERENCE = InferenceSpec()


def panel_of(records: pd.DataFrame) -> pd.DataFrame:
    return aggregate_to_level(records, "loss", "event_id", AggregationSpec(), INFERENCE)


def test_clustered_values_take_preserves_whole_clusters():
    groups = [np.array([1.0, 2.0]), np.array([10.0]), np.array([100.0, 200.0, 300.0])]
    cv = ClusteredValues.from_groups(groups)
    assert cv.n_clusters == 3
    assert sorted(cv.take(np.array([0, 2])).tolist()) == [1.0, 2.0, 100.0, 200.0, 300.0]
    # A repeated cluster brings all of its members again.
    assert cv.take(np.array([1, 1, 1])).tolist() == [10.0, 10.0, 10.0]
    assert cv.cluster_values(2).tolist() == [100.0, 200.0, 300.0]


def test_clustered_values_take_handles_empty_selection():
    cv = ClusteredValues.from_groups([np.array([1.0]), np.array([])])
    assert cv.take(np.array([1])).size == 0


def test_paired_panel_uses_only_shared_clusters(records):
    worlds = sorted(records["world_id"].unique())
    dropped = set(worlds[:5])
    partial = records[~((records["policy_id"] == "policy_b") & records["world_id"].isin(dropped))]
    panel = build_paired_panel(panel_of(partial), METRIC, ["policy_a", "policy_b"], INFERENCE)
    assert panel.n_clusters == len(worlds) - 5
    assert set(panel.dropped_clusters["policy_b"]) == dropped
    assert "observed under every compared policy" in panel.estimand_conditioning
    assert panel.paired
    assert any("not evaluated under every" in n for n in panel.notes)


def test_unpaired_panel_is_loudly_flagged(records):
    worlds = sorted(records["world_id"].unique())
    partial = records[~((records["policy_id"] == "policy_b") & records["world_id"].isin(worlds[:5]))]
    panel = build_paired_panel(
        panel_of(partial), METRIC, ["policy_a", "policy_b"], INFERENCE,
        require_common_clusters=False,
    )
    assert not panel.paired
    assert any("UNPAIRED" in n for n in panel.notes)
    assert panel.n_clusters == len(worlds)


def test_paired_panel_needs_a_shared_cluster(records):
    worlds = sorted(records["world_id"].unique())
    half = set(worlds[: len(worlds) // 2])
    disjoint = pd.concat(
        [
            records[(records["policy_id"] == "policy_a") & records["world_id"].isin(half)],
            records[(records["policy_id"] == "policy_b") & ~records["world_id"].isin(half)],
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="nothing to pair"):
        build_paired_panel(panel_of(disjoint), METRIC, ["policy_a", "policy_b"], INFERENCE)


def test_bootstrap_is_reproducible_from_the_seed(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    first = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b", n_resamples=300, seed=5)
    second = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b", n_resamples=300, seed=5)
    assert first[2].ci == second[2].ci
    third = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b", n_resamples=300, seed=6)
    assert third[2].ci != first[2].ci


def test_paired_difference_equals_the_arm_difference(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    base, cand, diff = paired_cluster_bootstrap(
        panel, np.mean, "policy_a", "policy_b", n_resamples=300, seed=3
    )
    assert diff.estimate == pytest.approx(cand.estimate - base.estimate)


def test_paired_interval_is_narrower_than_the_marginal_ones(records):
    """Pairing removes the shared world effect; that is the whole point of it."""
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    base, cand, diff = paired_cluster_bootstrap(
        panel, np.mean, "policy_a", "policy_b", n_resamples=600, seed=3
    )
    paired_width = diff.ci_high - diff.ci_low
    marginal_width = (base.ci_high - base.ci_low) + (cand.ci_high - cand.ci_low)
    assert paired_width < marginal_width


def test_observation_resampling_is_narrower_than_unit_resampling(records):
    """The pseudoreplication error, measured on one dataset."""
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    _, _, diff = paired_cluster_bootstrap(
        panel, np.mean, "policy_a", "policy_b", n_resamples=600, seed=3
    )
    resident = records[records["policy_id"] == "policy_a"]["loss"].to_numpy()
    naive = naive_iid_bootstrap(resident, np.mean, n_resamples=600, seed=3)
    assert (naive.ci_high - naive.ci_low) < (diff.ci_high - diff.ci_low)
    assert "PSEUDOREPLICATION" in naive.cluster_level
    assert any("must not be reported" in n for n in naive.notes)


def test_cluster_bootstrap_refuses_a_single_unit():
    cv = ClusteredValues.from_groups([np.array([1.0, 2.0, 3.0])])
    with pytest.raises(ValueError, match="at least 2"):
        cluster_bootstrap(cv, np.mean, n_resamples=200)


def test_bca_falls_back_rather_than_producing_nonsense():
    cv = ClusteredValues.from_groups([np.array([1.0])] * 6)
    result = cluster_bootstrap(cv, np.mean, n_resamples=200, seed=1, method="bca")
    assert result.estimate == 1.0
    assert any("fell back" in n for n in result.notes)


def test_bca_and_percentile_agree_roughly_on_a_symmetric_statistic(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    pct = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                   n_resamples=600, seed=3, method="percentile")[2]
    bca = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                   n_resamples=600, seed=3, method="bca")[2]
    width_pct = pct.ci_high - pct.ci_low
    width_bca = bca.ci_high - bca.ci_low
    assert 0.5 < width_bca / width_pct < 2.0


def test_basic_interval_is_the_reflected_percentile_interval(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    basic = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                     n_resamples=400, seed=3, method="basic")[2]
    pct = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                   n_resamples=400, seed=3, method="percentile")[2]
    assert basic.ci_low == pytest.approx(2 * pct.estimate - pct.ci_high)
    assert basic.ci_high == pytest.approx(2 * pct.estimate - pct.ci_low)


def test_unknown_bootstrap_method_raises(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    with pytest.raises(ValueError, match="unknown bootstrap method"):
        paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                 n_resamples=200, method="magic")


def test_design_effect_detects_clustering(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    deff = design_effect(panel, "policy_a")
    assert deff["available"]
    assert deff["design_effect"] >= 1.0
    assert deff["effective_sample_size"] <= deff["n_observations"]


def test_imbalance_report_flags_lopsided_clusters(records):
    thin = records.copy()
    first_world = sorted(thin["world_id"].unique())[0]
    mask = (thin["world_id"] == first_world) & (thin["policy_id"] == "policy_b")
    keep = thin.index[mask][:2]
    thin = thin.drop(index=[i for i in thin.index[mask] if i not in set(keep)])
    panel = build_paired_panel(panel_of(thin), METRIC, ["policy_a", "policy_b"], INFERENCE)
    report = imbalance_report(panel, max_imbalance=0.2)
    assert report["checked"]
    assert report["n_flagged_clusters"] >= 1


def test_per_cluster_statistics_shape(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    table = per_cluster_statistics(panel, np.mean)
    assert len(table) == panel.n_clusters
    assert {"policy_a", "policy_b", "world_id"} <= set(table.columns)


def test_single_policy_values_are_marginal(records):
    values, clusters = single_policy_values(panel_of(records), "policy_a", INFERENCE)
    assert values.n_clusters == len(clusters) == records["world_id"].nunique()
    with pytest.raises(ValueError, match="no usable values"):
        single_policy_values(panel_of(records), "policy_zzz", INFERENCE)


def test_unpaired_resample_is_wider_and_labelled(records):
    """comparison.paired: false must actually unpair the resample."""
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE)
    paired = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                      n_resamples=600, seed=3, paired=True)[2]
    unpaired = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                        n_resamples=600, seed=3, paired=False)[2]
    assert (unpaired.ci_high - unpaired.ci_low) > (paired.ci_high - paired.ci_low)
    assert any("UNPAIRED RESAMPLE" in n for n in unpaired.notes)
    assert not any("UNPAIRED RESAMPLE" in n for n in paired.notes)


def test_hierarchical_bootstrap_requires_a_level_below_the_unit(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE,
                               substructure=False)
    with pytest.raises(ValueError, match="no level below the resampling unit"):
        paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                 n_resamples=300, hierarchical=True)


def test_hierarchical_bootstrap_estimates_a_different_quantity(records):
    panel = build_paired_panel(panel_of(records), METRIC, ["policy_a", "policy_b"], INFERENCE,
                               substructure=True)
    one_stage = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                         n_resamples=600, seed=3)[2]
    two_stage = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                         n_resamples=600, seed=3, hierarchical=True)[2]
    assert two_stage.hierarchical and not one_stage.hierarchical
    assert (two_stage.ci_high - two_stage.ci_low) > (one_stage.ci_high - one_stage.ci_low)
    assert any("Two-stage bootstrap" in n for n in two_stage.notes)


def test_small_unit_counts_are_flagged_as_not_credible(records):
    worlds = sorted(records["world_id"].unique())[:8]
    small = records[records["world_id"].isin(worlds)]
    panel = build_paired_panel(panel_of(small), METRIC, ["policy_a", "policy_b"], INFERENCE)
    result = paired_cluster_bootstrap(panel, np.mean, "policy_a", "policy_b",
                                      n_resamples=400, seed=1, estimator_name="cvar")[2]
    assert not result.credibility["credible"]
    assert any("NOT CREDIBLE" in n for n in result.notes)
