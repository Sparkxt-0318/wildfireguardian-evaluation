"""The analysis configuration: every choice that could change a conclusion.

Nothing about an analysis is implicit.  The unit of inference, the aggregation
rules, the practical margins, the bootstrap seed and the missing-data policy
are all declared in one YAML file, and that file is hashed into the provenance
of every result it produces.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from wg_eval.metrics import METRIC_REGISTRY, TAIL_ESTIMATORS, definition_of
from wg_eval.schema import LEVELS

DIRECTIONS = ("lower_is_better", "higher_is_better")
MISSING_POLICIES = ("drop_record", "fail", "impute_worst", "impute_best")
BOOTSTRAP_METHODS = ("percentile", "bca", "basic")
AGGREGATION_RULES = ("mean", "sum", "median", "max", "min", "any", "all", "first")


class ConfigError(ValueError):
    """Raised when an analysis configuration is internally inconsistent."""


@dataclass(frozen=True)
class MetricSpec:
    """One declared metric: a column, an estimator, and the level it pools over."""

    name: str
    column: str
    estimator: str = "mean"
    level: str = "event"
    params: dict[str, Any] = field(default_factory=dict)
    direction: str = "lower_is_better"
    #: Practical-equivalence margin on the *difference* scale, in the metric's
    #: own units.  ``None`` means equivalence cannot be concluded for it.
    margin: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.estimator not in METRIC_REGISTRY:
            known = ", ".join(sorted(METRIC_REGISTRY))
            raise ConfigError(
                f"metric {self.name!r}: unknown estimator {self.estimator!r}; known: {known}"
            )
        if self.level not in LEVELS:
            raise ConfigError(
                f"metric {self.name!r}: level must be one of {LEVELS}; got {self.level!r}"
            )
        if self.direction not in DIRECTIONS:
            raise ConfigError(
                f"metric {self.name!r}: direction must be one of {DIRECTIONS}; got {self.direction!r}"
            )
        if self.margin is not None and self.margin <= 0:
            raise ConfigError(
                f"metric {self.name!r}: equivalence margin must be a positive number "
                "on the difference scale"
            )

    @property
    def is_tail_metric(self) -> bool:
        return self.estimator in TAIL_ESTIMATORS

    def definition(self) -> str:
        return definition_of(self.estimator, column=self.column, level=self.level, params=self.params)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["definition"] = self.definition()
        out["is_tail_metric"] = self.is_tail_metric
        return out


@dataclass(frozen=True)
class AggregationSpec:
    """How observations roll up the nesting hierarchy."""

    resident_to_event: dict[str, str] = field(default_factory=dict)
    event_to_world: dict[str, str] = field(default_factory=dict)
    default_rule: str = "mean"

    def __post_init__(self) -> None:
        for label, mapping in (
            ("resident_to_event", self.resident_to_event),
            ("event_to_world", self.event_to_world),
        ):
            for col, rule in mapping.items():
                if rule not in AGGREGATION_RULES:
                    raise ConfigError(
                        f"aggregation.{label}.{col}: rule {rule!r} is not one of {AGGREGATION_RULES}"
                    )
        if self.default_rule not in AGGREGATION_RULES:
            raise ConfigError(f"aggregation.default_rule {self.default_rule!r} is not supported")

    def rule_for(self, column: str, step: str) -> str:
        mapping = self.resident_to_event if step == "resident_to_event" else self.event_to_world
        return mapping.get(column, self.default_rule)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BootstrapSpec:
    """Resampling design.  ``cluster_level`` is the unit of inference."""

    n_resamples: int = 2000
    cluster_level: str = "world"
    seed: int = 0
    confidence_level: float = 0.95
    method: str = "percentile"

    def __post_init__(self) -> None:
        if self.n_resamples < 100:
            raise ConfigError("bootstrap.n_resamples below 100 gives unusable interval endpoints")
        if self.cluster_level not in ("world", "event", "resident"):
            raise ConfigError(
                f"bootstrap.cluster_level must be world, event or resident; got {self.cluster_level!r}"
            )
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
    require_common_worlds: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterSpec:
    """Row filters applied before analysis.  Both are recorded in provenance."""

    include: dict[str, list[Any]] = field(default_factory=dict)
    exclude: dict[str, list[Any]] = field(default_factory=dict)
    #: Named boolean expressions (pandas ``query`` syntax) that *drop* rows.
    exclusions: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissingDataSpec:
    """What happens to observations with a missing metric value."""

    policy: str = "drop_record"
    #: Require every policy to cover every world before comparing.
    require_complete_worlds: bool = True
    #: Worlds whose observation count differs between policies by more than
    #: this fraction raise a warning.
    max_count_imbalance: float = 0.2

    def __post_init__(self) -> None:
        if self.policy not in MISSING_POLICIES:
            raise ConfigError(f"missing_data.policy must be one of {MISSING_POLICIES}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EquivalenceSpec:
    """Practical margins, per metric, on the difference scale."""

    margins: dict[str, float] = field(default_factory=dict)
    #: One-sided alpha for TOST; the two one-sided tests each use this level.
    alpha: float = 0.05
    #: Metrics for which a one-sided non-inferiority claim is wanted.
    non_inferiority: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name, margin in self.margins.items():
            if margin <= 0:
                raise ConfigError(
                    f"equivalence.margins.{name} must be positive; a margin is a "
                    "practically-negligible *distance*, not a direction"
                )
        if not 0.0 < self.alpha < 0.5:
            raise ConfigError("equivalence.alpha must lie in (0, 0.5)")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisConfig:
    """A complete, self-describing analysis specification."""

    unit_of_inference: str = "world"
    metrics: list[MetricSpec] = field(default_factory=list)
    aggregation: AggregationSpec = field(default_factory=AggregationSpec)
    comparison: ComparisonSpec = field(default_factory=ComparisonSpec)
    bootstrap: BootstrapSpec = field(default_factory=BootstrapSpec)
    equivalence: EquivalenceSpec = field(default_factory=EquivalenceSpec)
    filters: FilterSpec = field(default_factory=FilterSpec)
    missing_data: MissingDataSpec = field(default_factory=MissingDataSpec)
    strata: list[str] = field(default_factory=list)
    failure_column: str = "failure_reason"
    label: str = ""
    source_path: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.unit_of_inference not in ("world", "event"):
            raise ConfigError(
                "unit_of_inference must be 'world' or 'event'. Residents are nested "
                "observations, not replicates, and may never be the unit of inference."
            )
        if not self.metrics:
            raise ConfigError("at least one metric must be declared")
        names = [m.name for m in self.metrics]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ConfigError(f"duplicate metric name(s): {', '.join(sorted(duplicates))}")
        if self.bootstrap.cluster_level == "resident":
            raise ConfigError(
                "bootstrap.cluster_level='resident' resamples nested observations as if "
                "they were independent experiments. This is pseudoreplication and is "
                "refused by default. Use redteam.resident_bootstrap_demo() to *demonstrate* "
                "the error deliberately."
            )
        unknown_margins = set(self.equivalence.margins) - set(names)
        if unknown_margins:
            raise ConfigError(
                f"equivalence.margins names unknown metric(s): {', '.join(sorted(unknown_margins))}"
            )
        unknown_ni = set(self.equivalence.non_inferiority) - set(names)
        if unknown_ni:
            raise ConfigError(
                f"equivalence.non_inferiority names unknown metric(s): {', '.join(sorted(unknown_ni))}"
            )

    def metric(self, name: str) -> MetricSpec:
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(f"no metric named {name!r}")

    def margin_for(self, name: str) -> float | None:
        """Margin from ``equivalence.margins``, falling back to the metric's own."""
        if name in self.equivalence.margins:
            return float(self.equivalence.margins[name])
        return self.metric(name).margin

    @property
    def cluster_level(self) -> str:
        return self.bootstrap.cluster_level

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "unit_of_inference": self.unit_of_inference,
            "metrics": [m.as_dict() for m in self.metrics],
            "aggregation": self.aggregation.as_dict(),
            "comparison": self.comparison.as_dict(),
            "bootstrap": self.bootstrap.as_dict(),
            "equivalence": self.equivalence.as_dict(),
            "filters": self.filters.as_dict(),
            "missing_data": self.missing_data.as_dict(),
            "strata": list(self.strata),
            "failure_column": self.failure_column,
            "source_path": self.source_path,
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.as_dict(), sort_keys=False)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def config_from_mapping(data: Mapping[str, Any], *, source_path: str | None = None) -> AnalysisConfig:
    """Build an :class:`AnalysisConfig` from a plain mapping (parsed YAML)."""
    data = copy.deepcopy(dict(data))

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
        margin = item.get("margin")
        metrics.append(
            MetricSpec(
                name=name,
                column=str(item["column"]),
                estimator=str(item.get("estimator", "mean")),
                level=str(item.get("level", "event")),
                params=dict(item.get("params") or {}),
                direction=str(item.get("direction", "lower_is_better")),
                margin=None if margin is None else float(margin),
                description=str(item.get("description", "")),
            )
        )

    agg_raw = dict(data.get("aggregation") or {})
    aggregation = AggregationSpec(
        resident_to_event=dict(agg_raw.get("resident_to_event") or {}),
        event_to_world=dict(agg_raw.get("event_to_world") or {}),
        default_rule=str(agg_raw.get("default_rule", "mean")),
    )

    cmp_raw = dict(data.get("comparison") or {})
    comparison = ComparisonSpec(
        baseline=cmp_raw.get("baseline"),
        candidates=[str(c) for c in _as_list(cmp_raw.get("candidates"))],
        paired=bool(cmp_raw.get("paired", True)),
        require_common_worlds=bool(cmp_raw.get("require_common_worlds", True)),
    )

    boot_raw = dict(data.get("bootstrap") or {})
    bootstrap = BootstrapSpec(
        n_resamples=int(boot_raw.get("n_resamples", 2000)),
        cluster_level=str(boot_raw.get("cluster_level", data.get("unit_of_inference", "world"))),
        seed=int(boot_raw.get("seed", 0)),
        confidence_level=float(boot_raw.get("confidence_level", 0.95)),
        method=str(boot_raw.get("method", "percentile")),
    )

    eq_raw = dict(data.get("equivalence") or {})
    equivalence = EquivalenceSpec(
        margins={str(k): float(v) for k, v in dict(eq_raw.get("margins") or {}).items()},
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
        require_complete_worlds=bool(miss_raw.get("require_complete_worlds", True)),
        max_count_imbalance=float(miss_raw.get("max_count_imbalance", 0.2)),
    )

    return AnalysisConfig(
        unit_of_inference=str(data.get("unit_of_inference", "world")),
        metrics=metrics,
        aggregation=aggregation,
        comparison=comparison,
        bootstrap=bootstrap,
        equivalence=equivalence,
        filters=filters,
        missing_data=missing,
        strata=[str(s) for s in _as_list(data.get("strata"))],
        failure_column=str(data.get("failure_column", "failure_reason")),
        label=str(data.get("label", "")),
        source_path=source_path,
        raw=dict(data),
    )


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
    metrics = list(metrics or [MetricSpec(name="mean_loss", column="loss", estimator="mean", level="event")])
    return AnalysisConfig(
        metrics=metrics,
        comparison=ComparisonSpec(baseline=baseline, candidates=list(candidates)),
        bootstrap=BootstrapSpec(seed=seed),
    )
