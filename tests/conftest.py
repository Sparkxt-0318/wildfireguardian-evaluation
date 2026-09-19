"""Shared fixtures.

The fixtures here are generated, not checked in as data, so that every test
knows the truth it is testing against.
"""

from __future__ import annotations

import pandas as pd
import pytest

from wg_eval.config import config_from_mapping
from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment

STRATA = {
    "landscape": ["flat", "steep"],
    "mobility": ["high", "low"],
    "fire_regime": ["surface", "crown"],
    "resource_level": ["scarce", "ample"],
}


@pytest.fixture(scope="session")
def records() -> pd.DataFrame:
    """A clean, balanced two-policy experiment with a true loss shift of +0.8."""
    return generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.6),
            PolicyBehaviour("policy_b", loss_shift=0.8, interaction_sd=0.6),
        ],
        WorldModel(
            n_worlds=32,
            events_per_world=3,
            residents_per_event=12,
            world_sd=3.0,
            strata=STRATA,
        ),
        seed=11,
    )


@pytest.fixture(scope="session")
def config_mapping() -> dict:
    return {
        "label": "test",
        "unit_of_inference": "world",
        "metrics": [
            {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event",
             "direction": "lower_is_better"},
            {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event",
             "params": {"alpha": 0.9}, "direction": "lower_is_better"},
            {"name": "success_rate", "column": "mission_success", "estimator": "mean",
             "level": "resident", "direction": "higher_is_better"},
        ],
        "aggregation": {
            "resident_to_event": {"loss": "mean", "mission_success": "mean", "resource_use": "sum"},
            "event_to_world": {"loss": "mean"},
        },
        "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
        "bootstrap": {"n_resamples": 400, "cluster_level": "world", "seed": 7,
                      "confidence_level": 0.95, "method": "percentile"},
        "equivalence": {"margins": {"mean_loss": 2.0}, "alpha": 0.05,
                        "non_inferiority": ["mean_loss"]},
        "strata": list(STRATA),
        "missing_data": {"policy": "drop_record"},
    }


@pytest.fixture
def config(config_mapping):
    return config_from_mapping(config_mapping)
