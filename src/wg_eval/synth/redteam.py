"""Adversarial scenarios: data built to fool a plausible-looking analysis.

Each scenario ships three things: records whose truth is known by
construction, the *wrong* analysis that the records are designed to reward,
and the right analysis that recovers the truth.  A scenario passes when the
wrong analysis reaches the wrong conclusion and the right one does not.

These are tests of the analysis, not of the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from wg_eval.bootstrap import naive_iid_bootstrap
from wg_eval.compare import ComparisonResult, compare_policies
from wg_eval.config import AnalysisConfig, config_from_mapping
from wg_eval.pairing import ClusteredValues
from wg_eval.synth.generators import (
    PolicyBehaviour,
    WorldModel,
    drop_records,
    generate_experiment,
    hardest_worlds,
)


#: Declared world-level design variables shared by every scenario. They are laid
#: out as a full factorial by the generator, so they are crossed rather than
#: collinear and a per-stratum result means what it appears to mean.
STRATA: dict[str, list[str]] = {
    "landscape": ["flat", "steep"],
    "mobility": ["high", "low"],
    "fire_regime": ["surface", "crown"],
    "resource_level": ["scarce", "ample"],
}

STRATUM_COLUMNS: list[str] = list(STRATA)


@dataclass
class Scenario:
    """One adversarial example."""

    key: str
    title: str
    #: The misleading conclusion the data is built to produce.
    trap: str
    #: What is actually true, by construction.
    truth: str
    #: The analysis choice that avoids the trap.
    defence: str
    build: Callable[[int], tuple[pd.DataFrame, dict[str, Any]]]
    config: Callable[[], AnalysisConfig]
    demonstrate: Callable[["Scenario", int], "ScenarioRun"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "trap": self.trap,
            "truth": self.truth,
            "defence": self.defence,
        }


@dataclass
class ScenarioRun:
    """The outcome of running a scenario: numbers plus a pass/fail on the trap."""

    scenario: Scenario
    records: pd.DataFrame
    truth: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)
    naive: dict[str, Any] = field(default_factory=dict)
    correct: dict[str, Any] = field(default_factory=dict)
    trap_reproduced: bool = False
    defence_worked: bool = False
    comparison: ComparisonResult | None = None
    lines: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """The scenario did its job: the trap fired and the defence held."""
        return self.trap_reproduced and self.defence_worked

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.as_dict(),
            "truth": _jsonable(self.truth),
            "naive": _jsonable(self.naive),
            "correct": _jsonable(self.correct),
            "findings": [_jsonable(f) for f in self.findings],
            "trap_reproduced": self.trap_reproduced,
            "defence_worked": self.defence_worked,
            "passed": self.passed,
            "n_records": int(len(self.records)),
            "lines": list(self.lines),
        }

    def to_text(self) -> str:
        head = [
            f"### {self.scenario.key}: {self.scenario.title}",
            "",
            f"TRAP     {self.scenario.trap}",
            f"TRUTH    {self.scenario.truth}",
            f"DEFENCE  {self.scenario.defence}",
            "",
        ]
        return "\n".join(head + self.lines)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return None if not np.isfinite(out) else out
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return value


# --------------------------------------------------------------------------
# Shared generating processes
# --------------------------------------------------------------------------

def _nested_pair(
    seed: int,
    *,
    n_worlds: int = 24,
    events_per_world: int = 3,
    residents_per_event: int = 40,
    true_shift: float = 0.6,
    interaction_sd: float = 1.2,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Strongly clustered records: many residents per world, real world effects."""
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=interaction_sd),
            PolicyBehaviour("policy_b", loss_shift=true_shift, interaction_sd=interaction_sd),
        ],
        WorldModel(
            n_worlds=n_worlds,
            events_per_world=events_per_world,
            residents_per_event=residents_per_event,
            world_sd=4.0,
            event_sd=1.2,
            resident_sd=1.0,
            strata=STRATA,
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": true_shift,
        "n_worlds": n_worlds,
        "observations_per_world": events_per_world * residents_per_event,
        "world_sd": 4.0,
        "interaction_sd": interaction_sd,
    }
    return frame, truth


def _paired_mean_difference(frame: pd.DataFrame, a: str, b: str) -> tuple[ClusteredValues, ClusteredValues]:
    """Per-world loss values for two policies over their shared worlds."""
    work = frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    worlds = sorted(
        set(work.loc[work["policy_id"] == a, "world_id"].astype("string"))
        & set(work.loc[work["policy_id"] == b, "world_id"].astype("string"))
    )
    groups = {}
    for policy in (a, b):
        sub = work[work["policy_id"] == policy]
        by_world = {
            str(k): v["loss"].to_numpy(dtype="float64")
            for k, v in sub.groupby("world_id", observed=True)
        }
        groups[policy] = ClusteredValues.from_groups([by_world[w] for w in worlds])
    return groups[a], groups[b]


def coverage_study(
    *,
    n_trials: int = 120,
    n_resamples: int = 400,
    n_worlds: int = 20,
    events_per_world: int = 2,
    residents_per_event: int = 30,
    true_shift: float = 0.6,
    interaction_sd: float = 1.2,
    confidence_level: float = 0.95,
    seed: int = 20260919,
) -> dict[str, Any]:
    """Measure the actual coverage of resident-level vs world-level intervals.

    Both analyses target the same estimand -- the true mean loss difference
    between the two policies -- on the same data.  Only the resampling unit
    differs.  Coverage is the fraction of trials whose interval contains the
    truth; an honest 95% interval covers about 95% of the time.
    """
    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**31 - 1, size=n_trials)
    rows = []
    for trial, trial_seed in enumerate(seeds):
        frame, _ = _nested_pair(
            int(trial_seed),
            n_worlds=n_worlds,
            events_per_world=events_per_world,
            residents_per_event=residents_per_event,
            true_shift=true_shift,
            interaction_sd=interaction_sd,
        )
        a_vals, b_vals = _paired_mean_difference(frame, "policy_a", "policy_b")

        # Correct: resample worlds, keeping both policies on the same worlds.
        n_clusters = a_vals.n_clusters
        draw_rng = np.random.default_rng(int(trial_seed) + 1)
        draws = draw_rng.integers(0, n_clusters, size=(n_resamples, n_clusters))
        reps = np.array(
            [float(b_vals.take(d).mean() - a_vals.take(d).mean()) for d in draws],
            dtype="float64",
        )
        alpha = (1.0 - confidence_level) / 2.0
        world_lo, world_hi = float(np.quantile(reps, alpha)), float(np.quantile(reps, 1 - alpha))

        # Wrong: resample residents as if each were an independent experiment.
        a_flat, b_flat = a_vals.all_values(), b_vals.all_values()
        naive_a = naive_iid_bootstrap(a_flat, np.mean, n_resamples=n_resamples, seed=int(trial_seed) + 2,
                                      confidence_level=confidence_level)
        naive_b = naive_iid_bootstrap(b_flat, np.mean, n_resamples=n_resamples, seed=int(trial_seed) + 3,
                                      confidence_level=confidence_level)
        naive_reps = naive_b.replicates[: min(len(naive_a.replicates), len(naive_b.replicates))] - \
            naive_a.replicates[: min(len(naive_a.replicates), len(naive_b.replicates))]
        naive_lo, naive_hi = float(np.quantile(naive_reps, alpha)), float(np.quantile(naive_reps, 1 - alpha))

        rows.append(
            {
                "trial": trial,
                "world_lo": world_lo,
                "world_hi": world_hi,
                "world_covers": bool(world_lo <= true_shift <= world_hi),
                "world_width": world_hi - world_lo,
                "naive_lo": naive_lo,
                "naive_hi": naive_hi,
                "naive_covers": bool(naive_lo <= true_shift <= naive_hi),
                "naive_width": naive_hi - naive_lo,
            }
        )

    table = pd.DataFrame(rows)
    return {
        "n_trials": n_trials,
        "true_difference": true_shift,
        "confidence_level": confidence_level,
        "design": {
            "n_worlds": n_worlds,
            "events_per_world": events_per_world,
            "residents_per_event": residents_per_event,
            "observations_per_world": events_per_world * residents_per_event,
        },
        "world_level": {
            "coverage": float(table["world_covers"].mean()),
            "mean_width": float(table["world_width"].mean()),
        },
        "resident_level": {
            "coverage": float(table["naive_covers"].mean()),
            "mean_width": float(table["naive_width"].mean()),
        },
        "width_ratio_world_over_resident": float(
            table["world_width"].mean() / max(table["naive_width"].mean(), 1e-12)
        ),
        "table": table,
    }


# --------------------------------------------------------------------------
# Configs
# --------------------------------------------------------------------------

_BASE_METRICS = [
    {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event",
     "direction": "lower_is_better"},
    {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event",
     "params": {"alpha": 0.9, "tail": "upper"}, "direction": "lower_is_better"},
    {"name": "p90_loss", "column": "loss", "estimator": "quantile", "level": "event",
     "params": {"q": 0.9}, "direction": "lower_is_better"},
    {"name": "success_rate", "column": "mission_success", "estimator": "mean",
     "level": "resident", "direction": "higher_is_better"},
]

_BASE_AGGREGATION = {
    "resident_to_event": {
        "loss": "mean",
        "mission_success": "mean",
        "travel_time": "mean",
        "resource_use": "sum",
        "responder_exposure": "sum",
    },
    "event_to_world": {"loss": "mean", "mission_success": "mean"},
    "default_rule": "mean",
}


def _config(
    *,
    baseline: str = "policy_a",
    candidates: tuple[str, ...] = ("policy_b",),
    margins: dict[str, float] | None = None,
    require_common_worlds: bool = True,
    seed: int = 20260919,
    n_resamples: int = 1500,
    metrics: list[dict[str, Any]] | None = None,
    label: str = "",
) -> AnalysisConfig:
    return config_from_mapping(
        {
            "label": label,
            "unit_of_inference": "world",
            "metrics": metrics or _BASE_METRICS,
            "aggregation": _BASE_AGGREGATION,
            "comparison": {
                "baseline": baseline,
                "candidates": list(candidates),
                "paired": True,
                "require_common_worlds": require_common_worlds,
            },
            "bootstrap": {
                "n_resamples": n_resamples,
                "cluster_level": "world",
                "seed": seed,
                "confidence_level": 0.95,
                "method": "percentile",
            },
            "equivalence": {"margins": margins or {}, "alpha": 0.05},
            "strata": list(STRATUM_COLUMNS),
            "missing_data": {"policy": "drop_record"},
        }
    )


# --------------------------------------------------------------------------
# Scenario 1 & 2 -- pseudoreplication
# --------------------------------------------------------------------------

def _build_pseudoreplication(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _nested_pair(seed, n_worlds=20, events_per_world=2, residents_per_event=30)


def _demo_resident_bootstrap(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    a_vals, b_vals = _paired_mean_difference(frame, "policy_a", "policy_b")
    naive_a = naive_iid_bootstrap(a_vals.all_values(), np.mean, n_resamples=1500, seed=seed)
    naive_b = naive_iid_bootstrap(b_vals.all_values(), np.mean, n_resamples=1500, seed=seed + 1)
    n = min(len(naive_a.replicates), len(naive_b.replicates))
    naive_reps = naive_b.replicates[:n] - naive_a.replicates[:n]
    naive_lo, naive_hi = float(np.quantile(naive_reps, 0.025)), float(np.quantile(naive_reps, 0.975))

    correct = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    comparison = correct.get("mean_loss", "policy_b")
    world_width = comparison.difference.ci_high - comparison.difference.ci_low
    naive_width = naive_hi - naive_lo

    coverage = coverage_study(n_trials=60, n_resamples=300, n_worlds=20,
                              events_per_world=2, residents_per_event=30, seed=seed)

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth)
    run.naive = {
        "analysis": "iid bootstrap over residents, both arms independent",
        "difference": float(naive_b.estimate - naive_a.estimate),
        "ci": [naive_lo, naive_hi],
        "width": naive_width,
        "n_units_claimed": int(len(a_vals.all_values())),
        "measured_coverage_of_95pct_interval": coverage["resident_level"]["coverage"],
    }
    run.correct = {
        "analysis": "paired world-level cluster bootstrap",
        "difference": comparison.difference.estimate,
        "ci": [comparison.difference.ci_low, comparison.difference.ci_high],
        "width": world_width,
        "n_units": comparison.difference.n_clusters,
        "measured_coverage_of_95pct_interval": coverage["world_level"]["coverage"],
    }
    run.comparison = correct
    run.trap_reproduced = naive_width < world_width
    run.defence_worked = (
        comparison.difference.ci_low <= truth["true_mean_loss_difference"] <= comparison.difference.ci_high
    )
    run.findings = [
        {
            "finding": "resident-level resampling understates uncertainty",
            "width_ratio": float(world_width / max(naive_width, 1e-12)),
            "detail": (
                f"The correct interval is {world_width / max(naive_width, 1e-12):.1f}x wider. "
                f"The naive analysis claims {len(a_vals.all_values())} independent units "
                f"where the design supplies {comparison.difference.n_clusters}."
            ),
        },
        {
            "finding": "the understatement shows up as under-coverage",
            "resident_coverage": coverage["resident_level"]["coverage"],
            "world_coverage": coverage["world_level"]["coverage"],
            "detail": (
                f"Over {coverage['n_trials']} independent replications of the whole "
                f"experiment, the nominal-95% resident-level interval covered the true "
                f"difference {coverage['resident_level']['coverage']:.0%} of the time; the "
                f"world-level interval covered it {coverage['world_level']['coverage']:.0%} "
                "of the time."
            ),
        },
    ]
    deff = comparison.diagnostics["design_effect"]["policy_a"]
    run.lines = [
        f"records: {len(frame)} observations in {truth['n_worlds']} worlds "
        f"({truth['observations_per_world']} observations per world per policy)",
        f"true mean loss difference (B - A): {truth['true_mean_loss_difference']:+.3f}",
        "",
        f"  WRONG  resident-level bootstrap: {run.naive['difference']:+.3f} "
        f"[{naive_lo:+.3f}, {naive_hi:+.3f}]  width {naive_width:.3f}",
        f"  RIGHT  world-level paired bootstrap: {comparison.difference.estimate:+.3f} "
        f"[{comparison.difference.ci_low:+.3f}, {comparison.difference.ci_high:+.3f}]  "
        f"width {world_width:.3f}",
        "",
        f"  width ratio (right / wrong): {world_width / max(naive_width, 1e-12):.1f}x",
        f"  measured coverage over {coverage['n_trials']} replications -- "
        f"resident: {coverage['resident_level']['coverage']:.0%}, "
        f"world: {coverage['world_level']['coverage']:.0%} (nominal 95%)",
    ]
    if deff.get("available"):
        run.lines.append(
            f"  design effect for policy_a: {deff['design_effect']:.1f} "
            f"(ICC {deff['icc']:.2f}, {deff['n_observations']} observations worth "
            f"{deff['effective_sample_size']:.0f} independent ones)"
        )
    return run


def _demo_world_bootstrap(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    coverage = coverage_study(n_trials=100, n_resamples=300, n_worlds=20,
                              events_per_world=2, residents_per_event=30, seed=seed)
    # More clusters, same observation budget per world: the percentile interval's
    # small-sample under-coverage should shrink towards nominal.
    coverage_large = coverage_study(n_trials=50, n_resamples=300, n_worlds=60,
                                    events_per_world=2, residents_per_event=30, seed=seed + 11)
    result = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    comparison = result.get("mean_loss", "policy_b")
    covered = comparison.difference.ci_low <= truth["true_mean_loss_difference"] <= comparison.difference.ci_high

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    run.correct = {
        "analysis": "paired world-level cluster bootstrap",
        "difference": comparison.difference.estimate,
        "ci": [comparison.difference.ci_low, comparison.difference.ci_high],
        "covers_truth": covered,
        "measured_coverage": coverage["world_level"]["coverage"],
        "measured_coverage_60_worlds": coverage_large["world_level"]["coverage"],
        "n_clusters": comparison.difference.n_clusters,
    }
    run.naive = {
        "analysis": "iid bootstrap over residents (for contrast)",
        "measured_coverage": coverage["resident_level"]["coverage"],
    }
    # The defence is calibration, not a single lucky interval.
    run.trap_reproduced = coverage["resident_level"]["coverage"] < 0.90
    run.defence_worked = coverage["world_level"]["coverage"] >= 0.85
    run.findings = [
        {
            "finding": "world-level cluster bootstrap is approximately calibrated",
            "measured_coverage": coverage["world_level"]["coverage"],
            "nominal": 0.95,
            "detail": (
                f"Across {coverage['n_trials']} replications the nominal-95% world-level "
                f"interval covered the true difference "
                f"{coverage['world_level']['coverage']:.0%} of the time at 20 worlds and "
                f"{coverage_large['world_level']['coverage']:.0%} at 60 worlds. The "
                "percentile bootstrap under-covers slightly when clusters are few, which is "
                "why the validator warns below 20 clusters rather than staying silent -- but "
                "it is calibrated in a way the resident-level interval never becomes, at any "
                "number of residents."
            ),
        },
        {
            "finding": "more residents per world cannot fix a resident-level interval",
            "detail": (
                "The resident-level interval narrows as residents are added while its "
                "coverage stays broken, because the missing variance is between worlds. "
                "Only more worlds buy precision that is real."
            ),
        },
    ]
    run.lines = [
        f"true mean loss difference (B - A): {truth['true_mean_loss_difference']:+.3f}",
        f"  world-level paired interval on this draw: "
        f"[{comparison.difference.ci_low:+.3f}, {comparison.difference.ci_high:+.3f}] "
        f"-> {'covers' if covered else 'misses'} the truth",
        f"  measured coverage over {coverage['n_trials']} replications at 20 worlds: "
        f"{coverage['world_level']['coverage']:.0%} (nominal 95%)",
        f"  measured coverage over {coverage_large['n_trials']} replications at 60 worlds: "
        f"{coverage_large['world_level']['coverage']:.0%} (nominal 95%)",
        f"  for contrast, resident-level coverage: {coverage['resident_level']['coverage']:.0%}",
        f"  mean interval width -- world: {coverage['world_level']['mean_width']:.3f}, "
        f"resident: {coverage['resident_level']['mean_width']:.3f}",
    ]
    return run


# --------------------------------------------------------------------------
# Scenario 3 -- tail risk disagreement
# --------------------------------------------------------------------------

def _build_tail_disagreement(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour(
                "policy_a",
                loss_shift=-3.2,
                interaction_sd=0.6,
                catastrophe_prob=0.04,
                catastrophe_magnitude=60.0,
                failure_mix={"unreachable": 0.55, "late_arrival": 0.25, "resource_exhausted": 0.20},
            ),
            PolicyBehaviour(
                "policy_b",
                loss_shift=0.0,
                interaction_sd=0.6,
                failure_mix={"late_arrival": 0.55, "route_blocked": 0.30, "resource_exhausted": 0.15},
            ),
        ],
        WorldModel(
            n_worlds=60,
            events_per_world=4,
            residents_per_event=15,
            world_sd=2.5,
            event_sd=0.8,
            resident_sd=1.0,
            strata=STRATA,
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": 0.8,
        "design": (
            "policy_a shifts loss down by 3.2 but carries a 4% chance of a +60 catastrophe "
            "per observation, worth +2.4 on average -- a net mean advantage of 0.8 bought "
            "with a heavy tail"
        ),
        "expected_direction": {"mean": "policy_a", "tail": "policy_b"},
    }
    return frame, truth


def _demo_tail(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    config = scenario.config()
    result = compare_policies(frame, config, validate=False)
    mean_cmp = result.get("mean_loss", "policy_b")
    cvar_cmp = result.get("cvar90_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    run.naive = {
        "analysis": "report mean loss only",
        "favours": mean_cmp.verdict.favours,
        "difference": mean_cmp.difference.estimate,
        "ci": [mean_cmp.difference.ci_low, mean_cmp.difference.ci_high],
        "conclusion_if_reported_alone": mean_cmp.verdict.sentence,
    }
    run.correct = {
        "analysis": "report mean and tail together",
        "mean_favours": mean_cmp.verdict.favours,
        "cvar90_favours": cvar_cmp.verdict.favours,
        "cvar90_difference": cvar_cmp.difference.estimate,
        "cvar90_ci": [cvar_cmp.difference.ci_low, cvar_cmp.difference.ci_high],
        "disagreements": result.disagreements,
    }
    run.trap_reproduced = mean_cmp.verdict.favours == "policy_a"
    run.defence_worked = bool(
        cvar_cmp.verdict.favours == "policy_b"
        and any(d["kind"] == "central_vs_tail" for d in result.disagreements)
    )
    run.findings = [
        {
            "finding": "mean and tail resolve in opposite directions",
            "mean_favours": mean_cmp.verdict.favours,
            "tail_favours": cvar_cmp.verdict.favours,
            "detail": (
                "Reporting either statistic alone yields a confident and opposite "
                "recommendation. The disagreement is the result, and the decision needs a "
                "pre-declared risk preference to resolve it."
            ),
        }
    ]
    run.lines = [
        f"  mean_loss   B - A = {mean_cmp.difference.estimate:+.3f} "
        f"[{mean_cmp.difference.ci_low:+.3f}, {mean_cmp.difference.ci_high:+.3f}] "
        f"-> favours {mean_cmp.verdict.favours}",
        f"  cvar90_loss B - A = {cvar_cmp.difference.estimate:+.3f} "
        f"[{cvar_cmp.difference.ci_low:+.3f}, {cvar_cmp.difference.ci_high:+.3f}] "
        f"-> favours {cvar_cmp.verdict.favours}",
        "",
    ] + [f"  {d['message']}" for d in result.disagreements]
    return run


# --------------------------------------------------------------------------
# Scenario 4 -- practical equivalence
# --------------------------------------------------------------------------

def _build_equivalence(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.25),
            PolicyBehaviour("policy_b", loss_shift=0.03, interaction_sd=0.25),
        ],
        WorldModel(
            n_worlds=120,
            events_per_world=4,
            residents_per_event=25,
            world_sd=3.0,
            event_sd=0.6,
            resident_sd=1.0,
            strata=STRATA,
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": 0.03,
        "practical_margin": 0.40,
        "design": "a real but negligible difference, with enough worlds to resolve it",
    }
    return frame, truth


def _demo_equivalence(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    config = scenario.config()
    result = compare_policies(frame, config, validate=False, metrics=["mean_loss"])
    cmp_ = result.get("mean_loss", "policy_b")
    eq = cmp_.equivalence

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    p = cmp_.difference.p_two_sided
    run.naive = {
        "analysis": "declare 'no difference' because the interval includes zero",
        "p_two_sided": p,
        "ci": [cmp_.difference.ci_low, cmp_.difference.ci_high],
        "why_wrong": (
            "An interval that includes zero is consistent with zero and with every other "
            "value it contains. Without a margin it cannot distinguish 'the same' from "
            "'we did not look hard enough'."
        ),
    }
    run.correct = {
        "analysis": "TOST against a declared practical margin",
        "margin": eq.margin if eq else None,
        "tost_interval": [eq.ci_low, eq.ci_high] if eq else None,
        "conclusion": eq.conclusion if eq else None,
        "interpretation": eq.interpretation if eq else None,
        "verdict": cmp_.verdict.label,
    }
    run.trap_reproduced = not cmp_.difference.excludes_zero
    run.defence_worked = bool(eq and eq.conclusion == "equivalent")
    run.findings = [
        {
            "finding": "equivalence requires a margin, and with one it is concludable",
            "margin": eq.margin if eq else None,
            "conclusion": eq.conclusion if eq else None,
            "detail": eq.interpretation if eq else "no margin declared",
        }
    ]
    run.lines = [
        f"  true difference: {truth['true_mean_loss_difference']:+.3f}, "
        f"declared practical margin: +/-{truth['practical_margin']}",
        f"  paired difference: {cmp_.difference.estimate:+.4f} "
        f"[{cmp_.difference.ci_low:+.4f}, {cmp_.difference.ci_high:+.4f}]"
        + (f"  (bootstrap p = {p:.3f})" if p is not None else ""),
        "",
        "  WRONG  'p > 0.05, therefore the policies are the same.'",
        f"  RIGHT  {eq.interpretation if eq else 'no margin declared'}",
        f"  verdict: {cmp_.verdict.label} -- {cmp_.verdict.sentence}",
    ]
    return run


# --------------------------------------------------------------------------
# Scenario 5 -- missing worlds reverse the ranking
# --------------------------------------------------------------------------

def _build_missing_worlds(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    full = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.5),
            PolicyBehaviour("policy_b", loss_shift=1.2, interaction_sd=0.5),
        ],
        WorldModel(
            n_worlds=60,
            events_per_world=3,
            residents_per_event=25,
            world_sd=5.0,
            event_sd=0.8,
            resident_sd=1.0,
            strata=STRATA,
        ),
        seed=seed,
    )
    hardest = hardest_worlds(full[full["policy_id"] == "policy_a"], 22)
    frame = drop_records(full, policy="policy_b", worlds=hardest)
    truth = {
        "true_mean_loss_difference": 1.2,
        "dropped_worlds": hardest,
        "n_dropped": len(hardest),
        "design": (
            "policy_b has no results on the 22 hardest worlds -- the pattern produced by "
            "runs that crash, time out, or are quietly excluded"
        ),
    }
    return frame, truth


def _demo_missing_worlds(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    paired_config = scenario.config()
    unpaired_config = _config(require_common_worlds=False, label="unpaired (wrong)")

    paired = compare_policies(frame, paired_config, validate=False, metrics=["mean_loss"])
    unpaired = compare_policies(frame, unpaired_config, validate=False, metrics=["mean_loss"])
    p_cmp = paired.get("mean_loss", "policy_b")
    u_cmp = unpaired.get("mean_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=paired)
    run.naive = {
        "analysis": "compare each policy over whatever worlds it has",
        "difference": u_cmp.difference.estimate,
        "ci": [u_cmp.difference.ci_low, u_cmp.difference.ci_high],
        "favours": u_cmp.verdict.favours,
    }
    run.correct = {
        "analysis": "paired comparison restricted to worlds both policies ran",
        "difference": p_cmp.difference.estimate,
        "ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
        "favours": p_cmp.verdict.favours,
        "n_common_worlds": p_cmp.difference.n_clusters,
        "excluded_worlds": truth["n_dropped"],
    }
    run.trap_reproduced = u_cmp.difference.estimate < 0 < p_cmp.difference.estimate
    run.defence_worked = p_cmp.verdict.favours == "policy_a"
    run.findings = [
        {
            "finding": "the missing worlds, not the policies, drive the unpaired ranking",
            "unpaired_difference": u_cmp.difference.estimate,
            "paired_difference": p_cmp.difference.estimate,
            "true_difference": truth["true_mean_loss_difference"],
            "detail": (
                f"policy_b is absent from the {truth['n_dropped']} hardest worlds. Comparing "
                "each policy over its own world set credits policy_b with the easy material "
                "it happened to be given."
            ),
        }
    ]
    run.lines = [
        f"  true difference (B - A): {truth['true_mean_loss_difference']:+.3f} "
        "(positive = B is worse)",
        f"  policy_b is missing from {truth['n_dropped']} of 60 worlds -- the hardest ones",
        "",
        f"  WRONG  unpaired over available worlds: {u_cmp.difference.estimate:+.3f} "
        f"[{u_cmp.difference.ci_low:+.3f}, {u_cmp.difference.ci_high:+.3f}] "
        f"-> favours {u_cmp.verdict.favours}",
        f"  RIGHT  paired on the {p_cmp.difference.n_clusters} shared worlds: "
        f"{p_cmp.difference.estimate:+.3f} "
        f"[{p_cmp.difference.ci_low:+.3f}, {p_cmp.difference.ci_high:+.3f}] "
        f"-> favours {p_cmp.verdict.favours}",
        "",
        "  The ranking reverses. Nothing about either policy changed; only which worlds "
        "were counted.",
    ]
    return run


# --------------------------------------------------------------------------
# Scenario 6 -- a policy tested on easier worlds
# --------------------------------------------------------------------------

def _build_easier_worlds(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    n_worlds = 60
    # Worlds alternate flat/steep; steep carries a large difficulty penalty.
    easy = [i for i in range(n_worlds) if i % 2 == 0]
    hard = [i for i in range(n_worlds) if i % 2 == 1]
    a_worlds = easy + hard[:6]          # policy_a mostly on easy worlds
    b_worlds = hard + easy[:6]          # policy_b mostly on hard worlds
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.4, world_subset=a_worlds),
            PolicyBehaviour("policy_b", loss_shift=-1.5, interaction_sd=0.4, world_subset=b_worlds),
        ],
        WorldModel(
            n_worlds=n_worlds,
            events_per_world=3,
            residents_per_event=25,
            world_sd=1.5,
            event_sd=0.7,
            resident_sd=1.0,
            strata=STRATA,
            stratum_effects={"landscape": {"flat": -4.0, "steep": 4.0}},
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": -1.5,
        "design": (
            "policy_a ran on 30 flat worlds and 6 steep; policy_b on 30 steep and 6 flat. "
            "Only 12 worlds carry both."
        ),
        "n_common_worlds": 12,
    }
    return frame, truth


def _demo_easier_worlds(scenario: Scenario, seed: int) -> ScenarioRun:
    from wg_eval.stratify import compare_by_stratum

    frame, truth = scenario.build(seed)
    paired_config = scenario.config()
    unpaired_config = _config(require_common_worlds=False, label="unpaired (wrong)")

    paired = compare_policies(frame, paired_config, validate=False, metrics=["mean_loss"])
    unpaired = compare_policies(frame, unpaired_config, validate=False, metrics=["mean_loss"])
    strat = compare_by_stratum(
        frame, _config(metrics=[_BASE_METRICS[0]], label="stratified"), "landscape",
        min_clusters=4, include_overall=False,
    )
    p_cmp = paired.get("mean_loss", "policy_b")
    u_cmp = unpaired.get("mean_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=paired)
    run.naive = {
        "analysis": "pool every world each policy ran and compare the means",
        "difference": u_cmp.difference.estimate,
        "ci": [u_cmp.difference.ci_low, u_cmp.difference.ci_high],
        "favours": u_cmp.verdict.favours,
    }
    run.correct = {
        "analysis": "paired on shared worlds, plus the allocation ledger and per-stratum results",
        "difference": p_cmp.difference.estimate,
        "ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
        "favours": p_cmp.verdict.favours,
        "n_common_worlds": p_cmp.difference.n_clusters,
        "allocation_balance": strat.balance,
        "per_stratum_favours": {
            k: v.get("mean_loss", "policy_b").verdict.favours for k, v in strat.per_stratum.items()
        },
    }
    run.trap_reproduced = u_cmp.verdict.favours == "policy_a"
    run.defence_worked = p_cmp.verdict.favours == "policy_b"
    run.findings = [
        {
            "finding": "allocation, not policy quality, drives the pooled ranking",
            "max_share_gap": strat.balance.get("max_share_gap"),
            "detail": (
                "The allocation table shows the policies were run on different terrain in "
                "different proportions. Pairing on the shared worlds and reporting per "
                "stratum both recover the true direction."
            ),
        }
    ]
    run.lines = [
        f"  true difference (B - A): {truth['true_mean_loss_difference']:+.3f} "
        "(negative = B is better)",
        f"  allocation: {truth['design']}",
        "",
        f"  WRONG  pooled over each policy's own worlds: {u_cmp.difference.estimate:+.3f} "
        f"[{u_cmp.difference.ci_low:+.3f}, {u_cmp.difference.ci_high:+.3f}] "
        f"-> favours {u_cmp.verdict.favours}",
        f"  RIGHT  paired on the {p_cmp.difference.n_clusters} shared worlds: "
        f"{p_cmp.difference.estimate:+.3f} "
        f"[{p_cmp.difference.ci_low:+.3f}, {p_cmp.difference.ci_high:+.3f}] "
        f"-> favours {p_cmp.verdict.favours}",
        "",
        f"  allocation imbalance on `landscape`: "
        f"{strat.balance.get('max_share_gap', float('nan')):.0%} share gap",
    ] + [
        f"  stratum {k}: favours {v.get('mean_loss', 'policy_b').verdict.favours}"
        for k, v in sorted(strat.per_stratum.items())
    ]
    return run


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            key="resident_bootstrap_false_precision",
            title="Resident-level bootstrap manufactures precision",
            trap="Resampling residents treats 1,200 nested observations as 1,200 experiments "
                 "and returns a confidence interval several times too narrow.",
            truth="The design supplies 20 independent worlds. The true mean loss difference "
                  "is +0.6.",
            defence="Resample whole worlds; residents travel with the world they belong to.",
            build=_build_pseudoreplication,
            config=lambda: _config(label="pseudoreplication demo"),
            demonstrate=_demo_resident_bootstrap,
        ),
        Scenario(
            key="world_bootstrap_correct",
            title="World-level bootstrap is calibrated",
            trap="A single interval proves nothing; calibration has to be measured over "
                 "repeated experiments.",
            truth="The true mean loss difference is +0.6 in every replication.",
            defence="Measure coverage: a nominal 95% interval should contain the truth about "
                    "95% of the time.",
            build=_build_pseudoreplication,
            config=lambda: _config(label="world-level bootstrap"),
            demonstrate=_demo_world_bootstrap,
        ),
        Scenario(
            key="tail_risk_disagreement",
            title="Mean favours A while tail risk favours B",
            trap="policy_a has the lower average loss, so a mean-only report recommends it.",
            truth="policy_a carries a 4% per-observation chance of a +60 loss; its 90% CVaR is "
                  "far worse than policy_b's while its mean is 0.8 better.",
            defence="Declare tail metrics up front and report the disagreement rather than "
                    "resolving it silently.",
            build=_build_tail_disagreement,
            config=lambda: _config(label="tail disagreement"),
            demonstrate=_demo_tail,
        ),
        Scenario(
            key="practical_equivalence",
            title="Practically equivalent, and provably so",
            trap="The interval includes zero, so the report says 'no significant difference' "
                 "and readers hear 'the same'.",
            truth="The true difference is +0.03 against a practical margin of 0.40 -- real, "
                  "and negligible.",
            defence="TOST against the declared margin turns 'we failed to detect' into "
                    "'we established equivalence'.",
            build=_build_equivalence,
            config=lambda: _config(margins={"mean_loss": 0.40}, label="equivalence"),
            demonstrate=_demo_equivalence,
        ),
        Scenario(
            key="missing_worlds_reverse_ranking",
            title="Missing worlds reverse the ranking",
            trap="policy_b has no results on the 22 hardest worlds; comparing each policy "
                 "over its own worlds makes the worse policy look better.",
            truth="policy_b is worse by +1.2 mean loss on every world.",
            defence="Pair on the worlds both policies ran, and record every world excluded.",
            build=_build_missing_worlds,
            config=lambda: _config(label="missing worlds"),
            demonstrate=_demo_missing_worlds,
        ),
        Scenario(
            key="easier_worlds_confound",
            title="A policy wins only because it was tested on easier worlds",
            trap="policy_a was run mostly on flat worlds and policy_b mostly on steep ones; "
                 "pooling the two makes policy_a look better.",
            truth="policy_b is better by -1.5 mean loss on identical worlds.",
            defence="Pair on shared worlds, print the allocation ledger, and report per stratum.",
            build=_build_easier_worlds,
            config=lambda: _config(label="allocation confound"),
            demonstrate=_demo_easier_worlds,
        ),
    ]
}


def list_scenarios() -> list[Scenario]:
    """Every registered scenario, in declaration order."""
    return list(SCENARIOS.values())


def build_scenario(key: str, seed: int = 20260919) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate one scenario's records and its known truth."""
    if key not in SCENARIOS:
        raise KeyError(f"unknown scenario {key!r}; known: {', '.join(SCENARIOS)}")
    return SCENARIOS[key].build(seed)


def run_scenario(key: str, seed: int = 20260919) -> ScenarioRun:
    """Build a scenario and run both the wrong and the right analysis on it."""
    if key not in SCENARIOS:
        raise KeyError(f"unknown scenario {key!r}; known: {', '.join(SCENARIOS)}")
    scenario = SCENARIOS[key]
    return scenario.demonstrate(scenario, seed)
