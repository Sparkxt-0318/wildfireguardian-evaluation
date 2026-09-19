"""Reproducible report rendering.

A report is not a summary; it is the audit trail.  Anything that could change
the conclusion appears in it: the cluster count, the exclusions, the margins,
the seed and the checksum of the file the numbers came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from wg_eval.compare import ComparisonResult, MetricComparison
from wg_eval.stratify import StratifiedResult
from wg_eval.version import code_version


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
    """One metric's verdict, phrased so it cannot be misread as 'no difference'."""
    return comparison.verdict.sentence


def render_markdown(
    result: ComparisonResult,
    *,
    title: str = "Evaluation report",
    stratified: dict[str, StratifiedResult] | None = None,
    include_provenance: bool = True,
) -> str:
    """Render a full comparison as Markdown."""
    lines: list[str] = [f"# {title}", ""]

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
    cfg = result.config
    lines += [
        f"- **Unit of inference**: `{cfg.unit_of_inference}`",
        f"- **Resampling**: {cfg.bootstrap.n_resamples} bootstrap resamples of whole "
        f"`{cfg.bootstrap.cluster_level}`s, seed `{cfg.bootstrap.seed}`, "
        f"method `{cfg.bootstrap.method}`, {cfg.bootstrap.confidence_level:.0%} intervals",
        f"- **Pairing**: {'paired on shared clusters' if cfg.comparison.require_common_worlds else 'UNPAIRED (policies on different cluster sets)'}",
        f"- **Missing data**: `{cfg.missing_data.policy}`",
        f"- **Code**: {code_version()}",
        "",
    ]

    if result.validation is not None:
        summary = result.validation.summary
        lines += ["### Design", ""]
        lines += [
            f"- worlds: **{summary.get('n_worlds', 'n/a')}**, events: "
            f"{summary.get('n_events', 'n/a')}, observations: {summary.get('n_rows', 'n/a')}",
            f"- observations per world: {summary.get('observations_per_world', 'n/a')} "
            "(these are nested, not independent)",
            f"- policies: {', '.join(summary.get('policies', []))}",
            f"- worlds carrying every policy: {summary.get('n_common_worlds', 'n/a')}",
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
                f"removed {f['rows_removed']} rows, {f['worlds_removed']} worlds"
            )
        for e in prep.exclusions_applied:
            lines.append(
                f"- exclusion `{e['name']}` (`{e['expression']}`): "
                f"removed {e['rows_removed']} rows, {e['worlds_removed']} worlds"
            )
    else:
        lines.append("- none applied")
    lines.append("")

    lines += ["## 2. Results", ""]
    rows = []
    for c in result.comparisons:
        margin = c.equivalence.margin if c.equivalence else None
        rows.append(
            [
                c.metric.name,
                c.metric.level,
                f"{c.candidate} vs {c.baseline}",
                _fmt(c.baseline_result.estimate),
                _fmt(c.candidate_result.estimate),
                _fmt(c.difference.estimate),
                f"[{_fmt(c.difference.ci_low)}, {_fmt(c.difference.ci_high)}]",
                str(c.difference.n_clusters),
                _fmt(margin) if margin is not None else "none",
                c.verdict.label,
            ]
        )
    lines.append(
        _table(
            rows,
            [
                "metric", "level", "contrast", "baseline", "candidate", "difference",
                f"{cfg.bootstrap.confidence_level:.0%} CI", "clusters", "margin", "verdict",
            ],
        )
    )
    lines.append("")

    lines += ["### Verdicts", ""]
    for c in result.comparisons:
        lines.append(f"- **{c.metric.name}** — {verdict_line(c)}")
        if c.equivalence:
            lines.append(f"  - equivalence: {c.equivalence.interpretation}")
            for note in c.equivalence.notes:
                lines.append(f"  - {note}")
        if c.non_inferiority_result:
            lines.append(f"  - non-inferiority: {c.non_inferiority_result.interpretation}")
        for note in c.notes:
            lines.append(f"  - note: {note}")
    lines.append("")

    if result.disagreements:
        lines += ["## 3. Disagreements between metrics", ""]
        for d in result.disagreements:
            lines.append(f"- {d['message']}")
        lines.append("")
    else:
        lines += ["## 3. Disagreements between metrics", "", "- none detected among resolved verdicts", ""]

    lines += ["## 4. Design diagnostics", ""]
    diag_rows = []
    for c in result.comparisons:
        for policy, deff in c.diagnostics.get("design_effect", {}).items():
            if not deff.get("available"):
                continue
            diag_rows.append(
                [
                    c.metric.name,
                    policy,
                    _fmt(deff["icc"], 3),
                    _fmt(deff["mean_cluster_size"], 3),
                    _fmt(deff["design_effect"], 3),
                    _fmt(deff["effective_sample_size"], 4),
                    str(deff["n_observations"]),
                ]
            )
    if diag_rows:
        lines.append(
            _table(
                diag_rows,
                ["metric", "policy", "ICC", "mean cluster size", "design effect",
                 "effective n", "observations"],
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
        lines += ["- one observation per cluster; the design effect is 1 by construction", ""]

    if result.failures is not None:
        lines += ["## 5. Failure analysis", "", "```", result.failures.to_text(), "```", ""]
        if result.failures.shifts is not None and not result.failures.shifts.empty:
            shift_rows = [
                [
                    str(r["failure_reason"]),
                    _fmt(r["baseline_rate"], 3),
                    _fmt(r["candidate_rate"], 3),
                    _fmt(r["mean_paired_shift"], 3),
                    str(int(r["n_clusters"])),
                ]
                for _, r in result.failures.shifts.iterrows()
            ]
            lines += [
                "Paired per-world shift in each failure mode (candidate − baseline):",
                "",
                _table(shift_rows, ["reason", "baseline rate", "candidate rate", "paired shift", "worlds"]),
                "",
            ]

    if stratified:
        lines += ["## 6. Stratified results", ""]
        for column, strat in stratified.items():
            lines += [f"### By `{column}`", ""]
            table = strat.summary_table()
            if table.empty:
                lines += ["- no stratum had enough clusters to analyse", ""]
            else:
                srows = [
                    [
                        str(r[column]),
                        str(r["metric"]),
                        f"{r['candidate']} vs {r['baseline']}",
                        _fmt(r["difference"]),
                        f"[{_fmt(r['ci_low'])}, {_fmt(r['ci_high'])}]",
                        str(int(r["n_clusters"])),
                        str(r["verdict"]),
                    ]
                    for _, r in table.iterrows()
                ]
                lines.append(
                    _table(srows, [column, "metric", "contrast", "difference", "CI", "clusters", "verdict"])
                )
                lines.append("")
            if strat.allocation is not None and not strat.allocation.empty:
                arows = [
                    [str(r["policy_id"]), str(r["stratum_value"]), str(int(r["n_worlds"])),
                     f"{r['share_of_policy_worlds']:.0%}"]
                    for _, r in strat.allocation.iterrows()
                ]
                lines += [
                    "Allocation ledger (how many worlds of each stratum each policy actually ran):",
                    "",
                    _table(arows, ["policy", column, "worlds", "share of that policy's worlds"]),
                    "",
                ]
            for note in strat.notes:
                lines.append(f"- {note}")
            for label, reason in sorted(strat.skipped.items()):
                lines.append(f"- stratum `{label}` skipped: {reason}")
            lines.append("")

    if result.notes:
        lines += ["## 7. Notes carried from the analysis", ""]
        for note in dict.fromkeys(result.notes):
            lines.append(f"- {note}")
        lines.append("")

    if include_provenance:
        lines += ["## 8. Provenance", ""]
        for c in result.comparisons:
            if c.provenance is None:
                continue
            lines += [
                f"<details><summary><code>{c.metric.name}</code> — "
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
        "Reading rules for this report:",
        "",
        "1. An interval that includes zero means **undetermined**, never \"no difference\". "
        "Only a TOST against a declared margin can support equivalence.",
        "2. Every interval here comes from resampling whole "
        f"`{cfg.bootstrap.cluster_level}`s. Observation counts are reported for context and "
        "are not the sample size.",
        "3. Where metrics disagree, the disagreement is the finding. Do not pick the "
        "flattering one after the fact.",
        "",
    ]
    return "\n".join(lines)


def render_text(result: ComparisonResult) -> str:
    """Compact console rendering of a comparison."""
    lines = [
        f"unit of inference: {result.config.unit_of_inference}  |  "
        f"bootstrap: {result.config.bootstrap.n_resamples} x "
        f"{result.config.bootstrap.cluster_level}, seed {result.config.bootstrap.seed}",
        "",
    ]
    table = result.summary_table()
    if not table.empty:
        with pd.option_context("display.width", 200, "display.max_columns", 40):
            lines.append(table.to_string(index=False))
    lines.append("")
    for c in result.comparisons:
        lines.append(f"* {verdict_line(c)}")
        if c.equivalence:
            lines.append(f"    equivalence: {c.equivalence.interpretation}")
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
    if pd.isna(obj) if np.isscalar(obj) else False:
        return None
    return str(obj)


@dataclass
class ReportPaths:
    markdown: Path | None = None
    json: Path | None = None
    summary_csv: Path | None = None


def write_report(
    result: ComparisonResult,
    outdir: str | Path,
    *,
    title: str = "Evaluation report",
    stratified: dict[str, StratifiedResult] | None = None,
    basename: str = "report",
    formats: Iterable[str] = ("markdown", "json", "csv"),
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
    return paths


#: Backwards-compatible alias for the JSON encoder hook.
_json_default = json_default
