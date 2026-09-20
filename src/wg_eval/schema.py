"""The generic experiment-record schema.

The schema is deliberately domain-free.  It names the columns a record table
may carry and what each one means structurally.  It does **not** fix the
nesting order: which identifier is the independent unit is declared in the
analysis configuration (``inference.primary_unit``) and checked against the
records by :mod:`wg_eval.hierarchy`.  The common case is::

    world  ->  event  ->  observation (resident)

but a design where one event spans many worlds declares the reverse, and the
library verifies the declaration rather than assuming either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["id", "categorical", "boolean", "numeric"]

#: Short level names for the default hierarchy, coarsest first.
LEVELS: tuple[str, ...] = ("world", "event", "resident")

#: The default hierarchy as column names, coarsest first. Configurations may
#: declare a different one; this is only the starting assumption.
DEFAULT_LEVEL_ORDER: tuple[str, ...] = ("world_id", "event_id", "resident_id")


@dataclass(frozen=True)
class ColumnSpec:
    """Declared meaning of one column of an experiment-record table."""

    name: str
    kind: Kind
    required: bool = False
    nullable: bool = True
    description: str = ""
    #: For numeric columns: values outside this range raise a warning.
    plausible_range: tuple[float | None, float | None] = (None, None)
    #: Nesting level at which the column is constant, when it is a design
    #: variable rather than an outcome (``None`` for outcomes).
    constant_at: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


SCHEMA: tuple[ColumnSpec, ...] = (
    ColumnSpec(
        "world_id",
        "id",
        required=True,
        nullable=False,
        description="Experimental unit. The default -- but declared -- unit of inference.",
        constant_at="world",
    ),
    ColumnSpec(
        "event_id",
        "id",
        required=True,
        nullable=False,
        description="A grouping between the world and the observation, in either direction.",
        constant_at="event",
    ),
    ColumnSpec(
        "policy_id",
        "id",
        required=True,
        nullable=False,
        description="The treatment arm / system configuration under evaluation.",
    ),
    ColumnSpec(
        "resident_id",
        "id",
        required=True,
        nullable=False,
        description="One nested observation inside an event. NOT a replicate.",
    ),
    ColumnSpec(
        "action",
        "categorical",
        description="Action taken for this observation. Descriptive only.",
    ),
    ColumnSpec(
        "mission_success",
        "boolean",
        description="Binary per-observation outcome.",
    ),
    ColumnSpec(
        "loss",
        "numeric",
        description="Primary cost outcome. Lower is better by convention.",
        plausible_range=(0.0, None),
    ),
    ColumnSpec(
        "travel_time",
        "numeric",
        description="Time-to-outcome cost.",
        plausible_range=(0.0, None),
    ),
    ColumnSpec(
        "resource_use",
        "numeric",
        description="Consumed resource per observation.",
        plausible_range=(0.0, None),
    ),
    ColumnSpec(
        "responder_exposure",
        "numeric",
        description="Risk borne by responders on this observation.",
        plausible_range=(0.0, None),
    ),
    ColumnSpec(
        "failure_reason",
        "categorical",
        description="Why the observation failed. Null when it did not.",
    ),
    ColumnSpec(
        "stratum",
        "categorical",
        description="Default declared stratum label for the unit.",
        constant_at="world",
    ),
    ColumnSpec(
        "run_status",
        "categorical",
        description=(
            "How the run that produced this row ended: completed, crashed, timeout, "
            "infeasible, not_evaluated, excluded. A failed run is not an absent row."
        ),
    ),
)

SCHEMA_BY_NAME: dict[str, ColumnSpec] = {c.name: c for c in SCHEMA}

ID_COLUMNS: tuple[str, ...] = ("world_id", "event_id", "policy_id", "resident_id")
CORE_COLUMNS: tuple[str, ...] = tuple(c.name for c in SCHEMA)
OUTCOME_COLUMNS: tuple[str, ...] = (
    "mission_success",
    "loss",
    "travel_time",
    "resource_use",
    "responder_exposure",
)
NUMERIC_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in SCHEMA if c.kind in ("numeric", "boolean")
)
REQUIRED_COLUMNS: tuple[str, ...] = tuple(c.name for c in SCHEMA if c.required)

#: Columns that may not be used as strata, because they are outcomes or keys.
NON_STRATUM_COLUMNS: frozenset[str] = frozenset(ID_COLUMNS) | frozenset(OUTCOME_COLUMNS) | {
    "failure_reason",
    "action",
    "run_status",
}

#: Column names that look like sampling weights. They are reported and ignored;
#: see docs/ESTIMANDS.md "Weighted units" for why they are not applied.
WEIGHT_LIKE_COLUMNS: frozenset[str] = frozenset(
    {"weight", "weights", "unit_weight", "sample_weight", "sampling_weight"}
)


def level_keys(level: str, hierarchy: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Composite key for a nesting level, under ``hierarchy`` (default order).

    Prefer :meth:`wg_eval.hierarchy.InferenceSpec.keys_for`, which uses the
    hierarchy the configuration actually declared. This helper exists for code
    that only has the default order to work with.

    >>> level_keys("event")
    ('world_id', 'event_id')
    """
    order = hierarchy or DEFAULT_LEVEL_ORDER
    name = {"world": "world_id", "event": "event_id", "resident": "resident_id"}.get(level, level)
    if name not in order:
        raise ValueError(f"unknown level {level!r}; expected one of {order}")
    return tuple(order[: order.index(name) + 1])


def describe_schema() -> str:
    """Human-readable rendering of the schema, used by the CLI and docs."""
    width = max(len(c.name) for c in SCHEMA)
    lines = [f"{'column'.ljust(width)}  kind         req  description", "-" * 96]
    for c in SCHEMA:
        lines.append(
            f"{c.name.ljust(width)}  {c.kind.ljust(11)}  "
            f"{'yes' if c.required else ' no'}  {c.description}"
        )
    return "\n".join(lines)
