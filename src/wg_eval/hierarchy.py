"""The declared inference structure, and checking it against the records.

The library refuses to guess which grouping is independent.  A configuration
declares one::

    inference:
      primary_unit: world_id
      nested_units: [event_id, resident_id]

``primary_unit`` is the independent replicate -- the thing that is resampled
and the thing the estimand generalises over.  ``nested_units`` are the
dependent levels inside it, ordered coarse to fine.

Nothing here assumes ``world_id`` is above ``event_id``.  A design where one
shock spans many worlds is declared the other way round::

    inference:
      primary_unit: event_id
      nested_units: [world_id, resident_id]

and the declaration is then *checked against the data*.  Distinct identifiers
are not evidence of independence, so a declaration that contradicts the
observed containment is a hard failure rather than a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

import pandas as pd

#: How one identifier column relates to another in a particular table.
Relation = Literal[
    "one_to_one",      # each value of a <-> exactly one value of b
    "a_within_b",      # each value of a sits inside exactly one b (b is coarser)
    "b_within_a",      # each value of b sits inside exactly one a (a is coarser)
    "crossed",         # neither containment holds
]

#: Short names accepted for the default hierarchy, for configs written before
#: units were declared by column name.
UNIT_ALIASES: dict[str, str] = {
    "world": "world_id",
    "event": "event_id",
    "resident": "resident_id",
    "observation": "resident_id",
    "unit": "world_id",
}

#: Columns that are never inference units: the treatment label and outcomes.
NEVER_A_UNIT: frozenset[str] = frozenset(
    {
        "policy_id",
        "action",
        "mission_success",
        "loss",
        "travel_time",
        "resource_use",
        "responder_exposure",
        "failure_reason",
        "run_status",
    }
)

DEFAULT_PRIMARY_UNIT = "world_id"
DEFAULT_NESTED_UNITS: tuple[str, ...] = ("event_id", "resident_id")


class InferenceStructureError(ValueError):
    """The declared inference structure contradicts the records."""


def resolve_unit(name: str) -> str:
    """Map a short level name (``"world"``) to its column (``"world_id"``)."""
    return UNIT_ALIASES.get(str(name), str(name))


@dataclass(frozen=True)
class InferenceSpec:
    """The declared unit of inference and the levels nested inside it."""

    primary_unit: str = DEFAULT_PRIMARY_UNIT
    nested_units: tuple[str, ...] = DEFAULT_NESTED_UNITS

    def __post_init__(self) -> None:
        primary = resolve_unit(self.primary_unit)
        nested = tuple(resolve_unit(u) for u in self.nested_units)
        object.__setattr__(self, "primary_unit", primary)
        object.__setattr__(self, "nested_units", nested)

        if primary in NEVER_A_UNIT:
            raise InferenceStructureError(
                f"inference.primary_unit={primary!r} is a treatment label or an outcome, "
                "not an experimental unit. Resampling it would resample the thing being "
                "compared."
            )
        bad = [u for u in nested if u in NEVER_A_UNIT]
        if bad:
            raise InferenceStructureError(
                f"inference.nested_units contains non-unit column(s) {bad}. Outcomes and "
                "the policy label are not levels of the experimental design."
            )
        if primary in nested:
            raise InferenceStructureError(
                f"inference.primary_unit={primary!r} also appears in nested_units. The "
                "primary unit is the outermost level and is not nested inside itself."
            )
        seen = set()
        for u in nested:
            if u in seen:
                raise InferenceStructureError(f"inference.nested_units repeats {u!r}")
            seen.add(u)

    @property
    def levels(self) -> tuple[str, ...]:
        """Every declared unit, coarsest first, starting at the primary unit."""
        return (self.primary_unit, *self.nested_units)

    @property
    def finest(self) -> str:
        return self.levels[-1]

    def index_of(self, unit: str) -> int:
        unit = resolve_unit(unit)
        try:
            return self.levels.index(unit)
        except ValueError:
            raise KeyError(
                f"{unit!r} is not a declared inference level; declared levels are "
                f"{list(self.levels)}"
            ) from None

    def keys_for(self, unit: str) -> tuple[str, ...]:
        """Composite key identifying one instance of ``unit``.

        An identifier is only unique in the context of the levels above it, so a
        unit's identity is the tuple from the primary unit down to it.  This is
        what makes a bare ``resident_id`` that repeats across events harmless.
        """
        return self.levels[: self.index_of(unit) + 1]

    def is_coarser_or_equal(self, unit: str, other: str) -> bool:
        """True when ``unit`` sits at or above ``other`` in the declared order."""
        return self.index_of(unit) <= self.index_of(other)

    def steps(self) -> tuple[tuple[str, str], ...]:
        """Roll-up transitions, finest first: ``((resident_id, event_id), ...)``."""
        levels = self.levels
        return tuple((levels[i + 1], levels[i]) for i in reversed(range(len(levels) - 1)))

    def as_dict(self) -> dict[str, object]:
        return {"primary_unit": self.primary_unit, "nested_units": list(self.nested_units)}

    @property
    def chain(self) -> str:
        """The declared levels as ``"a > b > c"``, coarsest first."""
        return " > ".join(self.levels)

    def describe(self) -> str:
        levels = len(self.nested_units)
        return (
            f"{self.chain} (resampling unit: {self.primary_unit}; "
            f"{levels} nested level{'' if levels == 1 else 's'})"
        )


def nesting_relation(frame: pd.DataFrame, a: str, b: str) -> Relation:
    """Classify how identifier column ``a`` relates to column ``b`` in ``frame``.

    ``"a_within_b"`` means every value of ``a`` appears under exactly one value
    of ``b``, so ``b`` is the coarser grouping.
    """
    for col in (a, b):
        if col not in frame.columns:
            raise KeyError(f"column {col!r} is not present in the records")
    sub = frame[[a, b]].dropna()
    if sub.empty:
        return "crossed"
    a_within_b = int(sub.groupby(a, observed=True)[b].nunique().max()) == 1
    b_within_a = int(sub.groupby(b, observed=True)[a].nunique().max()) == 1
    if a_within_b and b_within_a:
        return "one_to_one"
    if a_within_b:
        return "a_within_b"
    if b_within_a:
        return "b_within_a"
    return "crossed"


def candidate_unit_columns(frame: pd.DataFrame, declared: Iterable[str] = ()) -> list[str]:
    """Columns that could be experimental units: id-like, not outcomes.

    Used to look for a coarser grouping the configuration failed to declare.
    """
    declared = set(declared)
    out = []
    for column in frame.columns:
        name = str(column)
        if name in NEVER_A_UNIT or name in declared:
            continue
        if not (name.endswith("_id") or name in UNIT_ALIASES.values()):
            continue
        out.append(name)
    return out


def check_structure(frame: pd.DataFrame, inference: InferenceSpec) -> list[dict[str, object]]:
    """Check a declared structure against the records.

    Returns a list of findings, each a dict with ``code``, ``severity``
    (``"error"`` or ``"warning"``), ``message`` and supporting detail.  The
    caller decides what to do with them; :func:`require_valid_structure` raises.

    The checks, in order of how badly they mislead:

    1. **Inverted declaration.** A declared nested level that in fact *contains*
       the primary unit. Resampling the primary unit would then resample
       fragments of a larger dependent group, and the interval would be too
       narrow for exactly the reason a resident-level bootstrap is.
    2. **Undeclared coarser grouping.** An id-like column outside the
       declaration that strictly contains the primary unit.
    3. **Crossed levels.** A declared level that is neither nested in nor
       containing the level above it, which makes the hierarchy ill-defined.
    4. **Degenerate levels.** A nested level that is one-to-one with the level
       above it, which adds no structure and usually signals a mistake.
    """
    findings: list[dict[str, object]] = []
    levels = inference.levels

    missing = [u for u in levels if u not in frame.columns]
    if missing:
        findings.append(
            {
                "code": "undeclared_unit_column_missing",
                "severity": "error",
                "message": (
                    f"declared inference level(s) {missing} are not columns of the records"
                ),
                "detail": {"missing": missing},
            }
        )
        return findings

    primary = inference.primary_unit

    for i in range(len(levels) - 1):
        coarse, fine = levels[i], levels[i + 1]
        relation = nesting_relation(frame, fine, coarse)
        if relation == "b_within_a":
            findings.append(
                {
                    "code": "inverted_nesting",
                    "severity": "error",
                    "message": (
                        f"the records say {coarse!r} is nested inside {fine!r}, but the "
                        f"configuration declares {fine!r} as nested inside {coarse!r}. "
                        f"The independent unit is {fine!r}, not {coarse!r}. Declare "
                        f"inference.primary_unit: {fine} -- resampling {coarse!r} would "
                        f"break up a dependent group and understate uncertainty, which is "
                        f"the same error as resampling observations."
                    ),
                    "detail": {"coarser_in_data": fine, "declared_coarser": coarse},
                }
            )
        elif relation == "crossed":
            findings.append(
                {
                    "code": "crossed_levels",
                    "severity": "error",
                    "message": (
                        f"{fine!r} is neither nested inside nor containing {coarse!r}; the "
                        "two levels cross. A crossed design has no single hierarchy and "
                        "this library cannot resample it correctly. Declare the level that "
                        "is genuinely independent as the primary unit, or analyse the "
                        "crossed factor as a stratum."
                    ),
                    "detail": {"levels": [coarse, fine], "relation": relation},
                }
            )
        elif relation == "one_to_one":
            findings.append(
                {
                    "code": "degenerate_level",
                    "severity": "warning",
                    "message": (
                        f"{fine!r} is one-to-one with {coarse!r}: every {coarse} contains "
                        f"exactly one {fine}. The level adds no structure, and declaring it "
                        "does not add replicates."
                    ),
                    "detail": {"levels": [coarse, fine]},
                }
            )

    for column in candidate_unit_columns(frame, declared=levels):
        relation = nesting_relation(frame, primary, column)
        if relation == "a_within_b":
            n_groups = int(frame[column].nunique())
            findings.append(
                {
                    "code": "undeclared_coarser_grouping",
                    "severity": "error",
                    "message": (
                        f"every {primary} sits inside exactly one {column!r}, and there are "
                        f"only {n_groups} distinct {column} value(s). {column!r} is a coarser "
                        f"grouping than the declared primary unit, so the {primary}s inside "
                        f"one {column} are not independent replicates. Declare "
                        f"inference.primary_unit: {column}, or state why {column} is not a "
                        "source of shared variation by removing it from the records."
                    ),
                    "detail": {"column": column, "n_groups": n_groups},
                }
            )

    n_primary = int(frame[primary].nunique())
    if n_primary < 2:
        findings.append(
            {
                "code": "single_primary_unit",
                "severity": "error",
                "message": (
                    f"only {n_primary} distinct {primary} value(s); with one replicate there "
                    "is no between-unit variation to estimate and no inference is possible"
                ),
                "detail": {"n_units": n_primary, "unit": primary},
            }
        )
    return findings


def require_valid_structure(frame: pd.DataFrame, inference: InferenceSpec) -> list[dict[str, object]]:
    """Raise on any error-severity finding; return the warnings."""
    findings = check_structure(frame, inference)
    errors = [f for f in findings if f["severity"] == "error"]
    if errors:
        joined = "\n  ".join(f"[{f['code']}] {f['message']}" for f in errors)
        raise InferenceStructureError(
            "the declared inference structure contradicts the records:\n  " + joined
        )
    return [f for f in findings if f["severity"] == "warning"]


def unit_labels(frame: pd.DataFrame, inference: InferenceSpec, unit: str) -> pd.Series:
    """Composite identifier of each row's ``unit``, as a string Series."""
    keys = inference.keys_for(unit)
    missing = [k for k in keys if k not in frame.columns]
    if missing:
        raise KeyError(
            f"cannot label {unit!r}: the table lacks {missing}, which are needed because a "
            f"{unit} is only identified in the context of the level(s) above it"
        )
    if len(keys) == 1:
        return frame[keys[0]].astype("string")
    return frame[list(keys)].astype("string").agg("\x1f".join, axis=1)


def infer_default(frame: pd.DataFrame) -> InferenceSpec:
    """The default structure, restricted to levels the table actually has.

    This is a convenience for exploratory use.  It is **not** an inference of
    independence: the result is still checked by :func:`check_structure`, and a
    coarser undeclared grouping is still an error.
    """
    levels = [u for u in (DEFAULT_PRIMARY_UNIT, *DEFAULT_NESTED_UNITS) if u in frame.columns]
    if not levels:
        raise InferenceStructureError(
            "the records contain none of the default unit columns "
            f"({DEFAULT_PRIMARY_UNIT}, {', '.join(DEFAULT_NESTED_UNITS)}); declare "
            "inference.primary_unit explicitly"
        )
    return InferenceSpec(primary_unit=levels[0], nested_units=tuple(levels[1:]))
