"""The comparison orchestrator: data in, defensible statements out.

This module is the only place that decides what a result *means*.  Everything
it can do wrong -- an unchecked inference structure, an unpaired comparison, a
failed run vanishing from a denominator, equivalence without a margin, a tail
metric quietly disagreeing with a mean, an uncorrected family of secondary
tests -- is either refused outright or surfaced as a note attached to the
result.

It never names an overall winner.  It reports, per metric, what the interval
supports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from wg_eval.aggregate import (
    PreparationLog,
    apply_filters,
    apply_missing_policy,
    apply_run_status,
    observation_counts,
    panel as build_panel,
    run_status_ledger,
)
from wg_eval.bootstrap import BootstrapResult, design_effect, paired_cluster_bootstrap
from wg_eval.config import AnalysisConfig, MetricSpec
from wg_eval.dataio import DataSource
from wg_eval.equivalence import (
    EquivalenceResult,
    MarginRequired,
    Verdict,
    classify_difference,
    non_inferiority,
    tost,
)
from wg_eval.failures import FailureSummary, summarize_failures
from wg_eval.hierarchy import require_valid_structure
from wg_eval.metrics import build_estimator
from wg_eval.multiplicity import FamilyResult, apply_to_family, summarize
from wg_eval.pairing import PairedPanel, build_paired_panel, imbalance_report
from wg_eval.provenance import Provenance, provenance_for
from wg_eval.validate import ValidationReport, validate_for_config

#: A standardized shift this large between shared and excluded units means the
#: complete-case population is not a stand-in for the target population.
OVERLAP_SHIFT_THRESHOLD = 0.2


@dataclass
class MetricComparison:
    """One metric, one (baseline, candidate) pair, one statement."""

    metric: MetricSpec
    baseline: str
    candidate: str
    baseline_result: BootstrapResult
    candidate_result: BootstrapResult
    difference: BootstrapResult
    verdict: Verdict
    equivalence: EquivalenceResult | None = None
    non_inferiority_result: EquivalenceResult | None = None
    panel_summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None
    notes: list[str] = field(default_factory=list)
    #: Filled in after multiplicity correction over the declared family.
    adjusted: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        return self.metric.role

    @property
    def favours(self) -> str | None:
        return self.verdict.favours

    @property
    def key(self) -> str:
        return f"{self.metric.name}|{self.candidate}_vs_{self.baseline}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.as_dict(),
            "role": self.role,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "baseline_result": self.baseline_result.as_dict(),
            "candidate_result": self.candidate_result.as_dict(),
            "difference": self.difference.as_dict(),
            "verdict": self.verdict.as_dict(),
            "equivalence": self.equivalence.as_dict() if self.equivalence else None,
            "non_inferiority": (
                self.non_inferiority_result.as_dict() if self.non_inferiority_result else None
            ),
            "panel": self.panel_summary,
            "diagnostics": self.diagnostics,
            "multiplicity": self.adjusted,
            "provenance": self.provenance.as_dict() if self.provenance else None,
            "notes": list(self.notes),
        }


@dataclass
class ComparisonResult:
    """Every metric comparison from one analysis, with its shared context."""

    config: AnalysisConfig
    comparisons: list[MetricComparison] = field(default_factory=list)
    validation: ValidationReport | None = None
    source: DataSource | None = None
    preparation: PreparationLog | None = None
    failures: FailureSummary | None = None
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    families: list[FamilyResult] = field(default_factory=list)
    run_status: dict[str, Any] = field(default_factory=dict)
    structure_warnings: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.role == "primary"]

    def by_role(self, role: str) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.role == role]

    def for_metric(self, name: str) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.metric.name == name]

    def get(self, metric: str, candidate: str, baseline: str | None = None) -> MetricComparison:
        for c in self.comparisons:
            if c.metric.name == metric and c.candidate == candidate:
                if baseline is None or c.baseline == baseline:
                    return c
        raise KeyError(f"no comparison for metric={metric!r} candidate={candidate!r}")

    @property
    def ordered_comparisons(self) -> list[MetricComparison]:
        """Primary first, then secondary, then exploratory."""
        order = {"primary": 0, "secondary": 1, "exploratory": 2}
        return sorted(
            self.comparisons,
            key=lambda c: (order.get(c.role, 3), self.comparisons.index(c)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "source": self.source.as_dict() if self.source else None,
            "validation": self.validation.as_dict() if self.validation else None,
            "preparation": self.preparation.as_dict() if self.preparation else None,
            "run_status": self.run_status,
            "structure_warnings": self.structure_warnings,
            "comparisons": [c.as_dict() for c in self.ordered_comparisons],
            "multiplicity": summarize(self.families),
            "failures": self.failures.as_dict() if self.failures else None,
            "disagreements": self.disagreements,
            "notes": list(self.notes),
        }

    def summary_table(self) -> pd.DataFrame:
        """One row per comparison: estimate, interval, statement."""
        rows = []
        for c in self.ordered_comparisons:
            margin = c.equivalence.margin if c.equivalence else None
            rows.append(
                {
                    "role": c.role,
                    "metric": c.metric.name,
                    "direction": c.metric.direction,
                    "level": c.metric.level,
                    "baseline": c.baseline,
                    "candidate": c.candidate,
                    "baseline_estimate": c.baseline_result.estimate,
                    "candidate_estimate": c.candidate_result.estimate,
                    "difference": c.difference.estimate,
                    "ci_low": c.difference.ci_low,
                    "ci_high": c.difference.ci_high,
                    "n_units": c.difference.n_clusters,
                    "margin": margin.describe() if margin else "",
                    "p_raw": c.adjusted.get("p_raw", np.nan),
                    "p_adjusted": c.adjusted.get("p_adjusted", np.nan),
                    "verdict": c.verdict.label,
                    "favours": c.verdict.favours or "",
                }
            )
        return pd.DataFrame(rows)


def _resolve_policies(frame: pd.DataFrame, config: AnalysisConfig) -> tuple[str, list[str]]:
    available = sorted(str(p) for p in frame["policy_id"].dropna().unique())
    if len(available) < 2:
        raise ValueError(f"need at least two policies to compare; found {available}")
    baseline = config.comparison.baseline or available[0]
    if baseline not in available:
        raise ValueError(f"baseline policy {baseline!r} is not present; available: {available}")
    candidates = config.comparison.candidates or [p for p in available if p != baseline]
    missing = [c for c in candidates if c not in available]
    if missing:
        raise ValueError(f"candidate policy/policies {missing} are not present; available: {available}")
    candidates = [c for c in candidates if c != baseline]
    if not candidates:
        raise ValueError("no candidate policies remain after removing the baseline")
    return baseline, candidates


def compare_policies(
    frame: pd.DataFrame,
    config: AnalysisConfig,
    *,
    source: DataSource | None = None,
    validate: bool = True,
    include_failures: bool = True,
    metrics: Iterable[str] | None = None,
) -> ComparisonResult:
    """Run every declared metric comparison, paired at the declared unit.

    Parameters
    ----------
    frame:
        Experiment records conforming to the generic schema.
    config:
        The analysis configuration.  Its checksum lands in every provenance block.
    source:
        Fingerprint of the file the records came from, if any.
    validate:
        Run the full validation first and refuse to proceed on errors.  The
        declared inference structure is checked against the records **either
        way**: a structure that contradicts the data is never analysed.
    include_failures:
        Also produce the failure-reason breakdown.
    metrics:
        Restrict the run to these metric names.
    """
    structure_warnings = require_valid_structure(frame, config.inference)

    validation = None
    if validate:
        validation = validate_for_config(frame, config)
        validation.raise_if_errors()

    unit = config.inference.primary_unit
    filtered, prep = apply_filters(frame, config.filters, unit=unit)
    if filtered.empty:
        raise ValueError("no rows remain after filters; nothing to compare")

    status_ledger = run_status_ledger(filtered, config.missing_data, unit=unit)
    filtered, status_info = apply_run_status(filtered, config.missing_data)
    prep.run_status = {"ledger": status_ledger, "applied": status_info}
    prep.aggregation_rules = config.aggregation.as_dict()
    if filtered.empty:
        raise ValueError("no rows remain after run-status handling; nothing to compare")

    baseline, candidates = _resolve_policies(filtered, config)
    wanted = set(metrics) if metrics is not None else None
    selected = [m for m in config.ordered_metrics if wanted is None or m.name in wanted]
    if not selected:
        raise ValueError("no metrics selected")

    strata = tuple(s for s in config.strata if s in filtered.columns)
    result = ComparisonResult(
        config=config,
        validation=validation,
        source=source,
        preparation=prep,
        run_status=prep.run_status,
        structure_warnings=structure_warnings,
    )
    if validation is not None:
        result.notes.extend(w.message for w in validation.warnings)
    result.notes.extend(str(w["message"]) for w in structure_warnings)

    counts = observation_counts(filtered, config.inference)
    if len(counts):
        result.notes.append(
            f"{int(counts['n_observations'].sum())} observations nested in "
            f"{filtered[unit].nunique()} {unit}s; the bootstrap resamples {unit}s, not "
            "observations."
        )
    if status_info.get("applied"):
        result.notes.append(_run_status_note(status_info, status_ledger))

    for metric in selected:
        cleaned, missing_info = apply_missing_policy(
            filtered, metric.column, config.missing_data, direction=metric.direction
        )
        metric_prep = PreparationLog(
            n_input_rows=prep.n_input_rows,
            n_output_rows=int(len(cleaned)),
            filters_applied=prep.filters_applied,
            exclusions_applied=prep.exclusions_applied,
            missing_data=missing_info,
            run_status=prep.run_status,
            aggregation_rules=config.aggregation.as_dict(),
        )
        panel_frame = build_panel(cleaned, metric, config.aggregation, config.inference, strata=strata)
        estimator = build_estimator(metric.estimator, metric.resolved_params())

        for candidate in candidates:
            paired = build_paired_panel(
                panel_frame,
                metric,
                [baseline, candidate],
                config.inference,
                require_common_clusters=config.comparison.require_common_units,
                substructure=config.bootstrap.hierarchical,
            )
            comparison = _compare_one(
                metric=metric,
                paired=paired,
                panel_frame=panel_frame,
                estimator=estimator,
                baseline=baseline,
                candidate=candidate,
                config=config,
                source=source,
                preparation=metric_prep,
            )
            result.comparisons.append(comparison)

    result.families = _apply_multiplicity(result)
    result.disagreements = detect_disagreements(result)
    for d in result.disagreements:
        result.notes.append(d["message"])
    for family in result.families:
        result.notes.extend(family.notes)

    if include_failures and config.failure_column in filtered.columns:
        try:
            result.failures = summarize_failures(
                filtered,
                column=config.failure_column,
                policies=tuple([baseline, *candidates]),
                cluster_column=unit,
                baseline=baseline,
                candidate=candidates[0],
            )
        except (KeyError, ValueError) as exc:  # pragma: no cover - defensive
            result.notes.append(f"failure analysis skipped: {exc}")

    return result


def _run_status_note(status_info: dict[str, Any], ledger: dict[str, Any]) -> str:
    parts = []
    if status_info.get("n_counted_as_failure"):
        parts.append(
            f"{status_info['n_counted_as_failure']} run(s) that did not complete are counted as "
            "failed observations and remain in the denominator"
        )
    if status_info.get("n_excluded_documented"):
        parts.append(
            f"{status_info['n_excluded_documented']} run(s) are excluded as documented "
            "infeasible, which conditions the estimand on feasibility"
        )
    if status_info.get("n_left_missing"):
        parts.append(
            f"{status_info['n_left_missing']} run(s) are left to the missing-data policy"
        )
    mechanism = ledger.get("assumed_mechanism", "unknown")
    body = "; ".join(parts) or "all runs completed"
    return f"RUN STATUS: {body}. Declared missingness mechanism: {mechanism}."


def _compare_one(
    *,
    metric: MetricSpec,
    paired: PairedPanel,
    panel_frame: pd.DataFrame,
    estimator,
    baseline: str,
    candidate: str,
    config: AnalysisConfig,
    source: DataSource | None,
    preparation: PreparationLog,
) -> MetricComparison:
    boot = config.bootstrap
    base_res, cand_res, diff_res = paired_cluster_bootstrap(
        paired,
        estimator,
        baseline,
        candidate,
        n_resamples=boot.n_resamples,
        seed=boot.seed,
        confidence_level=boot.confidence_level,
        method=boot.method,
        hierarchical=boot.hierarchical,
        estimator_name=metric.estimator,
        paired=config.comparison.paired,
    )

    margin = config.margin_for(metric.name)
    equivalence: EquivalenceResult | None = None
    if margin is not None and diff_res.replicates is not None:
        equivalence = tost(
            diff_res.replicates,
            diff_res.estimate,
            margin,
            alpha=config.equivalence.alpha,
            metric_name=metric.name,
        )

    ni: EquivalenceResult | None = None
    if metric.name in config.equivalence.non_inferiority and diff_res.replicates is not None:
        try:
            ni = non_inferiority(
                diff_res.replicates,
                diff_res.estimate,
                margin,
                alpha=config.equivalence.alpha,
                direction=metric.direction,
                metric_name=metric.name,
            )
        except MarginRequired as exc:
            paired.notes.append(str(exc))

    verdict = classify_difference(
        metric_name=metric.name,
        baseline=baseline,
        candidate=candidate,
        difference=diff_res.estimate,
        ci_low=diff_res.ci_low,
        ci_high=diff_res.ci_high,
        confidence_level=boot.confidence_level,
        direction=metric.direction,
        equivalence=equivalence,
        n_units=diff_res.n_clusters,
        unit_label=paired.cluster_level,
    )

    overlap = overlap_representativeness(panel_frame, paired, estimator, baseline, candidate)
    diagnostics = {
        "design_effect": {p: design_effect(paired, p) for p in (baseline, candidate)},
        "imbalance": imbalance_report(paired, config.missing_data.max_count_imbalance),
        "paired_unit_sets": paired.paired,
        "paired_resample": config.comparison.paired,
        "estimand_conditioning": paired.estimand_conditioning,
        "overlap": overlap,
        "credibility": diff_res.credibility,
        "bounds_check": diff_res.bounds_check,
    }

    notes = list(paired.notes) + list(diff_res.notes)
    if margin is None:
        notes.append(
            f"No practical margin declared for {metric.name}; equivalence cannot be concluded "
            "for this metric however wide or narrow the interval is."
        )
    imbalance = diagnostics["imbalance"]
    if imbalance.get("checked") and imbalance.get("n_flagged_clusters"):
        notes.append(
            f"{imbalance['n_flagged_clusters']} {paired.cluster_level}(s) contribute very "
            f"different observation counts under the two policies (largest relative gap "
            f"{imbalance['max_relative_imbalance']:.0%}); the paired difference there is "
            "estimated with unequal precision."
        )
    if overlap.get("checked") and overlap.get("representativeness_questionable"):
        notes.append(overlap["message"])

    provenance = provenance_for(
        source=source,
        config=config,
        metric=metric,
        preparation=preparation,
        bootstrap=boot,
        unit_of_inference=config.inference.primary_unit,
        confidence_level=boot.confidence_level,
        inference=config.inference.as_dict(),
        comparison={
            "baseline": baseline,
            "candidate": candidate,
            "shared_unit_sets": paired.paired,
            "paired_resample": config.comparison.paired,
        },
        estimand={
            "contrast": f"{candidate} - {baseline}",
            "unit": paired.cluster_level,
            "aggregation_level": metric.level,
            "conditioning": paired.estimand_conditioning,
            "direction": metric.direction,
            "tail_side": metric.tail_side,
        },
        panel=paired.as_dict(),
    )

    return MetricComparison(
        metric=metric,
        baseline=baseline,
        candidate=candidate,
        baseline_result=base_res,
        candidate_result=cand_res,
        difference=diff_res,
        verdict=verdict,
        equivalence=equivalence,
        non_inferiority_result=ni,
        panel_summary=paired.as_dict(),
        diagnostics=diagnostics,
        provenance=provenance,
        notes=notes,
    )


def overlap_representativeness(
    panel_frame: pd.DataFrame,
    paired: PairedPanel,
    estimator,
    baseline: str,
    candidate: str,
) -> dict[str, Any]:
    """Ask whether the shared units resemble the ones the pairing excluded.

    A paired complete-case estimate is internally correct on the units it uses.
    Whether it stands in for the target population depends on whether overlap is
    related to the outcome, and that is checkable: compare each policy's values
    on the shared units against its values on the units the other policy never
    saw.  A large shift means the complete-case population is not a stand-in.
    """
    from wg_eval.hierarchy import unit_labels

    if paired.n_clusters == paired.n_union_clusters:
        return {
            "checked": True,
            "all_units_shared": True,
            "representativeness_questionable": False,
            "message": "",
        }
    work = panel_frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work["__cluster__"] = unit_labels(work, paired.inference, paired.cluster_level)
    shared = set(paired.clusters)

    arms: dict[str, Any] = {}
    flagged = False
    for policy in (baseline, candidate):
        sub = work[work["policy_id"] == policy]
        inside = pd.to_numeric(
            sub.loc[sub["__cluster__"].isin(shared), "value"], errors="coerce"
        ).dropna()
        outside = pd.to_numeric(
            sub.loc[~sub["__cluster__"].isin(shared), "value"], errors="coerce"
        ).dropna()
        if len(inside) < 2 or len(outside) < 2:
            arms[policy] = {"comparable": False, "n_shared": len(inside), "n_excluded": len(outside)}
            continue
        pooled_sd = float(np.sqrt((inside.var(ddof=1) + outside.var(ddof=1)) / 2.0))
        shift = float(outside.mean() - inside.mean())
        standardized = shift / pooled_sd if pooled_sd > 0 else float("nan")
        questionable = bool(np.isfinite(standardized) and abs(standardized) > OVERLAP_SHIFT_THRESHOLD)
        flagged = flagged or questionable
        arms[policy] = {
            "comparable": True,
            "n_shared": int(len(inside)),
            "n_excluded": int(len(outside)),
            "mean_on_shared": float(inside.mean()),
            "mean_on_excluded": float(outside.mean()),
            "shift": shift,
            "standardized_shift": standardized,
            "questionable": questionable,
        }

    message = ""
    if flagged:
        message = (
            f"COMPLETE-CASE REPRESENTATIVENESS: the {paired.cluster_level}s excluded by the "
            f"pairing differ from the shared ones on this metric by more than "
            f"{OVERLAP_SHIFT_THRESHOLD} pooled SD. The paired estimate remains correct for the "
            f"{paired.n_clusters} shared {paired.cluster_level}s, but it should not be read as "
            f"an estimate for the full set of {paired.n_union_clusters} observed "
            f"{paired.cluster_level}s, still less for the target population."
        )
    return {
        "checked": True,
        "all_units_shared": False,
        "threshold": OVERLAP_SHIFT_THRESHOLD,
        "by_policy": arms,
        "representativeness_questionable": flagged,
        "message": message,
    }


def _apply_multiplicity(result: ComparisonResult) -> list[FamilyResult]:
    """Correct each declared role family and attach the outcome to each test."""
    config = result.config
    alpha = config.equivalence.alpha
    families: list[FamilyResult] = []
    by_role: dict[str, list[MetricComparison]] = {}
    for c in result.comparisons:
        by_role.setdefault(c.role, []).append(c)

    for role, group in by_role.items():
        pvalues = {c.key: c.difference.p_two_sided for c in group}
        family = apply_to_family(
            role=role,
            method=config.multiplicity.correction_for(role),
            alpha=alpha,
            pvalues=pvalues,
        )
        lookup = {t.key: t for t in family.tests}
        for c in group:
            test = lookup[c.key]
            c.adjusted = test.as_dict()
            if test.method != "none" and test.survives is False and c.verdict.favours:
                c.notes.append(
                    f"This finding does not survive the declared {test.method} correction over "
                    f"the {role} family of {test.family_size} test(s) "
                    f"(adjusted p = {test.p_adjusted:.3g} at alpha = {alpha:g}). The interval "
                    "shown is marginal and is not adjusted for multiplicity."
                )
        families.append(family)
    return families


def detect_disagreements(result: ComparisonResult) -> list[dict[str, Any]]:
    """Find central-vs-tail and cross-metric conflicts between resolved findings.

    A mean that favours A while a tail statistic on the same column favours B is
    not a contradiction to be averaged away; it is the finding.  The library
    reports both contrasts with their uncertainty and does not choose.
    """
    out: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, str], list[MetricComparison]] = {}
    for c in result.comparisons:
        by_pair.setdefault((c.baseline, c.candidate), []).append(c)

    for (baseline, candidate), group in by_pair.items():
        resolved = [c for c in group if c.verdict.favours]
        central = [c for c in resolved if not c.metric.is_tail_metric]
        tail = [c for c in resolved if c.metric.is_tail_metric]
        for cm in central:
            for tm in tail:
                if cm.metric.column != tm.metric.column:
                    continue
                if cm.verdict.favours == tm.verdict.favours:
                    continue
                out.append(
                    {
                        "kind": "central_vs_tail",
                        "baseline": baseline,
                        "candidate": candidate,
                        "column": cm.metric.column,
                        "direction": cm.metric.direction,
                        "central": _contrast_block(cm),
                        "tail": _contrast_block(tm),
                        "message": (
                            f"CENTRAL AND TAIL CONTRASTS DISAGREE on `{cm.metric.column}` "
                            f"({cm.metric.direction}; the {tm.metric.tail_side} tail is the "
                            f"harmful one). "
                            f"{cm.metric.name}: {cm.difference.estimate:+.4g} "
                            f"[{cm.difference.ci_low:+.4g}, {cm.difference.ci_high:+.4g}] "
                            f"favours {cm.verdict.favours}. "
                            f"{tm.metric.name}: {tm.difference.estimate:+.4g} "
                            f"[{tm.difference.ci_low:+.4g}, {tm.difference.ci_high:+.4g}] "
                            f"favours {tm.verdict.favours}. "
                            "Both contrasts are reported with their intervals; this library "
                            "does not rank the two and implies no overall preference. Which "
                            "one governs the decision is a declared risk preference, not a "
                            "statistical result."
                        ),
                    }
                )
        favoured = {c.verdict.favours for c in resolved}
        if len(favoured) > 1:
            out.append(
                {
                    "kind": "metric_split",
                    "baseline": baseline,
                    "candidate": candidate,
                    "favours": sorted(f for f in favoured if f),
                    "by_metric": {c.metric.name: c.verdict.favours for c in resolved},
                    "message": (
                        f"METRICS RESOLVE IN DIFFERENT DIRECTIONS for {candidate} vs {baseline}: "
                        + ", ".join(
                            f"{c.metric.name} ({c.role}) favours {c.verdict.favours}"
                            for c in resolved
                        )
                        + ". No single ranking follows; the primary metric declared in advance "
                        "is the one the analysis was designed to answer."
                    ),
                }
            )
    return out


def _contrast_block(c: MetricComparison) -> dict[str, Any]:
    return {
        "metric": c.metric.name,
        "estimator": c.metric.estimator,
        "tail_side": c.metric.tail_side,
        "difference": c.difference.estimate,
        "ci": [c.difference.ci_low, c.difference.ci_high],
        "n_units": c.difference.n_clusters,
        "favours": c.verdict.favours,
        "role": c.role,
    }
