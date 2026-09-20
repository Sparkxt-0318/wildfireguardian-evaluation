"""Stratified comparison, allocation accounting and redundancy diagnostics.

Three questions live here.  Does the contrast hold in every declared stratum,
or only on average?  Were the policies even *allocated* comparable material?
And do the declared strata carry independent information, or are two of them
the same variable wearing different names?

None of these diagnostics claim causal confounding.  A disagreement between an
aggregate and a within-stratum contrast is a fact about the arithmetic; why it
arose is a question about the design that this library cannot answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from wg_eval.compare import ComparisonResult, compare_policies
from wg_eval.config import AnalysisConfig
from wg_eval.dataio import DataSource

MISSING_LABEL = "__missing__"

#: Strata with fewer resolved units than this are not given a contrast at all.
DEFAULT_MIN_UNITS = 5
#: Cells smaller than this are reported as thin support for a stratum contrast.
THIN_CELL = 5


@dataclass
class StratifiedResult:
    """Per-stratum comparisons plus allocation and redundancy diagnostics."""

    column: str
    overall: ComparisonResult | None
    unit: str = "world_id"
    per_stratum: dict[str, ComparisonResult] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    allocation: pd.DataFrame | None = None
    ledger: dict[str, Any] = field(default_factory=dict)
    balance: dict[str, Any] = field(default_factory=dict)
    redundancy: list[dict[str, Any]] = field(default_factory=list)
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "unit": self.unit,
            "strata": sorted(self.per_stratum),
            "skipped": self.skipped,
            "allocation": _records(self.allocation),
            "ledger": self.ledger,
            "balance": self.balance,
            "redundancy": self.redundancy,
            "disagreements": self.disagreements,
            "notes": list(self.notes),
            "overall": self.overall.as_dict() if self.overall else None,
            "per_stratum": {k: v.as_dict() for k, v in self.per_stratum.items()},
        }

    def summary_table(self) -> pd.DataFrame:
        """One row per (stratum, metric, candidate)."""
        frames = []
        if self.overall is not None:
            table = self.overall.summary_table()
            if len(table):
                table.insert(0, self.column, "__all__")
                frames.append(table)
        for name, res in sorted(self.per_stratum.items()):
            table = res.summary_table()
            if len(table):
                table.insert(0, self.column, name)
                frames.append(table)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


def _records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return frame.astype("object").where(frame.notna(), None).to_dict(orient="records")


# ---------------------------------------------------------------------------
# stratum bookkeeping
# ---------------------------------------------------------------------------

def unit_stratum_map(frame: pd.DataFrame, column: str, *, unit: str = "world_id") -> pd.DataFrame:
    """Assign each unit its modal value of ``column``; nulls become ``__missing__``.

    Strata are unit-level design variables.  If a unit's rows disagree the modal
    value is used and the disagreement was already flagged by the validator --
    never silently split a unit across strata, because that would break the
    pairing.
    """
    if column not in frame.columns:
        raise KeyError(f"stratum column {column!r} is not present in the records")
    work = frame[[unit, column]].copy()
    work[column] = work[column].astype("string").fillna(MISSING_LABEL)
    modal = (
        work.groupby([unit, column], dropna=False, observed=True)
        .size()
        .rename("n")
        .reset_index()
        .sort_values([unit, "n", column], ascending=[True, False, True])
        .drop_duplicates(unit)
        .rename(columns={column: "stratum_value"})
    )
    return modal[[unit, "stratum_value"]].reset_index(drop=True)


def allocation_table(frame: pd.DataFrame, column: str, *, unit: str = "world_id") -> pd.DataFrame:
    """Units per (policy, stratum) -- the allocation ledger."""
    mapping = unit_stratum_map(frame, column, unit=unit)
    joined = frame[[unit, "policy_id"]].drop_duplicates().merge(mapping, on=unit, how="left")
    joined["policy_id"] = joined["policy_id"].astype("string")
    table = (
        joined.groupby(["policy_id", "stratum_value"], dropna=False, observed=True)
        .size()
        .rename("n_units")
        .reset_index()
    )
    totals = table.groupby("policy_id", observed=True)["n_units"].transform("sum")
    table["share_of_policy_units"] = table["n_units"] / totals
    return table.sort_values(["policy_id", "stratum_value"]).reset_index(drop=True)


def allocation_ledger(
    frame: pd.DataFrame,
    config: AnalysisConfig,
    *,
    columns: Sequence[str] = (),
) -> dict[str, Any]:
    """A machine-readable account of what each policy was actually given.

    Covers units per policy, shared and unique units, counts at every declared
    nested level, strata composition, run-status/missing-reason counts, and the
    target-versus-observed composition of each stratum.  Nothing here is
    "corrected": imbalance is reported so a reader can judge it, and adjusting
    for it is a separate, explicitly requested step.
    """
    inference = config.inference
    unit = inference.primary_unit
    policies = sorted(str(p) for p in frame["policy_id"].dropna().unique())
    per_policy_units = {
        p: set(frame.loc[frame["policy_id"].astype("string") == p, unit].astype("string"))
        for p in policies
    }
    shared = set.intersection(*per_policy_units.values()) if per_policy_units else set()
    union = set().union(*per_policy_units.values()) if per_policy_units else set()

    per_policy: list[dict[str, Any]] = []
    for policy in policies:
        sub = frame[frame["policy_id"].astype("string") == policy]
        row: dict[str, Any] = {
            "policy_id": policy,
            "n_units": len(per_policy_units[policy]),
            "n_shared_units": len(per_policy_units[policy] & shared),
            "n_unique_units": len(per_policy_units[policy] - set().union(
                *[v for k, v in per_policy_units.items() if k != policy]
            )) if len(policies) > 1 else len(per_policy_units[policy]),
            "n_rows": int(len(sub)),
        }
        for level in inference.levels[1:]:
            if level in sub.columns:
                row[f"n_{level}"] = int(sub[level].nunique())
        per_policy.append(row)

    status_counts: dict[str, dict[str, int]] = {}
    status_column = config.missing_data.status_column
    if status_column in frame.columns:
        statuses = frame[status_column].astype("string").fillna("unspecified")
        for policy in policies:
            mask = frame["policy_id"].astype("string") == policy
            status_counts[policy] = {
                str(k): int(v) for k, v in statuses[mask].value_counts().items()
            }

    failure_counts: dict[str, dict[str, int]] = {}
    if config.failure_column in frame.columns and "mission_success" in frame.columns:
        success = pd.to_numeric(frame["mission_success"], errors="coerce")
        reasons = frame[config.failure_column].astype("string").fillna("unspecified")
        for policy in policies:
            mask = (frame["policy_id"].astype("string") == policy) & (success == 0)
            failure_counts[policy] = {
                str(k): int(v) for k, v in reasons[mask].value_counts().items()
            }

    strata_blocks: dict[str, Any] = {}
    for column in list(columns) or list(config.strata):
        if column not in frame.columns:
            continue
        table = allocation_table(frame, column, unit=unit)
        observed = unit_stratum_map(frame, column, unit=unit)
        target_mix = (
            observed["stratum_value"].value_counts(normalize=True).round(6).to_dict()
        )
        shared_units = pd.Series(sorted(shared), dtype="string")
        shared_mix = (
            observed[observed[unit].astype("string").isin(shared_units)]["stratum_value"]
            .value_counts(normalize=True)
            .round(6)
            .to_dict()
        )
        strata_blocks[column] = {
            "allocation": _records(table),
            "balance": allocation_balance(frame, column, unit=unit),
            "target_composition_all_units": {str(k): float(v) for k, v in target_mix.items()},
            "observed_composition_shared_units": {str(k): float(v) for k, v in shared_mix.items()},
            "composition_shift": _composition_shift(target_mix, shared_mix),
        }

    return {
        "unit": unit,
        "hierarchy": list(inference.levels),
        "policies": policies,
        "n_units_total": len(union),
        "n_units_shared": len(shared),
        "per_policy": per_policy,
        "run_status_counts": status_counts,
        "failure_reason_counts": failure_counts,
        "strata": strata_blocks,
        "note": (
            "Reported, not corrected. Adjusting a comparison for allocation imbalance is a "
            "separate analysis that must be requested explicitly."
        ),
    }


def _composition_shift(target: dict[Any, float], observed: dict[Any, float]) -> dict[str, float]:
    """How the stratum mix differs between all units and the shared ones."""
    keys = set(map(str, target)) | set(map(str, observed))
    return {
        k: round(float(observed.get(k, 0.0)) - float(target.get(k, 0.0)), 6) for k in sorted(keys)
    }


def allocation_balance(frame: pd.DataFrame, column: str, *, unit: str = "world_id") -> dict[str, Any]:
    """Quantify how unevenly policies were allocated across strata.

    ``max_share_gap`` is the largest difference, over strata, between any two
    policies' share of their own units in that stratum.  Zero means balanced
    allocation; large values mean the policies were not given comparable
    material, so an unpaired comparison is not measuring the policies alone.
    """
    table = allocation_table(frame, column, unit=unit)
    wide = table.pivot_table(
        index="stratum_value", columns="policy_id", values="share_of_policy_units", aggfunc="sum"
    ).fillna(0.0)
    if wide.shape[1] < 2:
        return {"checked": False, "reason": "fewer than two policies"}
    gaps = (wide.max(axis=1) - wide.min(axis=1)).astype("float64")
    worst = gaps.idxmax() if len(gaps) else None
    return {
        "checked": True,
        "column": column,
        "max_share_gap": float(gaps.max()) if len(gaps) else 0.0,
        "worst_stratum": None if worst is None else str(worst),
        "shares": {
            str(idx): {str(k): float(v) for k, v in row.items()} for idx, row in wide.iterrows()
        },
    }


def stratum_redundancy(
    frame: pd.DataFrame, columns: Sequence[str], *, unit: str = "world_id"
) -> list[dict[str, Any]]:
    """Detect strata that do not carry independent information.

    Redundant stratification dimensions look like extra evidence and are not.
    Five patterns are reported:

    * **identical** -- two columns partition the units the same way;
    * **deterministic nesting** -- one column is a refinement of another, so a
      contrast "within" the finer one is also within the coarser one;
    * **near-empty cells** -- a level with too few units to support a contrast;
    * **policy confounding** -- a level evaluated under only one policy;
    * **single level** -- a column with one value, which strata nothing.
    """
    findings: list[dict[str, Any]] = []
    columns = [c for c in columns if c in frame.columns]
    if not columns:
        return findings

    maps = {c: unit_stratum_map(frame, c, unit=unit).set_index(unit)["stratum_value"] for c in columns}

    for column, values in maps.items():
        levels = values.nunique()
        if levels < 2:
            findings.append(
                {
                    "code": "single_level_stratum",
                    "message": (
                        f"stratum {column!r} takes one value across all {unit}s; it partitions "
                        "nothing and adds no evidence"
                    ),
                    "detail": {"column": column, "n_levels": int(levels)},
                }
            )
        counts = values.value_counts()
        thin = {str(k): int(v) for k, v in counts.items() if v < THIN_CELL}
        if thin:
            findings.append(
                {
                    "code": "thin_stratum_cell",
                    "message": (
                        f"stratum {column!r} has level(s) with fewer than {THIN_CELL} {unit}s "
                        f"({thin}); a contrast inside them is not supportable"
                    ),
                    "detail": {"column": column, "thin_levels": thin},
                }
            )

    policy_by_unit = (
        frame[[unit, "policy_id"]]
        .astype({"policy_id": "string"})
        .drop_duplicates()
        .groupby(unit)["policy_id"]
        .apply(lambda s: frozenset(s.tolist()))
    )
    all_policies = frozenset(frame["policy_id"].astype("string").dropna().unique().tolist())
    for column, values in maps.items():
        joined = pd.DataFrame({"stratum": values}).join(policy_by_unit.rename("policies"))
        for level, group in joined.dropna().groupby("stratum", observed=True):
            seen = frozenset().union(*group["policies"].tolist()) if len(group) else frozenset()
            if len(all_policies) > 1 and seen != all_policies:
                findings.append(
                    {
                        "code": "stratum_confounded_with_policy",
                        "message": (
                            f"stratum {column!r} level {str(level)!r} was evaluated under only "
                            f"{sorted(seen)}; policy and stratum are not separable there, so a "
                            "contrast inside that level cannot be formed"
                        ),
                        "detail": {
                            "column": column,
                            "level": str(level),
                            "policies_present": sorted(seen),
                        },
                    }
                )

    names = list(maps)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            left, right = maps[a].align(maps[b], join="inner")
            if left.empty:
                continue
            a_in_b = int(left.groupby(right, observed=True).nunique().max() or 0) <= 1
            b_in_a = int(right.groupby(left, observed=True).nunique().max() or 0) <= 1
            if a_in_b and b_in_a:
                findings.append(
                    {
                        "code": "identical_strata",
                        "message": (
                            f"strata {a!r} and {b!r} partition the {unit}s identically. They are "
                            "one dimension under two names and do not provide independent "
                            "stratified evidence."
                        ),
                        "detail": {"columns": [a, b]},
                    }
                )
            elif a_in_b or b_in_a:
                coarse, fine = (b, a) if a_in_b else (a, b)
                findings.append(
                    {
                        "code": "nested_strata",
                        "message": (
                            f"stratum {fine!r} is a deterministic refinement of {coarse!r}: every "
                            f"{fine} level sits inside one {coarse} level. Results within "
                            f"{fine!r} are not independent of results within {coarse!r}."
                        ),
                        "detail": {"coarse": coarse, "fine": fine},
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# stratified comparison
# ---------------------------------------------------------------------------

def compare_by_stratum(
    frame: pd.DataFrame,
    config: AnalysisConfig,
    column: str,
    *,
    source: DataSource | None = None,
    min_units: int = DEFAULT_MIN_UNITS,
    include_overall: bool = True,
) -> StratifiedResult:
    """Repeat the whole paired comparison inside each level of ``column``.

    Strata with fewer than ``min_units`` shared units are skipped with a
    recorded reason rather than analysed into an uninterpretable interval.
    """
    unit = config.inference.primary_unit
    mapping = unit_stratum_map(frame, column, unit=unit)
    work = frame.merge(mapping, on=unit, how="left")
    work["stratum_value"] = work["stratum_value"].astype("string").fillna(MISSING_LABEL)

    result = StratifiedResult(column=column, overall=None, unit=unit)
    result.allocation = allocation_table(frame, column, unit=unit)
    result.balance = allocation_balance(frame, column, unit=unit)
    result.ledger = allocation_ledger(frame, config, columns=[column])
    result.redundancy = stratum_redundancy(frame, [column, *[c for c in config.strata if c != column]], unit=unit)

    if result.balance.get("checked") and result.balance["max_share_gap"] > 0.10:
        result.notes.append(
            f"ALLOCATION IMBALANCE on `{column}`: policies differ by up to "
            f"{result.balance['max_share_gap']:.0%} in their share of {unit}s per stratum "
            f"(largest gap in {result.balance['worst_stratum']}). This is a description of "
            "the allocation, not a diagnosis of its cause. The paired analysis is restricted "
            "to shared units; the per-stratum results below are the check that matters."
        )

    if include_overall:
        result.overall = compare_policies(work, config, source=source, validate=False)

    for value, group in work.groupby("stratum_value", dropna=False, observed=True):
        label = str(value)
        n_units = int(group[unit].nunique())
        policies = sorted(group["policy_id"].astype("string").dropna().unique().tolist())
        shared = _shared_units(group, policies, unit)
        if len(policies) < 2:
            result.skipped[label] = (
                f"only {len(policies)} policy present in this stratum, so no contrast exists here"
            )
            continue
        if len(shared) < min_units:
            result.skipped[label] = (
                f"only {len(shared)} {unit}(s) carry every policy (min_units={min_units}); a "
                f"contrast here would not be supportable ({n_units} unit(s) present in total)"
            )
            continue
        try:
            result.per_stratum[label] = compare_policies(
                group.reset_index(drop=True), config, source=source, validate=False,
                include_failures=False,
            )
        except (ValueError, KeyError) as exc:
            result.skipped[label] = str(exc)

    result.disagreements = _aggregate_vs_stratum(result)
    for item in result.disagreements:
        result.notes.append(item["message"])
    for item in result.redundancy:
        result.notes.append(item["message"])
    return result


def _shared_units(frame: pd.DataFrame, policies: list[str], unit: str) -> list[str]:
    if len(policies) < 2:
        return []
    sets = [
        set(frame.loc[frame["policy_id"].astype("string") == p, unit].astype("string"))
        for p in policies
    ]
    return sorted(set.intersection(*sets))


def _aggregate_vs_stratum(result: StratifiedResult) -> list[dict[str, Any]]:
    """Report arithmetic disagreements between aggregate and stratified contrasts.

    The wording is deliberate.  A reversal between an aggregate and its strata
    is a property of the numbers; it can arise from unequal allocation, from
    genuine effect heterogeneity, or from noise in thin strata.  Calling it
    "confounding" would assert a causal explanation this library has no way to
    check.
    """
    out: list[dict[str, Any]] = []
    by_metric: dict[tuple[str, str, str], dict[str, str]] = {}
    supported: dict[tuple[str, str, str], dict[str, int]] = {}
    for label, res in result.per_stratum.items():
        for c in res.comparisons:
            key = (c.metric.name, c.baseline, c.candidate)
            if c.verdict.favours:
                by_metric.setdefault(key, {})[label] = c.verdict.favours
                supported.setdefault(key, {})[label] = c.difference.n_clusters

    aggregate_favours: dict[tuple[str, str, str], str | None] = {}
    if result.overall is not None:
        for c in result.overall.comparisons:
            aggregate_favours[(c.metric.name, c.baseline, c.candidate)] = c.verdict.favours

    for key, per_stratum in by_metric.items():
        metric, baseline, candidate = key
        directions = set(per_stratum.values())
        counts = supported.get(key, {})
        if len(directions) > 1:
            out.append(
                {
                    "kind": "direction_differs_between_strata",
                    "metric": metric,
                    "baseline": baseline,
                    "candidate": candidate,
                    "by_stratum": per_stratum,
                    "units_per_stratum": counts,
                    "message": (
                        f"DIRECTION DIFFERS BETWEEN STRATA for {metric} ({candidate} vs "
                        f"{baseline}): "
                        + ", ".join(
                            f"{k} favours {v} (n={counts.get(k, '?')})"
                            for k, v in sorted(per_stratum.items())
                        )
                        + ". An aggregate number averages over this and describes no single "
                        "stratum. The reason for the difference is not determined here."
                    ),
                }
            )
            continue
        only = next(iter(directions))
        pooled = aggregate_favours.get(key)
        if pooled and pooled != only:
            out.append(
                {
                    "kind": "aggregate_contradicts_strata",
                    "metric": metric,
                    "baseline": baseline,
                    "candidate": candidate,
                    "aggregate_favours": pooled,
                    "stratum_favours": only,
                    "units_per_stratum": counts,
                    "message": (
                        f"AGGREGATE AND WITHIN-STRATUM CONTRASTS DISAGREE for {metric}: the "
                        f"aggregate favours {pooled} while every resolved stratum favours "
                        f"{only}. This is an arithmetic fact, not a diagnosis; unequal "
                        "allocation across strata is one explanation among several. Read the "
                        "allocation ledger before interpreting either number."
                    ),
                }
            )
    return out


def stratified_estimate(
    result: StratifiedResult, metric: str, candidate: str, *, weights: dict[str, float] | None = None
) -> dict[str, Any]:
    """Stratum-weighted mean of the per-stratum differences.

    Defaults to weighting each stratum by its number of shared units, which
    recovers the aggregate estimate when allocation is balanced.  This is an
    explicitly requested adjustment, never applied automatically.
    """
    rows = []
    for label, res in result.per_stratum.items():
        try:
            comparison = res.get(metric, candidate)
        except KeyError:
            continue
        rows.append(
            {
                "stratum": label,
                "difference": comparison.difference.estimate,
                "n_units": comparison.difference.n_clusters,
                "ci_low": comparison.difference.ci_low,
                "ci_high": comparison.difference.ci_high,
            }
        )
    if not rows:
        return {"available": False, "reason": "no per-stratum comparison for this metric/candidate"}
    table = pd.DataFrame(rows)
    if weights:
        table["weight"] = table["stratum"].map(weights).astype("float64")
        if table["weight"].isna().any():
            missing = table.loc[table["weight"].isna(), "stratum"].tolist()
            raise KeyError(f"no weight supplied for stratum/strata {missing}")
    else:
        table["weight"] = table["n_units"].astype("float64")
    total = float(table["weight"].sum())
    if total <= 0:
        return {"available": False, "reason": "weights sum to zero"}
    estimate = float((table["difference"] * table["weight"]).sum() / total)
    return {
        "available": True,
        "metric": metric,
        "candidate": candidate,
        "weighted_difference": estimate,
        "weighting": "supplied" if weights else "shared units per stratum",
        "per_stratum": table.assign(weight_share=lambda d: d["weight"] / total).to_dict(
            orient="records"
        ),
        "note": (
            "A stratum-weighted difference re-balances an unequal allocation. It has no "
            "interval here and it is not a substitute for reporting the per-stratum results, "
            "which may point in different directions."
        ),
    }
