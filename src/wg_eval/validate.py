"""Schema and structural validation of experiment records.

The validator's job is not to check that data is *pretty*.  It is to refuse
data whose structure would make a downstream conclusion misleading: a declared
inference unit that the records contradict, broken nesting, duplicate keys,
single-unit designs, impossible values, redundant strata, or policy coverage so
unbalanced that a paired comparison is impossible.

Severity follows one rule: **if the mistake makes the output look better than
the truth warrants, refuse; if it makes it noisier, warn.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

import numpy as np
import pandas as pd

from wg_eval.aggregate import KNOWN_RUN_STATUSES
from wg_eval.config import AnalysisConfig, Bounds, MetricSpec
from wg_eval.hierarchy import InferenceSpec, check_structure, infer_default
from wg_eval.schema import (
    ID_COLUMNS,
    NON_STRATUM_COLUMNS,
    REQUIRED_COLUMNS,
    SCHEMA,
    SCHEMA_BY_NAME,
    WEIGHT_LIKE_COLUMNS,
)

Severity = Literal["error", "warning", "info"]

#: Below this many units, a cluster bootstrap interval is not trustworthy.
MIN_UNITS_FOR_BOOTSTRAP = 20


@dataclass(frozen=True)
class ValidationIssue:
    """One finding about a record table."""

    code: str
    severity: Severity
    message: str
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
    inference: InferenceSpec | None = None,
    metrics: Sequence[MetricSpec] = (),
    status_column: str = "run_status",
    unit_of_inference: str | None = None,
) -> ValidationReport:
    """Validate a record table against the schema and the declared structure.

    Parameters
    ----------
    frame:
        Records, one row per observation.
    strata:
        Extra columns the analysis declares as strata.
    inference:
        The declared inference structure.  When omitted the default
        (``world_id > event_id > resident_id``, restricted to present columns)
        is assumed *and still checked*: a coarser undeclared grouping is an
        error either way.
    metrics:
        Declared metrics, so value bounds and orientation can be checked.
    unit_of_inference:
        Legacy alias for a primary unit, accepted for older callers.
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
        return report

    if len(frame) == 0:
        report.add("empty_table", "error", "the record table has no rows")
        report.summary["n_rows"] = 0
        return report

    if inference is None:
        inference = infer_default(frame)
        if unit_of_inference:
            from wg_eval.hierarchy import resolve_unit

            primary = resolve_unit(unit_of_inference)
            nested = tuple(u for u in inference.levels if u != primary)
            inference = InferenceSpec(primary_unit=primary, nested_units=nested)

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

    # --- weights are refused, not silently applied -----------------------
    weightish = [str(c) for c in frame.columns if str(c) in WEIGHT_LIKE_COLUMNS]
    if weightish:
        report.add(
            "weight_column_ignored",
            "warning",
            f"column(s) {weightish} look like sampling weights and are IGNORED. A sampling "
            "weight across units and a scenario probability inside one unit are different "
            "quantities; this library applies neither. See docs/ESTIMANDS.md.",
            columns=weightish,
        )

    # --- null keys -------------------------------------------------------
    for col in ID_COLUMNS:
        n_null = int(frame[col].isna().sum())
        if n_null:
            report.add(
                "null_key", "error", f"{n_null} row(s) have a null {col}",
                column=col, n_null=n_null,
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

    # --- declared inference structure -------------------------------------
    for finding in check_structure(frame, inference):
        report.add(
            str(finding["code"]),
            "error" if finding["severity"] == "error" else "warning",
            str(finding["message"]),
            **dict(finding.get("detail", {})),  # type: ignore[arg-type]
        )

    # --- classic broken nesting (event under several worlds) --------------
    if {"world_id", "event_id"} <= set(frame.columns) and inference.levels[:2] == (
        "world_id",
        "event_id",
    ):
        worlds_per_event = frame.groupby("event_id", dropna=False)["world_id"].nunique()
        broken = worlds_per_event[worlds_per_event > 1]
        if len(broken) and not any(i.code == "inverted_nesting" for i in report.issues):
            report.add(
                "broken_nesting",
                "error",
                f"{len(broken)} event_id value(s) appear under more than one world_id; "
                "event identifiers must be unique across worlds, or the hierarchy must be "
                "declared the other way round (inference.primary_unit: event_id)",
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
                    column=str(name), dtype=str(col.dtype),
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
                        column=str(name), examples=_truncate(sorted(set(bad.tolist()))),
                    )
            lo, hi = spec.plausible_range
            finite = values.replace([np.inf, -np.inf], np.nan).dropna()
            if lo is not None and len(finite) and float(finite.min()) < lo:
                report.add(
                    "implausible_value", "warning",
                    f"column {name!r} has value(s) below the plausible minimum {lo}",
                    column=str(name), minimum=float(finite.min()),
                )
            if hi is not None and len(finite) and float(finite.max()) > hi:
                report.add(
                    "implausible_value", "warning",
                    f"column {name!r} has value(s) above the plausible maximum {hi}",
                    column=str(name), maximum=float(finite.max()),
                )
            n_nonfinite = int(np.isinf(values.to_numpy(dtype="float64", na_value=np.nan)).sum())
            if n_nonfinite:
                report.add(
                    "non_finite_value", "error",
                    f"column {name!r} contains {n_nonfinite} non-finite value(s)",
                    column=str(name),
                )
            n_missing = int(values.isna().sum())
            if n_missing:
                report.add(
                    "missing_outcome", "warning",
                    f"column {name!r} has {n_missing} missing value(s) "
                    f"({n_missing / len(frame):.1%} of rows); the configured missing-data "
                    "policy and run-status handling decide their fate",
                    column=str(name), n_missing=n_missing,
                )

    # --- declared metric bounds -------------------------------------------
    for metric in metrics:
        _check_bounds(report, frame, metric)
        for note in metric.orientation_warnings():
            report.add("metric_orientation", "warning", note, metric=metric.name)

    # --- run status --------------------------------------------------------
    if status_column in frame.columns:
        statuses = frame[status_column].astype("string").fillna("unspecified")
        counts = statuses.value_counts()
        unknown = sorted(set(counts.index) - set(KNOWN_RUN_STATUSES) - {"unspecified"})
        report.add(
            "run_status_present", "info",
            f"`{status_column}` present: "
            + ", ".join(f"{k}={int(v)}" for k, v in counts.items()),
            counts={str(k): int(v) for k, v in counts.items()},
        )
        if unknown:
            report.add(
                "unknown_run_status", "warning",
                f"run status value(s) {unknown} are outside the known vocabulary "
                f"{list(KNOWN_RUN_STATUSES)}; declare their handling in "
                "missing_data.status_handling or they default to 'missing'",
                statuses=unknown,
            )
        n_incomplete = int((statuses != "completed").sum())
        if n_incomplete:
            by_policy = (
                frame.loc[statuses != "completed"]
                .groupby("policy_id", dropna=False)
                .size()
                .to_dict()
            )
            report.add(
                "incomplete_runs", "warning",
                f"{n_incomplete} row(s) record a run that did not complete. A failed run is "
                "not automatically a missing value -- it may itself be an outcome. Declare "
                "missing_data.status_handling so it does not leave the denominator silently.",
                n_rows=n_incomplete,
                by_policy={str(k): int(v) for k, v in by_policy.items()},
            )

    # --- failure_reason consistency ----------------------------------------
    if "failure_reason" in frame.columns and "mission_success" in frame.columns:
        success = pd.to_numeric(frame["mission_success"], errors="coerce")
        reason_present = frame["failure_reason"].notna() & (
            frame["failure_reason"].astype("string").str.len().fillna(0) > 0
        )
        n_fail_no_reason = int(((success == 0) & ~reason_present).sum())
        n_success_with_reason = int(((success == 1) & reason_present).sum())
        if n_fail_no_reason:
            report.add(
                "failure_without_reason", "warning",
                f"{n_fail_no_reason} failed observation(s) carry no failure_reason; failure "
                "analysis will bucket them as 'unspecified'",
                n_rows=n_fail_no_reason,
            )
        if n_success_with_reason:
            report.add(
                "success_with_failure_reason", "warning",
                f"{n_success_with_reason} successful observation(s) carry a failure_reason",
                n_rows=n_success_with_reason,
            )

    # --- strata -------------------------------------------------------------
    # The generic `stratum` column is a convenience label. It is checked only
    # when nothing else was declared: adding it alongside explicit strata would
    # flag it as redundant with them, which is true and useless.
    declared_strata = list(dict.fromkeys(strata))
    if not declared_strata and "stratum" in frame.columns:
        declared_strata = ["stratum"]
    primary = inference.primary_unit
    usable_strata: list[str] = []
    for col in declared_strata:
        if col in NON_STRATUM_COLUMNS:
            report.add(
                "outcome_used_as_stratum", "error",
                f"{col!r} is an outcome or an identifier and may not be used as a stratum. "
                "Subsetting on something the policy influenced selects on the result and "
                "makes the comparison uninterpretable.",
                column=col,
            )
            continue
        if col not in frame.columns:
            report.add(
                "missing_stratum_column", "error",
                f"declared stratum column {col!r} is not present in the records", column=col,
            )
            continue
        usable_strata.append(col)
        per_unit = frame.groupby(primary, dropna=False)[col].nunique(dropna=False)
        varying = per_unit[per_unit > 1]
        if len(varying):
            report.add(
                "stratum_varies_within_unit", "warning",
                f"stratum column {col!r} is not constant within {len(varying)} {primary}(s); "
                "a unit will be assigned to its modal stratum, which blurs the comparison",
                column=col, units=_truncate(varying.index.astype(str).tolist()),
            )
        if frame[col].isna().any():
            report.add(
                "null_stratum", "warning",
                f"stratum column {col!r} has null values; those rows form an explicit "
                "'__missing__' stratum rather than being silently dropped",
                column=col, n_null=int(frame[col].isna().sum()),
            )

    if len(usable_strata) >= 1:
        from wg_eval.stratify import stratum_redundancy

        for finding in stratum_redundancy(frame, usable_strata, unit=primary):
            report.add(
                str(finding["code"]), "warning", str(finding["message"]),
                **dict(finding.get("detail", {})),  # type: ignore[arg-type]
            )

    # --- design structure ----------------------------------------------------
    n_units = int(frame[primary].nunique())
    n_rows = int(len(frame))
    policies = sorted(str(p) for p in frame["policy_id"].dropna().unique())

    if 2 <= n_units < MIN_UNITS_FOR_BOOTSTRAP:
        report.add(
            "few_units", "warning",
            f"only {n_units} {primary}(s) present; cluster-bootstrap intervals are unreliable "
            f"below ~{MIN_UNITS_FOR_BOOTSTRAP} units. Report them with the unit count attached.",
            n_units=n_units, unit=primary,
        )

    if len(policies) < 2:
        report.add(
            "single_policy", "warning",
            f"only {len(policies)} policy present; no comparison is possible", policies=policies,
        )

    ratio = float(n_rows) / max(n_units, 1)
    report.add(
        "nesting_ratio", "info",
        f"{n_rows} observations nested in {n_units} {primary}s ({ratio:.1f} per unit). Treating "
        "observations as independent would overstate the sample size by up to this factor.",
        observations_per_unit=round(ratio, 2), unit=primary,
    )

    # --- policy coverage / balance --------------------------------------------
    coverage = frame.groupby("policy_id", dropna=False)[primary].nunique()
    common = _common_units(frame, policies, primary)
    if len(policies) >= 2:
        incomplete = {str(p): int(n_units - coverage.get(p, 0)) for p in policies}
        missing_any = {k: v for k, v in incomplete.items() if v > 0}
        if missing_any:
            report.add(
                "unbalanced_policy_coverage", "warning",
                f"policies are not evaluated on the same set of {primary}s: {len(common)} of "
                f"{n_units} carry every policy. Unpaired comparison over differing unit sets "
                "can reverse a ranking; the paired analysis restricts to the common set, which "
                "conditions the estimand on being observed under every policy.",
                units_missing_per_policy=missing_any,
                n_common_units=len(common), n_units=n_units,
            )
        if len(common) == 0:
            report.add(
                "no_common_units", "error",
                f"no {primary} carries every policy; a paired comparison is impossible",
                policies=policies,
            )

    report.summary = {
        "n_rows": n_rows,
        "primary_unit": primary,
        "hierarchy": " > ".join(inference.levels),
        "n_units": n_units,
        "n_policies": len(policies),
        "policies": policies,
        "n_common_units": len(common),
        "observations_per_unit": round(ratio, 2),
    }
    return report


def _check_bounds(report: ValidationReport, frame: pd.DataFrame, metric: MetricSpec) -> None:
    """Values outside a metric's declared support are impossible, not unusual."""
    bounds: Bounds = metric.bounds
    if not bounds.declared or metric.column not in frame.columns:
        return
    values = pd.to_numeric(frame[metric.column], errors="coerce")
    finite = values.replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return
    if bounds.lower is not None and float(finite.min()) < bounds.lower:
        report.add(
            "value_outside_declared_bounds", "error",
            f"metric {metric.name!r} declares a lower bound of {bounds.lower} for column "
            f"`{metric.column}`, but the records contain {float(finite.min()):.6g}. Either the "
            "bound or the data is wrong; both cannot be right.",
            metric=metric.name, column=metric.column, minimum=float(finite.min()),
        )
    if bounds.upper is not None and float(finite.max()) > bounds.upper:
        report.add(
            "value_outside_declared_bounds", "error",
            f"metric {metric.name!r} declares an upper bound of {bounds.upper} for column "
            f"`{metric.column}`, but the records contain {float(finite.max()):.6g}.",
            metric=metric.name, column=metric.column, maximum=float(finite.max()),
        )


def validate_for_config(frame: pd.DataFrame, config: AnalysisConfig) -> ValidationReport:
    """Validate against everything a particular analysis configuration declares."""
    report = validate_records(
        frame,
        strata=config.strata,
        inference=config.inference,
        metrics=config.metrics,
        status_column=config.missing_data.status_column,
    )
    if config.primary_metric is None:
        report.add(
            "no_primary_metric", "warning",
            "no metric is declared primary. Without a declared primary outcome the headline "
            "finding is whichever metric the reader notices first.",
        )
    for metric in config.metrics:
        margin = config.margin_for(metric.name)
        if margin is not None and not margin.documented:
            report.add(
                "undocumented_margin", "warning",
                f"the equivalence margin for {metric.name!r} records no source. A margin "
                "justified after the interval is known is not a predeclared margin.",
                metric=metric.name,
            )
    if config.protocol.analysis_status == "unspecified":
        report.add(
            "analysis_status_unspecified", "info",
            "analysis_status is unspecified: this analysis is not asserted to be "
            "preregistered, and the existence of a config file is not evidence that it was.",
        )
    return report


def _common_units(frame: pd.DataFrame, policies: list[str], unit: str) -> list[str]:
    """Units on which every policy in ``policies`` has at least one record."""
    if not policies:
        return []
    sets = [
        set(frame.loc[frame["policy_id"].astype("string") == p, unit].astype("string"))
        for p in policies
    ]
    common = set.intersection(*sets) if sets else set()
    return sorted(common)
