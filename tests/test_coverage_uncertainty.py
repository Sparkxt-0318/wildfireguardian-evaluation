"""Coverage is an estimate, and its own uncertainty has to be reported."""

from __future__ import annotations

import math

import pytest
from scipy import stats

from wg_eval.coverage import (
    CoverageEstimate,
    compare_coverage,
    estimate_coverage,
    monte_carlo_se,
    replications_for_precision,
    wilson_interval,
)


def test_wilson_matches_an_independent_implementation():
    """Checked against scipy's binomtest, not against this package's own arithmetic."""
    for successes, n in [(90, 100), (62, 100), (49, 50), (1, 20), (0, 30), (30, 30)]:
        ours = wilson_interval(successes, n, 0.95)
        theirs = stats.binomtest(successes, n).proportion_ci(
            confidence_level=0.95, method="wilson"
        )
        assert ours[0] == pytest.approx(theirs.low, abs=1e-9)
        assert ours[1] == pytest.approx(theirs.high, abs=1e-9)


def test_wilson_stays_inside_the_unit_interval_at_the_extremes():
    assert wilson_interval(0, 40)[0] == pytest.approx(0.0, abs=1e-12)
    assert wilson_interval(40, 40)[1] == pytest.approx(1.0, abs=1e-12)
    assert 0.0 <= wilson_interval(0, 40)[1] <= 1.0
    assert 0.0 <= wilson_interval(40, 40)[0] <= 1.0


def test_monte_carlo_se_is_the_binomial_standard_error():
    assert monte_carlo_se(0.9, 100) == pytest.approx(math.sqrt(0.9 * 0.1 / 100))
    assert math.isnan(monte_carlo_se(0.9, 0))


def test_a_coverage_estimate_carries_everything_a_reader_needs():
    est = estimate_coverage(
        [True] * 90 + [False] * 10, label="unit-level", nominal_level=0.95,
        widths=[1.7] * 100,
    )
    payload = est.as_dict()
    for key in ("n_replications", "nominal_level", "empirical_coverage", "monte_carlo_se",
                "wilson_ci", "consistent_with_nominal", "mean_interval_width"):
        assert key in payload
    assert payload["empirical_coverage"] == pytest.approx(0.90)
    assert payload["n_replications"] == 100


def test_ninety_percent_from_a_hundred_replications_excludes_nominal():
    """The finding that corrected the v0.1.0 red-team write-up."""
    est = estimate_coverage([True] * 90 + [False] * 10, label="x", nominal_level=0.95)
    assert not est.consistent_with_nominal
    lo, hi = est.wilson_ci
    assert hi < 0.95


def test_ninety_percent_from_thirty_replications_does_not_exclude_nominal():
    """Same point estimate, fewer replications, and the claim no longer holds."""
    est = estimate_coverage([True] * 27 + [False] * 3, label="x", nominal_level=0.95)
    assert est.empirical_coverage == pytest.approx(0.90)
    assert est.consistent_with_nominal


def test_describe_reports_the_replication_count_and_the_interval():
    est = estimate_coverage([True] * 93 + [False] * 7, label="unit-level", nominal_level=0.95)
    text = est.describe()
    assert "R=100" in text and "MC SE" in text and "Wilson CI" in text


def test_comparing_two_coverage_estimates_accounts_for_both_errors():
    a = estimate_coverage([True] * 93 + [False] * 7, label="a", nominal_level=0.95)
    b = estimate_coverage([True] * 62 + [False] * 38, label="b", nominal_level=0.95)
    out = compare_coverage(a, b)
    assert out["difference"] == pytest.approx(0.31)
    assert out["monte_carlo_se"] == pytest.approx(
        math.sqrt(a.monte_carlo_se**2 + b.monte_carlo_se**2)
    )
    assert out["distinguishable"]

    close_a = estimate_coverage([True] * 93 + [False] * 7, label="a", nominal_level=0.95)
    close_b = estimate_coverage([True] * 91 + [False] * 9, label="b", nominal_level=0.95)
    assert not compare_coverage(close_a, close_b)["distinguishable"]


def test_replications_needed_for_a_target_precision():
    assert replications_for_precision(0.02) == 457
    assert replications_for_precision(0.05) < replications_for_precision(0.01)
    with pytest.raises(ValueError):
        replications_for_precision(0.0)


def test_an_empty_study_does_not_pretend_to_know_anything():
    est = CoverageEstimate(label="x", n_replications=0, n_covered=0, nominal_level=0.95)
    assert math.isnan(est.empirical_coverage)
    assert all(math.isnan(v) for v in est.wilson_ci)
