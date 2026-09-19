"""A hierarchical generator with a known variance decomposition.

The model is deliberately plain::

    loss[w,e,r,p] = base
                  + W[w]            (world effect, shared by every policy)
                  + I[w,p]          (world x policy interaction)
                  + shift[p]        (the true policy effect -- the estimand)
                  + E[w,e,p]        (event effect)
                  + eps[w,e,r,p]    (observation noise)
                  + catastrophe     (a rare additive spike)

``W`` cancels in a paired comparison.  ``I`` does not, and it is the reason a
world-level resample is mandatory: it is the between-world variation in the
*difference*, and no amount of extra residents per world reduces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

DEFAULT_FAILURE_MIX: dict[str, float] = {
    "unreachable": 0.30,
    "late_arrival": 0.30,
    "resource_exhausted": 0.20,
    "route_blocked": 0.20,
}

ACTIONS: tuple[str, ...] = ("evacuate", "shelter", "hold", "reroute")


@dataclass
class PolicyBehaviour:
    """How one policy departs from the baseline world process."""

    name: str
    #: True additive effect on loss. This is the estimand a comparison targets.
    loss_shift: float = 0.0
    #: Scales the observation-level noise (spread without changing the mean).
    loss_sd_scale: float = 1.0
    #: SD of the world x policy interaction. The dominant term in a paired SE.
    interaction_sd: float = 0.0
    #: Probability that an observation suffers an additive catastrophe.
    catastrophe_prob: float = 0.0
    catastrophe_magnitude: float = 0.0
    #: Additive shift on the success logit.
    success_shift: float = 0.0
    travel_shift: float = 0.0
    resource_shift: float = 0.0
    exposure_shift: float = 0.0
    failure_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FAILURE_MIX))
    #: Restrict this policy to a subset of worlds (0-based indices). ``None`` = all.
    world_subset: Sequence[int] | None = None

    def runs_on(self, world_index: int) -> bool:
        return self.world_subset is None or world_index in set(self.world_subset)


@dataclass
class WorldModel:
    """The shared, policy-independent part of the generating process."""

    n_worlds: int = 40
    events_per_world: int | tuple[int, int] = 3
    residents_per_event: int | tuple[int, int] = 25
    base_loss: float = 10.0
    world_sd: float = 3.0
    event_sd: float = 1.0
    resident_sd: float = 1.0
    base_travel_time: float = 18.0
    base_resource_use: float = 1.0
    base_exposure: float = 0.6
    #: Extra world-level columns: name -> category labels, assigned round-robin.
    strata: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: Additive loss offset per stratum level, e.g. {"landscape": {"steep": 4.0}}.
    stratum_effects: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    world_id_prefix: str = "w"
    #: Success logit intercept and slope on centred loss.
    success_intercept: float = 1.4
    success_loss_slope: float = 0.12


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
    world_difficulty: Sequence[float] | None = None,
) -> pd.DataFrame:
    """Generate a record table with a known truth.

    Parameters
    ----------
    policies:
        One :class:`PolicyBehaviour` per arm.  The first is conventionally the
        baseline, but nothing here depends on that.
    world:
        The shared world process.  Defaults to :class:`WorldModel`.
    seed:
        Seed for the whole draw; the same seed reproduces the table byte for byte.
    world_difficulty:
        Supply the world effects directly instead of drawing them, when a
        scenario needs an exactly-known difficulty ordering.

    Returns
    -------
    pandas.DataFrame
        One row per (world, event, policy, resident) with every core schema
        column plus the declared stratum columns.
    """
    world = world or WorldModel()
    policies = list(policies)
    if not policies:
        raise ValueError("at least one policy behaviour is required")
    rng = np.random.default_rng(seed)

    if world_difficulty is not None:
        if len(world_difficulty) != world.n_worlds:
            raise ValueError(
                f"world_difficulty has {len(world_difficulty)} entries but n_worlds is {world.n_worlds}"
            )
        world_effects = np.asarray(world_difficulty, dtype="float64")
    else:
        world_effects = rng.normal(0.0, world.world_sd, size=world.n_worlds)

    # World-level design variables (strata), assigned deterministically so that
    # a scenario can reason about which worlds are "easy".
    # Declared strata are laid out as a full factorial: each column cycles at the
    # product of the widths of the columns before it, so the columns are crossed
    # rather than collinear. Two 2-level columns therefore give four world types
    # in equal numbers, not two.
    stratum_values: dict[str, list[str]] = {}
    block = 1
    for column, levels in world.strata.items():
        levels = list(levels)
        if not levels:
            raise ValueError(f"stratum column {column!r} declares no levels")
        stratum_values[column] = [levels[(i // block) % len(levels)] for i in range(world.n_worlds)]
        block *= len(levels)
    for column, effects in world.stratum_effects.items():
        if column not in stratum_values:
            raise ValueError(f"stratum_effects references undeclared stratum column {column!r}")
        world_effects = world_effects + np.array(
            [float(effects.get(stratum_values[column][i], 0.0)) for i in range(world.n_worlds)]
        )

    interactions = {
        p.name: rng.normal(0.0, p.interaction_sd, size=world.n_worlds) if p.interaction_sd > 0
        else np.zeros(world.n_worlds)
        for p in policies
    }

    n_events = [_draw_count(rng, world.events_per_world) for _ in range(world.n_worlds)]
    n_residents = {
        (w, e): _draw_count(rng, world.residents_per_event)
        for w in range(world.n_worlds)
        for e in range(n_events[w])
    }

    rows: list[dict[str, object]] = []
    for w in range(world.n_worlds):
        world_id = f"{world.world_id_prefix}{w:04d}"
        for e in range(n_events[w]):
            event_id = f"{world_id}-e{e:02d}"
            n_res = n_residents[(w, e)]
            for policy in policies:
                if not policy.runs_on(w):
                    continue
                event_effect = float(rng.normal(0.0, world.event_sd))
                noise = rng.normal(0.0, world.resident_sd * policy.loss_sd_scale, size=n_res)
                mean_loss = (
                    world.base_loss
                    + world_effects[w]
                    + interactions[policy.name][w]
                    + policy.loss_shift
                    + event_effect
                )
                loss = mean_loss + noise
                if policy.catastrophe_prob > 0.0 and policy.catastrophe_magnitude != 0.0:
                    hit = rng.random(n_res) < policy.catastrophe_prob
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
                success = (rng.random(n_res) < p_success).astype("int64")

                travel = np.clip(
                    world.base_travel_time
                    + policy.travel_shift
                    + 0.35 * world_effects[w]
                    + rng.normal(0.0, 2.0, size=n_res),
                    0.0,
                    None,
                )
                resource = np.clip(
                    world.base_resource_use + policy.resource_shift + rng.gamma(2.0, 0.25, size=n_res),
                    0.0,
                    None,
                )
                exposure = np.clip(
                    world.base_exposure
                    + policy.exposure_shift
                    + 0.02 * world_effects[w]
                    + rng.normal(0.0, 0.12, size=n_res),
                    0.0,
                    None,
                )
                actions = rng.choice(ACTIONS, size=n_res)
                reasons = _draw_failure_reasons(rng, policy.failure_mix, n_res)

                for r in range(n_res):
                    row: dict[str, object] = {
                        "world_id": world_id,
                        "event_id": event_id,
                        "policy_id": policy.name,
                        "resident_id": f"{event_id}-r{r:03d}",
                        "action": str(actions[r]),
                        "mission_success": int(success[r]),
                        "loss": float(loss[r]),
                        "travel_time": float(travel[r]),
                        "resource_use": float(resource[r]),
                        "responder_exposure": float(exposure[r]),
                        "failure_reason": None if success[r] == 1 else reasons[r],
                        "stratum": _default_stratum(stratum_values, w),
                    }
                    for column, values in stratum_values.items():
                        row[column] = values[w]
                    rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("the generator produced no rows; check world_subset settings")
    return _cast(frame)


def _default_stratum(stratum_values: Mapping[str, Sequence[str]], world_index: int) -> str:
    if not stratum_values:
        return "all"
    return "|".join(f"{k}={v[world_index]}" for k, v in sorted(stratum_values.items()))


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
    for col in ("world_id", "event_id", "policy_id", "resident_id", "action", "failure_reason", "stratum"):
        if col in out.columns:
            out[col] = out[col].astype("string")
    for col in ("loss", "travel_time", "resource_use", "responder_exposure"):
        if col in out.columns:
            out[col] = out[col].astype("float64")
    if "mission_success" in out.columns:
        out["mission_success"] = out["mission_success"].astype("int64")
    return out.reset_index(drop=True)


def drop_records(
    frame: pd.DataFrame,
    *,
    policy: str,
    worlds: Iterable[str],
) -> pd.DataFrame:
    """Remove a policy's records for the named worlds (missing-data scenarios)."""
    worlds = pd.Series(list(worlds), dtype="string")
    mask = (frame["policy_id"].astype("string") == policy) & frame["world_id"].astype("string").isin(worlds)
    return frame.loc[~mask].reset_index(drop=True)


def hardest_worlds(frame: pd.DataFrame, n: int, *, column: str = "loss") -> list[str]:
    """The ``n`` worlds with the highest mean ``column``, hardest first."""
    means = frame.groupby("world_id", observed=True)[column].mean().sort_values(ascending=False)
    return [str(w) for w in means.index[:n]]
