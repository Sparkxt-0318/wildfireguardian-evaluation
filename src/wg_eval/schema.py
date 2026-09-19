"""The generic experiment-record schema.

The schema is deliberately domain-free.  It describes a three-level nesting::

    world  ->  event  ->  observation (resident)

and a treatment label (``policy_id``) applied at the world/event level.  Any
producer of experiment records that can express its results in these terms can
be evaluated by this library; nothing here knows what the records mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["id", "categorical", "boolean", "numeric"]

#: Nesting levels, coarsest first.  ``analysis`` levels are derived.
LEVELS: tuple[str, ...] = ("world", "event", "resident")


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
        description="Independent replicate. The default unit of inference.",
        constant_at="world",
    ),
    ColumnSpec(
        "event_id",
        "id",
        required=True,
        nullable=False,
        description="An event nested within exactly one world.",
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
        description="Default declared stratum label for the world.",
        constant_at="world",
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
}


def level_keys(level: str) -> tuple[str, ...]:
    """Return the grouping key for a nesting level, excluding ``policy_id``.

    >>> level_keys("event")
    ('world_id', 'event_id')
    """
    if level == "world":
        return ("world_id",)
    if level == "event":
        return ("world_id", "event_id")
    if level == "resident":
        return ("world_id", "event_id", "resident_id")
    raise ValueError(f"unknown level {level!r}; expected one of {LEVELS}")


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
