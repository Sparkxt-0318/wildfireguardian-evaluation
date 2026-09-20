"""Reproducible report rendering.

A report is not a summary; it is the audit trail.  Anything that could change
the conclusion appears in it: the declared inference structure, the estimand
and what it is conditioned on, the unit count, the run-status ledger, the
exclusions, the margins and their provenance, the multiplicity family, the seed
and the checksum of the file the numbers came from.

The prose is constrained.  Generated text may not claim that a comparison
*proves* anything, that two options are *the same* or show *no difference*,
that either is *safe*, or that one is *better overall*.  Those are claims about
a decision; this library reports evidence about one metric at a time.
:func:`audit_wording` enforces the list, and the test suite runs it over every
report the package can produce.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from wg_eval.compare import ComparisonResult, MetricComparison
from wg_eval.stratify import StratifiedResult
from wg_eval.version import REPORT_GENERATOR_VERSION, code_version

#: Claims generated prose may not make. Phrase-level rather than word-level on
#: purpose: "the same units" is a fact about the design, while "the policies are
#: the same" is an unsupported conclusion, and a word-level ban cannot tell them
#: apart without making the reports unreadable.
FORBIDDEN_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bprove[sdn]?\b", "statistics does not prove; say what the interval supports"),
    (r"\bproven\b", "statistics does not prove; say what the interval supports"),
    (r"\bno difference\b", "say 'inconclusive' or 'equivalent within the declared margin'"),
    (r"\bno significant difference\b", "the phrase that causes the error this library prevents"),
    (r"\bare the same\b", "say 'equivalent within the declared margin'"),
    (r"\bis the same as\b", "say 'equivalent within the declared margin'"),
    (r"\bidentical performance\b", "say 'equivalent within the declared margin'"),
    (r"\bbetter overall\b", "this library compares one metric at a time"),
    (r"\bworse overall\b", "this library compares one metric at a time"),
    (r"\boverall winner\b", "this library does not rank policies"),
    (r"\bis safe\b", "safety is a domain judgement, not an interval"),
    (r"\bare safe\b", "safety is a domain judgement, not an interval"),
    (r"\bbeats\b", "say which policy the interval favours on this metric"),
)


def audit_wording(text: str) -> list[dict[str, Any]]:
    """Find forbidden claims in generated prose.

    Returns one finding per match, with the offending phrase and what to say
    instead.  An empty list means the text makes no claim the library is not
    entitled to make.
    """
    findings: list[dict[str, Any]] = []
    for pattern, guidance in FORBIDDEN_PHRASES:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 60)
            findings.append(
                {
                    "phrase": match.group(0),
                    "pattern": pattern,
                    "guidance": guidance,
                    "context": text[start : match.end() + 60].replace("\n", " "),
                }
            )
    return findings


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "n/a"
    return f"{number:.{places}g}"


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out = ["| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(header)) + " |"]
    out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        out.append("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return "\n".join(out)


def verdict_line(comparison: MetricComparison) -> str:
    """One metric's finding, phrased so it cannot be misread as a decision."""
    return comparison.verdict.sentence


def render_markdown(
    result: ComparisonResult,
    *,
    title: str = "Evaluation report",
    stratified: dict[str, StratifiedResult] | None = None,
    include_provenance: bool = True,
) -> str:
    """Render a full comparison as Markdown."""
    cfg = result.config
    unit = cfg.inference.primary_unit
    lines: list[str] = [f"# {title}", ""]

    # -- 1. what was analysed ------------------------------------------------
    source = result.source
    lines += ["## 1. What was analysed", ""]
    if source:
        lines += [
            f"- **Source**: `{source.path}`",
            f"- **Checksum**: `{source.checksum}`",
            f"- **Rows**: {source.n_rows} ({source.format})",
        ]
    else:
        lines.append("- **Source**: records supplied in memory (no file checksum available)")
    lines += [
        f"- **Inference structure**: `{cfg.inference.chain}` — resampling unit "
        f"`{unit}`, declared and checked against the records",
        f"- **Resampling**: {cfg.bootstrap.n_resamples} bootstrap resamples of whole "
        f"`{unit}`s, seed `{cfg.bootstrap.seed}`, method `{cfg.bootstrap.method}`"
        + (", two-stage" if cfg.bootstrap.hierarchical else "")
        + f", {cfg.bootstrap.confidence_level:.0%} intervals",
        "- **Pairing**: "
        + (
            "paired on shared units"
            if cfg.comparison.require_common_units
            else "UNPAIRED (policies on different unit sets)"
        ),
        f"- **Missing data**: policy `{cfg.missing_data.policy}`, assumed mechanism "
        f"`{cfg.missing_data.assumed_mechanism}`",
        f"- **Analysis status**: `{cfg.protocol.analysis_status}`"
        + (
            f" (protocol hash `{cfg.protocol.protocol_hash}`)"
            if cfg.protocol.protocol_hash
            else " -- not asserted to be preregistered"
        ),
        f"- **Code**: {code_version()} | report generator {REPORT_GENERATOR_VERSION}",
        "",
    ]

    if result.validation is not None:
        summary = result.validation.summary
        lines += ["### Design", ""]
        lines += [
            f"- hierarchy: **{summary.get('hierarchy', 'n/a')}**",
            f"- {unit}s: **{summary.get('n_units', 'n/a')}**, observations: "
            f"{summary.get('n_rows', 'n/a')}",
            f"- observations per {unit}: {summary.get('observations_per_unit', 'n/a')} "
            "(nested, not independent)",
            f"- policies: {', '.join(summary.get('policies', []))}",
            f"- {unit}s carrying every policy: {summary.get('n_common_units', 'n/a')}",
            "",
        ]
        if result.validation.warnings:
            lines += ["### Validation warnings", ""]
            lines += [f"- {w.message}" for w in result.validation.warnings]
            lines.append("")

    prep = result.preparation
    lines += ["### Filters and exclusions", ""]
    if prep and (prep.filters_applied or prep.exclusions_applied):
        for f in prep.filters_applied:
            lines.append(
                f"- filter `{f['kind']}` on `{f['column']}` = {f['values']}: "
                f"removed {f['rows_removed']} rows, {f['units_removed']} {unit}s"
            )
        for e in prep.exclusions_applied:
            lines.append(
                f"- exclusion `{e['name']}` (`{e['expression']}`): "
                f"removed {e['rows_removed']} rows, {e['units_removed']} {unit}s"
            )
    else:
        lines.append("- none applied")
    lines.append("")

    lines += _run_status_section(result)

    # -- 2. results, primary first -------------------------------------------
    lines += ["## 2. Findings", ""]
    primary = result.by_role("primary")
    if primary:
        lines += ["### Primary outcome", ""]
        for c in primary:
            lines += _finding_block(c, cfg)
    else:
        lines += [
            "### Primary outcome",
            "",
            "- No metric was declared primary. Every finding below is therefore secondary or "
            "exploratory, and none of them is the outcome this analysis was designed around.",
            "",
        ]
    for role, heading in (("secondary", "Secondary outcomes"), ("exploratory", "Exploratory outcomes")):
        group = result.by_role(role)
        if not group:
            continue
        lines += [f"### {heading}", ""]
        for c in group:
            lines += _finding_block(c, cfg)

    lines += ["### All contrasts", ""]
    rows = []
    for c in result.ordered_comparisons:
        margin = c.equivalence.margin if c.equivalence else None
        rows.append(
            [
                c.role,
                c.metric.name,
                c.metric.direction.replace("_is_better", ""),
                f"{c.candidate} vs {c.baseline}",
                _fmt(c.baseline_result.estimate),
                _fmt(c.candidate_result.estimate),
                _fmt(c.difference.estimate),
                f"[{_fmt(c.difference.ci_low)}, {_fmt(c.difference.ci_high)}]",
                str(c.difference.n_clusters),
                margin.describe() if margin else "none",
                _fmt(c.adjusted.get("p_adjusted"), 3),
                c.verdict.label,
            ]
        )
    lines.append(
        _table(
            rows,
            [
                "role", "metric", "better", "contrast", "baseline", "candidate", "difference",
                f"{cfg.bootstrap.confidence_level:.0%} CI", unit + "s", "margin", "adj. p",
                "finding",
            ],
        )
    )
    lines += [
        "",
        "`adj. p` is adjusted within the declared role family; intervals are marginal and are "
        "not adjusted for multiplicity.",
        "",
    ]

    # -- 3. disagreements -----------------------------------------------------
    lines += ["## 3. Where the metrics disagree", ""]
    if result.disagreements:
        for d in result.disagreements:
            lines.append(f"- {d['message']}")
    else:
        lines.append("- no disagreement detected among resolved findings")
    lines.append("")

    # -- 4. multiplicity ------------------------------------------------------
    lines += ["## 4. Analysis families and multiplicity", ""]
    if result.families:
        frows = []
        for family in result.families:
            for test in family.tests:
                frows.append(
                    [
                        test.role,
                        test.key,
                        test.method,
                        str(test.family_size),
                        _fmt(test.p_raw, 3),
                        _fmt(test.p_adjusted, 3),
                        "-" if test.survives is None else ("yes" if test.survives else "no"),
                    ]
                )
        lines.append(
            _table(frows, ["role", "test", "method", "family size", "p (raw)", "p (adj)", "survives"])
        )
        lines.append("")
        for family in result.families:
            for note in family.notes:
                lines.append(f"- {note}")
    else:
        lines.append("- no families were formed")
    lines.append("")

    # -- 5. diagnostics --------------------------------------------------------
    lines += ["## 5. Design diagnostics", ""]
    diag_rows = []
    for c in result.ordered_comparisons:
        for policy, deff in c.diagnostics.get("design_effect", {}).items():
            if not deff.get("available"):
                continue
            diag_rows.append(
                [
                    c.metric.name, policy, _fmt(deff["icc"], 3), _fmt(deff["mean_cluster_size"], 3),
                    _fmt(deff["design_effect"], 3), _fmt(deff["effective_sample_size"], 4),
                    str(deff["n_observations"]),
                ]
            )
    if diag_rows:
        lines.append(
            _table(
                diag_rows,
                ["metric", "policy", "ICC", "mean unit size", "design effect", "effective n",
                 "observations"],
            )
        )
        lines += [
            "",
            "A design effect of *d* means the nested observations carry the information of "
            "roughly `observations / d` independent ones. It is a diagnostic; the cluster "
            "bootstrap, not this number, produces the intervals.",
            "",
        ]
    else:
        lines += ["- one value per unit; the design effect is 1 by construction", ""]

    credibility_rows = [
        [c.metric.name, str(c.difference.n_clusters), "; ".join(c.difference.credibility.get("reasons", []))]
        for c in result.ordered_comparisons
        if c.difference.credibility.get("reasons")
    ]
    if credibility_rows:
        lines += [
            "### Statistics not credible at this sample size",
            "",
            _table(credibility_rows, ["metric", unit + "s", "why"]),
            "",
        ]

    bounds_rows = [
        [c.metric.name, _fmt(c.difference.ci_low), _fmt(c.difference.ci_high),
         str(c.difference.bounds_check.get("support"))]
        for c in result.ordered_comparisons
        if c.difference.bounds_check.get("impossible")
    ]
    if bounds_rows:
        lines += [
            "### Interval endpoints outside the declared support",
            "",
            _table(bounds_rows, ["metric", "ci low", "ci high", "support"]),
            "",
            "Endpoints are reported unclamped. Truncating them would narrow the interval "
            "without making the construction appropriate for a bounded outcome.",
            "",
        ]

    # -- 6. failures ------------------------------------------------------------
    if result.failures is not None:
        lines += ["## 6. Failure analysis", "", "```", result.failures.to_text(), "```", ""]
        if result.failures.shifts is not None and not result.failures.shifts.empty:
            shift_rows = [
                [
                    str(r["failure_reason"]), _fmt(r["baseline_rate"], 3),
                    _fmt(r["candidate_rate"], 3), _fmt(r["mean_paired_shift"], 3),
                    str(int(r["n_clusters"])),
                ]
                for _, r in result.failures.shifts.iterrows()
            ]
            lines += [
                f"Paired per-{unit} shift in each failure mode (candidate - baseline):",
                "",
                _table(shift_rows, ["reason", "baseline rate", "candidate rate", "paired shift",
                                    unit + "s"]),
                "",
            ]

    # -- 7. strata ---------------------------------------------------------------
    if stratified:
        lines += ["## 7. Stratified results and allocation", ""]
        for column, strat in stratified.items():
            lines += [f"### By `{column}`", ""]
            table = strat.summary_table()
            if table.empty:
                lines += ["- no stratum had enough shared units to support a contrast", ""]
            else:
                srows = [
                    [
                        str(r[column]), str(r["role"]), str(r["metric"]),
                        f"{r['candidate']} vs {r['baseline']}", _fmt(r["difference"]),
                        f"[{_fmt(r['ci_low'])}, {_fmt(r['ci_high'])}]", str(int(r["n_units"])),
                        str(r["verdict"]),
                    ]
                    for _, r in table.iterrows()
                ]
                lines.append(
                    _table(srows, [column, "role", "metric", "contrast", "difference", "CI",
                                   unit + "s", "finding"])
                )
                lines.append("")
            if strat.allocation is not None and not strat.allocation.empty:
                arows = [
                    [str(r["policy_id"]), str(r["stratum_value"]), str(int(r["n_units"])),
                     f"{r['share_of_policy_units']:.0%}"]
                    for _, r in strat.allocation.iterrows()
                ]
                lines += [
                    f"Allocation ledger (how many {unit}s of each stratum each policy ran):",
                    "",
                    _table(arows, ["policy", column, unit + "s", "share of that policy's units"]),
                    "",
                ]
            ledger = strat.ledger or {}
            composition = (ledger.get("strata", {}).get(column) or {}).get("composition_shift")
            if composition:
                crows = [[k, f"{v:+.1%}"] for k, v in composition.items()]
                lines += [
                    "Composition shift, all observed units to shared units:",
                    "",
                    _table(crows, [column, "shift in share"]),
                    "",
                    "A large shift means the paired analysis runs on a different mix of "
                    "material than the full set of observed units.",
                    "",
                ]
            for note in strat.notes:
                lines.append(f"- {note}")
            for label, reason in sorted(strat.skipped.items()):
                lines.append(f"- stratum `{label}` not contrasted: {reason}")
            lines.append("")

    # -- 8. notes -----------------------------------------------------------------
    if result.notes:
        lines += ["## 8. Notes carried from the analysis", ""]
        for note in dict.fromkeys(result.notes):
            lines.append(f"- {note}")
        lines.append("")

    # -- 9. provenance -------------------------------------------------------------
    if include_provenance:
        lines += ["## 9. Provenance and reproducibility", ""]
        for c in result.ordered_comparisons:
            if c.provenance is None:
                continue
            lines += [
                f"<details><summary><code>{c.metric.name}</code> ({c.role}) -- "
                f"{c.candidate} vs {c.baseline}</summary>",
                "",
                "```",
                c.provenance.to_text(),
                "```",
                "",
                "</details>",
                "",
            ]

    lines += [
        "---",
        "",
        "How to read this report:",
        "",
        "1. An interval that includes zero means **undetermined**. Only a test against a "
        "declared margin can support equivalence, and equivalence within a margin is a "
        "statistical statement, not a statement that two options are interchangeable in use.",
        f"2. Every interval comes from resampling whole `{unit}`s. Observation counts are "
        "context, not the sample size.",
        "3. Findings are ordered primary, secondary, exploratory. A favourable exploratory "
        "metric is not a headline.",
        "4. Where metrics disagree, the disagreement is the finding. This report names no "
        "winner across metrics.",
        "",
    ]
    return "\n".join(lines)


def _finding_block(c: MetricComparison, cfg) -> list[str]:
    lines = [f"**{c.metric.name}** ({c.metric.direction}, at `{c.metric.level}` level)", ""]
    lines.append(f"- {verdict_line(c)}")
    if c.metric.is_tail_metric:
        lines.append(
            f"- tail convention: summarises the **{c.metric.tail_side}** tail, which is the "
            f"harmful end for a `{c.metric.direction}` metric"
        )
    if c.equivalence:
        lines.append(f"- equivalence: {c.equivalence.interpretation}")
        for note in c.equivalence.notes:
            lines.append(f"  - {note}")
    if c.non_inferiority_result:
        lines.append(f"- non-inferiority: {c.non_inferiority_result.interpretation}")
    lines.append(f"- estimand conditioning: {c.diagnostics.get('estimand_conditioning', 'n/a')}")
    for note in dict.fromkeys(c.notes):
        lines.append(f"- note: {note}")
    lines.append("")
    return lines


def _run_status_section(result: ComparisonResult) -> list[str]:
    ledger = (result.run_status or {}).get("ledger") or {}
    if not ledger.get("available"):
        return []
    rows = [
        [str(r["policy_id"]), str(r["status"]), str(r["handling"]), str(int(r["n_rows"])),
         str(int(r["n_units"]))]
        for r in ledger.get("counts", [])
    ]
    lines = [
        "### Run status ledger",
        "",
        _table(rows, ["policy", "status", "handling", "rows", "units"]),
        "",
        f"Declared missingness mechanism: `{ledger.get('assumed_mechanism', 'unknown')}`.",
    ]
    if ledger.get("mechanism_justification"):
        lines.append(f"Justification: {ledger['mechanism_justification']}")
    if ledger.get("undeclared_statuses"):
        lines.append(
            f"Statuses with no declared handling (defaulted): {ledger['undeclared_statuses']}."
        )
    lines += [
        "",
        "A run that did not complete is not automatically a missing value. Rows handled as "
        "`failure` stay in the denominator; rows handled as `excluded_documented` condition the "
        "estimand on feasibility.",
        "",
    ]
    return lines


def render_text(result: ComparisonResult) -> str:
    """Compact console rendering of a comparison."""
    cfg = result.config
    lines = [
        f"inference: {cfg.inference.describe()}",
        f"bootstrap: {cfg.bootstrap.n_resamples} x {cfg.inference.primary_unit}, "
        f"seed {cfg.bootstrap.seed}, method {cfg.bootstrap.method}",
        f"analysis status: {cfg.protocol.analysis_status}",
        "",
    ]
    table = result.summary_table()
    if not table.empty:
        with pd.option_context("display.width", 220, "display.max_columns", 40):
            lines.append(table.to_string(index=False))
    lines.append("")
    for c in result.ordered_comparisons:
        lines.append(f"* [{c.role}] {verdict_line(c)}")
        if c.equivalence:
            lines.append(f"    equivalence: {c.equivalence.interpretation}")

    disagreement_messages = {d["message"] for d in result.disagreements}
    notes = [n for n in dict.fromkeys(result.notes) if n not in disagreement_messages]
    if notes:
        lines.append("")
        for note in notes:
            lines.append(f"! {note}")
    if result.disagreements:
        lines.append("")
        for d in result.disagreements:
            lines.append(f"! {d['message']}")
    return "\n".join(lines)


def result_to_json(
    result: ComparisonResult,
    *,
    stratified: dict[str, StratifiedResult] | None = None,
    indent: int = 2,
) -> str:
    """Serialise a comparison (and optional strata) to JSON."""
    payload: dict[str, Any] = result.as_dict()
    if stratified:
        payload["stratified"] = {k: v.as_dict() for k, v in stratified.items()}
    return json.dumps(payload, indent=indent, default=json_default)


def json_default(obj: Any) -> Any:
    """JSON encoder hook for numpy scalars, frames, paths and non-finite floats."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return None if not np.isfinite(value) else value
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, pd.Series):
        return obj.to_dict()
    if isinstance(obj, Path):
        return str(obj)
    if np.isscalar(obj) and pd.isna(obj):
        return None
    return str(obj)


#: Backwards-compatible alias for the JSON encoder hook.
_json_default = json_default


@dataclass
class ReportPaths:
    markdown: Path | None = None
    json: Path | None = None
    summary_csv: Path | None = None
    manifest: Path | None = None


def write_report(
    result: ComparisonResult,
    outdir: str | Path,
    *,
    title: str = "Evaluation report",
    stratified: dict[str, StratifiedResult] | None = None,
    basename: str = "report",
    formats: Iterable[str] = ("markdown", "json", "csv", "manifest"),
) -> ReportPaths:
    """Write the report to ``outdir`` in the requested formats."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = ReportPaths()
    formats = set(formats)
    if "markdown" in formats:
        paths.markdown = outdir / f"{basename}.md"
        paths.markdown.write_text(
            render_markdown(result, title=title, stratified=stratified), encoding="utf-8"
        )
    if "json" in formats:
        paths.json = outdir / f"{basename}.json"
        paths.json.write_text(result_to_json(result, stratified=stratified), encoding="utf-8")
    if "csv" in formats:
        paths.summary_csv = outdir / f"{basename}_summary.csv"
        result.summary_table().to_csv(paths.summary_csv, index=False)
    if "manifest" in formats:
        paths.manifest = outdir / f"{basename}_manifest.json"
        paths.manifest.write_text(
            json.dumps(reproducibility_manifest(result), indent=2, default=json_default),
            encoding="utf-8",
        )
    return paths


def reproducibility_manifest(result: ComparisonResult) -> dict[str, Any]:
    """The hashes a second person needs to reproduce this report exactly."""
    first = next(iter(result.ordered_comparisons), None)
    manifest = first.provenance.manifest.as_dict() if first and first.provenance else {}
    return {
        "manifest": manifest,
        "scientific_fingerprints": {
            c.metric.name + "|" + c.candidate: c.provenance.scientific_fingerprint()
            for c in result.ordered_comparisons
            if c.provenance
        },
        "source": result.source.as_dict() if result.source else None,
        "inference": result.config.inference.as_dict(),
        "bootstrap": result.config.bootstrap.as_dict(),
        "analysis_status": result.config.protocol.as_dict(),
        "note": (
            "code_version identifies the package source. analysis_config_hash, "
            "source_data_hash and protocol_hash move independently of it, and of each other. "
            "report_generator_version changes when the rendering changes without the numbers "
            "changing."
        ),
    }
