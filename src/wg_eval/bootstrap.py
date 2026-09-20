"""Cluster bootstrap over the declared unit of inference.

The resampling unit *is* the statistical claim.  Resampling observations asks
"what if we had drawn different observations from these same units?" -- a
question almost nobody is asking.  Resampling units asks "what if we had drawn
different units?", which is the question every generalisation depends on.  The
two give intervals that differ by roughly the square root of the design effect,
so the choice is not a detail.

Which interval is produced is likewise not a detail, and the methods are not
interchangeable.  ``docs/STATISTICAL_PROTOCOL.md`` section 5 states what each
one assumes and where it should not be trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Callable, Sequence

import numpy as np

from wg_eval.config import Bounds
from wg_eval.metrics import credibility
from wg_eval.pairing import ClusteredValues, PairedPanel

_NORM = NormalDist()
Estimator = Callable[[np.ndarray], float]

#: Below this many resampling units, the percentile interval's endpoints are
#: order statistics of very few distinct resamples and under-cover materially.
FEW_UNITS = 20


@dataclass
class BootstrapResult:
    """A point estimate with a cluster-bootstrap interval and its provenance."""

    estimate: float
    ci_low: float
    ci_high: float
    confidence_level: float
    method: str
    n_resamples: int
    n_effective: int
    seed: int
    cluster_level: str
    n_clusters: int
    standard_error: float
    hierarchical: bool = False
    p_two_sided: float | None = None
    #: Distinct resampled unit-index vectors actually drawn, when counted.
    notes: list[str] = field(default_factory=list)
    credibility: dict[str, Any] = field(default_factory=dict)
    bounds_check: dict[str, Any] = field(default_factory=dict)
    replicates: np.ndarray | None = field(default=None, repr=False)

    @property
    def ci(self) -> tuple[float, float]:
        return (self.ci_low, self.ci_high)

    @property
    def excludes_zero(self) -> bool:
        """True when the interval lies entirely above or entirely below zero."""
        return (self.ci_low > 0.0) or (self.ci_high < 0.0)

    def as_dict(self, include_replicates: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "estimate": _jsonable(self.estimate),
            "ci_low": _jsonable(self.ci_low),
            "ci_high": _jsonable(self.ci_high),
            "confidence_level": self.confidence_level,
            "method": self.method,
            "hierarchical": self.hierarchical,
            "n_resamples": self.n_resamples,
            "n_effective": self.n_effective,
            "seed": self.seed,
            "cluster_level": self.cluster_level,
            "n_clusters": self.n_clusters,
            "standard_error": _jsonable(self.standard_error),
            "p_two_sided": _jsonable(self.p_two_sided),
            "credibility": self.credibility,
            "bounds_check": self.bounds_check,
            "notes": list(self.notes),
        }
        if include_replicates and self.replicates is not None:
            out["replicates"] = [float(x) for x in self.replicates]
        return out


def _jsonable(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if not math.isfinite(value) else value


# ---------------------------------------------------------------------------
# interval constructions
# ---------------------------------------------------------------------------

def _percentile_ci(replicates: np.ndarray, confidence_level: float) -> tuple[float, float]:
    alpha = (1.0 - confidence_level) / 2.0
    return (
        float(np.quantile(replicates, alpha)),
        float(np.quantile(replicates, 1.0 - alpha)),
    )


def _basic_ci(replicates: np.ndarray, estimate: float, confidence_level: float) -> tuple[float, float]:
    lo, hi = _percentile_ci(replicates, confidence_level)
    return (2.0 * estimate - hi, 2.0 * estimate - lo)


def _bca_ci(
    replicates: np.ndarray,
    estimate: float,
    jackknife: np.ndarray | None,
    confidence_level: float,
    notes: list[str],
) -> tuple[float, float]:
    """Bias-corrected and accelerated interval, with a documented fallback."""
    if jackknife is None or len(jackknife) < 3 or not np.all(np.isfinite(jackknife)):
        notes.append(
            "BCa requested but the leave-one-unit-out jackknife was unusable; fell back to "
            "percentile endpoints."
        )
        return _percentile_ci(replicates, confidence_level)

    prop_below = float(np.mean(replicates < estimate))
    if prop_below <= 0.0 or prop_below >= 1.0:
        notes.append(
            "BCa bias correction is undefined (every replicate falls on one side of the "
            "estimate); fell back to percentile endpoints."
        )
        return _percentile_ci(replicates, confidence_level)
    z0 = _NORM.inv_cdf(prop_below)

    centred = jackknife.mean() - jackknife
    denom = 6.0 * (float(np.sum(centred**2)) ** 1.5)
    if denom == 0.0 or not math.isfinite(denom):
        notes.append(
            "BCa acceleration is undefined (no between-unit variation); fell back to "
            "percentile endpoints."
        )
        return _percentile_ci(replicates, confidence_level)
    accel = float(np.sum(centred**3)) / denom

    alpha = (1.0 - confidence_level) / 2.0
    out: list[float] = []
    for a in (alpha, 1.0 - alpha):
        z = _NORM.inv_cdf(a)
        adjusted = z0 + (z0 + z) / (1.0 - accel * (z0 + z))
        if not math.isfinite(adjusted):
            notes.append("BCa endpoint diverged; fell back to percentile endpoints.")
            return _percentile_ci(replicates, confidence_level)
        out.append(float(np.quantile(replicates, min(max(_NORM.cdf(adjusted), 0.0), 1.0))))
    return (out[0], out[1])


def _bootstrap_p_value(replicates: np.ndarray, estimate: float) -> float:
    """Two-sided bootstrap p-value for H0: theta = 0.

    Computed by inverting the percentile interval.  It is reported for
    completeness only; the interval and the equivalence test carry the
    conclusion, and no verdict in this library is based on this number.
    """
    n = len(replicates)
    if n == 0:
        return math.nan
    below = float(np.mean(replicates <= 0.0))
    above = float(np.mean(replicates >= 0.0))
    p = 2.0 * min(below, above)
    return float(min(1.0, max(p, 1.0 / (n + 1))))


def _resample_indices(rng: np.random.Generator, n_clusters: int, n_resamples: int) -> np.ndarray:
    return rng.integers(0, n_clusters, size=(n_resamples, n_clusters))


def _interval(
    method: str,
    replicates: np.ndarray,
    estimate: float,
    jackknife: np.ndarray | None,
    confidence_level: float,
    notes: list[str],
) -> tuple[float, float]:
    if method == "percentile":
        return _percentile_ci(replicates, confidence_level)
    if method == "basic":
        return _basic_ci(replicates, estimate, confidence_level)
    if method == "bca":
        return _bca_ci(replicates, estimate, jackknife, confidence_level, notes)
    raise ValueError(
        f"unknown bootstrap method {method!r}; supported methods are percentile, basic and bca. "
        "The studentized bootstrap is not implemented -- see docs/STATISTICAL_PROTOCOL.md."
    )


def small_sample_notes(
    *, estimator_name: str, n_clusters: int, n_values: int, cluster_level: str
) -> tuple[list[str], dict[str, Any]]:
    """Credibility findings for this statistic at this number of units."""
    notes: list[str] = []
    verdict = credibility(estimator_name, n_units=n_clusters, n_values=n_values)
    for reason in verdict["reasons"]:
        notes.append(f"NOT CREDIBLE AT THIS SAMPLE SIZE: {reason}.")
    if n_clusters < FEW_UNITS:
        notes.append(
            f"{n_clusters} {cluster_level}s is below the ~{FEW_UNITS} at which percentile "
            "cluster-bootstrap intervals approach their nominal level; the interval is "
            "likely narrower than its label."
        )
    return notes, verdict


def bounds_check(
    *, ci_low: float, ci_high: float, estimate: float, bounds: Bounds, is_difference: bool
) -> dict[str, Any]:
    """Flag interval endpoints outside the metric's declared support.

    Endpoints are **never clamped**.  An interval that runs past the support is
    evidence that the interval construction does not suit the metric, and
    hiding it by truncation would misstate the precision rather than fix it.
    """
    if not bounds.declared:
        return {"checked": False}
    lo_bound, hi_bound = bounds.difference_bounds() if is_difference else (bounds.lower, bounds.upper)
    violations = []
    if lo_bound is not None and math.isfinite(ci_low) and ci_low < lo_bound:
        violations.append({"endpoint": "ci_low", "value": ci_low, "bound": lo_bound})
    if hi_bound is not None and math.isfinite(ci_high) and ci_high > hi_bound:
        violations.append({"endpoint": "ci_high", "value": ci_high, "bound": hi_bound})
    return {
        "checked": True,
        "scale": "difference" if is_difference else "level",
        "support": [lo_bound, hi_bound],
        "violations": violations,
        "impossible": bool(violations),
        "note": (
            "Interval endpoints lie outside the metric's declared support. They are reported "
            "unclamped: truncating them would understate the interval's width without making "
            "the construction appropriate. Consider an interval on a transformed scale."
            if violations
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def cluster_bootstrap(
    values: ClusteredValues,
    estimator: Estimator,
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    confidence_level: float = 0.95,
    method: str = "percentile",
    cluster_level: str = "world_id",
    hierarchical: bool = False,
    estimator_name: str = "mean",
    bounds: Bounds | None = None,
) -> BootstrapResult:
    """Bootstrap a statistic by resampling whole units with replacement.

    Every observation inside a drawn unit travels with it, so within-unit
    correlation is preserved and the interval widens to the width the design
    actually earns.

    With ``hierarchical=True`` the draw is two-stage: units, then the level
    below each drawn unit.  See :func:`paired_cluster_bootstrap` for what that
    changes about the estimated quantity.
    """
    n_clusters = values.n_clusters
    if n_clusters < 2:
        raise ValueError(f"cluster bootstrap needs at least 2 {cluster_level}s; got {n_clusters}")
    estimate = float(estimator(values.all_values()))
    rng = np.random.default_rng(seed)
    draws = _resample_indices(rng, n_clusters, n_resamples)
    replicates = np.empty(n_resamples, dtype="float64")
    for b in range(n_resamples):
        sample = (
            values.take_hierarchical(draws[b], rng) if hierarchical else values.take(draws[b])
        )
        replicates[b] = estimator(sample)

    notes: list[str] = []
    finite = replicates[np.isfinite(replicates)]
    if len(finite) < n_resamples:
        notes.append(
            f"{n_resamples - len(finite)} of {n_resamples} bootstrap replicates were non-finite "
            "(an empty or degenerate resample) and were discarded."
        )
    if len(finite) < 100:
        raise ValueError(
            f"only {len(finite)} usable bootstrap replicates; the statistic is too degenerate "
            "on this data for interval estimation"
        )

    jackknife = None
    if method == "bca":
        jackknife = np.array(
            [estimator(values.take(np.delete(np.arange(n_clusters), i))) for i in range(n_clusters)],
            dtype="float64",
        )

    lo, hi = _interval(method, finite, estimate, jackknife, confidence_level, notes)
    extra, verdict = small_sample_notes(
        estimator_name=estimator_name,
        n_clusters=n_clusters,
        n_values=int(len(values.all_values())),
        cluster_level=cluster_level,
    )
    notes.extend(extra)
    checks = bounds_check(
        ci_low=lo, ci_high=hi, estimate=estimate,
        bounds=bounds or Bounds(), is_difference=False,
    )
    if checks.get("impossible"):
        notes.append(checks["note"])

    return BootstrapResult(
        estimate=estimate,
        ci_low=lo,
        ci_high=hi,
        confidence_level=confidence_level,
        method=method,
        n_resamples=n_resamples,
        n_effective=int(len(finite)),
        seed=seed,
        cluster_level=cluster_level,
        n_clusters=n_clusters,
        standard_error=float(finite.std(ddof=1)),
        hierarchical=hierarchical,
        p_two_sided=None,
        notes=notes,
        credibility=verdict,
        bounds_check=checks,
        replicates=finite,
    )


def paired_cluster_bootstrap(
    panel: PairedPanel,
    estimator: Estimator,
    baseline: str,
    candidate: str,
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    confidence_level: float = 0.95,
    method: str = "percentile",
    hierarchical: bool = False,
    estimator_name: str = "mean",
    paired: bool = True,
) -> tuple[BootstrapResult, BootstrapResult, BootstrapResult]:
    """Bootstrap two arms and their difference on a shared unit resample.

    Each replicate draws one set of units and evaluates *both* policies on it,
    so the shared unit-level noise cancels in the difference exactly as it does
    in the point estimate.  Returns ``(baseline, candidate, difference)`` where
    the difference is ``candidate - baseline``.

    With ``paired=False`` each arm is resampled independently, which is the
    two-independent-samples analysis.  It is offered because a configuration may
    honestly declare ``comparison.paired: false``, and it must then get what it
    asked for rather than a paired analysis wearing an unpaired label.  On data
    that really is paired it discards the design's precision.

    ``hierarchical=True`` additionally resamples, within each drawn unit, the
    level below it.  That estimates the uncertainty of a quantity defined over
    *both* a population of units and a population of sub-units within them.  It
    is not a free improvement: it widens the interval for a different estimand,
    and it is off by default.
    """
    for policy in (baseline, candidate):
        if policy not in panel.values:
            raise KeyError(f"policy {policy!r} is not in the paired panel")
    n_clusters = panel.n_clusters
    unit = panel.cluster_level
    if n_clusters < 2:
        raise ValueError(f"paired bootstrap needs at least 2 shared {unit}s; got {n_clusters}")
    if hierarchical and not (
        panel.values[baseline].has_substructure or panel.values[candidate].has_substructure
    ):
        raise ValueError(
            "bootstrap.hierarchical=True but the panel has no level below the resampling "
            f"unit ({unit}) for this metric. A two-stage bootstrap of a one-stage design is "
            "the one-stage bootstrap with extra steps; set hierarchical: false."
        )

    base_values = panel.values[baseline]
    cand_values = panel.values[candidate]
    est_base = float(estimator(base_values.all_values()))
    est_cand = float(estimator(cand_values.all_values()))
    est_diff = est_cand - est_base

    rng = np.random.default_rng(seed)
    draws = _resample_indices(rng, n_clusters, n_resamples)
    # A paired bootstrap applies ONE draw to both arms, so the shared unit-level
    # noise cancels in the difference exactly as it does in the point estimate.
    # An unpaired bootstrap draws each arm separately and does not.
    other = draws if paired else _resample_indices(rng, n_clusters, n_resamples)
    rep_base = np.empty(n_resamples, dtype="float64")
    rep_cand = np.empty(n_resamples, dtype="float64")
    for b in range(n_resamples):
        idx, idx_other = draws[b], other[b]
        if hierarchical:
            rep_base[b] = estimator(base_values.take_hierarchical(idx, rng))
            rep_cand[b] = estimator(cand_values.take_hierarchical(idx_other, rng))
        else:
            rep_base[b] = estimator(base_values.take(idx))
            rep_cand[b] = estimator(cand_values.take(idx_other))
    rep_diff = rep_cand - rep_base

    jack_base = jack_cand = jack_diff = None
    if method == "bca":
        keep = [np.delete(np.arange(n_clusters), i) for i in range(n_clusters)]
        jack_base = np.array([estimator(base_values.take(k)) for k in keep], dtype="float64")
        jack_cand = np.array([estimator(cand_values.take(k)) for k in keep], dtype="float64")
        jack_diff = jack_cand - jack_base

    metric_bounds = panel.metric.bounds
    results: list[BootstrapResult] = []
    for estimate, replicates, jackknife, label, is_diff, n_values in (
        (est_base, rep_base, jack_base, baseline, False, len(base_values.all_values())),
        (est_cand, rep_cand, jack_cand, candidate, False, len(cand_values.all_values())),
        (est_diff, rep_diff, jack_diff, f"{candidate}-{baseline}", True,
         min(len(base_values.all_values()), len(cand_values.all_values()))),
    ):
        notes: list[str] = []
        if not panel.paired:
            notes.append(
                "UNPAIRED UNIT SETS: the compared policies do not share their unit set; the "
                "difference confounds policy with unit difficulty."
            )
        if not paired and is_diff:
            notes.append(
                "UNPAIRED RESAMPLE (comparison.paired: false): each arm was resampled "
                "independently, so unit-level variation does not cancel in the difference. On "
                "data where units are shared this discards the design's precision."
            )
        finite = replicates[np.isfinite(replicates)]
        if len(finite) < 100:
            raise ValueError(
                f"only {len(finite)} usable bootstrap replicates for {label!r}; the statistic "
                "is too degenerate on this data"
            )
        if len(finite) < len(replicates):
            notes.append(f"{len(replicates) - len(finite)} non-finite replicate(s) discarded.")
        lo, hi = _interval(method, finite, estimate, jackknife, confidence_level, notes)
        extra, verdict = small_sample_notes(
            estimator_name=estimator_name,
            n_clusters=n_clusters,
            n_values=int(n_values),
            cluster_level=unit,
        )
        notes.extend(extra)
        checks = bounds_check(
            ci_low=lo, ci_high=hi, estimate=estimate, bounds=metric_bounds, is_difference=is_diff
        )
        if checks.get("impossible"):
            notes.append(checks["note"])
        if hierarchical:
            notes.append(
                "Two-stage bootstrap: units were resampled, then the level below each drawn "
                "unit. The interval covers variation in both populations, so it is not "
                "comparable with a one-stage interval for the same statistic."
            )
        results.append(
            BootstrapResult(
                estimate=estimate,
                ci_low=lo,
                ci_high=hi,
                confidence_level=confidence_level,
                method=method,
                n_resamples=n_resamples,
                n_effective=int(len(finite)),
                seed=seed,
                cluster_level=unit,
                n_clusters=n_clusters,
                standard_error=float(finite.std(ddof=1)),
                hierarchical=hierarchical,
                p_two_sided=_bootstrap_p_value(finite, estimate),
                notes=notes,
                credibility=verdict,
                bounds_check=checks,
                replicates=finite,
            )
        )
    return results[0], results[1], results[2]


def naive_iid_bootstrap(
    values: Sequence[float] | np.ndarray,
    estimator: Estimator,
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    confidence_level: float = 0.95,
) -> BootstrapResult:
    """Resample individual observations as if they were independent.

    This is the pseudoreplication error, implemented on purpose so that the
    red-team suite can measure how badly it under-covers.  It is never used by
    :func:`wg_eval.compare.compare_policies`; ``inference.primary_unit:
    resident_id`` is rejected by the config loader.
    """
    arr = np.asarray(values, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        raise ValueError("need at least 2 observations")
    rng = np.random.default_rng(seed)
    estimate = float(estimator(arr))
    draws = rng.integers(0, arr.size, size=(n_resamples, arr.size))
    replicates = np.array([estimator(arr[d]) for d in draws], dtype="float64")
    finite = replicates[np.isfinite(replicates)]
    lo, hi = _percentile_ci(finite, confidence_level)
    return BootstrapResult(
        estimate=estimate,
        ci_low=lo,
        ci_high=hi,
        confidence_level=confidence_level,
        method="percentile",
        n_resamples=n_resamples,
        n_effective=int(len(finite)),
        seed=seed,
        cluster_level="observation (PSEUDOREPLICATION)",
        n_clusters=int(arr.size),
        standard_error=float(finite.std(ddof=1)),
        p_two_sided=_bootstrap_p_value(finite, estimate),
        notes=[
            "This interval treats nested observations as independent replicates. It is shown "
            "for contrast only and must not be reported as a result."
        ],
        replicates=finite,
    )


def design_effect(panel: PairedPanel, policy: str) -> dict[str, Any]:
    """Estimate the design effect (variance inflation from clustering).

    Uses the one-way random-effects intraclass correlation over units:
    ``deff = 1 + (m - 1) * icc`` with ``m`` the average unit size.  It is a
    diagnostic, not an inference: the bootstrap does the inference.
    """
    values = panel.values[policy]
    sizes = values.lengths[values.lengths > 0]
    if len(sizes) < 2 or int(sizes.sum()) <= len(sizes):
        return {"available": False, "reason": "one value per unit; deff = 1 by construction"}
    groups = [values.cluster_values(i) for i in range(values.n_clusters) if values.lengths[i] > 0]
    grand = float(np.concatenate(groups).mean())
    k = len(groups)
    n_total = int(sum(len(g) for g in groups))
    ss_between = sum(len(g) * (float(g.mean()) - grand) ** 2 for g in groups)
    ss_within = sum(float(((g - g.mean()) ** 2).sum()) for g in groups)
    df_between, df_within = k - 1, n_total - k
    if df_between <= 0 or df_within <= 0:
        return {"available": False, "reason": "insufficient degrees of freedom"}
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    m0 = (n_total - sum(len(g) ** 2 for g in groups) / n_total) / df_between
    if ms_within == 0 or m0 == 0:
        return {"available": False, "reason": "zero within-unit variance"}
    var_between = max((ms_between - ms_within) / m0, 0.0)
    icc = var_between / (var_between + ms_within)
    mean_size = n_total / k
    return {
        "available": True,
        "icc": float(icc),
        "mean_cluster_size": float(mean_size),
        "design_effect": float(1.0 + (mean_size - 1.0) * icc),
        "effective_sample_size": float(n_total / max(1.0 + (mean_size - 1.0) * icc, 1e-12)),
        "n_observations": n_total,
        "n_clusters": k,
    }
