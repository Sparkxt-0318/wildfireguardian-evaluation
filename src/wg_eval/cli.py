"""``wg-eval`` -- the command line interface.

    wg-eval validate-results results.parquet
    wg-eval compare          results.parquet config.yaml
    wg-eval bootstrap        results.parquet config.yaml
    wg-eval report           results.parquet config.yaml --out reports/

Plus the supporting commands ``schema``, ``metrics``, ``init-config``,
``synth`` and ``redteam``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from wg_eval.aggregate import (
    apply_filters,
    apply_missing_policy,
    apply_run_status,
    panel as build_panel,
)
from wg_eval.bootstrap import cluster_bootstrap
from wg_eval.compare import compare_policies
from wg_eval.config import ConfigError, load_config
from wg_eval.dataio import load_records, write_records
from wg_eval.metrics import build_estimator, describe_registry
from wg_eval.pairing import single_policy_values
from wg_eval.report import (
    audit_wording,
    json_default,
    render_markdown,
    render_text,
    reproducibility_manifest,
    result_to_json,
    write_report,
)
from wg_eval.schema import describe_schema
from wg_eval.stratify import allocation_ledger, compare_by_stratum
from wg_eval.validate import validate_for_config, validate_records
from wg_eval.version import __version__, code_version

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

DEFAULT_CONFIG = """\
# wg-eval analysis configuration.
# Every choice that could change a conclusion lives here and is hashed into
# the provenance of every number produced from it.
label: example analysis

# Preregistration is never inferred from the existence of this file.
# Set `preregistered` only with a protocol hash/commit that someone else can check.
analysis_status: exploratory
# protocol:
#   hash: sha256:...
#   commit: abc1234
#   timestamp: 2026-09-01T00:00:00Z

# The independent unit is DECLARED and then checked against the records.
# Observations are nested, never replicates. `resident_id` is refused here.
inference:
  primary_unit: world_id
  nested_units: [event_id, resident_id]

metrics:
  # Exactly one metric may be primary. It is reported first.
  - name: mean_loss
    column: loss
    estimator: mean
    level: event_id
    direction: lower_is_better
    role: primary
    bounds: {lower: 0.0}
  - name: cvar90_loss
    column: loss
    estimator: cvar
    level: event_id
    # `tail: harmful` resolves against `direction`, so the harmful tail is
    # never chosen by accident. Set `upper`/`lower` to override deliberately.
    tail: harmful
    params: {alpha: 0.9}
    direction: lower_is_better
    role: secondary
  - name: p90_loss
    column: loss
    estimator: quantile
    level: event_id
    params: {q: 0.9}
    direction: lower_is_better
    role: exploratory
  - name: success_rate
    column: mission_success
    estimator: mean
    level: resident_id
    direction: higher_is_better
    role: secondary
    bounds: {lower: 0.0, upper: 1.0}

aggregation:
  resident_id->event_id:
    loss: mean
    mission_success: mean
    travel_time: mean
    resource_use: sum
    responder_exposure: sum
  event_id->world_id:
    loss: mean
    mission_success: mean
  default_rule: mean

comparison:
  baseline: policy_a
  candidates: [policy_b]
  paired: true
  require_common_units: true

bootstrap:
  n_resamples: 2000
  seed: 20260919
  confidence_level: 0.95
  # percentile | basic | bca. They are not interchangeable; see
  # docs/STATISTICAL_PROTOCOL.md section 5.
  method: percentile
  # Two-stage resampling estimates a different quantity. Off unless justified.
  hierarchical: false

equivalence:
  alpha: 0.05
  # A margin is the largest difference that would still be negligible.
  # It may be asymmetric, and it must record where it came from.
  margins:
    mean_loss:
      lower: 0.40
      upper: 0.40
      scale: absolute
      source: "declared before analysis; replace with your own justification"
  non_inferiority: [mean_loss]

multiplicity:
  # Families are the declared analysis roles. Primary is never corrected.
  secondary_correction: holm
  exploratory_correction: none

# Declared unit-level design variables. These names are examples; any
# unit-level column may be declared, and outcomes may not.
strata: [difficulty, scale, regime, capacity]

filters:
  include: {}
  exclude: {}
  exclusions: {}

missing_data:
  policy: drop_record
  require_complete_units: true
  max_count_imbalance: 0.2
  status_column: run_status
  status_handling:
    completed: completed
    crashed: failure
    timeout: failure
    infeasible: excluded_documented
    not_evaluated: missing
  assumed_mechanism: unknown
  mechanism_justification: ""

failure_column: failure_reason
"""


def _load(results: str, config: str | None):
    frame, source = load_records(results)
    cfg = load_config(config) if config else None
    return frame, source, cfg


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=json_default))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    frame, source = load_records(args.results)
    strata: list[str] = list(args.stratum or [])
    cfg = None
    if args.config:
        cfg = load_config(args.config)
        strata = list(dict.fromkeys(strata + list(cfg.strata)))
    report = (
        validate_for_config(frame, cfg)
        if cfg is not None
        else validate_records(frame, strata=strata)
    )
    if args.json:
        _emit_json({"source": source.as_dict(), **report.as_dict()})
    else:
        print(f"source: {source.path}")
        print(f"checksum: {source.checksum}")
        print(report.to_text())
    if report.errors:
        return EXIT_FAILED
    if args.strict and report.warnings:
        print("\nstrict mode: warnings are treated as failures", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    frame, source, cfg = _load(args.results, args.config)
    if cfg is None:  # pragma: no cover - argparse enforces this
        raise ConfigError("compare requires a config file")
    result = compare_policies(
        frame, cfg, source=source, validate=not args.no_validate,
        metrics=args.metric or None,
    )
    stratified = None
    if args.stratify:
        stratified = {
            column: compare_by_stratum(frame, cfg, column, source=source)
            for column in args.stratify
        }
    if args.json:
        print(result_to_json(result, stratified=stratified))
    elif args.markdown:
        print(render_markdown(result, title=args.title, stratified=stratified))
    else:
        print(render_text(result))
        if stratified:
            for column, strat in stratified.items():
                print(f"\nby {column}:")
                table = strat.summary_table()
                if not table.empty:
                    print(table.to_string(index=False))
                for note in strat.notes:
                    print(f"! {note}")
    return EXIT_OK


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Per-policy bootstrap estimates -- no contrast, no verdict."""
    frame, source, cfg = _load(args.results, args.config)
    if cfg is None:  # pragma: no cover
        raise ConfigError("bootstrap requires a config file")
    filtered, prep = apply_filters(frame, cfg.filters, unit=cfg.inference.primary_unit)
    filtered, status_info = apply_run_status(filtered, cfg.missing_data)
    prep.run_status = {"applied": status_info}
    policies = sorted(str(p) for p in filtered["policy_id"].dropna().unique())
    selected = [m for m in cfg.metrics if not args.metric or m.name in args.metric]
    if not selected:
        raise ConfigError("no metrics selected")

    rows: list[dict[str, Any]] = []
    for metric in selected:
        cleaned, _ = apply_missing_policy(
            filtered, metric.column, cfg.missing_data, direction=metric.direction
        )
        panel_frame = build_panel(cleaned, metric, cfg.aggregation, cfg.inference)
        estimator = build_estimator(metric.estimator, metric.resolved_params())
        for policy in policies:
            values, _clusters = single_policy_values(panel_frame, policy, cfg.inference)
            res = cluster_bootstrap(
                values,
                estimator,
                n_resamples=cfg.bootstrap.n_resamples,
                seed=cfg.bootstrap.seed,
                confidence_level=cfg.bootstrap.confidence_level,
                method=cfg.bootstrap.method,
                cluster_level=cfg.inference.primary_unit,
                estimator_name=metric.estimator,
                bounds=metric.bounds,
            )
            rows.append(
                {
                    "metric": metric.name,
                    "role": metric.role,
                    "level": metric.level,
                    "policy": policy,
                    "estimate": res.estimate,
                    "ci_low": res.ci_low,
                    "ci_high": res.ci_high,
                    "se": res.standard_error,
                    "n_units": res.n_clusters,
                    "n_observations": int(len(values.all_values())),
                    "credible": res.credibility.get("credible", True),
                }
            )
    table = pd.DataFrame(rows)
    if args.json:
        _emit_json(
            {
                "source": source.as_dict(),
                "bootstrap": cfg.bootstrap.as_dict(),
                "code_version": code_version(),
                "filters": prep.as_dict(),
                "results": table.to_dict(orient="records"),
            }
        )
    else:
        print(
            f"bootstrap: {cfg.bootstrap.n_resamples} resamples of whole "
            f"{cfg.inference.primary_unit}s, seed {cfg.bootstrap.seed}, "
            f"{cfg.bootstrap.confidence_level:.0%} {cfg.bootstrap.method} intervals"
        )
        print(f"source checksum: {source.checksum}\n")
        print(table.to_string(index=False))
        print(
            "\nThese are marginal, per-policy intervals. They are wider than the paired "
            "difference interval and must not be eyeballed for overlap to judge a "
            "difference -- use `wg-eval compare`."
        )
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    frame, source, cfg = _load(args.results, args.config)
    if cfg is None:  # pragma: no cover
        raise ConfigError("report requires a config file")
    result = compare_policies(frame, cfg, source=source, validate=not args.no_validate)
    columns = args.stratify if args.stratify is not None else list(cfg.strata)
    stratified = {
        column: compare_by_stratum(frame, cfg, column, source=source)
        for column in columns
        if column in frame.columns
    }
    paths = write_report(
        result,
        args.out,
        title=args.title,
        stratified=stratified or None,
        basename=args.basename,
    )
    for path in (paths.markdown, paths.json, paths.summary_csv, paths.manifest):
        if path is not None:
            print(f"wrote {path}")

    manifest = reproducibility_manifest(result)["manifest"]
    print("\nreproducibility manifest:")
    for key, value in manifest.items():
        if key != "environment":
            print(f"  {key}: {value}")

    # Generated prose must not make claims the library is not entitled to make.
    findings = audit_wording(paths.markdown.read_text(encoding="utf-8")) if paths.markdown else []
    if findings:
        print("\nWORDING AUDIT FAILED -- the generated report makes unsupported claims:",
              file=sys.stderr)
        for finding in findings[:10]:
            print(f"  {finding['phrase']!r}: {finding['guidance']}", file=sys.stderr)
            print(f"    ...{finding['context']}...", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def cmd_ledger(args: argparse.Namespace) -> int:
    """What each policy was actually given, before any comparison."""
    frame, source, cfg = _load(args.results, args.config)
    if cfg is None:  # pragma: no cover
        raise ConfigError("ledger requires a config file")
    ledger = allocation_ledger(frame, cfg, columns=cfg.strata)
    if args.json:
        _emit_json({"source": source.as_dict(), "ledger": ledger})
        return EXIT_OK
    print(f"unit of inference: {ledger['unit']}   hierarchy: {' > '.join(ledger['hierarchy'])}")
    print(f"units observed: {ledger['n_units_total']}   shared by every policy: "
          f"{ledger['n_units_shared']}\n")
    print(pd.DataFrame(ledger["per_policy"]).to_string(index=False))
    if ledger["run_status_counts"]:
        print("\nrun status:")
        print(pd.DataFrame(ledger["run_status_counts"]).fillna(0).astype(int).to_string())
    for column, block in ledger["strata"].items():
        print(f"\nstratum `{column}` composition shift (all units -> shared units):")
        for level, shift in block["composition_shift"].items():
            print(f"  {level}: {shift:+.1%}")
    print(f"\n{ledger['note']}")
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    print(describe_schema())
    print()
    print(
        "Residents are nested inside events, events inside worlds. The default unit of\n"
        "inference is the world. Extra columns are carried through and may be declared\n"
        "as strata."
    )
    return EXIT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    print(describe_registry())
    return EXIT_OK


def cmd_init_config(args: argparse.Namespace) -> int:
    path = Path(args.out)
    if path.exists() and not args.force:
        print(f"{path} already exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_USAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    print(f"wrote {path}")
    return EXIT_OK


def cmd_synth(args: argparse.Namespace) -> int:
    from wg_eval.synth.redteam import SCENARIOS, build_scenario

    if args.scenario not in SCENARIOS:
        print(
            f"unknown scenario {args.scenario!r}; known: {', '.join(SCENARIOS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    frame, truth = build_scenario(args.scenario, seed=args.seed)
    source = write_records(frame, args.out)
    print(f"wrote {source.path} ({source.n_rows} rows, {source.checksum})")
    truth_path = Path(args.out).with_suffix(".truth.json")
    truth_path.write_text(
        json.dumps({"scenario": args.scenario, "seed": args.seed, "truth": truth},
                   indent=2, default=json_default),
        encoding="utf-8",
    )
    print(f"wrote {truth_path}")
    return EXIT_OK


def cmd_redteam(args: argparse.Namespace) -> int:
    from wg_eval.synth.redteam import SCENARIOS, run_scenario

    keys = args.scenario or list(SCENARIOS)
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE

    runs = []
    for key in keys:
        run = run_scenario(key, seed=args.seed)
        runs.append(run)
        if not args.json:
            print("=" * 78)
            print(run.to_text())
            print(
                f"\n  trap reproduced: {run.trap_reproduced}   "
                f"defence worked: {run.defence_worked}   -> "
                f"{'PASS' if run.passed else 'FAIL'}\n"
            )
    if args.json:
        _emit_json({"seed": args.seed, "runs": [r.as_dict() for r in runs]})
    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        md = outdir / "redteam.md"
        md.write_text(
            "# Red-team scenarios\n\n"
            + f"seed `{args.seed}` | {code_version()}\n\n"
            + "\n\n".join(r.to_text() for r in runs)
            + "\n",
            encoding="utf-8",
        )
        (outdir / "redteam.json").write_text(
            json.dumps({"seed": args.seed, "runs": [r.as_dict() for r in runs]},
                       indent=2, default=json_default),
            encoding="utf-8",
        )
        print(f"wrote {md}")
        print(f"wrote {outdir / 'redteam.json'}")
    return EXIT_OK if all(r.passed for r in runs) else EXIT_FAILED


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wg-eval",
        description=(
            "Paired, event-level, pseudoreplication-resistant evaluation of nested "
            "experiment records."
        ),
    )
    parser.add_argument("--version", action="version", version=f"wg-eval {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-results", help="check a record table against the schema and nesting rules")
    p.add_argument("results")
    p.add_argument("--config", help="analysis config, so declared strata are checked too")
    p.add_argument("--stratum", action="append", help="extra stratum column to check (repeatable)")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("compare", help="paired policy comparison with verdicts")
    p.add_argument("results")
    p.add_argument("config")
    p.add_argument("--metric", action="append", help="restrict to this metric (repeatable)")
    p.add_argument("--stratify", action="append", help="also compare within this stratum column")
    p.add_argument("--no-validate", action="store_true", help="skip schema validation")
    p.add_argument("--json", action="store_true")
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--title", default="Evaluation report")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("bootstrap", help="per-policy cluster-bootstrap estimates")
    p.add_argument("results")
    p.add_argument("config")
    p.add_argument("--metric", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("report", help="write a full reproducible report")
    p.add_argument("results")
    p.add_argument("config")
    p.add_argument("--out", default="reports", help="output directory")
    p.add_argument("--basename", default="report")
    p.add_argument("--stratify", action="append", help="stratum columns (default: config `strata`)")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--title", default="Evaluation report")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("ledger", help="print the allocation and run-status ledger")
    p.add_argument("results")
    p.add_argument("config")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("schema", help="print the generic record schema")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("metrics", help="print the estimator registry")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("init-config", help="write a commented starter config")
    p.add_argument("--out", default="analysis.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_config)

    p = sub.add_parser("synth", help="generate a synthetic fixture from a red-team scenario")
    p.add_argument("--scenario", default="resident_bootstrap_false_precision")
    p.add_argument("--seed", type=int, default=20260919)
    p.add_argument("--out", default="fixtures/records.parquet")
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("redteam", help="run the adversarial scenarios")
    p.add_argument("--scenario", action="append", help="run only this scenario (repeatable)")
    p.add_argument("--seed", type=int, default=20260919)
    p.add_argument("--out", help="also write redteam.md / redteam.json here")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_redteam)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"wg-eval: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
