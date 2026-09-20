"""The analysis configuration: every choice that could change a conclusion.

Nothing about an analysis is implicit.  The inference structure, the
aggregation rules, the metric orientations, the practical margins and their
provenance, the analysis roles, the multiplicity family, the bootstrap seed
and the missing-data rules are all declared in one YAML file, and that file is
hashed into the provenance of every result it produces.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from wg_eval.hierarchy import (
    DEFAULT_NESTED_UNITS,
    DEFAULT_PRIMARY_UNIT,
    InferenceSpec,
    resolve_unit,
)
from wg_eval.metrics import METRIC_REGISTRY, TAIL_ESTIMATORS, definition_of

DIRECTIONS = ("lower_is_better", "higher_is_better")
TAIL_SIDES = ("harmful", "beneficial", "upper", "lower")
MISSING_POLICIES = ("drop_record", "fail", "impute_worst", "impute_best")
BOOTSTRAP_METHODS = ("percentile", "basic", "bca")
AGGREGATION_RULES = ("mean", "sum", "median", "max", "min", "any", "all", "first")
ANALYSIS_ROLES = ("primary", "secondary", "exploratory")
ANALYSIS_STATUSES = ("preregistered", "exploratory", "unspecified")
CORRECTIONS = ("none", "holm", "bonferroni")
MARGIN_SCALES = ("absolute", "standardized")
MECHANISMS = ("MCAR", "MAR", "MNAR", "unknown")
STATUS_HANDLING = ("completed", "failure", "excluded_documented", "missing")

#: Keys that used to configure something and now mean something different, or
#: that the library deliberately refuses.
REFUSED_KEYS: dict[str, str] = {
    "weights": (
        "unit weights are not supported. A sampling weight across experimental units and "
        "a scenario probability inside one unit are different quantities, and applying "
        "either silently would change the estimand without saying so. See "
        "docs/ESTIMANDS.md 'Weighted units'."
    ),
    "unit_weights": (
        "unit weights are not supported; see docs/ESTIMANDS.md 'Weighted units'."
    ),
}


class ConfigError(ValueError):
    """Raised when an analysis configuration is internally inconsistent."""


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bounds:
    """Declared support of a metric's values, used to detect impossible output."""

    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ConfigError(f"bounds.lower ({self.lower}) must be below bounds.upper ({self.upper})")

    @property
    def declared(self) -> bool:
        return self.lower is not None or self.upper is not None

    def contains(self, value: float) -> bool:
        if self.lower is not None and value < self.lower:
            return False
        if self.upper is not None and value > self.upper:
            return False
        return True

    def difference_bounds(self) -> tuple[float | None, float | None]:
        """Range a difference of two in-bounds values can occupy."""
        if self.lower is None or self.upper is None:
            return (None, None)
        span = float(self.upper) - float(self.lower)
        return (-span, span)

    def as_dict(self) -> dict[str, Any]:
        return {"lower": self.lower, "upper": self.upper}


@dataclass(frozen=True)
class Margin:
    """A practical-equivalence margin, possibly asymmetric, with its provenance.

    ``lower`` and ``upper`` are positive distances: the equivalence region on
    the difference scale is ``(-lower, +upper)``.  Asymmetry is permitted
    because "a bit worse" and "a bit better" are not always equally tolerable.
    """

    lower: float
    upper: float
    scale: str = "absolute"
    #: Where the number came from. Required in spirit; its absence is warned about
    #: everywhere the margin is used, because a margin chosen after seeing the
    #: data is a margin chosen to produce a verdict.
    source: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        if self.lower <= 0 or self.upper <= 0:
            raise ConfigError(
                "a margin is a positive distance on each side of zero; got "
                f"lower={self.lower}, upper={self.upper}"
            )
        if self.scale not in MARGIN_SCALES:
            raise ConfigError(f"margin.scale must be one of {MARGIN_SCALES}; got {self.scale!r}")

    @property
    def symmetric(self) -> bool:
        return self.lower == self.upper

    @property
    def documented(self) -> bool:
        return bool(self.source.strip())

    def describe(self) -> str:
        if self.symmetric:
            body = f"+/-{self.upper:g}"
        else:
            body = f"(-{self.lower:g}, +{self.upper:g})"
        return body if self.scale == "absolute" else f"{body} [{self.scale}]"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"symmetric": self.symmetric, "documented": self.documented}


def _margin_from(value: Any, *, metric: str) -> Margin | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        data = dict(value)
        if "lower" in data or "upper" in data:
            lower = data.get("lower", data.get("upper"))
            upper = data.get("upper", data.get("lower"))
        else:
            raise ConfigError(
                f"metric {metric!r}: a margin mapping needs `lower` and/or `upper`; got "
                f"keys {sorted(data)}"
            )
        return Margin(
            lower=float(lower),
            upper=float(upper),
            scale=str(data.get("scale", "absolute")),
            source=str(data.get("source", "")),
            declared_at=str(data.get("declared_at", "")),
        )
    magnitude = float(value)
    return Margin(lower=magnitude, upper=magnitude)


@dataclass(frozen=True)
class MetricSpec:
    """One declared metric: a column, an estimator, the level it pools over,
    its orientation, its support, and the role it plays in the analysis."""

    name: str
    column: str
    estimator: str = "mean"
    level: str = "event_id"
    params: dict[str, Any] = field(default_factory=dict)
    direction: str = "lower_is_better"
    #: Which tail of the distribution a tail estimator should summarise.
    #: ``harmful`` resolves against ``direction`` so orientation is never implicit.
    tail: str = "harmful"
    bounds: Bounds = field(default_factory=Bounds)
    margin: Margin | None = None
    role: str = "secondary"
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", resolve_unit(self.level))
        if self.estimator not in METRIC_REGISTRY:
            known = ", ".join(sorted(METRIC_REGISTRY))
            raise ConfigError(
                f"metric {self.name!r}: unknown estimator {self.estimator!r}; known: {known}"
            )
        if self.direction not in DIRECTIONS:
            raise ConfigError(
                f"metric {self.name!r}: direction must be one of {DIRECTIONS}; got {self.direction!r}"
            )
        if self.tail not in TAIL_SIDES:
            raise ConfigError(
                f"metric {self.name!r}: tail must be one of {TAIL_SIDES}; got {self.tail!r}"
            )
        if self.role not in ANALYSIS_ROLES:
            raise ConfigError(
                f"metric {self.name!r}: role must be one of {ANALYSIS_ROLES}; got {self.role!r}"
            )
        explicit_tail = self.params.get("tail")
        if explicit_tail is not None and explicit_tail != self.tail_side:
            raise ConfigError(
                f"metric {self.name!r}: params.tail={explicit_tail!r} contradicts "
                f"tail={self.tail!r} with direction={self.direction!r}, which implies the "
                f"{self.tail_side!r} tail. Set `tail: upper` or `tail: lower` explicitly if "
                "the other tail is really wanted."
            )

    # -- orientation ------------------------------------------------------
    @property
    def harmful_side(self) -> str:
        """Which end of the distribution is the bad end for this metric."""
        return "upper" if self.direction == "lower_is_better" else "lower"

    @property
    def tail_side(self) -> str:
        """The concrete tail (``upper``/``lower``) this metric's estimator uses."""
        if self.tail == "harmful":
            return self.harmful_side
        if self.tail == "beneficial":
            return "lower" if self.harmful_side == "upper" else "upper"
        return self.tail

    @property
    def is_tail_metric(self) -> bool:
        return self.estimator in TAIL_ESTIMATORS

    def resolved_params(self) -> dict[str, Any]:
        """Estimator parameters with orientation-dependent defaults filled in.

        ``cvar`` gets the harmful tail; ``quantile`` and ``p_exceed`` are left
        exactly as declared, because their orientation lives in ``q`` and
        ``threshold`` and guessing would change the estimand.
        """
        params = dict(self.params)
        if self.estimator == "cvar":
            params.setdefault("tail", self.tail_side)
        return params

    def orientation_warnings(self) -> list[str]:
        """Orientation mistakes that are legal but almost always unintended."""
        notes: list[str] = []
        if self.estimator == "quantile" and self.tail == "harmful":
            q = float(self.params.get("q", 0.9))
            if self.harmful_side == "upper" and q < 0.5:
                notes.append(
                    f"{self.name}: direction={self.direction} makes high values harmful, but "
                    f"q={q} summarises the low end. A harmful-tail quantile for this metric "
                    f"should use q > 0.5."
                )
            if self.harmful_side == "lower" and q > 0.5:
                notes.append(
                    f"{self.name}: direction={self.direction} makes low values harmful, but "
                    f"q={q} summarises the high end. A harmful-tail quantile for this metric "
                    f"should use q < 0.5."
                )
        if self.estimator in ("max", "min"):
            wanted = "max" if self.harmful_side == "upper" else "min"
            if self.estimator != wanted and self.tail == "harmful":
                notes.append(
                    f"{self.name}: the harmful extreme of a {self.direction} metric is its "
                    f"`{wanted}`, but the declared estimator is `{self.estimator}`."
                )
        return notes

    def definition(self) -> str:
        return definition_of(
            self.estimator, column=self.column, level=self.level, params=self.resolved_params()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "column": self.column,
            "estimator": self.estimator,
            "level": self.level,
            "params": dict(self.params),
            "resolved_params": self.resolved_params(),
            "direction": self.direction,
            "tail": self.tail,
            "tail_side": self.tail_side,
            "harmful_side": self.harmful_side,
            "bounds": self.bounds.as_dict(),
            "margin": self.margin.as_dict() if self.margin else None,
            "role": self.role,
            "description": self.description,
            "definition": self.definition(),
            "is_tail_metric": self.is_tail_metric,
        }


# ---------------------------------------------------------------------------
# supporting blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregationSpec:
    """How observations roll up the declared hierarchy.

    Rules are keyed by the transition they apply to, written ``"<from>-><to>"``
    with column names (``"resident_id->event_id"``).  The legacy spellings
    ``resident_to_event`` and ``event_to_world`` are accepted and translated.
    """

    by_step: dict[str, dict[str, str]] = field(default_factory=dict)
    default_rule: str = "mean"

    def __post_init__(self) -> None:
        for step, mapping in self.by_step.items():
            for col, rule in mapping.items():
                if rule not in AGGREGATION_RULES:
                    raise ConfigError(
                        f"aggregation[{step!r}][{col!r}]: rule {rule!r} is not one of "
                        f"{AGGREGATION_RULES}"
                    )
        if self.default_rule not in AGGREGATION_RULES:
            raise ConfigError(f"aggregation.default_rule {self.default_rule!r} is not supported")

    @staticmethod
    def step_key(from_unit: str, to_unit: str) -> str:
        return f"{resolve_unit(from_unit)}->{resolve_unit(to_unit)}"

    def rule_for(self, column: str, from_unit: str, to_unit: str) -> str:
        return self.by_step.get(self.step_key(from_unit, to_unit), {}).get(column, self.default_rule)

    def as_dict(self) -> dict[str, Any]:
        return {"by_step": {k: dict(v) for k, v in self.by_step.items()},
                "default_rule": self.default_rule}


@dataclass(frozen=True)
class BootstrapSpec:
    """Resampling design.  The unit comes from ``inference.primary_unit``."""

    n_resamples: int = 2000
    seed: int = 0
    confidence_level: float = 0.95
    method: str = "percentile"
    #: Two-stage resampling of the primary unit and then the level below it.
    #: Off by default; see docs/STATISTICAL_PROTOCOL.md section 5.
    hierarchical: bool = False

    def __post_init__(self) -> None:
        if self.n_resamples < 100:
            raise ConfigError("bootstrap.n_resamples below 100 gives unusable interval endpoints")
        if not 0.5 <= self.confidence_level < 1.0:
            raise ConfigError("bootstrap.confidence_level must lie in [0.5, 1.0)")
        if self.method not in BOOTSTRAP_METHODS:
            raise ConfigError(f"bootstrap.method must be one of {BOOTSTRAP_METHODS}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonSpec:
    """Which policies are compared, and how."""

    baseline: str | None = None
    candidates: list[str] = field(default_factory=list)
    paired: bool = True
    require_common_units: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterSpec:
    """Row filters applied before analysis.  All are recorded in provenance."""

    include: dict[str, list[Any]] = field(default_factory=dict)
    exclude: dict[str, list[Any]] = field(default_factory=dict)
    exclusions: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissingDataSpec:
    """What happens to missing values, failed runs and absent units."""

    policy: str = "drop_record"
    require_complete_units: bool = True
    max_count_imbalance: float = 0.2
    #: Column recording how a run ended, if the producer supplies one.
    status_column: str = "run_status"
    #: status value -> how it is treated. See docs/STATISTICAL_PROTOCOL.md section 7.
    status_handling: dict[str, str] = field(default_factory=dict)
    #: The analyst's declared belief about why units are missing.
    assumed_mechanism: str = "unknown"
    mechanism_justification: str = ""

    def __post_init__(self) -> None:
        if self.policy not in MISSING_POLICIES:
            raise ConfigError(f"missing_data.policy must be one of {MISSING_POLICIES}")
        if self.assumed_mechanism not in MECHANISMS:
            raise ConfigError(
                f"missing_data.assumed_mechanism must be one of {MECHANISMS}; got "
                f"{self.assumed_mechanism!r}"
            )
        for status, handling in self.status_handling.items():
            if handling not in STATUS_HANDLING:
                raise ConfigError(
                    f"missing_data.status_handling[{status!r}]={handling!r} is not one of "
                    f"{STATUS_HANDLING}"
                )

    def handling_for(self, status: str) -> str:
        return self.status_handling.get(str(status), "missing")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EquivalenceSpec:
    """Practical margins, per metric, on the difference scale."""

    margins: dict[str, Margin] = field(default_factory=dict)
    alpha: float = 0.05
    non_inferiority: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 0.5:
            raise ConfigError("equivalence.alpha must lie in (0, 0.5)")

    def as_dict(self) -> dict[str, Any]:
        return {
            "margins": {k: v.as_dict() for k, v in self.margins.items()},
            "alpha": self.alpha,
            "non_inferiority": list(self.non_inferiority),
        }


@dataclass(frozen=True)
class MultiplicitySpec:
    """The declared analysis family, and what correction applies to it.

    The library never infers a family.  A correction applied over the wrong
    family is worse than none, so the family is declared by assigning each
    metric an analysis role and naming the correction for the secondary one.
    """

    secondary_correction: str = "none"
    exploratory_correction: str = "none"
    #: Recorded so a reader can see the family size the correction assumed.
    justification: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("secondary_correction", self.secondary_correction),
            ("exploratory_correction", self.exploratory_correction),
        ):
            if value not in CORRECTIONS:
                raise ConfigError(f"multiplicity.{name} must be one of {CORRECTIONS}; got {value!r}")

    def correction_for(self, role: str) -> str:
        if role == "primary":
            return "none"
        if role == "secondary":
            return self.secondary_correction
        return self.exploratory_correction

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProtocolSpec:
    """Evidence that the analysis was specified before it was run.

    Preregistration is never inferred from the existence of a config file.  It
    is an assertion the analyst makes, and these fields are what makes it
    checkable by someone else.
    """

    analysis_status: str = "unspecified"
    protocol_hash: str = ""
    protocol_commit: str = ""
    protocol_timestamp: str = ""
    protocol_path: str = ""

    def __post_init__(self) -> None:
        if self.analysis_status not in ANALYSIS_STATUSES:
            raise ConfigError(
                f"analysis_status must be one of {ANALYSIS_STATUSES}; got {self.analysis_status!r}"
            )
        if self.analysis_status == "preregistered" and not (
            self.protocol_hash or self.protocol_commit or self.protocol_path
        ):
            raise ConfigError(
                "analysis_status: preregistered requires at least one of protocol_hash, "
                "protocol_commit or protocol_path. A claim of preregistration that cannot "
                "be checked is not a claim of preregistration."
            )

    @property
    def is_preregistered(self) -> bool:
        return self.analysis_status == "preregistered"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# the whole configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalysisConfig:
    """A complete, self-describing analysis specification."""

    inference: InferenceSpec = field(default_factory=InferenceSpec)
    metrics: list[MetricSpec] = field(default_factory=list)
    aggregation: AggregationSpec = field(default_factory=AggregationSpec)
    comparison: ComparisonSpec = field(default_factory=ComparisonSpec)
    bootstrap: BootstrapSpec = field(default_factory=BootstrapSpec)
    equivalence: EquivalenceSpec = field(default_factory=EquivalenceSpec)
    filters: FilterSpec = field(default_factory=FilterSpec)
    missing_data: MissingDataSpec = field(default_factory=MissingDataSpec)
    multiplicity: MultiplicitySpec = field(default_factory=MultiplicitySpec)
    protocol: ProtocolSpec = field(default_factory=ProtocolSpec)
    strata: list[str] = field(default_factory=list)
    failure_column: str = "failure_reason"
    label: str = ""
    source_path: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ConfigError("at least one metric must be declared")
        names = [m.name for m in self.metrics]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ConfigError(f"duplicate metric name(s): {', '.join(sorted(duplicates))}")

        levels = self.inference.levels
        for metric in self.metrics:
            if metric.level not in levels:
                raise ConfigError(
                    f"metric {metric.name!r} is computed at level {metric.level!r}, which is "
                    f"not a declared inference level. Declared levels are {list(levels)}."
                )
            if self.inference.is_coarser_or_equal(metric.level, self.inference.primary_unit) and (
                metric.level != self.inference.primary_unit
            ):
                raise ConfigError(
                    f"metric {metric.name!r} is computed at {metric.level!r}, which is coarser "
                    f"than the resampling unit {self.inference.primary_unit!r}"
                )

        unknown_margins = set(self.equivalence.margins) - set(names)
        if unknown_margins:
            raise ConfigError(
                f"equivalence.margins names unknown metric(s): {', '.join(sorted(unknown_margins))}"
            )
        unknown_ni = set(self.equivalence.non_inferiority) - set(names)
        if unknown_ni:
            raise ConfigError(
                f"equivalence.non_inferiority names unknown metric(s): "
                f"{', '.join(sorted(unknown_ni))}"
            )
        primary = [m.name for m in self.metrics if m.role == "primary"]
        if len(primary) > 1:
            raise ConfigError(
                f"more than one metric is declared primary ({', '.join(primary)}). A primary "
                "outcome that is plural is not a primary outcome; declare one and move the "
                "rest to secondary."
            )

    # -- lookups ----------------------------------------------------------
    def metric(self, name: str) -> MetricSpec:
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(f"no metric named {name!r}")

    def margin_for(self, name: str) -> Margin | None:
        if name in self.equivalence.margins:
            return self.equivalence.margins[name]
        return self.metric(name).margin

    def metrics_by_role(self, role: str) -> list[MetricSpec]:
        return [m for m in self.metrics if m.role == role]

    @property
    def primary_metric(self) -> MetricSpec | None:
        found = self.metrics_by_role("primary")
        return found[0] if found else None

    @property
    def ordered_metrics(self) -> list[MetricSpec]:
        """Primary first, then secondary, then exploratory -- report order."""
        order = {role: i for i, role in enumerate(ANALYSIS_ROLES)}
        return sorted(self.metrics, key=lambda m: (order[m.role], self.metrics.index(m)))

    @property
    def primary_unit(self) -> str:
        return self.inference.primary_unit

    @property
    def cluster_level(self) -> str:
        """Backwards-compatible alias for the resampling unit."""
        return self.inference.primary_unit

    @property
    def unit_of_inference(self) -> str:
        return self.inference.primary_unit

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "inference": self.inference.as_dict(),
            "metrics": [m.as_dict() for m in self.ordered_metrics],
            "aggregation": self.aggregation.as_dict(),
            "comparison": self.comparison.as_dict(),
            "bootstrap": self.bootstrap.as_dict(),
            "equivalence": self.equivalence.as_dict(),
            "multiplicity": self.multiplicity.as_dict(),
            "protocol": self.protocol.as_dict(),
            "filters": self.filters.as_dict(),
            "missing_data": self.missing_data.as_dict(),
            "strata": list(self.strata),
            "failure_column": self.failure_column,
            "source_path": self.source_path,
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.as_dict(), sort_keys=False)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


_LEGACY_STEPS = {
    "resident_to_event": ("resident_id", "event_id"),
    "event_to_world": ("event_id", "world_id"),
    "resident_to_world": ("resident_id", "world_id"),
    "world_to_event": ("world_id", "event_id"),
}


def _aggregation_from(data: Mapping[str, Any]) -> AggregationSpec:
    by_step: dict[str, dict[str, str]] = {}
    for key, mapping in dict(data).items():
        if key == "default_rule":
            continue
        if key == "by_step":
            for step, rules in dict(mapping or {}).items():
                by_step[str(step)] = {str(k): str(v) for k, v in dict(rules or {}).items()}
            continue
        if key in _LEGACY_STEPS:
            src, dst = _LEGACY_STEPS[key]
            step = AggregationSpec.step_key(src, dst)
        elif "->" in str(key):
            src, dst = str(key).split("->", 1)
            step = AggregationSpec.step_key(src.strip(), dst.strip())
        else:
            raise ConfigError(
                f"aggregation key {key!r} is not a roll-up step. Use '<from>-><to>' with "
                "column names, e.g. 'resident_id->event_id'."
            )
        by_step.setdefault(step, {}).update(
            {str(k): str(v) for k, v in dict(mapping or {}).items()}
        )
    return AggregationSpec(by_step=by_step, default_rule=str(data.get("default_rule", "mean")))


def _roles_from(data: Mapping[str, Any], names: list[str]) -> dict[str, str]:
    """Resolve the ``analysis_role`` block into metric -> role."""
    roles: dict[str, str] = {}
    primary = data.get("primary")
    if primary is not None:
        for name in _as_list(primary):
            roles[str(name)] = "primary"
    for role in ("secondary", "exploratory"):
        for name in _as_list(data.get(role)):
            roles[str(name)] = role
    unknown = sorted(set(roles) - set(names))
    if unknown:
        raise ConfigError(f"analysis_role names unknown metric(s): {', '.join(unknown)}")
    return roles


def config_from_mapping(data: Mapping[str, Any], *, source_path: str | None = None) -> AnalysisConfig:
    """Build an :class:`AnalysisConfig` from a plain mapping (parsed YAML)."""
    data = copy.deepcopy(dict(data))

    for key, reason in REFUSED_KEYS.items():
        if key in data:
            raise ConfigError(f"`{key}` is not a supported configuration key: {reason}")

    # -- inference --------------------------------------------------------
    inf_raw = dict(data.get("inference") or {})
    if "unit_of_inference" in data and "primary_unit" not in inf_raw:
        inf_raw["primary_unit"] = data["unit_of_inference"]
    if "bootstrap" in data and isinstance(data["bootstrap"], Mapping):
        legacy_unit = dict(data["bootstrap"]).get("cluster_level")
        if legacy_unit is not None and "primary_unit" not in inf_raw:
            inf_raw["primary_unit"] = legacy_unit
    primary_unit = resolve_unit(str(inf_raw.get("primary_unit", DEFAULT_PRIMARY_UNIT)))
    if "nested_units" in inf_raw:
        nested = tuple(resolve_unit(str(u)) for u in _as_list(inf_raw["nested_units"]))
    else:
        nested = tuple(
            u for u in (DEFAULT_PRIMARY_UNIT, *DEFAULT_NESTED_UNITS) if u != primary_unit
        )
        # Keep the default coarse-to-fine order relative to the chosen primary.
        default_order = (DEFAULT_PRIMARY_UNIT, *DEFAULT_NESTED_UNITS)
        if primary_unit in default_order:
            nested = tuple(u for u in default_order[default_order.index(primary_unit) + 1:])
    if primary_unit == "resident_id":
        raise ConfigError(
            "inference.primary_unit: resident_id treats nested observations as independent "
            "experiments. That is pseudoreplication and is refused. If observations really "
            "are the replicates, the records have no nesting and should not declare any."
        )
    inference = InferenceSpec(primary_unit=primary_unit, nested_units=nested)

    # -- metrics ----------------------------------------------------------
    metrics_raw = data.get("metrics") or []
    if not isinstance(metrics_raw, list):
        raise ConfigError("`metrics` must be a list of metric specifications")
    metrics: list[MetricSpec] = []
    for item in metrics_raw:
        if not isinstance(item, Mapping):
            raise ConfigError(f"each metric must be a mapping; got {type(item).__name__}")
        item = dict(item)
        if "column" not in item:
            raise ConfigError(f"metric {item.get('name', '<unnamed>')!r} is missing `column`")
        name = str(item.get("name") or f"{item.get('estimator', 'mean')}_{item['column']}")
        bounds_raw = dict(item.get("bounds") or {})
        metrics.append(
            MetricSpec(
                name=name,
                column=str(item["column"]),
                estimator=str(item.get("estimator", "mean")),
                level=str(item.get("level", inference.finest)),
                params=dict(item.get("params") or {}),
                direction=str(item.get("direction", "lower_is_better")),
                tail=str(item.get("tail", "harmful")),
                bounds=Bounds(
                    lower=None if bounds_raw.get("lower") is None else float(bounds_raw["lower"]),
                    upper=None if bounds_raw.get("upper") is None else float(bounds_raw["upper"]),
                ),
                margin=_margin_from(item.get("margin"), metric=name),
                role=str(item.get("role", "secondary")),
                description=str(item.get("description", "")),
            )
        )

    names = [m.name for m in metrics]
    role_overrides = _roles_from(dict(data.get("analysis_role") or {}), names)
    if role_overrides:
        metrics = [
            MetricSpec(**{**asdict_metric(m), "role": role_overrides.get(m.name, m.role)})
            for m in metrics
        ]

    aggregation = _aggregation_from(dict(data.get("aggregation") or {}))

    cmp_raw = dict(data.get("comparison") or {})
    comparison = ComparisonSpec(
        baseline=cmp_raw.get("baseline"),
        candidates=[str(c) for c in _as_list(cmp_raw.get("candidates"))],
        paired=bool(cmp_raw.get("paired", True)),
        require_common_units=bool(
            cmp_raw.get("require_common_units", cmp_raw.get("require_common_worlds", True))
        ),
    )

    boot_raw = dict(data.get("bootstrap") or {})
    bootstrap = BootstrapSpec(
        n_resamples=int(boot_raw.get("n_resamples", 2000)),
        seed=int(boot_raw.get("seed", 0)),
        confidence_level=float(boot_raw.get("confidence_level", 0.95)),
        method=str(boot_raw.get("method", "percentile")),
        hierarchical=bool(boot_raw.get("hierarchical", False)),
    )

    eq_raw = dict(data.get("equivalence") or {})
    margins = {
        str(k): _margin_from(v, metric=str(k))
        for k, v in dict(eq_raw.get("margins") or {}).items()
    }
    equivalence = EquivalenceSpec(
        margins={k: v for k, v in margins.items() if v is not None},
        alpha=float(eq_raw.get("alpha", 0.05)),
        non_inferiority=[str(m) for m in _as_list(eq_raw.get("non_inferiority"))],
    )

    filt_raw = dict(data.get("filters") or {})
    filters = FilterSpec(
        include={str(k): _as_list(v) for k, v in dict(filt_raw.get("include") or {}).items()},
        exclude={str(k): _as_list(v) for k, v in dict(filt_raw.get("exclude") or {}).items()},
        exclusions={str(k): str(v) for k, v in dict(filt_raw.get("exclusions") or {}).items()},
    )

    miss_raw = dict(data.get("missing_data") or {})
    missing = MissingDataSpec(
        policy=str(miss_raw.get("policy", "drop_record")),
        require_complete_units=bool(
            miss_raw.get("require_complete_units", miss_raw.get("require_complete_worlds", True))
        ),
        max_count_imbalance=float(miss_raw.get("max_count_imbalance", 0.2)),
        status_column=str(miss_raw.get("status_column", "run_status")),
        status_handling={
            str(k): str(v) for k, v in dict(miss_raw.get("status_handling") or {}).items()
        },
        assumed_mechanism=str(miss_raw.get("assumed_mechanism", "unknown")),
        mechanism_justification=str(miss_raw.get("mechanism_justification", "")),
    )

    mult_raw = dict(data.get("multiplicity") or {})
    multiplicity = MultiplicitySpec(
        secondary_correction=str(mult_raw.get("secondary_correction", mult_raw.get("correction", "none"))),
        exploratory_correction=str(mult_raw.get("exploratory_correction", "none")),
        justification=str(mult_raw.get("justification", "")),
    )

    proto_raw = dict(data.get("protocol") or {})
    protocol = ProtocolSpec(
        analysis_status=str(data.get("analysis_status", proto_raw.get("analysis_status", "unspecified"))),
        protocol_hash=str(proto_raw.get("hash", proto_raw.get("protocol_hash", ""))),
        protocol_commit=str(proto_raw.get("commit", proto_raw.get("protocol_commit", ""))),
        protocol_timestamp=str(proto_raw.get("timestamp", proto_raw.get("protocol_timestamp", ""))),
        protocol_path=str(proto_raw.get("path", proto_raw.get("protocol_path", ""))),
    )

    return AnalysisConfig(
        inference=inference,
        metrics=metrics,
        aggregation=aggregation,
        comparison=comparison,
        bootstrap=bootstrap,
        equivalence=equivalence,
        filters=filters,
        missing_data=missing,
        multiplicity=multiplicity,
        protocol=protocol,
        strata=[str(s) for s in _as_list(data.get("strata"))],
        failure_column=str(data.get("failure_column", "failure_reason")),
        label=str(data.get("label", "")),
        source_path=source_path,
        raw=dict(data),
    )


def asdict_metric(metric: MetricSpec) -> dict[str, Any]:
    """Field values of a metric, suitable for rebuilding one with a change."""
    return {
        "name": metric.name,
        "column": metric.column,
        "estimator": metric.estimator,
        "level": metric.level,
        "params": dict(metric.params),
        "direction": metric.direction,
        "tail": metric.tail,
        "bounds": metric.bounds,
        "margin": metric.margin,
        "role": metric.role,
        "description": metric.description,
    }


def load_config(path: str | Path) -> AnalysisConfig:
    """Load and validate an analysis configuration from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"analysis config not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path}: top level of an analysis config must be a mapping")
    return config_from_mapping(data, source_path=str(path.resolve()))


def default_config(
    *,
    metrics: Iterable[MetricSpec] | None = None,
    baseline: str | None = None,
    candidates: Iterable[str] = (),
    seed: int = 0,
) -> AnalysisConfig:
    """A reasonable default config for exploratory use and tests."""
    metrics = list(
        metrics
        or [MetricSpec(name="mean_loss", column="loss", estimator="mean", level="event_id",
                       role="primary")]
    )
    return AnalysisConfig(
        metrics=metrics,
        comparison=ComparisonSpec(baseline=baseline, candidates=list(candidates)),
        bootstrap=BootstrapSpec(seed=seed),
    )
