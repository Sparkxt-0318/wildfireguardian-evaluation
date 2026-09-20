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

#: Every scenario the v0.1.0 audit requires, by key.
REQUIRED = [
    "observation_bootstrap_false_precision",
    "unit_bootstrap_calibration",
    "tail_risk_disagreement",
    "practical_equivalence",
    "missing_units_reverse_ranking",
    "easier_units_confound",
    "dependence_above_declared_unit",
    "outcome_dependent_missingness",
    "too_few_units",
    "wrong_cvar_tail",
    "asymmetric_margin",
    "bounded_outcome_interval",
    "uncorrected_family",
    "failed_runs_as_missing",
    "paired_data_analysed_unpaired",
    "metric_selected_after_the_fact",
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
def test_observation_bootstrap_is_measurably_over_confident():
    """Reported with Monte Carlo error, so the claim is about the procedure."""
    study = coverage_study(n_replications=150, n_resamples=250, n_units=20,
                           events_per_unit=2, observations_per_event=30, seed=4)
    observation = study["observation_level"]
    cluster = study["cluster_level"]
    assert not observation.consistent_with_nominal
    assert observation.wilson_ci[1] < cluster.empirical_coverage
    assert observation.mean_width < cluster.mean_width
    assert study["difference"]["distinguishable"]


def test_every_scenario_states_what_it_cannot_conclude():
    for scenario in list_scenarios():
        assert scenario.can_conclude.strip()
        assert scenario.cannot_conclude.strip()
        assert scenario.audit_item.strip()


def test_generator_records_run_status_without_dropping_the_row():
    frame = generate_experiment(
        [PolicyBehaviour("policy_a"),
         PolicyBehaviour("policy_b", run_failure_rates={"crashed": 0.5})],
        WorldModel(n_units=12, events_per_unit=2, observations_per_event=4),
        seed=2,
    )
    crashed = frame[frame["run_status"] == "crashed"]
    assert len(crashed) > 0
    assert crashed["loss"].isna().all()
    assert (frame["run_status"] == "completed").sum() > 0


def test_generator_can_put_events_above_units():
    frame = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b")],
        WorldModel(n_units=12, events_per_unit=1, observations_per_event=2,
                   units_per_shared_event=4),
        seed=1,
    )
    per_event = frame.groupby("event_id")["world_id"].nunique()
    assert (per_event == 4).all()


def test_generator_reproduces_byte_for_byte_from_a_seed():
    def make(seed):
        return generate_experiment(
            [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.5)],
            WorldModel(n_units=6, events_per_unit=2, observations_per_event=4),
            seed=seed,
        )

    first, second, other = make(3), make(3), make(4)
    assert first.equals(second)
    assert not first["loss"].equals(other["loss"])


def test_generator_honours_world_subsets():
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", unit_subset=[0, 1, 2]),
            PolicyBehaviour("policy_b", unit_subset=[2, 3, 4]),
        ],
        WorldModel(n_units=5, events_per_unit=1, observations_per_event=2),
        seed=1,
    )
    worlds_a = set(frame.loc[frame["policy_id"] == "policy_a", "world_id"])
    worlds_b = set(frame.loc[frame["policy_id"] == "policy_b", "world_id"])
    assert len(worlds_a & worlds_b) == 1


def test_generator_lays_strata_out_as_a_full_factorial():
    frame = generate_experiment(
        [PolicyBehaviour("policy_a")],
        WorldModel(
            n_units=8, events_per_unit=1, observations_per_event=1,
            strata={"difficulty": ["low", "high"], "scale": ["small", "large"]},
        ),
        seed=1,
    )
    worlds = frame.drop_duplicates("world_id")
    crossed = worlds.groupby(["difficulty", "scale"], observed=True).size()
    assert len(crossed) == 4
    assert crossed.nunique() == 1  # balanced, so the columns are not collinear


def test_unit_difficulty_can_be_supplied_exactly():
    difficulty = [0.0, 10.0, 20.0]
    frame = generate_experiment(
        [PolicyBehaviour("policy_a")],
        WorldModel(n_units=3, events_per_unit=1, observations_per_event=200,
                   unit_sd=0.0, event_sd=0.0, observation_sd=0.01),
        seed=1,
        unit_difficulty=difficulty,
    )
    means = frame.groupby("world_id", observed=True)["loss"].mean().sort_values()
    assert np.allclose(np.diff(means.to_numpy()), 10.0, atol=0.1)


def test_unit_difficulty_length_is_checked():
    with pytest.raises(ValueError, match="unit_difficulty"):
        generate_experiment(
            [PolicyBehaviour("policy_a")],
            WorldModel(n_units=3, events_per_unit=1, observations_per_event=1),
            unit_difficulty=[0.0, 1.0],
        )
