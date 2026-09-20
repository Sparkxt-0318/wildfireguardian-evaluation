"""A hierarchical generator with a known variance decomposition.

The model is deliberately plain::

    loss[u,e,r,p] = base
                  + U[u]            (unit effect, shared by every policy)
                  + I[u,p]          (unit x policy interaction)
                  + shift[p]        (the true policy effect -- the estimand)
                  + E[u,e,p]        (sub-group effect)
                  + eps[u,e,r,p]    (observation noise)
                  + catastrophe     (a rare additive spike)
                  + S[s]            (an optional shock shared by several units)

``U`` cancels in a paired comparison.  ``I`` does not, and it is the reason a
unit-level resample is mandatory: it is the between-unit variation in the
*difference*, and no number of extra observations per unit reduces it.  ``S``
exists so a scenario can put a dependence *above* the unit that a unit-level
bootstrap cannot see.

Nothing here models any real process.  Names are design vocabulary only: units,
events, observations, policies, strata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

DEFAULT_FAILURE_MIX: dict[str, float] = {
    "reason_a": 0.30,
    "reason_b": 0.30,
    "reason_c": 0.20,
    "reason_d": 0.20,
}

ACTIONS: tuple[str, ...] = ("action_1", "action_2", "action_3", "action_4")

#: Domain-neutral design variables used by the fixtures and scenarios.
DEFAULT_STRATA: dict[str, list[str]] = {
    "difficulty": ["low", "high"],
    "scale": ["small", "large"],
    "regime": ["regime_a", "regime_b"],
    "capacity": ["scarce", "ample"],
}


@dataclass
class PolicyBehaviour:
    """How one policy departs from the baseline unit process."""

    name: str
    #: True additive effect on loss. This is the estimand a comparison targets.
    loss_shift: float = 0.0
    #: Scales the observation-level noise (spread without changing the mean).
    loss_sd_scale: float = 1.0
    #: SD of the unit x policy interaction. The dominant term in a paired SE.
    interaction_sd: float = 0.0
    #: Probability that an observation suffers an additive catastrophe.
    catastrophe_prob: float = 0.0
    catastrophe_magnitude: float = 0.0
    #: Probability that a WHOLE event is a disaster, and by how much. Distinct
    #: from the per-observation spike: it puts the heavy tail at the event level,
    #: where an event-level tail statistic can see it.
    event_catastrophe_prob: float = 0.0
    event_catastrophe_magnitude: float = 0.0
    #: SD of a (shared-event-group x policy) effect. Unlike a shock shared by
    #: both policies -- which cancels in a paired difference -- this one does
    #: not, so it is what makes a unit-level resample wrong when units share a
    #: higher-level grouping.
    group_interaction_sd: float = 0.0
    #: Additive shift on the success logit.
    success_shift: float = 0.0
    travel_shift: float = 0.0
    resource_shift: float = 0.0
    exposure_shift: float = 0.0
    failure_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FAILURE_MIX))
    #: Restrict this policy to a subset of units (0-based indices). ``None`` = all.
    unit_subset: Sequence[int] | None = None
    #: status -> probability that a whole run ends that way instead of completing.
    #: e.g. ``{"crashed": 0.05}``. Applied per (unit, event, policy).
    run_failure_rates: dict[str, float] = field(default_factory=dict)
    #: Run failures concentrated on the hardest units, as a multiplier on the
    #: rate for units whose effect is above the median. >1 makes missingness
    #: depend on the outcome, which is the MNAR case.
    run_failure_difficulty_multiplier: float = 1.0

    def runs_on(self, unit_index: int) -> bool:
        return self.unit_subset is None or unit_index in set(self.unit_subset)


@dataclass
class WorldModel:
    """The shared, policy-independent part of the generating process."""

    n_units: int = 40
    events_per_unit: int | tuple[int, int] = 3
    observations_per_event: int | tuple[int, int] = 25
    base_loss: float = 10.0
    unit_sd: float = 3.0
    event_sd: float = 1.0
    observation_sd: float = 1.0
    base_travel_time: float = 18.0
    base_resource_use: float = 1.0
    base_exposure: float = 0.6
    #: Extra unit-level columns: name -> category labels, laid out as a factorial.
    strata: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: Additive loss offset per stratum level, e.g. {"difficulty": {"high": 4.0}}.
    stratum_effects: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    unit_id_prefix: str = "u"
    success_intercept: float = 1.4
    success_loss_slope: float = 0.12
    #: Group this many consecutive units under one shared ``event_id``, making
    #: the event a level *above* the unit. ``1`` keeps the usual nesting.
    units_per_shared_event: int = 1
    #: SD of the shock shared by every unit in one shared event group.
    shared_event_sd: float = 0.0

    # Backwards-compatible aliases -----------------------------------------
    @property
    def n_worlds(self) -> int:
        return self.n_units


def _draw_count(rng: np.random.Generator, spec: int | tuple[int, int]) -> int:
    if isinstance(spec, tuple):
        lo, hi = spec
        return int(rng.integers(lo, hi + 1))
    return int(spec)


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype="float64")))


def generate_experiment(
    policies: Iterable[PolicyBehaviour],
    world: WorldModel | None = None,
    *,
    seed: int = 0,
    unit_difficulty: Sequence[float] | None = None,
) -> pd.DataFrame:
    """Generate a record table with a known truth.

    Returns one row per (unit, event, policy, observation) with every core
    schema column, a ``run_status``, and the declared stratum columns.

    When ``world.units_per_shared_event > 1`` the ``event_id`` column names a
    group *containing* several units, so the hierarchy is ``event > unit >
    observation``.  Such records must declare ``inference.primary_unit:
    event_id``; declaring ``world_id`` is refused by the structure check.
    """
    world = world or WorldModel()
    policies = list(policies)
    if not policies:
        raise ValueError("at least one policy behaviour is required")
    rng = np.random.default_rng(seed)

    if unit_difficulty is not None:
        if len(unit_difficulty) != world.n_units:
            raise ValueError(
                f"unit_difficulty has {len(unit_difficulty)} entries but n_units is {world.n_units}"
            )
        unit_effects = np.asarray(unit_difficulty, dtype="float64")
    else:
        unit_effects = rng.normal(0.0, world.unit_sd, size=world.n_units)

    # A shock shared by every unit in one group: dependence above the unit.
    group_size = max(1, int(world.units_per_shared_event))
    n_groups = int(np.ceil(world.n_units / group_size))
    group_of_unit = np.array([i // group_size for i in range(world.n_units)])
    shared_shock = (
        rng.normal(0.0, world.shared_event_sd, size=n_groups)
        if world.shared_event_sd > 0
        else np.zeros(n_groups)
    )
    unit_effects = unit_effects + shared_shock[group_of_unit]

    stratum_values: dict[str, list[str]] = {}
    block = 1
    for column, levels in world.strata.items():
        levels = list(levels)
        if not levels:
            raise ValueError(f"stratum column {column!r} declares no levels")
        stratum_values[column] = [levels[(i // block) % len(levels)] for i in range(world.n_units)]
        block *= len(levels)
    for column, effects in world.stratum_effects.items():
        if column not in stratum_values:
            raise ValueError(f"stratum_effects references undeclared stratum column {column!r}")
        unit_effects = unit_effects + np.array(
            [float(effects.get(stratum_values[column][i], 0.0)) for i in range(world.n_units)]
        )

    median_effect = float(np.median(unit_effects))
    interactions = {
        p.name: (
            rng.normal(0.0, p.interaction_sd, size=world.n_units)
            if p.interaction_sd > 0
            else np.zeros(world.n_units)
        )
        for p in policies
    }
    # A (group x policy) effect: shared by every unit in one group, different
    # between policies, so it survives the paired difference.
    group_interactions = {
        p.name: (
            rng.normal(0.0, p.group_interaction_sd, size=n_groups)
            if p.group_interaction_sd > 0
            else np.zeros(n_groups)
        )
        for p in policies
    }

    n_events = [_draw_count(rng, world.events_per_unit) for _ in range(world.n_units)]
    n_obs = {
        (u, e): _draw_count(rng, world.observations_per_event)
        for u in range(world.n_units)
        for e in range(n_events[u])
    }

    rows: list[dict[str, object]] = []
    for u in range(world.n_units):
        unit_id = f"{world.unit_id_prefix}{u:04d}"
        for e in range(n_events[u]):
            event_id = (
                f"g{group_of_unit[u]:04d}-e{e:02d}" if group_size > 1 else f"{unit_id}-e{e:02d}"
            )
            n_rows = n_obs[(u, e)]
            for policy in policies:
                if not policy.runs_on(u):
                    continue
                status = _draw_run_status(rng, policy, unit_effects[u] > median_effect)
                event_effect = float(rng.normal(0.0, world.event_sd))
                noise = rng.normal(0.0, world.observation_sd * policy.loss_sd_scale, size=n_rows)
                mean_loss = (
                    world.base_loss
                    + unit_effects[u]
                    + interactions[policy.name][u]
                    + group_interactions[policy.name][group_of_unit[u]]
                    + policy.loss_shift
                    + event_effect
                )
                if policy.event_catastrophe_prob > 0.0 and rng.random() < policy.event_catastrophe_prob:
                    mean_loss = mean_loss + policy.event_catastrophe_magnitude
                loss = mean_loss + noise
                if policy.catastrophe_prob > 0.0 and policy.catastrophe_magnitude != 0.0:
                    hit = rng.random(n_rows) < policy.catastrophe_prob
                    loss = loss + hit * policy.catastrophe_magnitude
                loss = np.clip(loss, 0.0, None)

                centred = loss - world.base_loss
                p_success = np.clip(
                    _sigmoid(
                        world.success_intercept
                        - world.success_loss_slope * centred
                        + policy.success_shift
                    ),
                    0.01,
                    0.99,
                )
                success = (rng.random(n_rows) < p_success).astype("int64")
                travel = np.clip(
                    world.base_travel_time
                    + policy.travel_shift
                    + 0.35 * unit_effects[u]
                    + rng.normal(0.0, 2.0, size=n_rows),
                    0.0, None,
                )
                resource = np.clip(
                    world.base_resource_use + policy.resource_shift + rng.gamma(2.0, 0.25, size=n_rows),
                    0.0, None,
                )
                exposure = np.clip(
                    world.base_exposure
                    + policy.exposure_shift
                    + 0.02 * unit_effects[u]
                    + rng.normal(0.0, 0.12, size=n_rows),
                    0.0, None,
                )
                actions = rng.choice(ACTIONS, size=n_rows)
                reasons = _draw_failure_reasons(rng, policy.failure_mix, n_rows)
                completed = status == "completed"

                for r in range(n_rows):
                    row: dict[str, object] = {
                        "world_id": unit_id,
                        "event_id": event_id,
                        "policy_id": policy.name,
                        "resident_id": f"{unit_id}-e{e:02d}-r{r:03d}",
                        "run_status": status,
                        "action": str(actions[r]) if completed else None,
                        "mission_success": int(success[r]) if completed else None,
                        "loss": float(loss[r]) if completed else None,
                        "travel_time": float(travel[r]) if completed else None,
                        "resource_use": float(resource[r]) if completed else None,
                        "responder_exposure": float(exposure[r]) if completed else None,
                        "failure_reason": (
                            None if (not completed or success[r] == 1) else reasons[r]
                        ),
                        "stratum": _default_stratum(stratum_values, u),
                    }
                    for column, values in stratum_values.items():
                        row[column] = values[u]
                    rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("the generator produced no rows; check unit_subset settings")
    return _cast(frame)


def _draw_run_status(
    rng: np.random.Generator, policy: PolicyBehaviour, is_hard_unit: bool
) -> str:
    if not policy.run_failure_rates:
        return "completed"
    multiplier = policy.run_failure_difficulty_multiplier if is_hard_unit else 1.0
    draw = float(rng.random())
    cumulative = 0.0
    for status, rate in policy.run_failure_rates.items():
        cumulative += min(1.0, float(rate) * multiplier)
        if draw < cumulative:
            return status
    return "completed"


def _default_stratum(stratum_values: Mapping[str, Sequence[str]], unit_index: int) -> str:
    if not stratum_values:
        return "all"
    return "|".join(f"{k}={v[unit_index]}" for k, v in sorted(stratum_values.items()))


def _draw_failure_reasons(
    rng: np.random.Generator, mix: Mapping[str, float], size: int
) -> list[str]:
    if not mix:
        return ["unspecified"] * size
    labels = list(mix)
    weights = np.array([float(mix[k]) for k in labels], dtype="float64")
    total = weights.sum()
    if total <= 0:
        return ["unspecified"] * size
    return [str(x) for x in rng.choice(labels, size=size, p=weights / total)]


def _cast(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in (
        "world_id", "event_id", "policy_id", "resident_id", "action", "failure_reason",
        "stratum", "run_status",
    ):
        if col in out.columns:
            out[col] = out[col].astype("string")
    for col in ("loss", "travel_time", "resource_use", "responder_exposure", "mission_success"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# post-processing helpers used by scenarios
# ---------------------------------------------------------------------------

def drop_records(frame: pd.DataFrame, *, policy: str, units: Iterable[str]) -> pd.DataFrame:
    """Remove a policy's records for the named units (missing-data scenarios)."""
    units = pd.Series(list(units), dtype="string")
    mask = (frame["policy_id"].astype("string") == policy) & frame["world_id"].astype(
        "string"
    ).isin(units)
    return frame.loc[~mask].reset_index(drop=True)


def mark_status(
    frame: pd.DataFrame, *, policy: str, units: Iterable[str], status: str
) -> pd.DataFrame:
    """Record that a policy's runs on these units ended with ``status``.

    The rows stay in the table with null outcomes.  That is the difference
    between "this run failed" and "this run never existed", and it is the
    difference the library is built to keep.
    """
    units = pd.Series(list(units), dtype="string")
    mask = (frame["policy_id"].astype("string") == policy) & frame["world_id"].astype(
        "string"
    ).isin(units)
    out = frame.copy()
    out.loc[mask, "run_status"] = status
    for column in ("loss", "travel_time", "resource_use", "responder_exposure", "mission_success"):
        if column in out.columns:
            out.loc[mask, column] = np.nan
    for column in ("failure_reason", "action"):
        if column in out.columns:
            out.loc[mask, column] = pd.NA
    return out.reset_index(drop=True)


def hardest_units(frame: pd.DataFrame, n: int, *, column: str = "loss") -> list[str]:
    """The ``n`` units with the highest mean ``column``, hardest first."""
    means = frame.groupby("world_id", observed=True)[column].mean().sort_values(ascending=False)
    return [str(u) for u in means.index[:n]]


# Legacy aliases kept so older callers and fixtures keep working.
hardest_worlds = hardest_units
