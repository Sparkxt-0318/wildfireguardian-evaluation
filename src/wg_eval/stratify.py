"""Stratified comparison and confounding diagnostics.

Two questions live here.  First: does the conclusion hold in every declared
stratum, or only on average?  Second, and more dangerous: were the policies
even *allocated* to strata in the same proportions?  A policy tested mostly on
easy worlds will win an unstratified comparison on merit it does not have, and
the only way to see that is to count the allocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from wg_eval.compare import ComparisonResult, compare_policies
from wg_eval.config import AnalysisConfig
from wg_eval.dataio import DataSource

MISSING_LABEL = "__missing__"


@dataclass
class StratifiedResult:
    """Per-stratum comparisons plus the allocation diagnostics."""

    column: str
    overall: ComparisonResult | None
    per_stratum: dict[str, ComparisonResult] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    allocation: pd.DataFrame | None = None
    balance: dict[str, Any] = field(default_factory=dict)
    heterogeneity: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "strata": sorted(self.per_stratum),
            "skipped": self.skipped,
            "allocation": (
                self.allocation.astype("object").where(self.allocation.notna(), None).to_dict(orient="records")
                if self.allocation is not None
                else None
            ),
            "balance": self.balance,
            "heterogeneity": self.heterogeneity,
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


def world_stratum_map(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Assign each world its modal value of ``column``; nulls become ``__missing__``.

    Strata are world-level design variables.  If a world's rows disagree the
    modal value is used and the disagreement was already flagged by the
    validator -- never silently split a world across strata, because that would
    break the pairing.
    """
    if column not in frame.columns:
        raise KeyError(f"stratum column {column!r} is not present in the records")
    work = frame[["world_id", column]].copy()
    work[column] = work[column].astype("string").fillna(MISSING_LABEL)
    modal = (
        work.groupby(["world_id", column], dropna=False, observed=True)
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["world_id", "n", column], ascending=[True, False, True])
        .drop_duplicates("world_id")
        .rename(columns={column: "stratum_value"})
    )
    return modal[["world_id", "stratum_value"]].reset_index(drop=True)


def allocation_table(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Worlds per (policy, stratum) -- the allocation ledger."""
    mapping = world_stratum_map(frame, column)
    joined = frame[["world_id", "policy_id"]].drop_duplicates().merge(mapping, on="world_id", how="left")
    joined["policy_id"] = joined["policy_id"].astype("string")
    table = (
        joined.groupby(["policy_id", "stratum_value"], dropna=False, observed=True)
        .size()
        .rename("n_worlds")
        .reset_index()
    )
    totals = table.groupby("policy_id", observed=True)["n_worlds"].transform("sum")
    table["share_of_policy_worlds"] = table["n_worlds"] / totals
    return table.sort_values(["policy_id", "stratum_value"]).reset_index(drop=True)


def allocation_balance(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    """Quantify how unevenly policies were allocated across strata.

    ``max_share_gap`` is the largest difference, over strata, between any two
    policies' share of their own worlds in that stratum.  Zero means perfectly
    balanced allocation; large values mean the policies were not tested on
    comparable material and an unpaired comparison is confounded.
    """
    table = allocation_table(frame, column)
    wide = table.pivot_table(
        index="stratum_value", columns="policy_id", values="share_of_policy_worlds", aggfunc="sum"
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


def compare_by_stratum(
    frame: pd.DataFrame,
    config: AnalysisConfig,
    column: str,
    *,
    source: DataSource | None = None,
    min_clusters: int = 5,
    include_overall: bool = True,
) -> StratifiedResult:
    """Repeat the whole paired comparison inside each level of ``column``.

    Strata with fewer than ``min_clusters`` worlds are skipped with a recorded
    reason rather than analysed into a meaningless interval.
    """
    mapping = world_stratum_map(frame, column)
    work = frame.merge(mapping, on="world_id", how="left")
    work["stratum_value"] = work["stratum_value"].astype("string").fillna(MISSING_LABEL)

    result = StratifiedResult(column=column, overall=None)
    result.allocation = allocation_table(frame, column)
    result.balance = allocation_balance(frame, column)

    if result.balance.get("checked") and result.balance["max_share_gap"] > 0.10:
        result.notes.append(
            f"ALLOCATION IMBALANCE on `{column}`: policies differ by up to "
            f"{result.balance['max_share_gap']:.0%} in their share of worlds per stratum "
            f"(worst: {result.balance['worst_stratum']}). An unpaired comparison would "
            "confound policy with stratum difficulty; the paired analysis is restricted to "
            "shared worlds, and the per-stratum results below are the check that matters."
        )

    if include_overall:
        result.overall = compare_policies(work, config, source=source, validate=False)

    for value, group in work.groupby("stratum_value", dropna=False, observed=True):
        label = str(value)
        n_worlds = int(group["world_id"].nunique())
        n_policies = int(group["policy_id"].nunique())
        if n_policies < 2:
            result.skipped[label] = f"only {n_policies} policy present in this stratum"
            continue
        if n_worlds < min_clusters:
            result.skipped[label] = (
                f"only {n_worlds} world(s) (< min_clusters={min_clusters}); an interval here "
                "would be uninterpretable"
            )
            continue
        try:
            result.per_stratum[label] = compare_policies(
                group.reset_index(drop=True), config, source=source, validate=False,
                include_failures=False,
            )
        except (ValueError, KeyError) as exc:
            result.skipped[label] = str(exc)

    result.heterogeneity = _heterogeneity(result)
    for item in result.heterogeneity:
        result.notes.append(item["message"])
    return result


def _heterogeneity(result: StratifiedResult) -> list[dict[str, Any]]:
    """Flag metrics whose resolved direction flips between strata, or against the pool."""
    out: list[dict[str, Any]] = []
    by_metric: dict[tuple[str, str, str], dict[str, str]] = {}
    for label, res in result.per_stratum.items():
        for c in res.comparisons:
            key = (c.metric.name, c.baseline, c.candidate)
            if c.verdict.favours:
                by_metric.setdefault(key, {})[label] = c.verdict.favours

    overall_favours: dict[tuple[str, str, str], str | None] = {}
    if result.overall is not None:
        for c in result.overall.comparisons:
            overall_favours[(c.metric.name, c.baseline, c.candidate)] = c.verdict.favours

    for key, per_stratum in by_metric.items():
        metric, baseline, candidate = key
        directions = set(per_stratum.values())
        if len(directions) > 1:
            out.append(
                {
                    "kind": "sign_flip_between_strata",
                    "metric": metric,
                    "baseline": baseline,
                    "candidate": candidate,
                    "by_stratum": per_stratum,
                    "message": (
                        f"EFFECT REVERSES ACROSS STRATA for {metric} ({candidate} vs "
                        f"{baseline}): " + ", ".join(f"{k} favours {v}" for k, v in sorted(per_stratum.items()))
                        + ". A pooled number averages over a reversal and describes no stratum."
                    ),
                }
            )
            continue
        only = next(iter(directions))
        pooled = overall_favours.get(key)
        if pooled and pooled != only:
            out.append(
                {
                    "kind": "pooled_contradicts_strata",
                    "metric": metric,
                    "baseline": baseline,
                    "candidate": candidate,
                    "pooled_favours": pooled,
                    "stratum_favours": only,
                    "message": (
                        f"SIMPSON-STYLE REVERSAL for {metric}: the pooled comparison favours "
                        f"{pooled} while every resolved stratum favours {only}. Trust the "
                        "strata and check the allocation table."
                    ),
                }
            )
    return out


def stratified_estimate(
    result: StratifiedResult, metric: str, candidate: str, *, weights: dict[str, float] | None = None
) -> dict[str, Any]:
    """Stratum-weighted mean of the per-stratum differences.

    Defaults to weighting each stratum by its number of shared clusters, which
    recovers the pooled estimate when allocation is balanced and corrects it
    when it is not.
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
                "n_clusters": comparison.difference.n_clusters,
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
        table["weight"] = table["n_clusters"].astype("float64")
    total = float(table["weight"].sum())
    if total <= 0:
        return {"available": False, "reason": "weights sum to zero"}
    estimate = float((table["difference"] * table["weight"]).sum() / total)
    return {
        "available": True,
        "metric": metric,
        "candidate": candidate,
        "weighted_difference": estimate,
        "weighting": "supplied" if weights else "shared clusters per stratum",
        "per_stratum": table.assign(
            weight_share=lambda d: d["weight"] / total
        ).to_dict(orient="records"),
        "note": (
            "A stratum-weighted difference re-balances an unequal allocation. It is not a "
            "substitute for reporting the per-stratum results, which may disagree in sign."
        ),
    }

