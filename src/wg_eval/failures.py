"""Failure-reason analysis.

A success rate is a single number that hides the mechanism.  Two policies with
identical success rates can fail for entirely different reasons, and a policy
that improves the overall rate while multiplying its worst failure mode has not
improved.  So failures are always broken down, and always paired by cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

UNSPECIFIED = "unspecified"


@dataclass
class FailureSummary:
    """Per-policy failure composition and its cluster-level spread."""

    column: str
    policies: tuple[str, ...]
    #: One row per (policy, reason) with counts and shares.
    table: pd.DataFrame
    #: Per-policy totals.
    totals: pd.DataFrame
    #: Paired per-cluster shift in each reason's rate, candidate - baseline.
    shifts: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "policies": list(self.policies),
            "table": _records(self.table),
            "totals": _records(self.totals),
            "shifts": _records(self.shifts) if self.shifts is not None else None,
            "notes": list(self.notes),
        }

    def to_text(self) -> str:
        lines = [f"failure analysis on `{self.column}`"]
        for _, row in self.totals.iterrows():
            lines.append(
                f"  {row['policy_id']}: {int(row['n_failures'])} failures / "
                f"{int(row['n_observations'])} observations "
                f"({row['failure_rate']:.1%})"
            )
        lines.append("")
        lines.append(f"  {'policy'.ljust(14)}{'reason'.ljust(28)}{'count':>8}{'share':>9}{'rate':>9}")
        for _, row in self.table.iterrows():
            lines.append(
                f"  {str(row['policy_id']).ljust(14)}{str(row['failure_reason']).ljust(28)}"
                f"{int(row['count']):>8}{row['share_of_failures']:>9.1%}"
                f"{row['rate_of_observations']:>9.2%}"
            )
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return json_safe(frame).to_dict(orient="records")


def json_safe(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].astype("float64").replace([np.inf, -np.inf], np.nan)
        else:
            out[col] = out[col].astype("string")
    return out


def summarize_failures(
    frame: pd.DataFrame,
    *,
    column: str = "failure_reason",
    success_column: str = "mission_success",
    policies: tuple[str, ...] = (),
    cluster_column: str = "world_id",
    baseline: str | None = None,
    candidate: str | None = None,
) -> FailureSummary:
    """Break failures down by reason, per policy, with a paired per-cluster shift.

    Failures with no recorded reason are bucketed as ``"unspecified"`` rather
    than dropped: an unexplained failure is still a failure, and dropping it
    would flatter whichever policy records its reasons least diligently.
    """
    if column not in frame.columns:
        raise KeyError(f"failure column {column!r} is not present in the records")

    work = frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    if policies:
        work = work[work["policy_id"].isin(pd.Series(list(policies), dtype="string"))]
    if work.empty:
        raise ValueError("no rows remain for failure analysis")

    if success_column in work.columns:
        success = pd.to_numeric(work[success_column], errors="coerce")
        failed = success == 0
    else:
        failed = work[column].notna()

    reasons = work[column].astype("string")
    reasons = reasons.where(reasons.notna() & (reasons.str.len() > 0), UNSPECIFIED)
    work = work.assign(__failed__=failed.fillna(False), __reason__=reasons)

    totals = (
        work.groupby("policy_id", dropna=False, observed=True)
        .agg(n_observations=("__failed__", "size"), n_failures=("__failed__", "sum"))
        .reset_index()
    )
    totals["n_failures"] = totals["n_failures"].astype("int64")
    totals["failure_rate"] = totals["n_failures"] / totals["n_observations"]

    failures = work[work["__failed__"]]
    notes: list[str] = []
    if failures.empty:
        table = pd.DataFrame(
            columns=["policy_id", "failure_reason", "count", "share_of_failures", "rate_of_observations"]
        )
        notes.append("no failed observations in the selected rows")
    else:
        table = (
            failures.groupby(["policy_id", "__reason__"], dropna=False, observed=True)
            .size()
            .rename("count")
            .reset_index()
            .rename(columns={"__reason__": "failure_reason"})
        )
        totals_by_policy = table.groupby("policy_id", observed=True)["count"].transform("sum")
        table["share_of_failures"] = table["count"] / totals_by_policy
        obs = totals.set_index("policy_id")["n_observations"]
        table["rate_of_observations"] = table.apply(
            lambda r: r["count"] / float(obs.get(r["policy_id"], np.nan)), axis=1
        )
        table = table.sort_values(["policy_id", "count"], ascending=[True, False]).reset_index(drop=True)

    if UNSPECIFIED in set(table.get("failure_reason", pd.Series(dtype="string")).tolist()):
        notes.append(
            "some failures carry no reason and are bucketed as 'unspecified'; "
            "they are counted, not dropped"
        )

    shifts = None
    if baseline and candidate and cluster_column in work.columns:
        shifts = _paired_reason_shift(work, cluster_column, baseline, candidate)
        if shifts is not None and not shifts.empty:
            worsened = shifts[shifts["mean_paired_shift"] > 0]
            if len(worsened):
                top = worsened.sort_values("mean_paired_shift", ascending=False).iloc[0]
                notes.append(
                    f"{candidate} raises the per-{cluster_column} rate of "
                    f"'{top['failure_reason']}' by {top['mean_paired_shift']:+.2%} on average "
                    "relative to " + baseline + "; an improved overall rate can still hide "
                    "a worsened failure mode."
                )

    return FailureSummary(
        column=column,
        policies=tuple(sorted(work["policy_id"].dropna().unique().tolist())),
        table=table,
        totals=totals,
        shifts=shifts,
        notes=notes,
    )


def _paired_reason_shift(
    work: pd.DataFrame, cluster_column: str, baseline: str, candidate: str
) -> pd.DataFrame | None:
    """Mean within-cluster change in each reason's rate, candidate - baseline."""
    sub = work[work["policy_id"].isin(pd.Series([baseline, candidate], dtype="string"))]
    if sub.empty:
        return None
    # The numerator counts failures of each reason; the denominator counts every
    # observation in the cluster. Successes are not a failure mode and must not
    # appear as one, but they do belong in the denominator -- the quantity of
    # interest is "how often does this failure mode happen", not "what share of
    # failures is it".
    failed = sub[sub["__failed__"]]
    if failed.empty:
        return None
    rates = (
        failed.assign(__one__=1.0)
        .groupby([cluster_column, "policy_id", "__reason__"], dropna=False, observed=True)["__one__"]
        .sum()
        .reset_index(name="n_reason")
    )
    denom = (
        sub.groupby([cluster_column, "policy_id"], dropna=False, observed=True)
        .size()
        .reset_index(name="n_total")
    )
    merged = rates.merge(denom, on=[cluster_column, "policy_id"], how="left")
    merged["rate"] = merged["n_reason"] / merged["n_total"]

    reasons = sorted(set(failed["__reason__"].dropna().tolist()))
    clusters = sorted(set(merged[cluster_column].dropna().astype(str).tolist()))
    grid = pd.MultiIndex.from_product(
        [clusters, [baseline, candidate], reasons],
        names=[cluster_column, "policy_id", "__reason__"],
    ).to_frame(index=False)
    grid[cluster_column] = grid[cluster_column].astype("string")
    merged[cluster_column] = merged[cluster_column].astype("string")
    grid["policy_id"] = grid["policy_id"].astype("string")
    merged["__reason__"] = merged["__reason__"].astype("string")
    grid["__reason__"] = grid["__reason__"].astype("string")
    full = grid.merge(
        merged[[cluster_column, "policy_id", "__reason__", "rate"]],
        on=[cluster_column, "policy_id", "__reason__"],
        how="left",
    )
    full["rate"] = full["rate"].fillna(0.0)

    wide = full.pivot_table(
        index=[cluster_column, "__reason__"], columns="policy_id", values="rate", aggfunc="sum"
    ).reset_index()
    if baseline not in wide.columns or candidate not in wide.columns:
        return None
    # Only clusters where both policies actually ran.
    both = (
        sub.groupby(cluster_column, observed=True)["policy_id"].nunique().rename("n_pol").reset_index()
    )
    both[cluster_column] = both[cluster_column].astype("string")
    wide = wide.merge(both, on=cluster_column, how="left")
    wide = wide[wide["n_pol"] == 2]
    if wide.empty:
        return None
    wide["shift"] = wide[candidate] - wide[baseline]
    out = (
        wide.groupby("__reason__", observed=True)
        .agg(
            mean_paired_shift=("shift", "mean"),
            n_clusters=("shift", "size"),
            baseline_rate=(baseline, "mean"),
            candidate_rate=(candidate, "mean"),
        )
        .reset_index()
        .rename(columns={"__reason__": "failure_reason"})
    )
    return out.sort_values("mean_paired_shift", ascending=False).reset_index(drop=True)
