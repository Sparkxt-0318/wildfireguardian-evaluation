"""Cluster bootstrap over the unit of inference.

The resampling unit *is* the statistical claim.  Resampling residents asks
"what if we had drawn different residents from these same fires?" -- a question
almost nobody is asking.  Resampling worlds asks "what if we had drawn
different fires?", which is the question every generalisation depends on.  The
two give intervals that differ by a factor of roughly the square root of the
design effect, so the choice is not a detail.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Callable, Sequence

import numpy as np

from wg_eval.pairing import ClusteredValues, PairedPanel

_NORM = NormalDist()
Estimator = Callable[[np.ndarray], float]


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
    p_two_sided: float | None = None
    notes: list[str] = field(default_factory=list)
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
            "n_resamples": self.n_resamples,
            "n_effective": self.n_effective,
            "seed": self.seed,
            "cluster_level": self.cluster_level,
            "n_clusters": self.n_clusters,
            "standard_error": _jsonable(self.standard_error),
            "p_two_sided": _jsonable(self.p_two_sided),
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
        notes.append("BCa requested but the leave-one-cluster-out jackknife was unusable; "
                     "fell back to percentile endpoints.")
        return _percentile_ci(replicates, confidence_level)

    prop_below = float(np.mean(replicates < estimate))
    if prop_below <= 0.0 or prop_below >= 1.0:
        notes.append("BCa bias correction is undefined (every replicate falls on one side "
                     "of the estimate); fell back to percentile endpoints.")
        return _percentile_ci(replicates, confidence_level)
    z0 = _NORM.inv_cdf(prop_below)

    centred = jackknife.mean() - jackknife
    denom = 6.0 * (float(np.sum(centred**2)) ** 1.5)
    if denom == 0.0 or not math.isfinite(denom):
        notes.append("BCa acceleration is undefined (no between-cluster variation); "
                     "fell back to percentile endpoints.")
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

    Computed by inverting the percentile interval: the smallest 1 - level at
    which the interval still excludes zero.  It is reported for completeness
    only; the interval and the equivalence test carry the conclusion.
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


def cluster_bootstrap(
    values: ClusteredValues,
    estimator: Estimator,
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    confidence_level: float = 0.95,
    method: str = "percentile",
    cluster_level: str = "world",
) -> BootstrapResult:
    """Bootstrap a statistic by resampling whole clusters with replacement.

    Every observation inside a drawn cluster travels with it, so within-cluster
    correlation is preserved and the interval widens to the width the design
    actually earns.
    """
    n_clusters = values.n_clusters
    if n_clusters < 2:
        raise ValueError(
            f"cluster bootstrap needs at least 2 {cluster_level}s; got {n_clusters}"
        )
    estimate = float(estimator(values.all_values()))
    rng = np.random.default_rng(seed)
    draws = _resample_indices(rng, n_clusters, n_resamples)
    replicates = np.empty(n_resamples, dtype="float64")
    for b in range(n_resamples):
        replicates[b] = estimator(values.take(draws[b]))

    notes: list[str] = []
    finite = replicates[np.isfinite(replicates)]
    if len(finite) < n_resamples:
        notes.append(
            f"{n_resamples - len(finite)} of {n_resamples} bootstrap replicates were "
            "non-finite (an empty or degenerate resample) and were discarded."
        )
    if len(finite) < 100:
        raise ValueError(
            f"only {len(finite)} usable bootstrap replicates; the statistic is too "
            "degenerate on this data for interval estimation"
        )

    jackknife = None
    if method == "bca":
        jackknife = np.array(
            [
                estimator(values.take(np.delete(np.arange(n_clusters), i)))
                for i in range(n_clusters)
            ],
            dtype="float64",
        )

    if method == "percentile":
        lo, hi = _percentile_ci(finite, confidence_level)
    elif method == "basic":
        lo, hi = _basic_ci(finite, estimate, confidence_level)
    elif method == "bca":
        lo, hi = _bca_ci(finite, estimate, jackknife, confidence_level, notes)
    else:
        raise ValueError(f"unknown bootstrap method {method!r}")

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
        p_two_sided=None,
        notes=notes,
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
) -> tuple[BootstrapResult, BootstrapResult, BootstrapResult]:
    """Bootstrap two arms and their difference on a shared cluster resample.

    Each replicate draws one set of clusters and evaluates *both* policies on
    it, so the shared cluster-level noise cancels in the difference exactly as
    it does in the point estimate.  Returns
    ``(baseline_result, candidate_result, difference_result)`` where the
    difference is ``candidate - baseline``.
    """
    for policy in (baseline, candidate):
        if policy not in panel.values:
            raise KeyError(f"policy {policy!r} is not in the paired panel")
    n_clusters = panel.n_clusters
    if n_clusters < 2:
        raise ValueError(
            f"paired bootstrap needs at least 2 shared {panel.cluster_level}s; got {n_clusters}"
        )

    base_values = panel.values[baseline]
    cand_values = panel.values[candidate]
    est_base = float(estimator(base_values.all_values()))
    est_cand = float(estimator(cand_values.all_values()))
    est_diff = est_cand - est_base

    rng = np.random.default_rng(seed)
    draws = _resample_indices(rng, n_clusters, n_resamples)
    rep_base = np.empty(n_resamples, dtype="float64")
    rep_cand = np.empty(n_resamples, dtype="float64")
    for b in range(n_resamples):
        idx = draws[b]
        rep_base[b] = estimator(base_values.take(idx))
        rep_cand[b] = estimator(cand_values.take(idx))
    rep_diff = rep_cand - rep_base

    jack_base = jack_cand = jack_diff = None
    if method == "bca":
        keep = [np.delete(np.arange(n_clusters), i) for i in range(n_clusters)]
        jack_base = np.array([estimator(base_values.take(k)) for k in keep], dtype="float64")
        jack_cand = np.array([estimator(cand_values.take(k)) for k in keep], dtype="float64")
        jack_diff = jack_cand - jack_base

    results = []
    for estimate, replicates, jackknife, label in (
        (est_base, rep_base, jack_base, baseline),
        (est_cand, rep_cand, jack_cand, candidate),
        (est_diff, rep_diff, jack_diff, f"{candidate}-{baseline}"),
    ):
        notes: list[str] = []
        if not panel.paired:
            notes.append(
                "UNPAIRED: the compared policies do not share their cluster set; "
                "the difference confounds policy with cluster difficulty."
            )
        finite = replicates[np.isfinite(replicates)]
        if len(finite) < 100:
            raise ValueError(
                f"only {len(finite)} usable bootstrap replicates for {label!r}; "
                "the statistic is too degenerate on this data"
            )
        if len(finite) < len(replicates):
            notes.append(
                f"{len(replicates) - len(finite)} non-finite replicate(s) discarded."
            )
        if method == "percentile":
            lo, hi = _percentile_ci(finite, confidence_level)
        elif method == "basic":
            lo, hi = _basic_ci(finite, estimate, confidence_level)
        elif method == "bca":
            lo, hi = _bca_ci(finite, estimate, jackknife, confidence_level, notes)
        else:
            raise ValueError(f"unknown bootstrap method {method!r}")
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
                cluster_level=panel.cluster_level,
                n_clusters=n_clusters,
                standard_error=float(finite.std(ddof=1)),
                p_two_sided=_bootstrap_p_value(finite, estimate),
                notes=notes,
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
    :func:`wg_eval.compare.compare_policies`; ``bootstrap.cluster_level:
    resident`` is rejected by the config loader.
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
        cluster_level="resident (PSEUDOREPLICATION)",
        n_clusters=int(arr.size),
        standard_error=float(finite.std(ddof=1)),
        p_two_sided=_bootstrap_p_value(finite, estimate),
        notes=[
            "This interval treats nested observations as independent replicates. "
            "It is shown for contrast only and must not be reported as a result."
        ],
        replicates=finite,
    )


def design_effect(panel: PairedPanel, policy: str) -> dict[str, Any]:
    """Estimate the design effect (variance inflation from clustering).

    Uses the one-way random-effects intraclass correlation over clusters:
    ``deff = 1 + (m - 1) * icc`` with ``m`` the average cluster size.  It is a
    diagnostic, not an inference: the bootstrap does the inference.
    """
    values = panel.values[policy]
    sizes = values.lengths[values.lengths > 0]
    if len(sizes) < 2 or int(sizes.sum()) <= len(sizes):
        return {"available": False, "reason": "one observation per cluster; deff = 1 by construction"}
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
        return {"available": False, "reason": "zero within-cluster variance"}
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
