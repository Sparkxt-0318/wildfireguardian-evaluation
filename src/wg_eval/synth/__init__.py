"""Synthetic experiment-record generators and adversarial scenarios.

Nothing here models a real process.  These generators exist to produce data
whose *statistical structure* is known exactly, so that an analysis can be
checked against a truth it cannot see.
"""

from wg_eval.synth.generators import (
    DEFAULT_FAILURE_MIX,
    PolicyBehaviour,
    WorldModel,
    generate_experiment,
)
from wg_eval.synth.redteam import (
    SCENARIOS,
    Scenario,
    build_scenario,
    list_scenarios,
    run_scenario,
)

__all__ = [
    "DEFAULT_FAILURE_MIX",
    "PolicyBehaviour",
    "WorldModel",
    "generate_experiment",
    "SCENARIOS",
    "Scenario",
    "build_scenario",
    "list_scenarios",
    "run_scenario",
]
