#!/usr/bin/env python3
"""Regenerate the checked-in test fixtures.

    python tests/fixtures/generate_fixtures.py

The fixtures are small, deterministic and committed so that a reader can run
the CLI against real files without generating anything first.  Every one of
them is reproducible from this script, and ``tests/test_fixtures.py`` checks
that the committed bytes still match what the script produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from wg_eval.dataio import write_records
from wg_eval.synth.generators import (
    DEFAULT_STRATA as STRATA,
    PolicyBehaviour,
    WorldModel,
    drop_records,
    generate_experiment,
    hardest_units,
    mark_status,
)

HERE = Path(__file__).resolve().parent
SEED = 20260919

ANALYSIS_YAML = """\
# Fixture analysis config. See docs/STATISTICAL_PROTOCOL.md for what each
# choice commits you to, and docs/ESTIMANDS.md for what it makes the numbers mean.
label: fixture analysis
analysis_status: exploratory

inference:
  primary_unit: world_id
  nested_units: [event_id, resident_id]

metrics:
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
    tail: harmful
    params: {alpha: 0.9}
    direction: lower_is_better
    role: secondary
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
  n_resamples: 1000
  seed: 20260919
  confidence_level: 0.95
  method: percentile
  hierarchical: false

equivalence:
  alpha: 0.05
  margins:
    mean_loss:
      lower: 0.40
      upper: 0.40
      scale: absolute
      source: "fixture margin, fixed in generate_fixtures.py before any data existed"
  non_inferiority: [mean_loss]

multiplicity:
  secondary_correction: holm
  exploratory_correction: none

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

failure_column: failure_reason
"""


def paired_balanced() -> pd.DataFrame:
    """A clean, balanced two-policy experiment. True mean loss shift: +0.60."""
    return generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.8),
            PolicyBehaviour("policy_b", loss_shift=0.60, interaction_sd=0.8),
        ],
        WorldModel(
            n_units=24, events_per_unit=3, observations_per_event=10,
            unit_sd=3.5, strata=STRATA,
        ),
        seed=SEED,
    )


def missing_worlds() -> tuple[pd.DataFrame, list[str]]:
    """policy_b is absent from the 8 hardest worlds. True shift: +1.20."""
    full = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.5),
            PolicyBehaviour("policy_b", loss_shift=1.20, interaction_sd=0.5),
        ],
        WorldModel(
            n_units=24, events_per_unit=2, observations_per_event=10,
            unit_sd=5.0, strata=STRATA,
        ),
        seed=SEED + 1,
    )
    dropped = hardest_units(full[full["policy_id"] == "policy_a"], 8)
    return drop_records(full, policy="policy_b", units=dropped), dropped


def failed_runs() -> tuple[pd.DataFrame, list[str]]:
    """policy_b's runs crash on the 6 hardest units, recorded rather than absent."""
    full = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.4),
            PolicyBehaviour("policy_b", loss_shift=0.8, interaction_sd=0.4),
        ],
        WorldModel(n_units=20, events_per_unit=2, observations_per_event=8,
                   unit_sd=4.0, strata=STRATA),
        seed=SEED + 3,
    )
    crashed = hardest_units(full[full["policy_id"] == "policy_b"], 6)
    return mark_status(full, policy="policy_b", units=crashed, status="crashed"), crashed


def invalid_records() -> pd.DataFrame:
    """Records that must fail validation: duplicate keys and broken nesting."""
    base = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.3)],
        WorldModel(n_units=6, events_per_unit=2, observations_per_event=3),
        seed=SEED + 2,
    )
    broken = pd.concat([base, base.iloc[:2]], ignore_index=True)       # duplicate keys
    shared_event = broken.loc[0, "event_id"]
    other = broken.index[broken["world_id"] != broken.loc[0, "world_id"]][0]
    broken.loc[other, "event_id"] = shared_event                        # broken nesting
    broken.loc[broken.index[-1], "mission_success"] = 5                 # non-binary outcome
    return broken


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}

    frame = paired_balanced()
    source = write_records(frame, HERE / "paired_balanced.parquet")
    manifest["paired_balanced.parquet"] = {
        "checksum": source.checksum,
        "n_rows": source.n_rows,
        "truth": {"true_mean_loss_difference": 0.60, "n_worlds": 24},
        "description": "clean, balanced, fully paired",
    }

    frame, dropped = missing_worlds()
    source = write_records(frame, HERE / "missing_worlds.csv")
    manifest["missing_worlds.csv"] = {
        "checksum": source.checksum,
        "n_rows": source.n_rows,
        "truth": {
            "true_mean_loss_difference": 1.20,
            "n_worlds": 24,
            "policy_b_missing_from": dropped,
        },
        "description": "policy_b absent from the hardest worlds; unpaired analysis reverses",
    }

    frame = invalid_records()
    source = write_records(frame, HERE / "invalid_records.csv")
    manifest["invalid_records.csv"] = {
        "checksum": source.checksum,
        "n_rows": source.n_rows,
        "expected_validation_errors": [
            "duplicate_records", "broken_nesting", "non_binary_outcome",
        ],
        "description": "must fail validation",
    }

    frame, crashed = failed_runs()
    source = write_records(frame, HERE / "failed_runs.parquet")
    manifest["failed_runs.parquet"] = {
        "checksum": source.checksum,
        "n_rows": source.n_rows,
        "truth": {
            "true_mean_loss_difference": 0.80,
            "n_units": 20,
            "crashed_units": crashed,
            "mechanism": "MNAR",
        },
        "description": "policy_b crashes on the hardest units; rows present with null outcomes",
    }

    (HERE / "analysis.yaml").write_text(ANALYSIS_YAML, encoding="utf-8")
    (HERE / "MANIFEST.json").write_text(
        json.dumps({"seed": SEED, "fixtures": manifest}, indent=2), encoding="utf-8"
    )
    for name, info in manifest.items():
        print(f"{name}: {info['n_rows']} rows  {info['checksum']}")


if __name__ == "__main__":
    main()
