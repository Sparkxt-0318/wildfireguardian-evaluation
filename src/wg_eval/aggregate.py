"""Filtering, run status, missing data and roll-up through the declared hierarchy.

Aggregation is where pseudoreplication is either introduced or prevented.
Rolling observations up to events and events up to units *in declared steps* is
not the same as pooling every observation and taking one mean: when groups
differ in size the two differ, and only one of them matches the estimand the
analyst has in mind.  So every step is declared, and every step is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from wg_eval.config import AggregationSpec, FilterSpec, MetricSpec, MissingDataSpec
from wg_eval.hierarchy import InferenceSpec, unit_labels

#: Rule name -> pandas aggregation callable/name.
_PANDAS_RULE = {
    "mean": "mean",
    "sum": "sum",
    "median": "median",
    "max": "max",
    "min": "min",
    "first": "first",
    "any": "max",
    "all": "min",
}

#: Run statuses the library understands.  A producer may use any string; these
#: are the ones with a default handling rule.
KNOWN_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "crashed",
    "timeout",
    "infeasible",
    "not_evaluated",
    "excluded",
)

#: Default treatment when a config declares no ``status_handling`` for a value.
DEFAULT_STATUS_HANDLING: dict[str, str] = {
    "completed": "completed",
    "crashed": "missing",
    "timeout": "missing",
    "infeasible": "excluded_documented",
    "not_evaluated": "missing",
    "excluded": "excluded_documented",
}


@dataclass
class PreparationLog:
    """Everything that happened to the rows before a statistic touched them."""

    n_input_rows: int = 0
    n_output_rows: int = 0
    filters_applied: list[dict[str, Any]] = field(default_factory=list)
    exclusions_applied: list[dict[str, Any]] = field(default_factory=list)
    missing_data: dict[str, Any] = field(default_factory=dict)
    run_status: dict[str, Any] = field(default_factory=dict)
    aggregation_rules: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_input_rows": self.n_input_rows,
            "n_output_rows": self.n_output_rows,
            "n_rows_removed": self.n_input_rows - self.n_output_rows,
            "filters_applied": self.filters_applied,
            "exclusions_applied": self.exclusions_applied,
            "missing_data": self.missing_data,
            "run_status": self.run_status,
            "aggregation_rules": self.aggregation_rules,
        }


def apply_filters(
    frame: pd.DataFrame, filters: FilterSpec, *, unit: str = "world_id"
) -> tuple[pd.DataFrame, PreparationLog]:
    """Apply include/exclude filters and named exclusion expressions.

    Every filter records how many rows and how many *units* it removed, because
    a filter that silently drops whole units can reverse a ranking.
    """
    log = PreparationLog(n_input_rows=int(len(frame)))
    out = frame

    for column, values in filters.include.items():
        if column not in out.columns:
            raise KeyError(f"filters.include references unknown column {column!r}")
        before, units_before = len(out), _n_units(out, unit)
        wanted = pd.Series([str(v) for v in values], dtype="string")
        out = out[out[column].astype("string").isin(wanted)]
        log.filters_applied.append(
            {
                "kind": "include",
                "column": column,
                "values": [str(v) for v in values],
                "rows_removed": int(before - len(out)),
                "units_removed": int(units_before - _n_units(out, unit)),
            }
        )

    for column, values in filters.exclude.items():
        if column not in out.columns:
            raise KeyError(f"filters.exclude references unknown column {column!r}")
        before, units_before = len(out), _n_units(out, unit)
        unwanted = pd.Series([str(v) for v in values], dtype="string")
        out = out[~out[column].astype("string").isin(unwanted)]
        log.filters_applied.append(
            {
                "kind": "exclude",
                "column": column,
                "values": [str(v) for v in values],
                "rows_removed": int(before - len(out)),
                "units_removed": int(units_before - _n_units(out, unit)),
            }
        )

    for name, expression in filters.exclusions.items():
        before, units_before = len(out), _n_units(out, unit)
        dropped = out.query(expression)
        out = out.drop(index=dropped.index)
        log.exclusions_applied.append(
            {
                "name": name,
                "expression": expression,
                "rows_removed": int(before - len(out)),
                "units_removed": int(units_before - _n_units(out, unit)),
            }
        )

    log.n_output_rows = int(len(out))
    return out.reset_index(drop=True), log


def _n_units(frame: pd.DataFrame, unit: str) -> int:
    return int(frame[unit].nunique()) if unit in frame.columns else 0


# ---------------------------------------------------------------------------
# run status
# ---------------------------------------------------------------------------

def run_status_ledger(
    frame: pd.DataFrame, spec: MissingDataSpec, *, unit: str = "world_id"
) -> dict[str, Any]:
    """Count every run by policy and status, before anything is dropped.

    A run that crashed is not an absent row; it is a row that says the policy
    failed to produce a result.  Whether that is an outcome or a nuisance is a
    domain judgement, so the library records it either way and refuses to let it
    vanish silently from a denominator.
    """
    column = spec.status_column
    if column not in frame.columns:
        return {
            "available": False,
            "reason": f"no `{column}` column; every row is assumed to be a completed run",
            "column": column,
        }
    work = frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work[column] = work[column].astype("string").fillna("unspecified")

    counts = (
        work.groupby(["policy_id", column], dropna=False, observed=True)
        .agg(n_rows=(column, "size"), n_units=(unit, "nunique"))
        .reset_index()
        .rename(columns={column: "status"})
    )
    handling = {
        str(status): spec.status_handling.get(
            str(status), DEFAULT_STATUS_HANDLING.get(str(status), "missing")
        )
        for status in counts["status"].unique()
    }
    counts["handling"] = counts["status"].map(handling)

    unknown = sorted(set(counts["status"]) - set(KNOWN_RUN_STATUSES))
    undeclared = sorted(
        s for s in counts["status"].unique() if str(s) not in spec.status_handling
    )
    return {
        "available": True,
        "column": column,
        "counts": counts.to_dict(orient="records"),
        "handling": handling,
        "unknown_statuses": unknown,
        "undeclared_statuses": undeclared,
        "n_not_completed": int(counts.loc[counts["status"] != "completed", "n_rows"].sum()),
        "assumed_mechanism": spec.assumed_mechanism,
        "mechanism_justification": spec.mechanism_justification,
    }


def apply_run_status(
    frame: pd.DataFrame, spec: MissingDataSpec, *, success_column: str = "mission_success"
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Resolve rows whose run did not complete, under the declared handling.

    * ``completed``  -- kept as an ordinary observation.
    * ``failure``    -- kept, and counted as a failed observation: the binary
      outcome is forced to 0 so the run stays in the denominator.
    * ``excluded_documented`` -- removed, with counts recorded. The estimand
      becomes conditional on the runs that were feasible.
    * ``missing``    -- kept with its (missing) outcome, for the missing-data
      policy to resolve.
    """
    column = spec.status_column
    info: dict[str, Any] = {"column": column, "applied": False}
    if column not in frame.columns:
        return frame, info

    work = frame.copy()
    status = work[column].astype("string").fillna("unspecified")
    handling = status.map(
        lambda s: spec.status_handling.get(str(s), DEFAULT_STATUS_HANDLING.get(str(s), "missing"))
    )

    as_failure = handling == "failure"
    n_failure = int(as_failure.sum())
    if n_failure and success_column in work.columns:
        work.loc[as_failure, success_column] = 0
        if "failure_reason" in work.columns:
            blank = work["failure_reason"].isna() | (
                work["failure_reason"].astype("string").str.len().fillna(0) == 0
            )
            work.loc[as_failure & blank, "failure_reason"] = (
                "run_" + status[as_failure & blank].astype("string")
            )

    excluded = handling == "excluded_documented"
    n_excluded = int(excluded.sum())
    excluded_units = (
        sorted(work.loc[excluded, "world_id"].astype("string").unique().tolist())
        if n_excluded and "world_id" in work.columns
        else []
    )
    if n_excluded:
        work = work.loc[~excluded]

    info.update(
        {
            "applied": True,
            "n_counted_as_failure": n_failure,
            "n_excluded_documented": n_excluded,
            "excluded_units": excluded_units[:20],
            "n_left_missing": int((handling == "missing").sum()),
            "n_completed": int((handling == "completed").sum()),
        }
    )
    return work.reset_index(drop=True), info


# ---------------------------------------------------------------------------
# missing values
# ---------------------------------------------------------------------------

def apply_missing_policy(
    frame: pd.DataFrame,
    column: str,
    spec: MissingDataSpec,
    *,
    direction: str = "lower_is_better",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Resolve missing values of ``column`` under the declared policy.

    ``impute_worst`` / ``impute_best`` substitute the observed extreme *in the
    direction implied by the metric*, which is the honest sensitivity bound:
    imputing the worst case cannot flatter the policy that lost the data.
    """
    if column not in frame.columns:
        raise KeyError(f"metric column {column!r} is not present in the records")
    values = pd.to_numeric(frame[column], errors="coerce")
    missing_mask = ~np.isfinite(values.to_numpy(dtype="float64", na_value=np.nan))
    n_missing = int(missing_mask.sum())
    info: dict[str, Any] = {
        "column": column,
        "policy": spec.policy,
        "assumed_mechanism": spec.assumed_mechanism,
        "n_missing": n_missing,
        "n_before": int(len(frame)),
    }
    if n_missing == 0:
        info["n_after"] = int(len(frame))
        return frame, info

    info["missing_by_policy"] = {
        str(k): int(v)
        for k, v in frame.loc[missing_mask].groupby("policy_id", dropna=False).size().items()
    }

    if spec.policy == "fail":
        raise ValueError(
            f"{n_missing} missing value(s) in column {column!r} and missing_data.policy='fail'. "
            "Decide explicitly how they should be handled."
        )
    if spec.policy == "drop_record":
        out = frame.loc[~missing_mask].reset_index(drop=True)
    else:
        finite = values[~missing_mask]
        if len(finite) == 0:
            raise ValueError(f"column {column!r} has no finite values to impute from")
        worst_is_max = direction == "lower_is_better"
        if spec.policy == "impute_worst":
            fill = float(finite.max()) if worst_is_max else float(finite.min())
        else:  # impute_best
            fill = float(finite.min()) if worst_is_max else float(finite.max())
        out = frame.copy()
        out[column] = values.fillna(fill)
        info["imputed_value"] = fill
    info["n_after"] = int(len(out))
    return out, info


# ---------------------------------------------------------------------------
# roll-up
# ---------------------------------------------------------------------------

def aggregate_to_level(
    frame: pd.DataFrame,
    column: str,
    level: str,
    aggregation: AggregationSpec,
    inference: InferenceSpec,
    *,
    extra_keys: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Roll ``column`` up to ``level``, one row per (keys..., policy_id).

    The roll-up walks the declared hierarchy one step at a time from the finest
    level to ``level``; it never pools levels implicitly.
    """
    if column not in frame.columns:
        raise KeyError(f"column {column!r} is not present in the records")
    target_index = inference.index_of(level)
    levels = inference.levels

    carry = tuple(k for k in extra_keys if k in frame.columns)
    needed = list(dict.fromkeys([*levels, "policy_id", column, *carry]))
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise KeyError(f"records lack column(s) {missing} required by the declared hierarchy")
    work = frame[needed].copy()
    work[column] = pd.to_numeric(work[column], errors="coerce")

    current = len(levels) - 1
    if current == target_index:
        keys = list(levels[: target_index + 1])
        out = work[[*keys, "policy_id", column, *carry]].rename(columns={column: "value"})
        return out.reset_index(drop=True)

    while current > target_index:
        from_unit, to_unit = levels[current], levels[current - 1]
        rule = aggregation.rule_for(column, from_unit, to_unit)
        keys = [*levels[:current], "policy_id", *carry]
        work = _group_agg(work, keys, column, rule)
        current -= 1

    keys = list(levels[: target_index + 1])
    return work[[*keys, "policy_id", column, *carry]].rename(columns={column: "value"}).reset_index(
        drop=True
    )


def _group_agg(frame: pd.DataFrame, keys: list[str], column: str, rule: str) -> pd.DataFrame:
    if rule not in _PANDAS_RULE:
        raise ValueError(f"unsupported aggregation rule {rule!r}")
    grouped = frame.groupby(keys, dropna=False, observed=True)[column].agg(_PANDAS_RULE[rule])
    return grouped.reset_index()


def panel(
    frame: pd.DataFrame,
    metric: MetricSpec,
    aggregation: AggregationSpec,
    inference: InferenceSpec,
    *,
    strata: tuple[str, ...] = (),
) -> pd.DataFrame:
    """The analysis panel for one metric: values at the metric's level."""
    return aggregate_to_level(
        frame, metric.column, metric.level, aggregation, inference, extra_keys=strata
    )


def cluster_labels(
    panel_frame: pd.DataFrame, inference: InferenceSpec, unit: str | None = None
) -> pd.Series:
    """The resampling-unit identifier of each row of a panel."""
    return unit_labels(panel_frame, inference, unit or inference.primary_unit)


def observation_counts(frame: pd.DataFrame, inference: InferenceSpec) -> pd.DataFrame:
    """Per (unit, policy) counts at every declared level -- the nesting ledger."""
    primary = inference.primary_unit
    aggs: dict[str, tuple[str, str]] = {}
    for level in inference.levels[1:]:
        aggs[f"n_{level}"] = (level, "nunique")
    aggs["n_observations"] = (primary, "size")
    return (
        frame.groupby([primary, "policy_id"], dropna=False, observed=True)
        .agg(**aggs)
        .reset_index()
    )
