"""Schema and structural validation of experiment records.

The validator's job is not to check that data is *pretty*.  It is to refuse
data whose structure would make a downstream conclusion misleading: broken
nesting, duplicate keys, single-cluster designs, or policy coverage so
unbalanced that a paired comparison is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from wg_eval.schema import (
    ID_COLUMNS,
    NON_STRATUM_COLUMNS,
    REQUIRED_COLUMNS,
    SCHEMA,
    SCHEMA_BY_NAME,
)

Severity = Literal["error", "warning", "info"]

#: Below this many clusters, a cluster bootstrap interval is not trustworthy.
MIN_CLUSTERS_FOR_BOOTSTRAP = 20


@dataclass(frozen=True)
class ValidationIssue:
    """One finding about a record table."""

    code: str
    severity: Severity
    message: str
    #: Small, JSON-serialisable evidence: counts, offending keys (truncated).
    detail: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.severity.upper()}] {self.code}: {self.message}"

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class ValidationReport:
    """Result of validating a record table."""

    issues: list[ValidationIssue] = field(default_factory=list)
    summary: dict[str, object] = field(default_factory=dict)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def ok(self) -> bool:
        """True when nothing blocks analysis (warnings do not block)."""
        return not self.errors

    def add(self, code: str, severity: Severity, message: str, **detail: object) -> None:
        self.issues.append(ValidationIssue(code, severity, message, dict(detail)))

    def raise_if_errors(self) -> None:
        if self.errors:
            joined = "\n  ".join(str(i) for i in self.errors)
            raise ValueError(f"record validation failed:\n  {joined}")

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "summary": self.summary,
            "issues": [i.as_dict() for i in self.issues],
        }

    def to_text(self) -> str:
        lines = [
            f"validation: {'PASS' if self.ok else 'FAIL'} "
            f"({len(self.errors)} errors, {len(self.warnings)} warnings)"
        ]
        for key, value in self.summary.items():
            lines.append(f"  {key}: {value}")
        if self.issues:
            lines.append("")
        for issue in self.issues:
            lines.append(f"  {issue}")
            for key, value in issue.detail.items():
                lines.append(f"      {key}: {value}")
        return "\n".join(lines)


def _truncate(values: Iterable[object], limit: int = 5) -> list[object]:
    items = list(values)
    head = items[:limit]
    if len(items) > limit:
        head.append(f"... (+{len(items) - limit} more)")
    return head


def validate_records(
    frame: pd.DataFrame,
    *,
    strata: Iterable[str] = (),
    unit_of_inference: str = "world",
) -> ValidationReport:
    """Validate a record table against the generic schema and nesting rules.

    Parameters
    ----------
    frame:
        Records, one row per observation.
    strata:
        Extra columns that the analysis config declares as strata.  They must
        exist and be constant within a world.
    unit_of_inference:
        ``"world"`` or ``"event"``; controls the cluster-count warning.
    """
    report = ValidationReport()
    strata = tuple(strata)

    # --- presence of required columns -----------------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        report.add(
            "missing_required_columns",
            "error",
            f"required columns are absent: {', '.join(missing)}",
            missing=missing,
        )
        report.summary["n_rows"] = int(len(frame))
        return report  # nothing further is meaningful

    if len(frame) == 0:
        report.add("empty_table", "error", "the record table has no rows")
        report.summary["n_rows"] = 0
        return report

    known = {c.name for c in SCHEMA}
    extras = [str(c) for c in frame.columns if str(c) not in known]
    if extras:
        report.add(
            "extra_columns",
            "info",
            f"{len(extras)} column(s) outside the core schema are carried through",
            columns=_truncate(extras, 12),
        )

    absent_optional = [c.name for c in SCHEMA if not c.required and c.name not in frame.columns]
    if absent_optional:
        report.add(
            "absent_optional_columns",
            "info",
            "optional schema columns not present in this table",
            columns=absent_optional,
        )

    # --- null keys -------------------------------------------------------
    for col in ID_COLUMNS:
        n_null = int(frame[col].isna().sum())
        if n_null:
            report.add(
                "null_key",
                "error",
                f"{n_null} row(s) have a null {col}",
                column=col,
                n_null=n_null,
            )

    # --- duplicate observation keys --------------------------------------
    key = list(ID_COLUMNS)
    dup_mask = frame.duplicated(subset=key, keep=False)
    n_dup = int(dup_mask.sum())
    if n_dup:
        offending = frame.loc[dup_mask, key].drop_duplicates()
        report.add(
            "duplicate_records",
            "error",
            f"{n_dup} row(s) share a (world_id, event_id, policy_id, resident_id) key; "
            "duplicated observations inflate apparent sample size",
            n_rows=n_dup,
            examples=_truncate(offending.astype(str).agg("/".join, axis=1).tolist()),
        )

    # --- nesting: an event belongs to exactly one world -------------------
    worlds_per_event = frame.groupby("event_id", dropna=False)["world_id"].nunique()
    broken = worlds_per_event[worlds_per_event > 1]
    if len(broken):
        report.add(
            "broken_nesting",
            "error",
            f"{len(broken)} event_id value(s) appear under more than one world_id; "
            "event identifiers must be unique across worlds or nesting is undefined",
            events=_truncate(broken.index.astype(str).tolist()),
        )

    # --- dtype / domain checks -------------------------------------------
    for name in frame.columns:
        spec = SCHEMA_BY_NAME.get(str(name))
        if spec is None:
            continue
        col = frame[name]
        if spec.kind in ("numeric", "boolean"):
            if not pd.api.types.is_numeric_dtype(col):
                report.add(
                    "non_numeric_column",
                    "error",
                    f"column {name!r} is declared {spec.kind} but could not be coerced to numbers",
                    column=str(name),
                    dtype=str(col.dtype),
                )
                continue
            values = pd.to_numeric(col, errors="coerce")
            if spec.kind == "boolean":
                bad = values.dropna()
                bad = bad[~bad.isin([0.0, 1.0])]
                if len(bad):
                    report.add(
                        "non_binary_outcome",
                        "error",
                        f"column {name!r} must contain only 0/1; found {len(bad)} other value(s)",
                        column=str(name),
                        examples=_truncate(sorted(set(bad.tolist()))),
                    )
            lo, hi = spec.plausible_range
            finite = values.replace([np.inf, -np.inf], np.nan).dropna()
            if lo is not None and len(finite) and float(finite.min()) < lo:
                report.add(
                    "implausible_value",
                    "warning",
                    f"column {name!r} has value(s) below the plausible minimum {lo}",
                    column=str(name),
                    minimum=float(finite.min()),
                )
            if hi is not None and len(finite) and float(finite.max()) > hi:
                report.add(
                    "implausible_value",
                    "warning",
                    f"column {name!r} has value(s) above the plausible maximum {hi}",
                    column=str(name),
                    maximum=float(finite.max()),
                )
            n_nonfinite = int(np.isinf(values.to_numpy(dtype="float64", na_value=np.nan)).sum())
            if n_nonfinite:
                report.add(
                    "non_finite_value",
                    "error",
                    f"column {name!r} contains {n_nonfinite} non-finite value(s)",
                    column=str(name),
                )
            n_missing = int(values.isna().sum())
            if n_missing:
                report.add(
                    "missing_outcome",
                    "warning",
                    f"column {name!r} has {n_missing} missing value(s) "
                    f"({n_missing / len(frame):.1%} of rows); the configured "
                    "missing-data policy will decide their fate",
                    column=str(name),
                    n_missing=n_missing,
                )

    # --- failure_reason consistency --------------------------------------
    if "failure_reason" in frame.columns and "mission_success" in frame.columns:
        success = pd.to_numeric(frame["mission_success"], errors="coerce")
        reason_present = frame["failure_reason"].notna() & (
            frame["failure_reason"].astype("string").str.len().fillna(0) > 0
        )
        n_fail_no_reason = int(((success == 0) & ~reason_present).sum())
        n_success_with_reason = int(((success == 1) & reason_present).sum())
        if n_fail_no_reason:
            report.add(
                "failure_without_reason",
                "warning",
                f"{n_fail_no_reason} failed observation(s) carry no failure_reason; "
                "failure analysis will bucket them as 'unspecified'",
                n_rows=n_fail_no_reason,
            )
        if n_success_with_reason:
            report.add(
                "success_with_failure_reason",
                "warning",
                f"{n_success_with_reason} successful observation(s) carry a failure_reason",
                n_rows=n_success_with_reason,
            )

    # --- strata ----------------------------------------------------------
    declared_strata = list(dict.fromkeys(list(strata) + (["stratum"] if "stratum" in frame.columns else [])))
    for col in declared_strata:
        if col in NON_STRATUM_COLUMNS:
            report.add(
                "outcome_used_as_stratum",
                "error",
                f"{col!r} is an outcome or an identifier and may not be used as a stratum. "
                "Subsetting on something the policy influenced selects on the result and "
                "makes the comparison uninterpretable.",
                column=col,
            )
            continue
        if col not in frame.columns:
            report.add(
                "missing_stratum_column",
                "error",
                f"declared stratum column {col!r} is not present in the records",
                column=col,
            )
            continue
        per_world = frame.groupby("world_id", dropna=False)[col].nunique(dropna=False)
        varying = per_world[per_world > 1]
        if len(varying):
            report.add(
                "stratum_varies_within_world",
                "warning",
                f"stratum column {col!r} is not constant within {len(varying)} world(s); "
                "a world will be assigned to its modal stratum, which blurs the comparison",
                column=col,
                worlds=_truncate(varying.index.astype(str).tolist()),
            )
        if frame[col].isna().any():
            report.add(
                "null_stratum",
                "warning",
                f"stratum column {col!r} has null values; those rows form an explicit "
                "'__missing__' stratum rather than being silently dropped",
                column=col,
                n_null=int(frame[col].isna().sum()),
            )

    # --- design structure -------------------------------------------------
    n_worlds = int(frame["world_id"].nunique())
    n_events = int(frame["event_id"].nunique())
    n_residents = int(len(frame))
    policies = sorted(str(p) for p in frame["policy_id"].dropna().unique())

    n_clusters = n_worlds if unit_of_inference == "world" else n_events
    if n_clusters < 2:
        report.add(
            "single_cluster",
            "error",
            f"only {n_clusters} {unit_of_inference}(s) present; with one cluster there is "
            "no between-cluster variation to estimate and no inference is possible",
            n_clusters=n_clusters,
            unit_of_inference=unit_of_inference,
        )
    elif n_clusters < MIN_CLUSTERS_FOR_BOOTSTRAP:
        report.add(
            "few_clusters",
            "warning",
            f"only {n_clusters} {unit_of_inference}(s) present; cluster-bootstrap "
            f"intervals are unreliable below ~{MIN_CLUSTERS_FOR_BOOTSTRAP} clusters. "
            "Report them with the cluster count attached.",
            n_clusters=n_clusters,
            unit_of_inference=unit_of_inference,
        )

    if len(policies) < 2:
        report.add(
            "single_policy",
            "warning",
            f"only {len(policies)} policy present; no comparison is possible",
            policies=policies,
        )

    # --- pseudoreplication exposure ---------------------------------------
    per_cluster = frame.groupby(["world_id", "policy_id"], dropna=False).size()
    if len(per_cluster):
        ratio = float(n_residents) / max(n_worlds, 1)
        report.add(
            "nesting_ratio",
            "info",
            f"{n_residents} observations nested in {n_worlds} worlds "
            f"({ratio:.1f} observations per world). Treating observations as "
            "independent would overstate the sample size by up to this factor.",
            observations_per_world=round(ratio, 2),
            max_observations_in_one_world_policy=int(per_cluster.max()),
        )

    # --- policy coverage / balance ----------------------------------------
    coverage = frame.groupby("policy_id", dropna=False)["world_id"].nunique()
    common = _common_worlds(frame, policies)
    if len(policies) >= 2:
        incomplete = {str(p): int(n_worlds - coverage.get(p, 0)) for p in policies}
        missing_any = {k: v for k, v in incomplete.items() if v > 0}
        if missing_any:
            report.add(
                "unbalanced_policy_coverage",
                "warning",
                f"policies are not evaluated on the same set of worlds: "
                f"{len(common)} of {n_worlds} world(s) carry every policy. "
                "Unpaired comparison over differing world sets can reverse a ranking; "
                "the paired analysis will restrict to the common set and record the exclusions.",
                worlds_missing_per_policy=missing_any,
                n_common_worlds=len(common),
                n_worlds=n_worlds,
            )
        if len(common) == 0:
            report.add(
                "no_common_worlds",
                "error",
                "no world carries every policy; a paired comparison is impossible",
                policies=policies,
            )

    report.summary = {
        "n_rows": n_residents,
        "n_worlds": n_worlds,
        "n_events": n_events,
        "n_policies": len(policies),
        "policies": policies,
        "n_common_worlds": len(common),
        "observations_per_world": round(float(n_residents) / max(n_worlds, 1), 2),
    }
    return report


def _common_worlds(frame: pd.DataFrame, policies: list[str]) -> list[str]:
    """Worlds on which every policy in ``policies`` has at least one record."""
    if not policies:
        return []
    sets = [
        set(frame.loc[frame["policy_id"].astype("string") == p, "world_id"].astype("string"))
        for p in policies
    ]
    common = set.intersection(*sets) if sets else set()
    return sorted(common)
