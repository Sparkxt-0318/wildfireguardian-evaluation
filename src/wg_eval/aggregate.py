"""Filtering, missing-data handling and roll-up through the nesting hierarchy.

Aggregation is where pseudoreplication is either introduced or prevented.
Rolling residents up to events and events up to worlds *in two declared steps*
is not the same as pooling every resident and taking one mean: when events
differ in size the two differ, and only one of them matches the estimand the
analyst has in mind.  So both steps are declared, and both are recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from wg_eval.config import AggregationSpec, FilterSpec, MetricSpec, MissingDataSpec
from wg_eval.schema import level_keys

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


@dataclass
class PreparationLog:
    """Everything that happened to the rows before a statistic touched them."""

    n_input_rows: int = 0
    n_output_rows: int = 0
    filters_applied: list[dict[str, Any]] = field(default_factory=list)
    exclusions_applied: list[dict[str, Any]] = field(default_factory=list)
    missing_data: dict[str, Any] = field(default_factory=dict)
    aggregation_rules: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_input_rows": self.n_input_rows,
            "n_output_rows": self.n_output_rows,
            "n_rows_removed": self.n_input_rows - self.n_output_rows,
            "filters_applied": self.filters_applied,
            "exclusions_applied": self.exclusions_applied,
            "missing_data": self.missing_data,
            "aggregation_rules": self.aggregation_rules,
        }


def apply_filters(frame: pd.DataFrame, filters: FilterSpec) -> tuple[pd.DataFrame, PreparationLog]:
    """Apply include/exclude filters and named exclusion expressions.

    Every filter records how many rows and how many worlds it removed, because
    a filter that silently drops whole worlds can reverse a ranking.
    """
    log = PreparationLog(n_input_rows=int(len(frame)))
    out = frame

    for column, values in filters.include.items():
        if column not in out.columns:
            raise KeyError(f"filters.include references unknown column {column!r}")
        before, worlds_before = len(out), _n_worlds(out)
        wanted = pd.Series([str(v) for v in values], dtype="string")
        out = out[out[column].astype("string").isin(wanted)]
        log.filters_applied.append(
            {
                "kind": "include",
                "column": column,
                "values": [str(v) for v in values],
                "rows_removed": int(before - len(out)),
                "worlds_removed": int(worlds_before - _n_worlds(out)),
            }
        )

    for column, values in filters.exclude.items():
        if column not in out.columns:
            raise KeyError(f"filters.exclude references unknown column {column!r}")
        before, worlds_before = len(out), _n_worlds(out)
        unwanted = pd.Series([str(v) for v in values], dtype="string")
        out = out[~out[column].astype("string").isin(unwanted)]
        log.filters_applied.append(
            {
                "kind": "exclude",
                "column": column,
                "values": [str(v) for v in values],
                "rows_removed": int(before - len(out)),
                "worlds_removed": int(worlds_before - _n_worlds(out)),
            }
        )

    for name, expression in filters.exclusions.items():
        before, worlds_before = len(out), _n_worlds(out)
        dropped = out.query(expression)
        out = out.drop(index=dropped.index)
        log.exclusions_applied.append(
            {
                "name": name,
                "expression": expression,
                "rows_removed": int(before - len(out)),
                "worlds_removed": int(worlds_before - _n_worlds(out)),
            }
        )

    log.n_output_rows = int(len(out))
    return out.reset_index(drop=True), log


def _n_worlds(frame: pd.DataFrame) -> int:
    return int(frame["world_id"].nunique()) if "world_id" in frame.columns else 0


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


def aggregate_to_level(
    frame: pd.DataFrame,
    column: str,
    level: str,
    aggregation: AggregationSpec,
    *,
    extra_keys: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Roll ``column`` up to ``level``, one row per (keys..., policy_id).

    The roll-up always proceeds through the declared hierarchy
    (resident -> event -> world); it never pools levels implicitly.
    """
    if level not in ("world", "event", "resident"):
        raise ValueError(f"unknown level {level!r}")
    if column not in frame.columns:
        raise KeyError(f"column {column!r} is not present in the records")

    carry = tuple(k for k in extra_keys if k in frame.columns)
    work = frame[list(dict.fromkeys(["world_id", "event_id", "resident_id", "policy_id", column, *carry]))].copy()
    work[column] = pd.to_numeric(work[column], errors="coerce")

    if level == "resident":
        out = work.rename(columns={column: "value"})
        return out[["world_id", "event_id", "resident_id", "policy_id", "value", *carry]].reset_index(drop=True)

    rule = aggregation.rule_for(column, "resident_to_event")
    event = _group_agg(work, ["world_id", "event_id", "policy_id", *carry], column, rule)
    if level == "event":
        return event.rename(columns={column: "value"}).reset_index(drop=True)

    rule2 = aggregation.rule_for(column, "event_to_world")
    world = _group_agg(event, ["world_id", "policy_id", *carry], column, rule2)
    return world.rename(columns={column: "value"}).reset_index(drop=True)


def _group_agg(frame: pd.DataFrame, keys: list[str], column: str, rule: str) -> pd.DataFrame:
    if rule not in _PANDAS_RULE:
        raise ValueError(f"unsupported aggregation rule {rule!r}")
    grouped = frame.groupby(keys, dropna=False, observed=True)[column].agg(_PANDAS_RULE[rule])
    return grouped.reset_index()


def panel(
    frame: pd.DataFrame,
    metric: MetricSpec,
    aggregation: AggregationSpec,
    *,
    strata: tuple[str, ...] = (),
) -> pd.DataFrame:
    """The analysis panel for one metric: values at the metric's level.

    Returns a frame with ``world_id``, ``policy_id``, ``value`` and, where the
    level implies them, ``event_id`` / ``resident_id``, plus any declared
    stratum columns carried along for later subsetting.
    """
    return aggregate_to_level(frame, metric.column, metric.level, aggregation, extra_keys=strata)


def cluster_labels(panel_frame: pd.DataFrame, cluster_level: str) -> pd.Series:
    """The cluster identifier of each row of a panel, as a string Series."""
    keys = level_keys(cluster_level)
    missing = [k for k in keys if k not in panel_frame.columns]
    if missing:
        raise KeyError(
            f"panel lacks column(s) {missing} needed to cluster at level {cluster_level!r}; "
            "the metric level is coarser than the requested cluster level"
        )
    if len(keys) == 1:
        return panel_frame[keys[0]].astype("string")
    return panel_frame[list(keys)].astype("string").agg("\x1f".join, axis=1)


def observation_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """Per (world, policy) observation counts -- the pseudoreplication ledger."""
    counts = (
        frame.groupby(["world_id", "policy_id"], dropna=False, observed=True)
        .agg(n_events=("event_id", "nunique"), n_observations=("resident_id", "size"))
        .reset_index()
    )
    return counts
