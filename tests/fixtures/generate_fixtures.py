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
    PolicyBehaviour,
    WorldModel,
    drop_records,
    generate_experiment,
    hardest_worlds,
)

HERE = Path(__file__).resolve().parent
SEED = 20260919

STRATA = {
    "landscape": ["flat", "steep"],
    "mobility": ["high", "low"],
    "fire_regime": ["surface", "crown"],
    "resource_level": ["scarce", "ample"],
}

ANALYSIS_YAML = """\
# Fixture analysis config. See docs/STATISTICAL_PROTOCOL.md for what each
# choice commits you to.
label: fixture analysis
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
  n_resamples: 1000
  cluster_level: world
  seed: 20260919
  confidence_level: 0.95
  method: percentile

equivalence:
  alpha: 0.05
  margins:
    mean_loss: 0.40
  non_inferiority: [mean_loss]

strata: [landscape, mobility, fire_regime, resource_level]

missing_data:
  policy: drop_record
  require_complete_worlds: true
  max_count_imbalance: 0.2

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
            n_worlds=24, events_per_world=3, residents_per_event=10,
            world_sd=3.5, strata=STRATA,
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
            n_worlds=24, events_per_world=2, residents_per_event=10,
            world_sd=5.0, strata=STRATA,
        ),
        seed=SEED + 1,
    )
    dropped = hardest_worlds(full[full["policy_id"] == "policy_a"], 8)
    return drop_records(full, policy="policy_b", worlds=dropped), dropped


def invalid_records() -> pd.DataFrame:
    """Records that must fail validation: duplicate keys and broken nesting."""
    base = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.3)],
        WorldModel(n_worlds=6, events_per_world=2, residents_per_event=3),
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

    (HERE / "analysis.yaml").write_text(ANALYSIS_YAML, encoding="utf-8")
    (HERE / "MANIFEST.json").write_text(
        json.dumps({"seed": SEED, "fixtures": manifest}, indent=2), encoding="utf-8"
    )
    for name, info in manifest.items():
        print(f"{name}: {info['n_rows']} rows  {info['checksum']}")


if __name__ == "__main__":
    main()
