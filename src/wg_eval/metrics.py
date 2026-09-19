"""The metric registry: estimators that map a vector of values to one number.

Every estimator here is a *point estimator applied at a declared nesting
level*.  A metric is therefore never just "mean loss"; it is "mean loss, over
events, after residents were averaged into events".  Changing the level
changes the estimand, so the level travels with the metric everywhere.
"""

from __future__ import annotations

import math
from typing import Callable, Mapping

import numpy as np

Estimator = Callable[[np.ndarray], float]

#: name -> (factory, human-readable definition template)
METRIC_REGISTRY: dict[str, tuple[Callable[..., Estimator], str]] = {}


def register_estimator(name: str, definition: str) -> Callable[[Callable[..., Estimator]], Callable[..., Estimator]]:
    """Register an estimator factory under ``name``.

    ``definition`` is a ``str.format`` template rendered with the estimator's
    parameters; it becomes the metric definition recorded in provenance.
    """

    def decorator(factory: Callable[..., Estimator]) -> Callable[..., Estimator]:
        if name in METRIC_REGISTRY:
            raise ValueError(f"estimator {name!r} is already registered")
        METRIC_REGISTRY[name] = (factory, definition)
        return factory

    return decorator


def _clean(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype="float64").ravel()
    return arr[np.isfinite(arr)]


@register_estimator("mean", "arithmetic mean of {level}-level values of `{column}`")
def _mean() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.mean()) if arr.size else math.nan

    return estimator


@register_estimator("rate", "proportion of {level}-level values of `{column}` equal to 1")
def _rate() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.mean()) if arr.size else math.nan

    return estimator


@register_estimator("sum", "sum of {level}-level values of `{column}`")
def _sum() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.sum()) if arr.size else math.nan

    return estimator


@register_estimator("median", "median of {level}-level values of `{column}`")
def _median() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(np.median(arr)) if arr.size else math.nan

    return estimator


@register_estimator("std", "sample standard deviation of {level}-level values of `{column}`")
def _std() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.std(ddof=1)) if arr.size > 1 else math.nan

    return estimator


@register_estimator("quantile", "the q={q} quantile (linear interpolation) of {level}-level values of `{column}`")
def _quantile(q: float = 0.9) -> Estimator:
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile q must lie in [0, 1]; got {q}")

    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(np.quantile(arr, q)) if arr.size else math.nan

    return estimator


@register_estimator(
    "cvar",
    "CVaR at alpha={alpha} ({tail} tail) of {level}-level values of `{column}`: "
    "the mean of the k = max(1, ceil((1-alpha) * n)) most extreme values",
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
        k = max(1, math.ceil((1.0 - alpha) * arr.size))
        ordered = np.sort(arr)
        worst = ordered[-k:] if tail == "upper" else ordered[:k]
        return float(worst.mean())

    return estimator


@register_estimator(
    "trimmed_mean",
    "mean of {level}-level values of `{column}` after trimming {proportion:.0%} from each tail",
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
    "proportion of {level}-level values of `{column}` strictly greater than {threshold}",
)
def _p_exceed(threshold: float = 0.0) -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float((arr > threshold).mean()) if arr.size else math.nan

    return estimator


@register_estimator("max", "maximum {level}-level value of `{column}`")
def _max() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.max()) if arr.size else math.nan

    return estimator


@register_estimator("min", "minimum {level}-level value of `{column}`")
def _min() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        arr = _clean(values)
        return float(arr.min()) if arr.size else math.nan

    return estimator


@register_estimator("count", "number of finite {level}-level values of `{column}`")
def _count() -> Estimator:
    def estimator(values: np.ndarray) -> float:
        return float(_clean(values).size)

    return estimator


#: Estimators whose value depends on the shape of the tail rather than the
#: centre.  Reports flag disagreement between these and central estimators.
TAIL_ESTIMATORS: frozenset[str] = frozenset({"quantile", "cvar", "max", "min", "p_exceed"})


def build_estimator(name: str, params: Mapping[str, object] | None = None) -> Estimator:
    """Instantiate a registered estimator with ``params``."""
    if name not in METRIC_REGISTRY:
        known = ", ".join(sorted(METRIC_REGISTRY))
        raise KeyError(f"unknown estimator {name!r}; registered estimators: {known}")
    factory, _ = METRIC_REGISTRY[name]
    return factory(**dict(params or {}))


def estimate(values: np.ndarray, name: str, params: Mapping[str, object] | None = None) -> float:
    """Apply a registered estimator to ``values`` in one call."""
    return build_estimator(name, params)(values)


def definition_of(name: str, *, column: str, level: str, params: Mapping[str, object] | None = None) -> str:
    """Render the human-readable definition string recorded in provenance."""
    if name not in METRIC_REGISTRY:
        raise KeyError(f"unknown estimator {name!r}")
    _, template = METRIC_REGISTRY[name]
    fields: dict[str, object] = {"column": column, "level": level}
    factory, _ = METRIC_REGISTRY[name]
    defaults = _factory_defaults(factory)
    fields.update(defaults)
    fields.update(dict(params or {}))
    try:
        return template.format(**fields)
    except (KeyError, ValueError):  # pragma: no cover - defensive
        return f"{name} of {level}-level values of `{column}` with params {dict(params or {})}"


def _factory_defaults(factory: Callable[..., Estimator]) -> dict[str, object]:
    import inspect

    sig = inspect.signature(factory)
    return {
        name: param.default
        for name, param in sig.parameters.items()
        if param.default is not inspect.Parameter.empty
    }


def describe_registry() -> str:
    """Human-readable listing of every registered estimator."""
    width = max(len(n) for n in METRIC_REGISTRY)
    lines = []
    for name in sorted(METRIC_REGISTRY):
        factory, template = METRIC_REGISTRY[name]
        defaults = _factory_defaults(factory)
        params = ", ".join(f"{k}={v!r}" for k, v in defaults.items()) or "-"
        lines.append(f"{name.ljust(width)}  params: {params}")
        lines.append(f"{' ' * width}  {template}")
    return "\n".join(lines)
