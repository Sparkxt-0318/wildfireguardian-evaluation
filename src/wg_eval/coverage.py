"""Uncertainty in an estimated coverage probability.

A simulation that reports "coverage was 90%" has reported a *statistic*, not a
property.  With 100 replications, an estimate of 0.90 is consistent with a true
coverage anywhere between roughly 0.82 and 0.95, and a reader who is not told
that will over-read the difference between 90% and 95%.

Two distinct uncertainties live in the same sentence and are kept apart here:

* **Bootstrap interval coverage** -- the probability that the procedure's
  nominal 95% interval contains the true value.  This is the property under
  study.
* **Monte Carlo uncertainty** -- how precisely a finite number of simulation
  replications pins that probability down.  This is the noise in the study of
  the property.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Sequence

import numpy as np

_NORM = NormalDist()


def monte_carlo_se(p_hat: float, n_replications: int) -> float:
    """Standard error of a coverage estimate from ``n`` independent replications."""
    if n_replications <= 0:
        return math.nan
    p = min(max(float(p_hat), 0.0), 1.0)
    return math.sqrt(p * (1.0 - p) / n_replications)


def wilson_interval(
    successes: int, n_replications: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because coverage estimates live near
    0.9-1.0, where the Wald interval misbehaves and can exceed 1.
    """
    if n_replications <= 0:
        return (math.nan, math.nan)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie in (0, 1)")
    z = _NORM.inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    n = float(n_replications)
    p = float(successes) / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class CoverageEstimate:
    """An empirical coverage probability with its Monte Carlo uncertainty."""

    label: str
    n_replications: int
    n_covered: int
    nominal_level: float
    mc_confidence: float = 0.95
    mean_width: float = math.nan

    @property
    def empirical_coverage(self) -> float:
        return float(self.n_covered) / self.n_replications if self.n_replications else math.nan

    @property
    def monte_carlo_se(self) -> float:
        return monte_carlo_se(self.empirical_coverage, self.n_replications)

    @property
    def wilson_ci(self) -> tuple[float, float]:
        return wilson_interval(self.n_covered, self.n_replications, self.mc_confidence)

    @property
    def consistent_with_nominal(self) -> bool:
        """True when the Monte Carlo interval does not exclude the nominal level.

        A coverage estimate that is *consistent with* nominal is not a
        demonstration that the procedure is calibrated; it is the absence of
        evidence that it is not, at this number of replications.
        """
        lo, hi = self.wilson_ci
        return bool(lo <= self.nominal_level <= hi)

    def as_dict(self) -> dict[str, Any]:
        lo, hi = self.wilson_ci
        return {
            "label": self.label,
            "n_replications": self.n_replications,
            "n_covered": self.n_covered,
            "nominal_level": self.nominal_level,
            "empirical_coverage": self.empirical_coverage,
            "monte_carlo_se": self.monte_carlo_se,
            "mc_confidence": self.mc_confidence,
            "wilson_ci": [lo, hi],
            "consistent_with_nominal": self.consistent_with_nominal,
            "mean_interval_width": None if math.isnan(self.mean_width) else self.mean_width,
        }

    def describe(self) -> str:
        lo, hi = self.wilson_ci
        return (
            f"{self.label}: {self.empirical_coverage:.1%} coverage of a nominal "
            f"{self.nominal_level:.0%} interval "
            f"(R={self.n_replications}, MC SE {self.monte_carlo_se:.3f}, "
            f"{self.mc_confidence:.0%} Wilson CI [{lo:.1%}, {hi:.1%}])"
        )


def estimate_coverage(
    covered: Sequence[bool] | np.ndarray,
    *,
    label: str,
    nominal_level: float,
    mc_confidence: float = 0.95,
    widths: Sequence[float] | np.ndarray | None = None,
) -> CoverageEstimate:
    """Summarise per-replication coverage indicators into a :class:`CoverageEstimate`."""
    indicators = np.asarray(covered, dtype=bool)
    mean_width = math.nan
    if widths is not None:
        finite = np.asarray(widths, dtype="float64")
        finite = finite[np.isfinite(finite)]
        if finite.size:
            mean_width = float(finite.mean())
    return CoverageEstimate(
        label=label,
        n_replications=int(indicators.size),
        n_covered=int(indicators.sum()),
        nominal_level=float(nominal_level),
        mc_confidence=mc_confidence,
        mean_width=mean_width,
    )


def replications_for_precision(
    target_half_width: float, *, around: float = 0.95, confidence: float = 0.95
) -> int:
    """Replications needed to pin coverage down to +/- ``target_half_width``.

    Reported next to any coverage study whose interval is too wide to answer the
    question it was run to answer.
    """
    if target_half_width <= 0:
        raise ValueError("target_half_width must be positive")
    z = _NORM.inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    return int(math.ceil(z * z * around * (1.0 - around) / (target_half_width**2)))


def compare_coverage(a: CoverageEstimate, b: CoverageEstimate) -> dict[str, Any]:
    """Difference between two coverage estimates, with its Monte Carlo error.

    Used to say whether two procedures' measured coverage really differ, rather
    than eyeballing two percentages.
    """
    diff = a.empirical_coverage - b.empirical_coverage
    se = math.sqrt(a.monte_carlo_se**2 + b.monte_carlo_se**2)
    z = _NORM.inv_cdf(0.975)
    return {
        "labels": [a.label, b.label],
        "difference": diff,
        "monte_carlo_se": se,
        "approx_95_ci": [diff - z * se, diff + z * se],
        "distinguishable": bool(abs(diff) > z * se),
    }
