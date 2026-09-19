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

from wg_eval.aggregate import apply_filters, apply_missing_policy, panel as build_panel
from wg_eval.bootstrap import cluster_bootstrap
from wg_eval.compare import compare_policies
from wg_eval.config import ConfigError, load_config
from wg_eval.dataio import load_records, write_records
from wg_eval.metrics import build_estimator, describe_registry
from wg_eval.pairing import single_policy_values
from wg_eval.report import (
    json_default,
    render_markdown,
    render_text,
    result_to_json,
    write_report,
)
from wg_eval.schema import describe_schema
from wg_eval.stratify import compare_by_stratum
from wg_eval.validate import validate_records
from wg_eval.version import __version__, code_version

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

DEFAULT_CONFIG = """\
# wg-eval analysis configuration.
# Every choice that could change a conclusion lives here and is hashed into
# the provenance of every number produced from it.
label: example analysis

# Residents are nested observations, not replicates. The unit of inference is
# the world; `resident` is rejected by the loader.
unit_of_inference: world

metrics:
  - name: mean_loss
    column: loss
    estimator: mean
    level: event
    direction: lower_is_better
  - name: cvar90_loss
    column: loss
    estimator: cvar
    level: event
    params: {alpha: 0.9, tail: upper}
    direction: lower_is_better
  - name: p90_loss
    column: loss
    estimator: quantile
    level: event
    params: {q: 0.9}
    direction: lower_is_better
  - name: success_rate
    column: mission_success
    estimator: mean
    level: resident
    direction: higher_is_better

aggregation:
  resident_to_event:
    loss: mean
    mission_success: mean
    travel_time: mean
    resource_use: sum
    responder_exposure: sum
  event_to_world:
    loss: mean
    mission_success: mean
  default_rule: mean

comparison:
  baseline: policy_a
  candidates: [policy_b]
  paired: true
  require_common_worlds: true

bootstrap:
  n_resamples: 2000
  cluster_level: world
  seed: 20260919
  confidence_level: 0.95
  method: percentile

equivalence:
  alpha: 0.05
  # A margin is the largest difference that would still be practically the same.
  # Without one, equivalence cannot be concluded -- only 'undetermined'.
  margins:
    mean_loss: 0.40
  non_inferiority: [mean_loss]

strata: [landscape, mobility, fire_regime, resource_level]

filters:
  include: {}
  exclude: {}
  exclusions: {}

missing_data:
  policy: drop_record
  require_complete_worlds: true
  max_count_imbalance: 0.2

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
    unit = "world"
    if args.config:
        cfg = load_config(args.config)
        strata = list(dict.fromkeys(strata + list(cfg.strata)))
        unit = cfg.unit_of_inference
    report = validate_records(frame, strata=strata, unit_of_inference=unit)
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
    filtered, prep = apply_filters(frame, cfg.filters)
    policies = sorted(str(p) for p in filtered["policy_id"].dropna().unique())
    selected = [m for m in cfg.metrics if not args.metric or m.name in args.metric]
    if not selected:
        raise ConfigError("no metrics selected")

    rows: list[dict[str, Any]] = []
    for metric in selected:
        cleaned, _ = apply_missing_policy(
            filtered, metric.column, cfg.missing_data, direction=metric.direction
        )
        panel_frame = build_panel(cleaned, metric, cfg.aggregation)
        estimator = build_estimator(metric.estimator, metric.params)
        for policy in policies:
            values, _clusters = single_policy_values(
                panel_frame, policy, cluster_level=cfg.cluster_level
            )
            res = cluster_bootstrap(
                values,
                estimator,
                n_resamples=cfg.bootstrap.n_resamples,
                seed=cfg.bootstrap.seed,
                confidence_level=cfg.bootstrap.confidence_level,
                method=cfg.bootstrap.method,
                cluster_level=cfg.cluster_level,
            )
            rows.append(
                {
                    "metric": metric.name,
                    "level": metric.level,
                    "policy": policy,
                    "estimate": res.estimate,
                    "ci_low": res.ci_low,
                    "ci_high": res.ci_high,
                    "se": res.standard_error,
                    "n_clusters": res.n_clusters,
                    "n_observations": int(len(values.all_values())),
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
            f"{cfg.cluster_level}s, seed {cfg.bootstrap.seed}, "
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
    print(f"wrote {paths.markdown}")
    print(f"wrote {paths.json}")
    print(f"wrote {paths.summary_csv}")
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
