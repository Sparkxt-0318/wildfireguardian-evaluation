"""Equivalence and non-inferiority testing against declared practical margins.

A large p-value is the absence of evidence of a difference.  It is not evidence
of the absence of a difference, and no amount of restating it makes it so.  To
claim two policies differ negligibly you must first say how large a difference
would still be negligible -- the margin -- and then show that the interval for
the difference fits inside it.

Two further distinctions are kept explicit, because collapsing either of them
is how an evaluation overstates what it found:

* A margin may be **asymmetric**. "A little worse" and "a little better" are
  not always equally tolerable, so the equivalence region is ``(-lower, +upper)``.
* **Statistical equivalence is not operational interchangeability.** This
  library can establish that a contrast lies inside a predeclared margin. Only
  the domain can say whether that margin marks a decision-relevant difference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from wg_eval.config import Margin

Conclusion = Literal[
    "statistically_equivalent",
    "not_equivalent",
    "inconclusive",
    "non_inferior",
    "not_shown_non_inferior",
]

#: Attached to every equivalence finding. The library states what it tested;
#: whether that margin means the options are interchangeable in practice is a
#: domain judgement it is not entitled to make.
DECISION_CAVEAT = (
    "STATISTICALLY_EQUIVALENT is not OPERATIONALLY_INTERCHANGEABLE: this is a statement "
    "that the estimated contrast lies inside the predeclared margin, not that the "
    "alternatives are interchangeable in use. Whether the margin marks a decision-relevant "
    "difference is a domain judgement outside this library."
)

UNDOCUMENTED_MARGIN_CAVEAT = (
    "The margin carries no recorded source. A margin justified after the interval is known "
    "is a margin chosen to produce a verdict; record `source` when declaring it."
)


class MarginRequired(ValueError):
    """Raised when an equivalence claim is attempted without a practical margin."""


@dataclass
class EquivalenceResult:
    """Outcome of a TOST or non-inferiority test on a bootstrap difference."""

    kind: str
    margin: Margin
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

    @property
    def margin_lower(self) -> float:
        return self.margin.lower

    @property
    def margin_upper(self) -> float:
        return self.margin.upper

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "margin": self.margin.as_dict(),
            "margin_lower": self.margin.lower,
            "margin_upper": self.margin.upper,
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
    if side == "lower":  # H0: difference <= -margin_lower
        p = float(np.mean(replicates <= threshold))
    else:  # H0: difference >= +margin_upper
        p = float(np.mean(replicates >= threshold))
    return float(min(1.0, max(p, 1.0 / (n + 1))))


def _require(margin: Margin | None, metric_name: str, kind: str) -> Margin:
    if margin is None:
        raise MarginRequired(
            f"{kind} for {metric_name} requires an explicit practical margin. Declare "
            "`equivalence.margins.<metric>` in the analysis config. Without a margin, a wide "
            "interval around zero means 'undetermined', never 'the alternatives match'."
        )
    return margin


def tost(
    replicates: np.ndarray,
    difference: float,
    margin: Margin | None,
    *,
    alpha: float = 0.05,
    metric_name: str = "the metric",
) -> EquivalenceResult:
    """Two one-sided tests for practical equivalence on a bootstrap difference.

    The two nulls are ``H0_lower: delta <= -margin.lower`` and
    ``H0_upper: delta >= +margin.upper``; rejecting both concludes equivalence
    within the declared region.  Equivalently, and as implemented, the central
    ``1 - 2*alpha`` interval must lie strictly inside ``(-lower, +upper)``.

    Raises
    ------
    MarginRequired
        If ``margin`` is ``None``.  There is no default margin: the smallest
        difference that still matters is a domain judgement, not a statistical
        one, and this library does not know the domain.
    """
    margin = _require(margin, metric_name, "equivalence")

    replicates = np.asarray(replicates, dtype="float64")
    replicates = replicates[np.isfinite(replicates)]
    ci_level = 1.0 - 2.0 * alpha
    lo = float(np.quantile(replicates, alpha))
    hi = float(np.quantile(replicates, 1.0 - alpha))

    p_lower = _asl(replicates, -margin.lower, "lower")
    p_upper = _asl(replicates, margin.upper, "upper")
    p_value = float(max(p_lower, p_upper))

    inside = (lo > -margin.lower) and (hi < margin.upper)
    outside = (lo >= margin.upper) or (hi <= -margin.lower)
    notes: list[str] = []
    if not margin.documented:
        notes.append(UNDOCUMENTED_MARGIN_CAVEAT)
    if not margin.symmetric:
        notes.append(
            f"The margin is asymmetric: {margin.lower:g} tolerated below zero, "
            f"{margin.upper:g} above. The equivalence region is not centred on zero."
        )
    if margin.scale != "absolute":
        notes.append(
            f"The margin is declared on the {margin.scale} scale, so its width in the "
            "metric's own units depends on a quantity estimated from these data."
        )

    if inside:
        conclusion: Conclusion = "statistically_equivalent"
        interpretation = (
            f"Equivalent within the declared margin {margin.describe()}: the {ci_level:.0%} "
            f"interval for the estimated difference ({lo:.4g}, {hi:.4g}) lies inside it."
        )
        notes.append(DECISION_CAVEAT)
    elif outside:
        conclusion = "not_equivalent"
        interpretation = (
            f"Not equivalent: the {ci_level:.0%} interval ({lo:.4g}, {hi:.4g}) lies entirely "
            f"outside the declared margin {margin.describe()}, so the estimated difference "
            f"exceeds what was declared negligible."
        )
    else:
        conclusion = "inconclusive"
        width = hi - lo
        region = margin.lower + margin.upper
        interpretation = (
            f"Inconclusive: the {ci_level:.0%} interval ({lo:.4g}, {hi:.4g}) is neither "
            f"contained in the declared margin {margin.describe()} nor entirely outside it. "
            f"This is 'undetermined', not a finding of equivalence."
        )
        if width > region:
            notes.append(
                f"The interval is {width / region:.1f}x wider than the equivalence region: "
                "the design cannot resolve differences at this margin. More independent "
                "units (not more observations per unit) are needed."
            )
    return EquivalenceResult(
        kind="tost",
        margin=margin,
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
    margin: Margin | None,
    *,
    alpha: float = 0.05,
    direction: str = "lower_is_better",
    metric_name: str = "the metric",
) -> EquivalenceResult:
    """One-sided non-inferiority of the candidate relative to the baseline.

    ``difference`` and ``replicates`` are on the ``candidate - baseline`` scale.
    The margin used is the one on the *harmful* side, which depends on the
    metric's orientation:

    * ``lower_is_better``: harm is a positive difference, so the test uses
      ``margin.upper`` and the one-sided upper bound.
    * ``higher_is_better``: harm is a negative difference, so it uses
      ``margin.lower`` and the one-sided lower bound.
    """
    margin = _require(margin, metric_name, "non-inferiority")
    if direction not in ("lower_is_better", "higher_is_better"):
        raise ValueError(f"unknown direction {direction!r}")

    replicates = np.asarray(replicates, dtype="float64")
    replicates = replicates[np.isfinite(replicates)]
    ci_level = 1.0 - alpha
    notes: list[str] = []
    if not margin.documented:
        notes.append(UNDOCUMENTED_MARGIN_CAVEAT)

    if direction == "lower_is_better":
        harmful_margin = margin.upper
        bound = float(np.quantile(replicates, 1.0 - alpha))
        ok = bound < harmful_margin
        p_value = _asl(replicates, harmful_margin, "upper")
        lo, hi = -math.inf, bound
        detail = (
            f"one-sided {ci_level:.0%} upper bound {bound:.4g} "
            f"{'<' if ok else 'is not below'} the harmful-side margin {harmful_margin:g}"
        )
    else:
        harmful_margin = margin.lower
        bound = float(np.quantile(replicates, alpha))
        ok = bound > -harmful_margin
        p_value = _asl(replicates, -harmful_margin, "lower")
        lo, hi = bound, math.inf
        detail = (
            f"one-sided {ci_level:.0%} lower bound {bound:.4g} "
            f"{'>' if ok else 'is not above'} the harmful-side margin {-harmful_margin:g}"
        )

    conclusion: Conclusion = "non_inferior" if ok else "not_shown_non_inferior"
    interpretation = (
        f"{'Non-inferior' if ok else 'Non-inferiority not established'} at the harmful-side "
        f"margin {harmful_margin:g} for a {direction} metric: {detail}."
    )
    if ok:
        notes.append(DECISION_CAVEAT)
    return EquivalenceResult(
        kind="non_inferiority",
        margin=margin,
        alpha=alpha,
        difference=float(difference),
        ci_low=lo,
        ci_high=hi,
        ci_level=ci_level,
        conclusion=conclusion,
        p_value=p_value,
        interpretation=interpretation,
        notes=notes,
    )


@dataclass
class Verdict:
    """The single sentence a reader is allowed to take away."""

    label: str
    sentence: str
    favours: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sentence": self.sentence,
            "favours": self.favours,
            "detail": self.detail,
            "caveats": list(self.caveats),
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
    n_units: int | None = None,
    unit_label: str = "units",
) -> Verdict:
    """Combine the superiority interval and the equivalence test into one verdict.

    The five labels are ``superior``, ``worse``, ``statistically_equivalent``,
    ``statistically_different_within_margin`` and ``inconclusive``.  "No
    difference" is not among them, by construction, and neither is any claim
    that one option is better *overall*: this function speaks about one metric.
    """
    better_is_negative = direction == "lower_is_better"
    excludes_zero = (ci_low > 0.0) or (ci_high < 0.0)
    candidate_better = (difference < 0) if better_is_negative else (difference > 0)
    plural = "" if n_units == 1 else "s"
    paired_on = (
        f", paired on {n_units} shared {unit_label}{plural}" if n_units is not None else ""
    )

    detail: dict[str, Any] = {
        "difference": _f(difference),
        "ci": [_f(ci_low), _f(ci_high)],
        "confidence_level": confidence_level,
        "direction": direction,
        "n_units": n_units,
        "equivalence": equivalence.as_dict() if equivalence else None,
    }
    caveats: list[str] = list(equivalence.notes) if equivalence else []

    interval_text = (
        f"the {confidence_level:.0%} interval for the estimated difference "
        f"({candidate} - {baseline}) is ({ci_low:.4g}, {ci_high:.4g})"
    )

    if excludes_zero:
        favoured = candidate if candidate_better else baseline
        if equivalence is not None and equivalence.conclusion == "statistically_equivalent":
            return Verdict(
                label="statistically_different_within_margin",
                sentence=(
                    f"{metric_name}: {interval_text} and excludes 0, yet the difference lies "
                    f"inside the declared margin {equivalence.margin.describe()}. The contrast "
                    f"is resolved in direction and negligible in declared size{paired_on}."
                ),
                favours=None,
                detail=detail,
                caveats=caveats,
            )
        return Verdict(
            label="superior" if candidate_better else "worse",
            sentence=(
                f"{metric_name}: {interval_text} and excludes 0, favouring {favoured} on this "
                f"metric{paired_on}."
            ),
            favours=favoured,
            detail=detail,
            caveats=caveats,
        )

    if equivalence is not None and equivalence.conclusion == "statistically_equivalent":
        return Verdict(
            label="statistically_equivalent",
            sentence=(
                f"{metric_name}: the estimated difference is equivalent within the declared "
                f"margin {equivalence.margin.describe()} -- the "
                f"{equivalence.ci_level:.0%} interval "
                f"({equivalence.ci_low:.4g}, {equivalence.ci_high:.4g}) lies inside it"
                f"{paired_on}."
            ),
            favours=None,
            detail=detail,
            caveats=caveats,
        )

    if equivalence is None:
        return Verdict(
            label="inconclusive",
            sentence=(
                f"{metric_name}: {interval_text} and includes 0. No practical margin was "
                f"declared, so equivalence cannot be assessed. This is 'undetermined', not a "
                f"finding that the policies match{paired_on}."
            ),
            favours=None,
            detail=detail,
            caveats=caveats,
        )

    return Verdict(
        label="inconclusive",
        sentence=(
            f"{metric_name}: {interval_text}, includes 0, and is too wide to fit inside the "
            f"declared margin {equivalence.margin.describe()}. Neither superiority nor "
            f"equivalence is established; the data do not resolve the question{paired_on}."
        ),
        favours=None,
        detail=detail,
        caveats=caveats,
    )
