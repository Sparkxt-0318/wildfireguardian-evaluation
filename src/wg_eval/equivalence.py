"""Equivalence and non-inferiority testing against declared practical margins.

A large p-value is the absence of evidence of a difference.  It is not
evidence of the absence of a difference, and no amount of restating it makes
it so.  To claim that two policies are practically the same you must first say
how large a difference would still be practically the same -- the margin -- and
then show that the interval for the difference fits inside it.

This module refuses to produce an equivalence verdict without a margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

Conclusion = Literal[
    "equivalent",
    "not_equivalent",
    "inconclusive",
    "non_inferior",
    "inferior",
    "superior",
    "worse",
]


class MarginRequired(ValueError):
    """Raised when an equivalence claim is attempted without a practical margin."""


@dataclass
class EquivalenceResult:
    """Outcome of a TOST or non-inferiority test on a bootstrap difference."""

    kind: str
    margin: float
    alpha: float
    difference: float
    ci_low: float
    ci_high: float
    ci_level: float
    conclusion: Conclusion
    p_lower: float | None = None
    p_upper: float | None = None
    p_value: float | None = None
    interpretation: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "margin": self.margin,
            "alpha": self.alpha,
            "difference": _f(self.difference),
            "ci_low": _f(self.ci_low),
            "ci_high": _f(self.ci_high),
            "ci_level": self.ci_level,
            "conclusion": self.conclusion,
            "p_lower": _f(self.p_lower),
            "p_upper": _f(self.p_upper),
            "p_value": _f(self.p_value),
            "interpretation": self.interpretation,
            "notes": list(self.notes),
        }


def _f(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if not math.isfinite(value) else value


def _asl(replicates: np.ndarray, threshold: float, side: str) -> float:
    """Bootstrap achieved significance level against a one-sided null."""
    n = len(replicates)
    if n == 0:
        return math.nan
    if side == "lower":  # H0: difference <= -margin
        p = float(np.mean(replicates <= threshold))
    else:  # H0: difference >= +margin
        p = float(np.mean(replicates >= threshold))
    return float(min(1.0, max(p, 1.0 / (n + 1))))


def tost(
    replicates: np.ndarray,
    difference: float,
    margin: float | None,
    *,
    alpha: float = 0.05,
    metric_name: str = "the metric",
) -> EquivalenceResult:
    """Two one-sided tests for practical equivalence on a bootstrap difference.

    Equivalence at level ``alpha`` is concluded exactly when the central
    ``1 - 2*alpha`` interval for the difference lies strictly inside
    ``(-margin, +margin)`` -- the standard confidence-interval formulation of
    TOST, here with bootstrap rather than normal-theory endpoints.

    Raises
    ------
    MarginRequired
        If ``margin`` is ``None``.  There is no default margin: the smallest
        difference that still matters is a domain judgement, not a statistical
        one, and this library does not know the domain.
    """
    if margin is None:
        raise MarginRequired(
            f"equivalence for {metric_name} requires an explicit practical margin. "
            "Declare `equivalence.margins.<metric>` in the analysis config. "
            "Without a margin, a wide interval around zero means 'we do not know', "
            "never 'the policies are the same'."
        )
    if margin <= 0:
        raise ValueError("the practical margin must be a positive distance")

    replicates = np.asarray(replicates, dtype="float64")
    replicates = replicates[np.isfinite(replicates)]
    ci_level = 1.0 - 2.0 * alpha
    lo = float(np.quantile(replicates, alpha))
    hi = float(np.quantile(replicates, 1.0 - alpha))

    p_lower = _asl(replicates, -margin, "lower")
    p_upper = _asl(replicates, margin, "upper")
    p_value = float(max(p_lower, p_upper))

    inside = (lo > -margin) and (hi < margin)
    outside = (lo >= margin) or (hi <= -margin)
    notes: list[str] = []
    if inside:
        conclusion: Conclusion = "equivalent"
        interpretation = (
            f"Equivalent within +/-{margin:g}: the {ci_level:.0%} interval for the "
            f"difference ({lo:.4g}, {hi:.4g}) lies entirely inside the declared "
            f"practical margin."
        )
    elif outside:
        conclusion = "not_equivalent"
        interpretation = (
            f"Not equivalent: the {ci_level:.0%} interval ({lo:.4g}, {hi:.4g}) lies "
            f"entirely outside +/-{margin:g}, so the difference is larger than the "
            f"declared practical margin."
        )
    else:
        conclusion = "inconclusive"
        width = hi - lo
        interpretation = (
            f"Inconclusive: the {ci_level:.0%} interval ({lo:.4g}, {hi:.4g}) is not "
            f"contained in +/-{margin:g} and does not exclude it either. This is "
            f"'we cannot tell', not 'no difference'."
        )
        if width > 2.0 * margin:
            notes.append(
                f"The interval is {width / (2.0 * margin):.1f}x wider than the "
                "equivalence window: the design cannot resolve differences at this "
                "margin. More clusters (not more observations per cluster) are needed."
            )
    return EquivalenceResult(
        kind="tost",
        margin=float(margin),
        alpha=alpha,
        difference=float(difference),
        ci_low=lo,
        ci_high=hi,
        ci_level=ci_level,
        conclusion=conclusion,
        p_lower=p_lower,
        p_upper=p_upper,
        p_value=p_value,
        interpretation=interpretation,
        notes=notes,
    )


def non_inferiority(
    replicates: np.ndarray,
    difference: float,
    margin: float | None,
    *,
    alpha: float = 0.05,
    direction: str = "lower_is_better",
    metric_name: str = "the metric",
) -> EquivalenceResult:
    """One-sided non-inferiority of the candidate relative to the baseline.

    ``difference`` and ``replicates`` are on the ``candidate - baseline`` scale.

    * ``lower_is_better``: non-inferior when the one-sided upper bound is below
      ``+margin`` (the candidate is not meaningfully worse).
    * ``higher_is_better``: non-inferior when the one-sided lower bound is above
      ``-margin``.
    """
    if margin is None:
        raise MarginRequired(
            f"non-inferiority for {metric_name} requires an explicit margin "
            "(`equivalence.margins.<metric>`)."
        )
    if margin <= 0:
        raise ValueError("the non-inferiority margin must be a positive distance")
    if direction not in ("lower_is_better", "higher_is_better"):
        raise ValueError(f"unknown direction {direction!r}")

    replicates = np.asarray(replicates, dtype="float64")
    replicates = replicates[np.isfinite(replicates)]
    ci_level = 1.0 - alpha

    if direction == "lower_is_better":
        bound = float(np.quantile(replicates, 1.0 - alpha))
        ok = bound < margin
        p_value = _asl(replicates, margin, "upper")
        lo, hi = -math.inf, bound
        detail = (
            f"one-sided {ci_level:.0%} upper bound {bound:.4g} "
            f"{'<' if ok else '>='} margin {margin:g}"
        )
    else:
        bound = float(np.quantile(replicates, alpha))
        ok = bound > -margin
        p_value = _asl(replicates, -margin, "lower")
        lo, hi = bound, math.inf
        detail = (
            f"one-sided {ci_level:.0%} lower bound {bound:.4g} "
            f"{'>' if ok else '<='} margin {-margin:g}"
        )

    conclusion: Conclusion = "non_inferior" if ok else "inferior"
    interpretation = (
        f"{'Non-inferior' if ok else 'Not shown to be non-inferior'} at margin "
        f"{margin:g}: {detail}."
    )
    return EquivalenceResult(
        kind="non_inferiority",
        margin=float(margin),
        alpha=alpha,
        difference=float(difference),
        ci_low=lo,
        ci_high=hi,
        ci_level=ci_level,
        conclusion=conclusion,
        p_value=p_value,
        interpretation=interpretation,
    )


@dataclass
class Verdict:
    """The single sentence a reader is allowed to take away."""

    label: str
    sentence: str
    favours: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sentence": self.sentence,
            "favours": self.favours,
            "detail": self.detail,
        }


def classify_difference(
    *,
    metric_name: str,
    baseline: str,
    candidate: str,
    difference: float,
    ci_low: float,
    ci_high: float,
    confidence_level: float,
    direction: str,
    equivalence: EquivalenceResult | None,
) -> Verdict:
    """Combine the superiority interval and the equivalence test into one verdict.

    The five possible labels are ``superior``, ``worse``, ``equivalent``,
    ``different_but_not_resolved`` and ``inconclusive``.  "No difference" is
    not among them, by construction.
    """
    better_is_negative = direction == "lower_is_better"
    excludes_zero = (ci_low > 0.0) or (ci_high < 0.0)
    candidate_better = (difference < 0) if better_is_negative else (difference > 0)

    detail: dict[str, Any] = {
        "difference": _f(difference),
        "ci": [_f(ci_low), _f(ci_high)],
        "confidence_level": confidence_level,
        "direction": direction,
        "equivalence": equivalence.as_dict() if equivalence else None,
    }

    if excludes_zero:
        winner = candidate if candidate_better else baseline
        loser = baseline if candidate_better else candidate
        if equivalence is not None and equivalence.conclusion == "equivalent":
            return Verdict(
                label="statistically_different_practically_equivalent",
                sentence=(
                    f"{metric_name}: the difference between {candidate} and {baseline} "
                    f"is statistically resolved (interval {ci_low:.4g} to {ci_high:.4g} "
                    f"excludes 0) but is smaller than the declared practical margin of "
                    f"{equivalence.margin:g}. Statistically detectable, practically equivalent."
                ),
                favours=None,
                detail=detail,
            )
        return Verdict(
            label="superior" if candidate_better else "worse",
            sentence=(
                f"{metric_name}: {winner} beats {loser}. The {confidence_level:.0%} "
                f"paired interval for {candidate} - {baseline} is "
                f"({ci_low:.4g}, {ci_high:.4g}) and excludes 0."
            ),
            favours=winner,
            detail=detail,
        )

    if equivalence is not None and equivalence.conclusion == "equivalent":
        return Verdict(
            label="equivalent",
            sentence=(
                f"{metric_name}: {candidate} and {baseline} are practically equivalent "
                f"within +/-{equivalence.margin:g}. The interval "
                f"({equivalence.ci_low:.4g}, {equivalence.ci_high:.4g}) fits inside the margin."
            ),
            favours=None,
            detail=detail,
        )

    if equivalence is None:
        return Verdict(
            label="inconclusive",
            sentence=(
                f"{metric_name}: the {confidence_level:.0%} interval for {candidate} - "
                f"{baseline} is ({ci_low:.4g}, {ci_high:.4g}) and includes 0. No practical "
                f"margin was declared, so equivalence cannot be assessed. This is "
                f"'undetermined', NOT 'no difference'."
            ),
            favours=None,
            detail=detail,
        )

    return Verdict(
        label="inconclusive",
        sentence=(
            f"{metric_name}: the interval for {candidate} - {baseline} includes 0 and is "
            f"too wide to fit inside the practical margin of {equivalence.margin:g}. "
            f"Neither superiority nor equivalence is established: the data do not resolve "
            f"the question. This is 'undetermined', NOT 'no difference'."
        ),
        favours=None,
        detail=detail,
    )
