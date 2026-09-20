"""Shared fixtures.

The fixtures here are generated, not checked in as data, so that every test
knows the truth it is testing against.
"""

from __future__ import annotations

import pandas as pd
import pytest

from wg_eval.config import config_from_mapping
from wg_eval.synth.generators import (
    DEFAULT_STRATA as STRATA,
    PolicyBehaviour,
    WorldModel,
    generate_experiment,
)


@pytest.fixture(scope="session")
def records() -> pd.DataFrame:
    """A clean, balanced two-policy experiment with a true loss shift of +0.8."""
    return generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.6),
            PolicyBehaviour("policy_b", loss_shift=0.8, interaction_sd=0.6),
        ],
        WorldModel(
            n_units=32,
            events_per_unit=3,
            observations_per_event=12,
            unit_sd=3.0,
            strata=STRATA,
        ),
        seed=11,
    )


@pytest.fixture(scope="session")
def config_mapping() -> dict:
    return {
        "label": "test",
        "inference": {"primary_unit": "world_id", "nested_units": ["event_id", "resident_id"]},
        "metrics": [
            {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
             "direction": "lower_is_better", "role": "primary"},
            {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event_id",
             "params": {"alpha": 0.9}, "direction": "lower_is_better", "role": "secondary"},
            {"name": "success_rate", "column": "mission_success", "estimator": "mean",
             "level": "resident_id", "direction": "higher_is_better", "role": "secondary",
             "bounds": {"lower": 0.0, "upper": 1.0}},
        ],
        "aggregation": {
            "resident_id->event_id": {"loss": "mean", "mission_success": "mean",
                                      "resource_use": "sum"},
            "event_id->world_id": {"loss": "mean"},
        },
        "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
        "bootstrap": {"n_resamples": 400, "seed": 7,
                      "confidence_level": 0.95, "method": "percentile"},
        "equivalence": {"margins": {"mean_loss": {"lower": 2.0, "upper": 2.0,
                                                  "source": "test fixture"}},
                        "alpha": 0.05, "non_inferiority": ["mean_loss"]},
        "strata": list(STRATA),
        "missing_data": {"policy": "drop_record"},
    }


@pytest.fixture
def config(config_mapping):
    return config_from_mapping(config_mapping)
