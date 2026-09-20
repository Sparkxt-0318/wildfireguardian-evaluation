"""Independent references for every core procedure.

Production code must not be its own oracle.  Each procedure here is checked
against one of two things the library did not compute:

* a value worked out by hand and written into the test as a literal, or
* an independent implementation (``scipy``, or a direct transcription of the
  textbook formula written here rather than imported).

Where a hand value is used, the arithmetic is spelled out in the docstring so a
reader can check it without running anything.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from wg_eval.aggregate import aggregate_to_level
from wg_eval.config import AggregationSpec, Margin, MetricSpec
from wg_eval.equivalence import non_inferiority, tost
from wg_eval.hierarchy import InferenceSpec
from wg_eval.metrics import estimate
from wg_eval.multiplicity import bonferroni, holm
from wg_eval.pairing import build_paired_panel
from wg_eval.bootstrap import paired_cluster_bootstrap

INFERENCE = InferenceSpec()


# ---------------------------------------------------------------------------
# paired mean difference
# ---------------------------------------------------------------------------

def test_paired_mean_difference_by_hand():
    """Three units. A: 10, 12, 14. B: 11, 15, 13.

    Per-unit differences (B - A): +1, +3, -1. Mean difference = 3/3 = 1.0.
    The difference of the arm means is 13 - 12 = 1.0, which must agree.
    """
    frame = pd.DataFrame(
        {
            "world_id": ["u1", "u1", "u2", "u2", "u3", "u3"],
            "event_id": ["u1e", "u1e", "u2e", "u2e", "u3e", "u3e"],
            "resident_id": list("abcdef"),
            "policy_id": ["policy_a", "policy_b"] * 3,
            "loss": [10.0, 11.0, 12.0, 15.0, 14.0, 13.0],
        }
    )
    metric = MetricSpec(name="m", column="loss", level="world_id")
    panel = aggregate_to_level(frame, "loss", "world_id", AggregationSpec(), INFERENCE)
    paired = build_paired_panel(panel, metric, ["policy_a", "policy_b"], INFERENCE)
    base, cand, diff = paired_cluster_bootstrap(
        paired, np.mean, "policy_a", "policy_b", n_resamples=200, seed=1
    )
    assert base.estimate == pytest.approx(12.0)
    assert cand.estimate == pytest.approx(13.0)
    assert diff.estimate == pytest.approx(1.0)

    per_unit = np.array([1.0, 3.0, -1.0])
    assert diff.estimate == pytest.approx(per_unit.mean())


def test_paired_bootstrap_se_matches_a_direct_transcription():
    """The library's replicates must match a loop written out here from scratch."""
    rng = np.random.default_rng(0)
    groups_a = [rng.normal(10, 1, size=4) for _ in range(12)]
    groups_b = [g + rng.normal(0.5, 0.2) for g in groups_a]

    # Zero-padded ids so the panel's lexicographic unit order matches the list order.
    frame = pd.DataFrame(
        {
            "world_id": np.repeat([f"u{i:02d}" for i in range(12)], 4).tolist() * 2,
            "event_id": np.repeat([f"u{i:02d}e" for i in range(12)], 4).tolist() * 2,
            "resident_id": [f"r{i}" for i in range(48)] * 2,
            "policy_id": ["policy_a"] * 48 + ["policy_b"] * 48,
            "loss": np.concatenate([np.concatenate(groups_a), np.concatenate(groups_b)]),
        }
    )
    metric = MetricSpec(name="m", column="loss", level="resident_id")
    panel = aggregate_to_level(frame, "loss", "resident_id", AggregationSpec(), INFERENCE)
    paired = build_paired_panel(panel, metric, ["policy_a", "policy_b"], INFERENCE)
    _, _, diff = paired_cluster_bootstrap(
        paired, np.mean, "policy_a", "policy_b", n_resamples=500, seed=42
    )

    # Independent transcription: draw unit indices, apply the SAME draw to both
    # arms, difference the means.
    draw_rng = np.random.default_rng(42)
    draws = draw_rng.integers(0, 12, size=(500, 12))
    reference = np.array(
        [
            np.concatenate([groups_b[i] for i in d]).mean()
            - np.concatenate([groups_a[i] for i in d]).mean()
            for d in draws
        ]
    )
    assert diff.estimate == pytest.approx(
        np.concatenate(groups_b).mean() - np.concatenate(groups_a).mean()
    )
    assert diff.ci_low == pytest.approx(float(np.quantile(reference, 0.025)), abs=1e-9)
    assert diff.ci_high == pytest.approx(float(np.quantile(reference, 0.975)), abs=1e-9)


# ---------------------------------------------------------------------------
# quantile, CVaR, exceedance
# ---------------------------------------------------------------------------

def test_quantile_matches_numpy_linear_interpolation_by_hand():
    """x = 1..10. q=0.9 sits at position 0.9*(10-1) = 8.1, i.e. between x[8]=9
    and x[9]=10, nine tenths of the way: 9 + 0.1*... -> 9.1."""
    x = np.arange(1.0, 11.0)
    assert estimate(x, "quantile", {"q": 0.9}) == pytest.approx(9.1)
    assert estimate(x, "quantile", {"q": 0.5}) == pytest.approx(5.5)
    assert estimate(x, "quantile", {"q": 0.0}) == 1.0
    assert estimate(x, "quantile", {"q": 1.0}) == 10.0


def test_cvar_by_hand_upper_and_lower():
    """x = 1..10, alpha = 0.7 -> k = ceil(0.3*10) = 3.

    Upper tail: mean(8, 9, 10) = 9. Lower tail: mean(1, 2, 3) = 2.
    """
    x = np.arange(1.0, 11.0)
    assert estimate(x, "cvar", {"alpha": 0.7}) == pytest.approx(9.0)
    assert estimate(x, "cvar", {"alpha": 0.7, "tail": "lower"}) == pytest.approx(2.0)


def test_cvar_k_survives_binary_floating_point():
    """(1-0.7)*10 evaluates to 3.0000000000000004 in binary floating point.

    An unrounded ceiling would give k=4 and average one value too many, so the
    implementation would contradict its own documented definition.
    """
    from wg_eval.metrics import tail_count

    assert tail_count(0.7, 10) == 3
    assert tail_count(0.9, 30) == 3
    assert tail_count(0.8, 5) == 1
    assert tail_count(0.95, 100) == 5
    assert tail_count(0.99, 1000) == 10
    # A genuine fraction still rounds up rather than down.
    assert tail_count(0.75, 10) == 3
    assert tail_count(0.71, 10) == 3


def test_cvar_k_is_a_ceiling_not_a_rounding():
    """n = 7, alpha = 0.9 -> (1-0.9)*7 = 0.7 -> k = 1, so CVaR is the extreme."""
    x = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0])
    assert estimate(x, "cvar", {"alpha": 0.9}) == 9.0
    # n = 7, alpha = 0.5 -> 3.5 -> k = 4 -> mean of the four largest: 3,4,5,9.
    assert estimate(x, "cvar", {"alpha": 0.5}) == pytest.approx((3 + 4 + 5 + 9) / 4)


def test_cvar_handles_ties_by_position_in_the_sorted_order():
    """Six values, four of them equal. alpha = 0.5 -> k = 3 -> mean(5, 5, 9)."""
    x = np.array([5.0, 5.0, 5.0, 5.0, 9.0, 1.0])
    assert estimate(x, "cvar", {"alpha": 0.5}) == pytest.approx((5 + 5 + 9) / 3)


def test_exceedance_probability_is_strict_by_hand():
    """x = [1, 2, 3, 4, 5], threshold 3 -> strictly greater: 4 and 5 -> 2/5."""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert estimate(x, "p_exceed", {"threshold": 3.0}) == pytest.approx(0.4)
    assert estimate(x, "p_exceed", {"threshold": 0.0}) == 1.0
    assert estimate(x, "p_exceed", {"threshold": 5.0}) == 0.0


def test_trimmed_mean_by_hand():
    """Ten values 1..10, proportion 0.2 -> floor(2) removed each end -> mean(3..8)."""
    x = np.arange(1.0, 11.0)
    assert estimate(x, "trimmed_mean", {"proportion": 0.2}) == pytest.approx(
        np.arange(3.0, 9.0).mean()
    )


def test_mean_and_std_match_numpy_and_scipy():
    rng = np.random.default_rng(3)
    x = rng.normal(5, 2, size=200)
    assert estimate(x, "mean") == pytest.approx(float(np.mean(x)))
    assert estimate(x, "std") == pytest.approx(float(stats.tstd(x)))
    assert estimate(x, "median") == pytest.approx(float(np.median(x)))


# ---------------------------------------------------------------------------
# TOST
# ---------------------------------------------------------------------------

def test_tost_matches_the_normal_theory_result_on_a_simple_dataset():
    """A near-exact normal bootstrap distribution should reproduce textbook TOST.

    With replicates ~ N(0.10, 0.05) and a margin of 0.25, the two one-sided
    tests are z_lower = (0.10 + 0.25)/0.05 = 7.0 and z_upper = (0.25 - 0.10)/0.05
    = 3.0. Both reject at 5%, so the verdict is equivalence, and the 90%
    interval is 0.10 +/- 1.6449*0.05 = (0.0178, 0.1822), inside +/-0.25.
    """
    rng = np.random.default_rng(11)
    replicates = rng.normal(0.10, 0.05, size=200_000)
    result = tost(replicates, 0.10, Margin(lower=0.25, upper=0.25, source="hand check"))

    z = stats.norm.ppf(0.95)
    assert result.ci_low == pytest.approx(0.10 - z * 0.05, abs=0.002)
    assert result.ci_high == pytest.approx(0.10 + z * 0.05, abs=0.002)
    assert result.conclusion == "statistically_equivalent"

    # The bootstrap ASLs should track the normal-theory one-sided p-values.
    assert result.p_lower == pytest.approx(stats.norm.sf(7.0), abs=1e-4)
    assert result.p_upper == pytest.approx(stats.norm.sf(3.0), abs=0.002)


def test_tost_boundary_behaviour_is_the_interval_inclusion_rule():
    """Equivalence holds exactly when the 1-2a interval fits inside the margin."""
    rng = np.random.default_rng(5)
    replicates = rng.normal(0.0, 0.10, size=200_000)
    half_width = float(np.quantile(replicates, 0.95))
    inside = tost(replicates, 0.0, Margin(lower=half_width * 1.05, upper=half_width * 1.05))
    outside = tost(replicates, 0.0, Margin(lower=half_width * 0.95, upper=half_width * 0.95))
    assert inside.conclusion == "statistically_equivalent"
    assert outside.conclusion == "inconclusive"


def test_non_inferiority_matches_a_one_sided_normal_bound():
    """replicates ~ N(0.10, 0.05); the one-sided 95% upper bound is 0.10 + 1.6449*0.05."""
    rng = np.random.default_rng(13)
    replicates = rng.normal(0.10, 0.05, size=200_000)
    bound = 0.10 + stats.norm.ppf(0.95) * 0.05
    generous = non_inferiority(replicates, 0.10, Margin(lower=1.0, upper=bound + 0.01))
    strict = non_inferiority(replicates, 0.10, Margin(lower=1.0, upper=bound - 0.01))
    assert generous.conclusion == "non_inferior"
    assert strict.conclusion == "not_shown_non_inferior"


# ---------------------------------------------------------------------------
# multiplicity
# ---------------------------------------------------------------------------

def test_holm_matches_a_textbook_worked_example():
    """p = .005, .011, .020, .040, .130 with m = 5.

    Step-down multipliers 5, 4, 3, 2, 1 give .025, .044, .060, .080, .130, and
    the running maximum leaves them unchanged because they already increase.
    """
    p = {"a": 0.005, "b": 0.011, "c": 0.020, "d": 0.040, "e": 0.130}
    out = holm(p)
    assert out == pytest.approx(
        {"a": 0.025, "b": 0.044, "c": 0.060, "d": 0.080, "e": 0.130}, abs=1e-12
    )


def test_holm_enforces_monotonicity():
    """p = .04, .041 with m = 2 -> 2*.04 = .08 and 1*.041 = .041; the running
    maximum must raise the second to .08 so it never sits below the first."""
    out = holm({"a": 0.040, "b": 0.041})
    assert out["a"] == pytest.approx(0.08)
    assert out["b"] == pytest.approx(0.08)


def test_holm_matches_statsmodels_style_reference_implementation():
    """Compared against a transcription of the definition, not the library's own code."""
    rng = np.random.default_rng(9)
    for _ in range(25):
        raw = {f"h{i}": float(p) for i, p in enumerate(rng.uniform(0, 1, size=8))}
        ours = holm(raw)
        keys = sorted(raw, key=lambda k: raw[k])
        m = len(keys)
        running, reference = 0.0, {}
        for rank, key in enumerate(keys):
            running = max(running, min(1.0, (m - rank) * raw[key]))
            reference[key] = running
        assert ours == pytest.approx(reference)


def test_bonferroni_is_the_single_step_version():
    p = {"a": 0.01, "b": 0.02, "c": 0.30}
    assert bonferroni(p) == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.90})
    # Holm is uniformly at least as powerful as Bonferroni.
    holm_out, bonf_out = holm(p), bonferroni(p)
    assert all(holm_out[k] <= bonf_out[k] + 1e-12 for k in p)


def test_bonferroni_clips_at_one():
    assert bonferroni({"a": 0.6, "b": 0.7})["a"] == 1.0


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def test_two_step_roll_up_differs_from_pooling_by_hand():
    """Event 1 has values 0, 0, 30 (mean 10); event 2 has 10, 20 (mean 15).

    Unit mean of event means = 12.5. Pooled observation mean = 60/5 = 12.0.
    """
    frame = pd.DataFrame(
        {
            "world_id": ["u1"] * 5,
            "event_id": ["e1", "e1", "e1", "e2", "e2"],
            "resident_id": list("abcde"),
            "policy_id": ["p"] * 5,
            "loss": [0.0, 0.0, 30.0, 10.0, 20.0],
        }
    )
    two_step = aggregate_to_level(frame, "loss", "world_id", AggregationSpec(), INFERENCE)
    assert two_step["value"].iloc[0] == pytest.approx(12.5)
    assert frame["loss"].mean() == pytest.approx(12.0)
    assert not math.isclose(12.5, 12.0)
