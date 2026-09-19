"""The comparison orchestrator: data in, defensible verdicts out.

This module is the only place that decides what a result *means*.  Everything
it can do wrong -- unpaired comparison, resident-level resampling, equivalence
without a margin, a tail metric quietly disagreeing with a mean -- is either
refused outright or surfaced as a note attached to the result.
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
    observation_counts,
    panel as build_panel,
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
from wg_eval.metrics import build_estimator
from wg_eval.pairing import PairedPanel, build_paired_panel, imbalance_report
from wg_eval.provenance import Provenance, provenance_for
from wg_eval.schema import LEVELS
from wg_eval.validate import ValidationReport, validate_records

_LEVEL_ORDER = {name: i for i, name in enumerate(LEVELS)}


@dataclass
class MetricComparison:
    """One metric, one (baseline, candidate) pair, one verdict."""

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

    @property
    def favours(self) -> str | None:
        return self.verdict.favours

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.as_dict(),
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
    notes: list[str] = field(default_factory=list)

    def for_metric(self, name: str) -> list[MetricComparison]:
        return [c for c in self.comparisons if c.metric.name == name]

    def get(self, metric: str, candidate: str, baseline: str | None = None) -> MetricComparison:
        for c in self.comparisons:
            if c.metric.name == metric and c.candidate == candidate:
                if baseline is None or c.baseline == baseline:
                    return c
        raise KeyError(f"no comparison for metric={metric!r} candidate={candidate!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "source": self.source.as_dict() if self.source else None,
            "validation": self.validation.as_dict() if self.validation else None,
            "preparation": self.preparation.as_dict() if self.preparation else None,
            "comparisons": [c.as_dict() for c in self.comparisons],
            "failures": self.failures.as_dict() if self.failures else None,
            "disagreements": self.disagreements,
            "notes": list(self.notes),
        }

    def summary_table(self) -> pd.DataFrame:
        """One row per comparison: estimate, interval, verdict."""
        rows = []
        for c in self.comparisons:
            rows.append(
                {
                    "metric": c.metric.name,
                    "level": c.metric.level,
                    "baseline": c.baseline,
                    "candidate": c.candidate,
                    "baseline_estimate": c.baseline_result.estimate,
                    "candidate_estimate": c.candidate_result.estimate,
                    "difference": c.difference.estimate,
                    "ci_low": c.difference.ci_low,
                    "ci_high": c.difference.ci_high,
                    "n_clusters": c.difference.n_clusters,
                    "margin": c.equivalence.margin if c.equivalence else np.nan,
                    "verdict": c.verdict.label,
                    "favours": c.verdict.favours or "",
                }
            )
        return pd.DataFrame(rows)


def _check_levels(config: AnalysisConfig) -> None:
    cluster = config.cluster_level
    for metric in config.metrics:
        if _LEVEL_ORDER[metric.level] < _LEVEL_ORDER[cluster]:
            raise ValueError(
                f"metric {metric.name!r} is computed at the {metric.level} level, which is "
                f"coarser than the {cluster}-level resampling unit. A {metric.level}-level "
                f"value cannot be assigned to a {cluster}. Either compute the metric at "
                f"{cluster} level or finer, or resample at the {metric.level} level."
            )


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
    """Run every declared metric comparison, paired at the unit of inference.

    Parameters
    ----------
    frame:
        Experiment records conforming to the generic schema.
    config:
        The analysis configuration.  Its checksum lands in every provenance block.
    source:
        Fingerprint of the file the records came from, if any.
    validate:
        Run :func:`wg_eval.validate.validate_records` first and refuse to
        proceed on errors.
    include_failures:
        Also produce the failure-reason breakdown.
    metrics:
        Restrict the run to these metric names.
    """
    _check_levels(config)

    validation = None
    if validate:
        validation = validate_records(
            frame, strata=config.strata, unit_of_inference=config.unit_of_inference
        )
        validation.raise_if_errors()

    filtered, prep = apply_filters(frame, config.filters)
    if filtered.empty:
        raise ValueError("no rows remain after filters; nothing to compare")
    prep.aggregation_rules = config.aggregation.as_dict()

    baseline, candidates = _resolve_policies(filtered, config)
    wanted = set(metrics) if metrics is not None else None
    selected = [m for m in config.metrics if wanted is None or m.name in wanted]
    if not selected:
        raise ValueError("no metrics selected")

    strata = tuple(s for s in config.strata if s in filtered.columns)
    result = ComparisonResult(
        config=config, validation=validation, source=source, preparation=prep
    )
    if validation is not None:
        result.notes.extend(w.message for w in validation.warnings)

    counts = observation_counts(filtered)
    if len(counts):
        result.notes.append(
            f"{int(counts['n_observations'].sum())} observations nested in "
            f"{filtered['world_id'].nunique()} worlds; the bootstrap resamples "
            f"{config.cluster_level}s, not observations."
        )

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
            aggregation_rules=config.aggregation.as_dict(),
        )
        panel_frame = build_panel(cleaned, metric, config.aggregation, strata=strata)
        estimator = build_estimator(metric.estimator, metric.params)

        for candidate in candidates:
            paired = build_paired_panel(
                panel_frame,
                metric,
                [baseline, candidate],
                cluster_level=config.cluster_level,
                require_common_clusters=config.comparison.require_common_worlds,
            )
            comparison = _compare_one(
                metric=metric,
                paired=paired,
                estimator=estimator,
                baseline=baseline,
                candidate=candidate,
                config=config,
                source=source,
                preparation=metric_prep,
            )
            result.comparisons.append(comparison)

    result.disagreements = detect_disagreements(result)
    for d in result.disagreements:
        result.notes.append(d["message"])

    if include_failures and config.failure_column in filtered.columns:
        try:
            result.failures = summarize_failures(
                filtered,
                column=config.failure_column,
                policies=tuple([baseline, *candidates]),
                cluster_column="world_id",
                baseline=baseline,
                candidate=candidates[0],
            )
        except (KeyError, ValueError) as exc:  # pragma: no cover - defensive
            result.notes.append(f"failure analysis skipped: {exc}")

    return result


def _compare_one(
    *,
    metric: MetricSpec,
    paired: PairedPanel,
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
            ni = None
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
    )

    diagnostics = {
        "design_effect": {p: design_effect(paired, p) for p in (baseline, candidate)},
        "imbalance": imbalance_report(paired, config.missing_data.max_count_imbalance),
        "paired": paired.paired,
    }

    notes = list(paired.notes)
    if margin is None:
        notes.append(
            f"No practical margin declared for {metric.name}; equivalence cannot be "
            "concluded for this metric however wide or narrow the interval is."
        )
    imbalance = diagnostics["imbalance"]
    if imbalance.get("checked") and imbalance.get("n_flagged_clusters"):
        notes.append(
            f"{imbalance['n_flagged_clusters']} {paired.cluster_level}(s) contribute very "
            f"different observation counts under the two policies (max relative imbalance "
            f"{imbalance['max_relative_imbalance']:.0%}); the paired difference there is "
            "estimated with unequal precision."
        )

    provenance = provenance_for(
        source=source,
        config=config,
        metric=metric,
        preparation=preparation,
        bootstrap=boot,
        unit_of_inference=config.unit_of_inference,
        confidence_level=boot.confidence_level,
        comparison={"baseline": baseline, "candidate": candidate, "paired": paired.paired},
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


def detect_disagreements(result: ComparisonResult) -> list[dict[str, Any]]:
    """Find central-vs-tail and cross-metric conflicts between resolved verdicts.

    A mean that favours A while a tail statistic on the same column favours B
    is not a contradiction to be averaged away; it is the finding.
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
                if cm.verdict.favours != tm.verdict.favours:
                    out.append(
                        {
                            "kind": "central_vs_tail",
                            "baseline": baseline,
                            "candidate": candidate,
                            "column": cm.metric.column,
                            "central_metric": cm.metric.name,
                            "central_favours": cm.verdict.favours,
                            "tail_metric": tm.metric.name,
                            "tail_favours": tm.verdict.favours,
                            "message": (
                                f"TAIL DISAGREEMENT on `{cm.metric.column}`: "
                                f"{cm.metric.name} favours {cm.verdict.favours} while "
                                f"{tm.metric.name} favours {tm.verdict.favours}. "
                                "Average performance and tail risk point in opposite "
                                "directions; reporting either alone would mislead."
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
                        f"METRIC SPLIT for {candidate} vs {baseline}: different declared "
                        f"metrics resolve in favour of different policies "
                        f"({', '.join(f'{c.metric.name}->{c.verdict.favours}' for c in resolved)}). "
                        "There is no single winner; the choice depends on which metric the "
                        "decision actually cares about, and that must be declared in advance."
                    ),
                }
            )
    return out
