"""The adversarial suite: each scenario must fool the wrong analysis and not the right one."""

from __future__ import annotations

import numpy as np
import pytest

from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment
from wg_eval.synth.redteam import (
    SCENARIOS,
    build_scenario,
    coverage_study,
    list_scenarios,
    run_scenario,
)

REQUIRED = [
    "resident_bootstrap_false_precision",
    "world_bootstrap_correct",
    "tail_risk_disagreement",
    "practical_equivalence",
    "missing_worlds_reverse_ranking",
    "easier_worlds_confound",
]


def test_every_required_scenario_exists():
    assert set(REQUIRED) <= set(SCENARIOS)
    assert len(list_scenarios()) == len(SCENARIOS)


def test_unknown_scenario_names_are_rejected():
    with pytest.raises(KeyError):
        build_scenario("does_not_exist")
    with pytest.raises(KeyError):
        run_scenario("does_not_exist")


@pytest.mark.parametrize("key", REQUIRED)
def test_scenario_records_are_generated_with_a_stated_truth(key):
    frame, truth = build_scenario(key, seed=20260919)
    assert len(frame) > 0
    assert truth
    assert {"world_id", "event_id", "policy_id", "resident_id"} <= set(frame.columns)


@pytest.mark.slow
@pytest.mark.parametrize("key", REQUIRED)
def test_scenario_traps_fire_and_defences_hold(key):
    run = run_scenario(key, seed=20260919)
    assert run.trap_reproduced, f"{key}: the misleading analysis did not reproduce the trap"
    assert run.defence_worked, f"{key}: the correct analysis failed to recover the truth"
    assert run.findings
    assert run.to_text()


@pytest.mark.slow
def test_resident_bootstrap_is_measurably_over_confident():
    study = coverage_study(n_trials=40, n_resamples=250, n_worlds=20,
                           events_per_world=2, residents_per_event=30, seed=4)
    assert study["resident_level"]["coverage"] < 0.85
    assert study["world_level"]["coverage"] > study["resident_level"]["coverage"]
    assert study["resident_level"]["mean_width"] < study["world_level"]["mean_width"]


def test_generator_reproduces_byte_for_byte_from_a_seed():
    def make(seed):
        return generate_experiment(
            [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.5)],
            WorldModel(n_worlds=6, events_per_world=2, residents_per_event=4),
            seed=seed,
        )

    first, second, other = make(3), make(3), make(4)
    assert first.equals(second)
    assert not first["loss"].equals(other["loss"])


def test_generator_honours_world_subsets():
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", world_subset=[0, 1, 2]),
            PolicyBehaviour("policy_b", world_subset=[2, 3, 4]),
        ],
        WorldModel(n_worlds=5, events_per_world=1, residents_per_event=2),
        seed=1,
    )
    worlds_a = set(frame.loc[frame["policy_id"] == "policy_a", "world_id"])
    worlds_b = set(frame.loc[frame["policy_id"] == "policy_b", "world_id"])
    assert len(worlds_a & worlds_b) == 1


def test_generator_lays_strata_out_as_a_full_factorial():
    frame = generate_experiment(
        [PolicyBehaviour("policy_a")],
        WorldModel(
            n_worlds=8, events_per_world=1, residents_per_event=1,
            strata={"landscape": ["flat", "steep"], "mobility": ["high", "low"]},
        ),
        seed=1,
    )
    worlds = frame.drop_duplicates("world_id")
    crossed = worlds.groupby(["landscape", "mobility"], observed=True).size()
    assert len(crossed) == 4
    assert crossed.nunique() == 1  # balanced, so the columns are not collinear


def test_world_difficulty_can_be_supplied_exactly():
    difficulty = [0.0, 10.0, 20.0]
    frame = generate_experiment(
        [PolicyBehaviour("policy_a")],
        WorldModel(n_worlds=3, events_per_world=1, residents_per_event=200,
                   world_sd=0.0, event_sd=0.0, resident_sd=0.01),
        seed=1,
        world_difficulty=difficulty,
    )
    means = frame.groupby("world_id", observed=True)["loss"].mean().sort_values()
    assert np.allclose(np.diff(means.to_numpy()), 10.0, atol=0.1)


def test_world_difficulty_length_is_checked():
    with pytest.raises(ValueError, match="world_difficulty"):
        generate_experiment(
            [PolicyBehaviour("policy_a")],
            WorldModel(n_worlds=3, events_per_world=1, residents_per_event=1),
            world_difficulty=[0.0, 1.0],
        )
