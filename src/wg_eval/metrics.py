"""The metric registry: estimators that map a vector of values to one number.

Every estimator here is a *point estimator applied at a declared nesting
level*.  A metric is therefore never just "mean loss"; it is "mean loss, over
events, after observations were averaged into events".  Changing the level
changes the estimand, so the level travels with the metric everywhere.

Finite-sample conventions are fixed and documented rather than inherited from
whatever a library happens to do.  ``docs/METRIC_REGISTRY.md`` states each one
with a hand-computable example, and ``tests/test_reference_calculations.py``
checks the code against those examples by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Mapping

import numpy as np

Estimator = Callable[[np.ndarray], float]


@dataclass(frozen=True)
class EstimatorSpec:
    """A registered estimator with everything a report needs to describe it."""

    name: str
    factory: Callable[..., Estimator]
    #: ``str.format`` template rendered with the estimator's parameters.
    definition: str
    #: Summarises a tail rather than the centre of the distribution.
    is_tail: bool = False
    #: Below this many values at the estimator's own level, the estimate is not
    #: credible and the library says so. ``None`` means no metric-specific floor.
    min_values: int | None = None
    #: Below this many *independent units*, the interval is not credible.
    #: Tail statistics need more units than central ones for the same stability.
    min_units: int = 8
    #: How the estimate behaves as the sample shrinks, for the warning text.
    small_sample_note: str = ""
    #: Extra parameters whose value changes the estimand and must be reported.
    estimand_params: tuple[str, ...] = field(default_factory=tuple)

    def build(self, params: Mapping[str, object] | None = None) -> Estimator:
        return self.factory(**dict(params or {}))


#: name -> EstimatorSpec
METRIC_REGISTRY: dict[str, EstimatorSpec] = {}


def register_estimator(
    name: str,
    definition: str,
    *,
    is_tail: bool = False,
    min_values: int | None = None,
    min_units: int = 8,
    small_sample_note: str = "",
    estimand_params: tuple[str, ...] = (),
) -> Callable[[Callable[..., Estimator]], Callable[..., Estimator]]:
    """Register an estimator factory under ``name``.

    ``definition`` is a ``str.format`` template rendered with the estimator's
    parameters; it becomes the metric definition recorded in provenance.
    """

    def decorator(factory: Callable[..., Estimator]) -> Callable[..., Estimator]:
        if name in METRIC_REGISTRY:
            raise ValueError(f"estimator {name!r} is already registered")
        METRIC_REGISTRY[name] = EstimatorSpec(
            name=name,
            factory=factory,
            definition=definition,
            is_tail=is_tail,
            min_values=min_values,
            min_units=min_units,
            small_sample_note=small_sample_note,
            estimand_params=estimand_params,
        )
        return factory

    return decorator


def tail_count(alpha: float, n: int) -> int:
    """How many observations the CVaR at ``alpha`` averages over ``n`` values.

    Defined as ``k = max(1, ceil((1 - alpha) * n))``.  The product is rounded to
    nine decimal places before the ceiling because binary floating point cannot
    represent most decimal alphas: ``(1 - 0.7) * 10`` evaluates to
    3.0000000000000004, whose ceiling is 4, so an unrounded implementation
    silently averages one value more than its own definition states for many
    ordinary (alpha, n) pairs.
    """
    return max(1, math.ceil(round((1.0 - alpha) * n, 9)))


def _clean(values: np.ndarray) -> np.ndarray:
    """Finite values only.  Non-finite input is excluded, never imputed here."""
    arr = np.asarray(values, dtype="float64").ravel()
    return arr[np.isfinite(arr)]


@register_estimator(
    "mean",
    "arithmetic mean of {level}-level values of `{column}`",
    min_units=5,
    small_sample_note="the mean is unbiased at any n; its interval is what degrades",
)
def _mean() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.mean()) if arr.size else math.nan

    return estimator


@register_estimator(
    "rate",
    "proportion of {level}-level values of `{column}` equal to 1",
    min_units=5,
    small_sample_note="a proportion from few units lands on a coarse grid of achievable values",
)
def _rate() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.mean()) if arr.size else math.nan

    return estimator


@register_estimator("sum", "sum of {level}-level values of `{column}`", min_units=5)
def _sum() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.sum()) if arr.size else math.nan

    return estimator


@register_estimator(
    "median",
    "median of {level}-level values of `{column}` (mean of the two central "
    "order statistics when the count is even)",
    min_units=8,
    small_sample_note="the sample median jumps between order statistics at small n",
)
def _median() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(np.median(arr)) if arr.size else math.nan

    return estimator


@register_estimator(
    "std",
    "sample standard deviation (ddof=1) of {level}-level values of `{column}`",
    min_values=2,
    min_units=10,
)
def _std() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.std(ddof=1)) if arr.size > 1 else math.nan

    return estimator


@register_estimator(
    "quantile",
    "the q={q} quantile of {level}-level values of `{column}`, by linear "
    "interpolation between order statistics (numpy 'linear' method: the "
    "quantile sits at position q*(n-1) in the sorted values, zero-indexed)",
    is_tail=True,
    min_values=2,
    min_units=20,
    small_sample_note=(
        "an extreme quantile from few units is determined by one or two order "
        "statistics and moves discontinuously when either changes"
    ),
    estimand_params=("q",),
)
def _quantile(q: float = 0.9) -> Estimator:
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile q must lie in [0, 1]; got {q}")

    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(np.quantile(arr, q)) if arr.size else math.nan

    return estimator


@register_estimator(
    "cvar",
    "CVaR at alpha={alpha} in the {tail} tail of {level}-level values of "
    "`{column}`: the unweighted mean of the k = max(1, ceil((1-alpha)*n)) most "
    "extreme values, with (1-alpha)*n rounded to 9 decimals before the ceiling, "
    "and ties included by position in the sorted order",
    is_tail=True,
    min_values=2,
    min_units=20,
    small_sample_note=(
        "CVaR averages the worst k = ceil((1-alpha)*n) values; when n is small k "
        "is 1 or 2 and the statistic is an extreme order statistic in disguise"
    ),
    estimand_params=("alpha", "tail"),
)
def _cvar(alpha: float = 0.9, tail: str = "upper") -> Estimator:
    if not 0.0 <= alpha < 1.0:
        raise ValueError(f"cvar alpha must lie in [0, 1); got {alpha}")
    if tail not in ("upper", "lower"):
        raise ValueError(f"cvar tail must be 'upper' or 'lower'; got {tail!r}")

    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        if arr.size == 0:
            return math.nan
        k = tail_count(alpha, arr.size)
        ordered = np.sort(arr)
        worst = ordered[-k:] if tail == "upper" else ordered[:k]
        return float(worst.mean())

    return estimator


@register_estimator(
    "trimmed_mean",
    "mean of {level}-level values of `{column}` after removing "
    "floor({proportion} * n) values from each end of the sorted order",
    min_values=2,
    min_units=10,
    estimand_params=("proportion",),
)
def _trimmed_mean(proportion: float = 0.1) -> Estimator:
    if not 0.0 <= proportion < 0.5:
        raise ValueError(f"trim proportion must lie in [0, 0.5); got {proportion}")

    def estimator(values: np.ndarray) -> float:
        arr = np.sort(_clean(values))
        if arr.size == 0:
            return math.nan
        cut = int(math.floor(proportion * arr.size))
        kept = arr[cut : arr.size - cut] if arr.size - 2 * cut > 0 else arr
        return float(kept.mean())

    return estimator


@register_estimator(
    "p_exceed",
    "proportion of {level}-level values of `{column}` strictly greater than "
    "{threshold} (a value exactly equal to the threshold does not count)",
    is_tail=True,
    min_units=20,
    small_sample_note=(
        "an exceedance probability estimated from few units is a count divided by a "
        "small denominator and can only take a handful of values"
    ),
    estimand_params=("threshold",),
)
def _p_exceed(threshold: float = 0.0) -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float((arr > threshold).mean()) if arr.size else math.nan

    return estimator


@register_estimator(
    "max",
    "maximum {level}-level value of `{column}`",
    is_tail=True,
    min_units=30,
    small_sample_note=(
        "the maximum has no limiting normal distribution and the bootstrap is not "
        "consistent for it; treat the interval as descriptive"
    ),
)
def _max() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.max()) if arr.size else math.nan

    return estimator


@register_estimator(
    "min",
    "minimum {level}-level value of `{column}`",
    is_tail=True,
    min_units=30,
    small_sample_note=(
        "the minimum has no limiting normal distribution and the bootstrap is not "
        "consistent for it; treat the interval as descriptive"
    ),
)
def _min() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.min()) if arr.size else math.nan

    return estimator


@register_estimator("count", "number of finite {level}-level values of `{column}`", min_units=1)
def _count() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        return float(_clean(values).size)

    return estimator


#: Estimators whose value depends on the shape of a tail rather than the centre.
TAIL_ESTIMATORS: frozenset[str] = frozenset(
    name for name, spec in METRIC_REGISTRY.items() if spec.is_tail
)


def build_estimator(name: str, params: Mapping[str, object] | None = None) -> Estimator:
    """Instantiate a registered estimator with ``params``."""
    if name not in METRIC_REGISTRY:
        known = ", ".join(sorted(METRIC_REGISTRY))
        raise KeyError(f"unknown estimator {name!r}; registered estimators: {known}")
    return METRIC_REGISTRY[name].build(params)


def estimate(values: np.ndarray, name: str, params: Mapping[str, object] | None = None) -> float:
    """Apply a registered estimator to ``values`` in one call."""
    return build_estimator(name, params)(values)


def _factory_defaults(factory: Callable[..., Estimator]) -> dict[str, object]:
    import inspect

    sig = inspect.signature(factory)
    return {
        name: param.default
        for name, param in sig.parameters.items()
        if param.default is not inspect.Parameter.empty
    }


def definition_of(
    name: str, *, column: str, level: str, params: Mapping[str, object] | None = None
) -> str:
    """Render the human-readable definition string recorded in provenance."""
    if name not in METRIC_REGISTRY:
        raise KeyError(f"unknown estimator {name!r}")
    spec = METRIC_REGISTRY[name]
    fields: dict[str, object] = {"column": column, "level": level}
    fields.update(_factory_defaults(spec.factory))
    fields.update(dict(params or {}))
    try:
        return spec.definition.format(**fields)
    except (KeyError, ValueError):  # pragma: no cover - defensive
        return f"{name} of {level}-level values of `{column}` with params {dict(params or {})}"


def credibility(name: str, *, n_units: int, n_values: int) -> dict[str, object]:
    """Judge whether an estimate is credible at the available sample size.

    Returns ``{"credible": bool, "reasons": [...]}``.  The thresholds are
    per estimator, not a universal minimum n: a mean of 8 units is coarse but
    usable, while a 90% CVaR of 8 units is the average of one value.
    """
    spec = METRIC_REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown estimator {name!r}")
    reasons: list[str] = []
    if spec.min_values is not None and n_values < spec.min_values:
        reasons.append(
            f"`{name}` needs at least {spec.min_values} values at its own level; {n_values} present"
        )
    if n_units < spec.min_units:
        reasons.append(
            f"`{name}` is not credible below ~{spec.min_units} independent units; "
            f"{n_units} available"
            + (f". {spec.small_sample_note}" if spec.small_sample_note else "")
        )
    return {
        "credible": not reasons,
        "reasons": reasons,
        "n_units": n_units,
        "n_values": n_values,
        "min_units": spec.min_units,
        "min_values": spec.min_values,
    }


def describe_registry() -> str:
    """Human-readable listing of every registered estimator."""
    width = max(len(n) for n in METRIC_REGISTRY)
    lines = []
    for name in sorted(METRIC_REGISTRY):
        spec = METRIC_REGISTRY[name]
        defaults = _factory_defaults(spec.factory)
        params = ", ".join(f"{k}={v!r}" for k, v in defaults.items()) or "-"
        flags = "tail" if spec.is_tail else "central"
        lines.append(f"{name.ljust(width)}  [{flags}, needs >= {spec.min_units} units]  params: {params}")
        lines.append(f"{' ' * width}  {spec.definition}")
    return "\n".join(lines)
