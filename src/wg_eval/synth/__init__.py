"""Synthetic experiment-record generators and adversarial scenarios.

Nothing here models a real process.  These generators exist to produce data
whose *statistical structure* is known exactly, so that an analysis can be
checked against a truth it cannot see.  The vocabulary is design vocabulary:
units, events, observations, policies, strata.
"""

from wg_eval.synth.generators import (
    DEFAULT_FAILURE_MIX,
    DEFAULT_STRATA,
    PolicyBehaviour,
    WorldModel,
    drop_records,
    generate_experiment,
    hardest_units,
    mark_status,
)
from wg_eval.synth.redteam import (
    SCENARIOS,
    Scenario,
    ScenarioRun,
    build_scenario,
    coverage_study,
    list_scenarios,
    run_scenario,
)

__all__ = [
    "DEFAULT_FAILURE_MIX",
    "DEFAULT_STRATA",
    "PolicyBehaviour",
    "WorldModel",
    "drop_records",
    "generate_experiment",
    "hardest_units",
    "mark_status",
    "SCENARIOS",
    "Scenario",
    "ScenarioRun",
    "build_scenario",
    "coverage_study",
    "list_scenarios",
    "run_scenario",
]
