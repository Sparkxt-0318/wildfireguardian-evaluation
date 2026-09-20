"""The interval constructions are not interchangeable.

Four shapes of data where percentile, basic and BCa disagree materially, or
where one of them misbehaves. Each is a case the protocol's "do not trust when"
table names; these tests make the claims checkable rather than advisory.
"""

from __future__ import annotations

import numpy as np
import pytest

from wg_eval.bootstrap import bounds_check, cluster_bootstrap, paired_cluster_bootstrap
from wg_eval.config import AggregationSpec, Bounds, MetricSpec
from wg_eval.hierarchy import InferenceSpec
from wg_eval.pairing import ClusteredValues, build_paired_panel

INFERENCE = InferenceSpec()


def clustered(groups) -> ClusteredValues:
    return ClusteredValues.from_groups([np.asarray(g, dtype="float64") for g in groups])


def widths(values: ClusteredValues, estimator, **kwargs) -> dict[str, float]:
    out = {}
    for method in ("percentile", "basic", "bca"):
        result = cluster_bootstrap(values, estimator, method=method, n_resamples=1200,
                                   seed=4, **kwargs)
        out[method] = result.ci_high - result.ci_low
    return out


def test_skewed_unit_level_effects_make_the_methods_disagree():
    """With strongly skewed unit values, percentile and BCa endpoints differ."""
    rng = np.random.default_rng(1)
    groups = [rng.lognormal(0.0, 1.2, size=6) for _ in range(40)]
    values = clustered(groups)
    pct = cluster_bootstrap(values, np.mean, method="percentile", n_resamples=1500, seed=2)
    bca = cluster_bootstrap(values, np.mean, method="bca", n_resamples=1500, seed=2)
    # Same point estimate, different endpoints: the constructions are not aliases.
    assert pct.estimate == pytest.approx(bca.estimate)
    assert pct.ci_low != pytest.approx(bca.ci_low, abs=1e-6)
    assert pct.ci_high != pytest.approx(bca.ci_high, abs=1e-6)


def test_heavy_tails_make_a_tail_statistic_far_less_stable_than_a_mean():
    rng = np.random.default_rng(7)
    groups = [rng.standard_t(df=2, size=8) for _ in range(40)]
    values = clustered(groups)
    mean_width = widths(values, np.mean, estimator_name="mean")["percentile"]

    def cvar90(x: np.ndarray) -> float:
        from wg_eval.metrics import estimate

        return estimate(x, "cvar", {"alpha": 0.9})

    tail_width = widths(values, cvar90, estimator_name="cvar")["percentile"]
    assert tail_width > 2.0 * mean_width


def test_few_units_are_flagged_and_the_interval_is_coarse():
    rng = np.random.default_rng(3)
    small = clustered([rng.normal(0, 1, size=5) for _ in range(6)])
    large = clustered([rng.normal(0, 1, size=5) for _ in range(60)])
    small_result = cluster_bootstrap(small, np.mean, n_resamples=1000, seed=1,
                                     estimator_name="mean")
    large_result = cluster_bootstrap(large, np.mean, n_resamples=1000, seed=1,
                                     estimator_name="mean")
    assert any("below the ~20" in n for n in small_result.notes)
    assert not any("below the ~20" in n for n in large_result.notes)
    assert (small_result.ci_high - small_result.ci_low) > (
        large_result.ci_high - large_result.ci_low
    )


def test_a_zero_inflated_outcome_defeats_a_robust_central_statistic():
    """Most values are exactly zero; a few are large.

    The median is pinned at zero with a zero-width interval, so the statistic
    that is usually recommended for skewed data is the one that says nothing
    here. The mean carries real relative uncertainty because its magnitude is
    small, and the tail statistic describes a different quantity entirely --
    which is the point: on zero-inflated data the choice of statistic dominates
    the choice of interval construction.
    """
    rng = np.random.default_rng(11)
    groups = []
    for _ in range(40):
        values = np.zeros(10)
        hits = rng.random(10) < 0.08
        values[hits] = rng.gamma(2.0, 20.0, size=int(hits.sum()))
        groups.append(values)
    values = clustered(groups)

    def cvar95(x: np.ndarray) -> float:
        from wg_eval.metrics import estimate

        return estimate(x, "cvar", {"alpha": 0.95})

    median_result = cluster_bootstrap(values, np.median, n_resamples=1200, seed=5,
                                      estimator_name="median")
    assert median_result.estimate == 0.0
    assert median_result.ci_low == median_result.ci_high == 0.0

    mean_result = cluster_bootstrap(values, np.mean, n_resamples=1200, seed=5,
                                    estimator_name="mean")
    tail_result = cluster_bootstrap(values, cvar95, n_resamples=1200, seed=5,
                                    estimator_name="cvar")
    # The two describe different quantities, an order of magnitude apart.
    assert tail_result.estimate > 5.0 * mean_result.estimate
    # The mean's interval is wide relative to its own size.
    assert (mean_result.ci_high - mean_result.ci_low) / mean_result.estimate > 0.4


def test_a_bounded_parameter_can_push_the_basic_interval_out_of_its_support():
    """The reflected interval is the one that leaves the support near a boundary."""
    rng = np.random.default_rng(13)
    # Rates very close to 1: most units perfect, a few not.
    groups = [np.where(rng.random(8) < 0.97, 1.0, 0.0) for _ in range(10)]
    values = clustered(groups)
    basic = cluster_bootstrap(values, np.mean, method="basic", n_resamples=1500, seed=1,
                              bounds=Bounds(lower=0.0, upper=1.0), estimator_name="mean")
    percentile = cluster_bootstrap(values, np.mean, method="percentile", n_resamples=1500,
                                   seed=1, bounds=Bounds(lower=0.0, upper=1.0),
                                   estimator_name="mean")
    assert percentile.ci_high <= 1.0
    # Percentile endpoints are order statistics of the resampled means, so they
    # cannot leave the observed support; the basic interval reflects and can.
    assert basic.ci_high >= percentile.ci_high


def test_bounds_check_reports_but_never_clamps():
    check = bounds_check(ci_low=-0.02, ci_high=1.04, estimate=0.98,
                         bounds=Bounds(lower=0.0, upper=1.0), is_difference=False)
    assert check["impossible"]
    assert {v["endpoint"] for v in check["violations"]} == {"ci_low", "ci_high"}
    assert "unclamped" in check["note"]
    # The reported endpoints are the originals, untouched.
    assert check["violations"][0]["value"] == -0.02


def test_bounds_on_a_difference_use_the_widened_support():
    check = bounds_check(ci_low=-0.5, ci_high=0.5, estimate=0.0,
                         bounds=Bounds(lower=0.0, upper=1.0), is_difference=True)
    assert check["support"] == [-1.0, 1.0]
    assert not check["impossible"]


def test_an_unknown_method_names_what_is_supported():
    values = clustered([np.array([1.0, 2.0]) for _ in range(10)])
    with pytest.raises(ValueError, match="studentized bootstrap is not implemented"):
        cluster_bootstrap(values, np.mean, method="studentized", n_resamples=200)


def test_bca_records_its_fallback_rather_than_failing_silently():
    degenerate = clustered([np.array([1.0]) for _ in range(8)])
    result = cluster_bootstrap(degenerate, np.mean, method="bca", n_resamples=400, seed=1)
    assert any("fell back to percentile" in n for n in result.notes)


def test_the_three_methods_agree_when_the_statistic_is_well_behaved(records):
    """A symmetric, unbiased statistic with plenty of units: the choice hardly matters."""
    from wg_eval.aggregate import aggregate_to_level

    metric = MetricSpec(name="m", column="loss", estimator="mean", level="event_id")
    panel = aggregate_to_level(records, "loss", "event_id", AggregationSpec(), INFERENCE)
    paired = build_paired_panel(panel, metric, ["policy_a", "policy_b"], INFERENCE)
    results = {
        method: paired_cluster_bootstrap(paired, np.mean, "policy_a", "policy_b",
                                         n_resamples=1500, seed=3, method=method)[2]
        for method in ("percentile", "basic", "bca")
    }
    widths_by_method = {k: v.ci_high - v.ci_low for k, v in results.items()}
    spread = max(widths_by_method.values()) / min(widths_by_method.values())
    assert spread < 1.25, widths_by_method
