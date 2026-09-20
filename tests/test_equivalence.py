"""Equivalence, non-inferiority, and the refusal to say "no difference"."""

from __future__ import annotations

import numpy as np
import pytest

from wg_eval.config import Margin
from wg_eval.equivalence import (
    DECISION_CAVEAT,
    MarginRequired,
    classify_difference,
    non_inferiority,
    tost,
)


def replicates(centre: float, sd: float, n: int = 4000, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(centre, sd, size=n)


def margin(lower: float, upper: float | None = None, source: str = "test") -> Margin:
    return Margin(lower=lower, upper=upper if upper is not None else lower, source=source)


def test_equivalence_requires_a_declared_margin():
    with pytest.raises(MarginRequired, match="explicit practical margin"):
        tost(replicates(0.0, 0.1), 0.0, None)


def test_margin_must_be_a_positive_distance():
    with pytest.raises(ValueError, match="positive distance"):
        Margin(lower=-1.0, upper=-1.0)


def test_tight_interval_inside_the_margin_is_equivalent():
    result = tost(replicates(0.01, 0.05), 0.01, margin(0.5))
    assert result.conclusion == "statistically_equivalent"
    assert result.ci_low > -0.5 and result.ci_high < 0.5
    assert "Equivalent within" in result.interpretation
    assert DECISION_CAVEAT in result.notes


def test_an_asymmetric_margin_can_reverse_the_verdict():
    """The same interval is equivalent under one declaration and not the other."""
    reps = replicates(0.25, 0.05)
    symmetric = tost(reps, 0.25, margin(0.5))
    asymmetric = tost(reps, 0.25, margin(0.5, 0.15))
    assert symmetric.conclusion == "statistically_equivalent"
    assert asymmetric.conclusion != "statistically_equivalent"
    assert any("asymmetric" in n for n in asymmetric.notes)


def test_an_undocumented_margin_is_flagged_wherever_it_is_used():
    result = tost(replicates(0.0, 0.05), 0.0, Margin(lower=0.5, upper=0.5, source=""))
    assert any("no recorded source" in n for n in result.notes)


def test_large_difference_is_not_equivalent():
    result = tost(replicates(2.0, 0.1), 2.0, margin(0.5))
    assert result.conclusion == "not_equivalent"


def test_wide_interval_around_zero_is_inconclusive_never_equivalent():
    """The central case the library exists to prevent."""
    result = tost(replicates(0.0, 3.0), 0.0, margin(0.2))
    assert result.conclusion == "inconclusive"
    assert "'undetermined'" in result.interpretation
    assert any("wider than the equivalence region" in n for n in result.notes)


def test_tost_p_value_is_the_larger_of_the_two_one_sided_tests():
    result = tost(replicates(0.1, 0.1), 0.1, margin(0.5))
    assert result.p_value == max(result.p_lower, result.p_upper)
    assert 0.0 < result.p_value <= 1.0


def test_tost_interval_level_is_one_minus_two_alpha():
    result = tost(replicates(0.0, 0.1), 0.0, margin(1.0), alpha=0.05)
    assert result.ci_level == pytest.approx(0.90)


def test_non_inferiority_lower_is_better():
    # Candidate is 0.1 worse on a lower-is-better metric, margin 0.5 -> non-inferior.
    ok = non_inferiority(replicates(0.1, 0.05), 0.1, margin(0.5), direction="lower_is_better")
    assert ok.conclusion == "non_inferior"
    # 0.9 worse -> not non-inferior.
    bad = non_inferiority(replicates(0.9, 0.05), 0.9, margin(0.5), direction="lower_is_better")
    assert bad.conclusion == "not_shown_non_inferior"


def test_non_inferiority_uses_the_harmful_side_of_an_asymmetric_margin():
    """lower_is_better: harm is a positive difference, so margin.upper governs."""
    reps = replicates(0.3, 0.05)
    tolerant = non_inferiority(reps, 0.3, margin(0.1, 0.5), direction="lower_is_better")
    strict = non_inferiority(reps, 0.3, margin(0.5, 0.1), direction="lower_is_better")
    assert tolerant.conclusion == "non_inferior"
    assert strict.conclusion == "not_shown_non_inferior"


def test_non_inferiority_higher_is_better_flips_the_bound():
    ok = non_inferiority(replicates(-0.1, 0.05), -0.1, margin(0.5), direction="higher_is_better")
    assert ok.conclusion == "non_inferior"
    bad = non_inferiority(replicates(-0.9, 0.05), -0.9, margin(0.5), direction="higher_is_better")
    assert bad.conclusion == "not_shown_non_inferior"


def test_non_inferiority_requires_a_margin_too():
    with pytest.raises(MarginRequired):
        non_inferiority(replicates(0.0, 0.1), 0.0, None)


def test_non_inferiority_rejects_an_unknown_direction():
    with pytest.raises(ValueError, match="direction"):
        non_inferiority(replicates(0.0, 0.1), 0.0, margin(0.5), direction="sideways")


def _verdict(diff, lo, hi, direction="lower_is_better", equivalence=None):
    return classify_difference(
        metric_name="mean_loss",
        baseline="policy_a",
        candidate="policy_b",
        difference=diff,
        ci_low=lo,
        ci_high=hi,
        confidence_level=0.95,
        direction=direction,
        equivalence=equivalence,
    )


def test_candidate_wins_when_the_interval_excludes_zero_in_its_favour():
    v = _verdict(-1.0, -1.5, -0.5)
    assert v.label == "superior" and v.favours == "policy_b"


def test_baseline_wins_when_the_interval_excludes_zero_the_other_way():
    v = _verdict(1.0, 0.5, 1.5)
    assert v.label == "worse" and v.favours == "policy_a"


def test_direction_is_honoured_for_higher_is_better_metrics():
    v = _verdict(1.0, 0.5, 1.5, direction="higher_is_better")
    assert v.label == "superior" and v.favours == "policy_b"


def test_interval_including_zero_without_a_margin_is_undetermined():
    v = _verdict(0.0, -2.0, 2.0)
    assert v.label == "inconclusive"
    assert "'undetermined'" in v.sentence
    assert v.favours is None


def test_interval_including_zero_with_a_margin_can_be_equivalent():
    eq = tost(replicates(0.0, 0.05), 0.0, margin(0.5))
    v = _verdict(0.0, -0.1, 0.1, equivalence=eq)
    assert v.label == "statistically_equivalent"
    assert "equivalent within the declared margin" in v.sentence
    assert any("OPERATIONALLY_INTERCHANGEABLE" in c for c in v.caveats)


def test_statistically_resolved_but_negligible_is_its_own_label():
    eq = tost(replicates(0.05, 0.005), 0.05, margin(0.5))
    v = _verdict(0.05, 0.04, 0.06, equivalence=eq)
    assert v.label == "statistically_different_within_margin"
    assert v.favours is None


def test_no_verdict_makes_a_forbidden_claim():
    from wg_eval.report import audit_wording

    eq = tost(replicates(0.0, 3.0), 0.0, margin(0.2))
    verdicts = [
        _verdict(-1.0, -1.5, -0.5),
        _verdict(1.0, 0.5, 1.5),
        _verdict(0.0, -2.0, 2.0),
        _verdict(0.0, -2.0, 2.0, equivalence=eq),
        _verdict(0.05, 0.04, 0.06, equivalence=tost(replicates(0.05, 0.005), 0.05, margin(0.5))),
    ]
    for v in verdicts:
        assert audit_wording(v.sentence) == [], v.sentence


def test_verdicts_report_the_unit_count_they_were_paired_on():
    v = classify_difference(
        metric_name="mean_loss", baseline="policy_a", candidate="policy_b",
        difference=-1.0, ci_low=-1.5, ci_high=-0.5, confidence_level=0.95,
        direction="lower_is_better", equivalence=None, n_units=31, unit_label="world_id",
    )
    assert "paired on 31 shared world_id" in v.sentence
