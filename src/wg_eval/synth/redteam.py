"""Adversarial scenarios: data built to fool a plausible-looking analysis.

Each scenario ships four things: records whose truth is known by construction,
the *wrong* analysis that the records are designed to reward, the right
analysis that recovers the truth, and an explicit statement of what the right
analysis still cannot conclude.

A scenario passes when the wrong analysis reaches the wrong conclusion **and**
the right one does not.  A scenario whose trap does not fire is testing
nothing, so both directions are asserted.

These are tests of the analysis, not of the data.  Nothing here models a real
process; the vocabulary is design vocabulary only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from wg_eval.bootstrap import naive_iid_bootstrap
from wg_eval.compare import ComparisonResult, compare_policies
from wg_eval.config import AnalysisConfig, config_from_mapping
from wg_eval.coverage import CoverageEstimate, compare_coverage, estimate_coverage
from wg_eval.hierarchy import InferenceSpec
from wg_eval.pairing import ClusteredValues
from wg_eval.synth.generators import (
    DEFAULT_STRATA,
    PolicyBehaviour,
    WorldModel,
    drop_records,
    generate_experiment,
    hardest_units,
    mark_status,
)

STRATA: dict[str, list[str]] = dict(DEFAULT_STRATA)
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
    #: What the defended analysis is entitled to say.
    can_conclude: str
    #: What it still cannot say, however clean the numbers look.
    cannot_conclude: str
    build: Callable[[int], tuple[pd.DataFrame, dict[str, Any]]]
    config: Callable[[], AnalysisConfig]
    demonstrate: Callable[["Scenario", int], "ScenarioRun"]
    #: Which audit item of the v0.1.0 review this scenario answers.
    audit_item: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "trap": self.trap,
            "truth": self.truth,
            "defence": self.defence,
            "can_conclude": self.can_conclude,
            "cannot_conclude": self.cannot_conclude,
            "audit_item": self.audit_item,
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
            f"TRAP       {self.scenario.trap}",
            f"TRUTH      {self.scenario.truth}",
            f"DEFENCE    {self.scenario.defence}",
            f"SUPPORTS   {self.scenario.can_conclude}",
            f"STILL NOT  {self.scenario.cannot_conclude}",
            "",
        ]
        return "\n".join(head + self.lines)


def _jsonable(value: Any) -> Any:
    if isinstance(value, CoverageEstimate):
        return value.as_dict()
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


# ---------------------------------------------------------------------------
# shared configuration
# ---------------------------------------------------------------------------

_BASE_METRICS = [
    {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
     "direction": "lower_is_better", "role": "primary", "bounds": {"lower": 0.0}},
    {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event_id",
     "params": {"alpha": 0.9}, "direction": "lower_is_better", "role": "secondary"},
    {"name": "p90_loss", "column": "loss", "estimator": "quantile", "level": "event_id",
     "params": {"q": 0.9}, "direction": "lower_is_better", "role": "exploratory"},
    {"name": "success_rate", "column": "mission_success", "estimator": "mean",
     "level": "resident_id", "direction": "higher_is_better", "role": "secondary",
     "bounds": {"lower": 0.0, "upper": 1.0}},
]

_BASE_AGGREGATION = {
    "resident_id->event_id": {
        "loss": "mean", "mission_success": "mean", "travel_time": "mean",
        "resource_use": "sum", "responder_exposure": "sum",
    },
    "event_id->world_id": {"loss": "mean", "mission_success": "mean"},
    "default_rule": "mean",
}

_STATUS_HANDLING = {
    "completed": "completed",
    "crashed": "failure",
    "timeout": "failure",
    "infeasible": "excluded_documented",
    "not_evaluated": "missing",
}


def _config(
    *,
    baseline: str = "policy_a",
    candidates: tuple[str, ...] = ("policy_b",),
    margins: dict[str, Any] | None = None,
    require_common_units: bool = True,
    seed: int = 20260919,
    n_resamples: int = 1500,
    metrics: list[dict[str, Any]] | None = None,
    label: str = "",
    primary_unit: str = "world_id",
    nested_units: tuple[str, ...] | None = None,
    aggregation: dict[str, Any] | None = None,
    secondary_correction: str = "none",
    exploratory_correction: str = "none",
    strata: list[str] | None = None,
    status_handling: dict[str, str] | None = None,
    assumed_mechanism: str = "unknown",
    method: str = "percentile",
    hierarchical: bool = False,
) -> AnalysisConfig:
    if nested_units is None:
        default_chain = ("world_id", "event_id", "resident_id")
        nested_units = tuple(u for u in default_chain if u != primary_unit)
        if primary_unit in default_chain:
            nested_units = default_chain[default_chain.index(primary_unit) + 1 :]
    return config_from_mapping(
        {
            "label": label,
            "inference": {"primary_unit": primary_unit, "nested_units": list(nested_units)},
            "metrics": metrics or _BASE_METRICS,
            "aggregation": aggregation or _BASE_AGGREGATION,
            "comparison": {
                "baseline": baseline,
                "candidates": list(candidates),
                "paired": True,
                "require_common_units": require_common_units,
            },
            "bootstrap": {
                "n_resamples": n_resamples,
                "seed": seed,
                "confidence_level": 0.95,
                "method": method,
                "hierarchical": hierarchical,
            },
            "equivalence": {"margins": margins or {}, "alpha": 0.05},
            "multiplicity": {
                "secondary_correction": secondary_correction,
                "exploratory_correction": exploratory_correction,
            },
            "strata": STRATUM_COLUMNS if strata is None else strata,
            "missing_data": {
                "policy": "drop_record",
                "status_handling": status_handling or _STATUS_HANDLING,
                "assumed_mechanism": assumed_mechanism,
            },
        }
    )


# ---------------------------------------------------------------------------
# shared generating processes and the coverage study
# ---------------------------------------------------------------------------

def _nested_pair(
    seed: int,
    *,
    n_units: int = 24,
    events_per_unit: int = 3,
    observations_per_event: int = 40,
    true_shift: float = 0.6,
    interaction_sd: float = 1.2,
    units_per_shared_event: int = 1,
    shared_event_sd: float = 0.0,
    group_interaction_sd: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Strongly clustered records: many observations per unit, real unit effects."""
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=interaction_sd,
                            group_interaction_sd=group_interaction_sd),
            PolicyBehaviour("policy_b", loss_shift=true_shift, interaction_sd=interaction_sd,
                            group_interaction_sd=group_interaction_sd),
        ],
        WorldModel(
            n_units=n_units,
            events_per_unit=events_per_unit,
            observations_per_event=observations_per_event,
            unit_sd=4.0,
            event_sd=1.2,
            observation_sd=1.0,
            strata=STRATA,
            units_per_shared_event=units_per_shared_event,
            shared_event_sd=shared_event_sd,
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": true_shift,
        "n_units": n_units,
        "observations_per_unit": events_per_unit * observations_per_event,
        "unit_sd": 4.0,
        "interaction_sd": interaction_sd,
    }
    return frame, truth


def _values_by(frame: pd.DataFrame, a: str, b: str, key: str = "world_id"):
    """Per-``key`` loss values for two policies over the keys they share."""
    work = frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work = work[work["loss"].notna()]
    keys = sorted(
        set(work.loc[work["policy_id"] == a, key].astype("string"))
        & set(work.loc[work["policy_id"] == b, key].astype("string"))
    )
    groups = {}
    for policy in (a, b):
        sub = work[work["policy_id"] == policy]
        by_key = {
            str(k): v["loss"].to_numpy(dtype="float64")
            for k, v in sub.groupby(key, observed=True)
        }
        groups[policy] = ClusteredValues.from_groups([by_key[k] for k in keys])
    return groups[a], groups[b]


def coverage_study(
    *,
    n_replications: int = 120,
    n_resamples: int = 400,
    n_units: int = 20,
    events_per_unit: int = 2,
    observations_per_event: int = 30,
    true_shift: float = 0.6,
    interaction_sd: float = 1.2,
    confidence_level: float = 0.95,
    seed: int = 20260919,
    units_per_shared_event: int = 1,
    shared_event_sd: float = 0.0,
    group_interaction_sd: float = 0.0,
    cluster_key: str = "world_id",
) -> dict[str, Any]:
    """Measure the actual coverage of observation-level vs unit-level intervals.

    Both analyses target the same estimand -- the true mean loss difference --
    on the same data.  Only the resampling unit differs.  Coverage is reported
    with its Monte Carlo standard error and a Wilson interval, because a
    coverage figure from a finite number of replications is itself an estimate:
    at R=100 an estimate of 0.90 is consistent with anything from about 0.83 to
    0.95, and reporting the bare percentage invites over-reading it.
    """
    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**31 - 1, size=n_replications)
    rows = []
    alpha = (1.0 - confidence_level) / 2.0
    for trial, trial_seed in enumerate(seeds):
        frame, _ = _nested_pair(
            int(trial_seed),
            n_units=n_units,
            events_per_unit=events_per_unit,
            observations_per_event=observations_per_event,
            true_shift=true_shift,
            interaction_sd=interaction_sd,
            units_per_shared_event=units_per_shared_event,
            shared_event_sd=shared_event_sd,
            group_interaction_sd=group_interaction_sd,
        )
        a_vals, b_vals = _values_by(frame, "policy_a", "policy_b", key=cluster_key)

        n_clusters = a_vals.n_clusters
        draw_rng = np.random.default_rng(int(trial_seed) + 1)
        draws = draw_rng.integers(0, n_clusters, size=(n_resamples, n_clusters))
        reps = np.array(
            [float(b_vals.take(d).mean() - a_vals.take(d).mean()) for d in draws], dtype="float64"
        )
        cluster_lo = float(np.quantile(reps, alpha))
        cluster_hi = float(np.quantile(reps, 1 - alpha))

        a_flat, b_flat = a_vals.all_values(), b_vals.all_values()
        naive_a = naive_iid_bootstrap(a_flat, np.mean, n_resamples=n_resamples,
                                      seed=int(trial_seed) + 2, confidence_level=confidence_level)
        naive_b = naive_iid_bootstrap(b_flat, np.mean, n_resamples=n_resamples,
                                      seed=int(trial_seed) + 3, confidence_level=confidence_level)
        n = min(len(naive_a.replicates), len(naive_b.replicates))
        naive_reps = naive_b.replicates[:n] - naive_a.replicates[:n]
        naive_lo = float(np.quantile(naive_reps, alpha))
        naive_hi = float(np.quantile(naive_reps, 1 - alpha))

        rows.append(
            {
                "trial": trial,
                "cluster_covers": bool(cluster_lo <= true_shift <= cluster_hi),
                "cluster_width": cluster_hi - cluster_lo,
                "naive_covers": bool(naive_lo <= true_shift <= naive_hi),
                "naive_width": naive_hi - naive_lo,
            }
        )

    table = pd.DataFrame(rows)
    cluster = estimate_coverage(
        table["cluster_covers"], label=f"{cluster_key}-level cluster bootstrap",
        nominal_level=confidence_level, widths=table["cluster_width"],
    )
    naive = estimate_coverage(
        table["naive_covers"], label="observation-level (pseudoreplicated) bootstrap",
        nominal_level=confidence_level, widths=table["naive_width"],
    )
    return {
        "n_replications": n_replications,
        "true_difference": true_shift,
        "confidence_level": confidence_level,
        "design": {
            "n_units": n_units,
            "events_per_unit": events_per_unit,
            "observations_per_event": observations_per_event,
            "observations_per_unit": events_per_unit * observations_per_event,
            "units_per_shared_event": units_per_shared_event,
            "shared_event_sd": shared_event_sd,
            "group_interaction_sd": group_interaction_sd,
            "cluster_key": cluster_key,
        },
        "cluster_level": cluster,
        "observation_level": naive,
        "difference": compare_coverage(cluster, naive),
        "table": table,
    }


# ---------------------------------------------------------------------------
# 1 & 2 -- pseudoreplication and its correction
# ---------------------------------------------------------------------------

def _build_pseudoreplication(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _nested_pair(seed, n_units=20, events_per_unit=2, observations_per_event=30)


def _demo_observation_bootstrap(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    a_vals, b_vals = _values_by(frame, "policy_a", "policy_b")
    naive_a = naive_iid_bootstrap(a_vals.all_values(), np.mean, n_resamples=1500, seed=seed)
    naive_b = naive_iid_bootstrap(b_vals.all_values(), np.mean, n_resamples=1500, seed=seed + 1)
    n = min(len(naive_a.replicates), len(naive_b.replicates))
    naive_reps = naive_b.replicates[:n] - naive_a.replicates[:n]
    naive_lo, naive_hi = float(np.quantile(naive_reps, 0.025)), float(np.quantile(naive_reps, 0.975))

    correct = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    comparison = correct.get("mean_loss", "policy_b")
    unit_width = comparison.difference.ci_high - comparison.difference.ci_low
    naive_width = naive_hi - naive_lo

    study = coverage_study(n_replications=200, n_resamples=300, n_units=20,
                           events_per_unit=2, observations_per_event=30, seed=seed)
    cluster, observation = study["cluster_level"], study["observation_level"]

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth)
    run.naive = {
        "analysis": "iid bootstrap over observations, both arms independent",
        "difference": float(naive_b.estimate - naive_a.estimate),
        "ci": [naive_lo, naive_hi],
        "width": naive_width,
        "n_units_claimed": int(len(a_vals.all_values())),
        "coverage": observation,
    }
    run.correct = {
        "analysis": "paired world_id-level cluster bootstrap",
        "difference": comparison.difference.estimate,
        "ci": [comparison.difference.ci_low, comparison.difference.ci_high],
        "width": unit_width,
        "n_units": comparison.difference.n_clusters,
        "coverage": cluster,
    }
    run.comparison = correct
    run.trap_reproduced = naive_width < unit_width
    # The defence is calibration, not one lucky interval: the wrong procedure's
    # coverage must be demonstrably below nominal, and the right one's must not be.
    run.defence_worked = bool(
        observation.wilson_ci[1] < cluster.empirical_coverage
        and not observation.consistent_with_nominal
    )
    run.findings = [
        {
            "finding": "observation-level resampling understates uncertainty",
            "width_ratio": float(unit_width / max(naive_width, 1e-12)),
            "detail": (
                f"The unit-level interval is {unit_width / max(naive_width, 1e-12):.1f}x wider. "
                f"The naive analysis claims {len(a_vals.all_values())} independent units where "
                f"the design supplies {comparison.difference.n_clusters}."
            ),
        },
        {
            "finding": "the understatement shows up as measurable under-coverage",
            "observation_level": observation.as_dict(),
            "cluster_level": cluster.as_dict(),
            "detail": (
                f"{observation.describe()} versus {cluster.describe()}. The two coverage "
                f"estimates differ by {study['difference']['difference']:.2f} "
                f"(MC SE {study['difference']['monte_carlo_se']:.3f}), so the gap is not "
                "Monte Carlo noise."
            ),
        },
    ]
    deff = comparison.diagnostics["design_effect"]["policy_a"]
    run.lines = [
        f"records: {len(frame)} observations in {truth['n_units']} units "
        f"({truth['observations_per_unit']} observations per unit per policy)",
        f"true mean loss difference (B - A): {truth['true_mean_loss_difference']:+.3f}",
        "",
        f"  WRONG  observation-level bootstrap: {run.naive['difference']:+.3f} "
        f"[{naive_lo:+.3f}, {naive_hi:+.3f}]  width {naive_width:.3f}",
        f"  RIGHT  unit-level paired bootstrap: {comparison.difference.estimate:+.3f} "
        f"[{comparison.difference.ci_low:+.3f}, {comparison.difference.ci_high:+.3f}]  "
        f"width {unit_width:.3f}",
        "",
        f"  width ratio (right / wrong): {unit_width / max(naive_width, 1e-12):.1f}x",
        f"  {observation.describe()}",
        f"  {cluster.describe()}",
    ]
    if deff.get("available"):
        run.lines.append(
            f"  design effect for policy_a: {deff['design_effect']:.1f} "
            f"(ICC {deff['icc']:.2f}, {deff['n_observations']} observations worth "
            f"{deff['effective_sample_size']:.0f} independent ones)"
        )
    return run


def _demo_unit_bootstrap(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    small = coverage_study(n_replications=200, n_resamples=300, n_units=20,
                           events_per_unit=2, observations_per_event=30, seed=seed)
    large = coverage_study(n_replications=200, n_resamples=300, n_units=60,
                           events_per_unit=2, observations_per_event=30, seed=seed + 11)
    result = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    comparison = result.get("mean_loss", "policy_b")
    covered = (
        comparison.difference.ci_low
        <= truth["true_mean_loss_difference"]
        <= comparison.difference.ci_high
    )

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    run.correct = {
        "analysis": "paired unit-level cluster bootstrap",
        "difference": comparison.difference.estimate,
        "ci": [comparison.difference.ci_low, comparison.difference.ci_high],
        "covers_truth": covered,
        "coverage_20_units": small["cluster_level"],
        "coverage_60_units": large["cluster_level"],
        "n_units": comparison.difference.n_clusters,
    }
    run.naive = {
        "analysis": "iid bootstrap over observations (for contrast)",
        "coverage_20_units": small["observation_level"],
        "coverage_60_units": large["observation_level"],
    }
    run.trap_reproduced = not small["observation_level"].consistent_with_nominal
    # At 20 units the percentile interval genuinely under-covers; the honest
    # claim is that it improves with more units, not that 20 is calibrated.
    run.defence_worked = bool(
        large["cluster_level"].empirical_coverage >= small["cluster_level"].empirical_coverage
        and large["cluster_level"].consistent_with_nominal
    )
    run.findings = [
        {
            "finding": "unit-level coverage approaches nominal as units are added",
            "coverage_20_units": small["cluster_level"].as_dict(),
            "coverage_60_units": large["cluster_level"].as_dict(),
            "detail": (
                f"At 20 units: {small['cluster_level'].describe()}"
                f"{' -- the Monte Carlo interval excludes the nominal level, so this is real under-coverage, not noise' if not small['cluster_level'].consistent_with_nominal else ''}. "
                f"At 60 units: {large['cluster_level'].describe()}."
            ),
        },
        {
            "finding": "more observations per unit cannot repair an observation-level interval",
            "detail": (
                "The observation-level interval narrows as observations are added while its "
                "coverage stays broken, because the missing variance is between units. Only "
                "more units buy precision that generalises."
            ),
        },
    ]
    run.lines = [
        f"true mean loss difference (B - A): {truth['true_mean_loss_difference']:+.3f}",
        f"  unit-level paired interval on this draw: "
        f"[{comparison.difference.ci_low:+.3f}, {comparison.difference.ci_high:+.3f}] "
        f"-> {'covers' if covered else 'misses'} the truth",
        "",
        f"  20 units: {small['cluster_level'].describe()}",
        f"  60 units: {large['cluster_level'].describe()}",
        f"  contrast, 20 units: {small['observation_level'].describe()}",
        "",
        f"  consistent with nominal at 20 units: {small['cluster_level'].consistent_with_nominal}",
        f"  consistent with nominal at 60 units: {large['cluster_level'].consistent_with_nominal}",
    ]
    return run


# ---------------------------------------------------------------------------
# 3 -- central and tail contrasts disagree
# ---------------------------------------------------------------------------

def _build_tail_disagreement(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", loss_shift=-3.2, interaction_sd=0.6,
                            catastrophe_prob=0.04, catastrophe_magnitude=60.0,
                            failure_mix={"reason_a": 0.55, "reason_b": 0.25, "reason_c": 0.20}),
            PolicyBehaviour("policy_b", loss_shift=0.0, interaction_sd=0.6,
                            failure_mix={"reason_b": 0.55, "reason_d": 0.30, "reason_c": 0.15}),
        ],
        WorldModel(n_units=60, events_per_unit=4, observations_per_event=15,
                   unit_sd=2.5, event_sd=0.8, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": 0.8,
        "design": (
            "policy_a shifts loss down by 3.2 but carries a 4% chance of a +60 spike per "
            "observation, worth +2.4 on average -- a net mean advantage of 0.8 bought with a "
            "heavy upper tail"
        ),
        "expected_direction": {"mean": "policy_a", "tail": "policy_b"},
    }
    return frame, truth


def _demo_tail(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    result = compare_policies(frame, scenario.config(), validate=False)
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
        "analysis": "report central and tail contrasts together, with both intervals",
        "orientation": mean_cmp.metric.direction,
        "harmful_tail": cvar_cmp.metric.tail_side,
        "mean": {"favours": mean_cmp.verdict.favours,
                 "difference": mean_cmp.difference.estimate,
                 "ci": [mean_cmp.difference.ci_low, mean_cmp.difference.ci_high]},
        "cvar90": {"favours": cvar_cmp.verdict.favours,
                   "difference": cvar_cmp.difference.estimate,
                   "ci": [cvar_cmp.difference.ci_low, cvar_cmp.difference.ci_high]},
        "disagreements": result.disagreements,
    }
    run.trap_reproduced = mean_cmp.verdict.favours == "policy_a"
    run.defence_worked = bool(
        cvar_cmp.verdict.favours == "policy_b"
        and any(d["kind"] == "central_vs_tail" for d in result.disagreements)
    )
    run.findings = [
        {
            "finding": "central and tail contrasts resolve in opposite directions",
            "mean_favours": mean_cmp.verdict.favours,
            "tail_favours": cvar_cmp.verdict.favours,
            "detail": (
                "Reporting either statistic alone yields a confident and opposite "
                "recommendation. The disagreement is the result; resolving it needs a risk "
                "preference declared in advance, which is not a statistical quantity."
            ),
        }
    ]
    run.lines = [
        f"  orientation: {mean_cmp.metric.direction}; harmful tail is the "
        f"{cvar_cmp.metric.tail_side} one",
        f"  mean_loss   B - A = {mean_cmp.difference.estimate:+.3f} "
        f"[{mean_cmp.difference.ci_low:+.3f}, {mean_cmp.difference.ci_high:+.3f}] "
        f"-> favours {mean_cmp.verdict.favours}",
        f"  cvar90_loss B - A = {cvar_cmp.difference.estimate:+.3f} "
        f"[{cvar_cmp.difference.ci_low:+.3f}, {cvar_cmp.difference.ci_high:+.3f}] "
        f"-> favours {cvar_cmp.verdict.favours}",
        "",
    ] + [f"  {d['message']}" for d in result.disagreements]
    return run


# ---------------------------------------------------------------------------
# 4 -- practical equivalence
# ---------------------------------------------------------------------------

def _build_equivalence(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.25),
            PolicyBehaviour("policy_b", loss_shift=0.03, interaction_sd=0.25),
        ],
        WorldModel(n_units=120, events_per_unit=4, observations_per_event=25,
                   unit_sd=3.0, event_sd=0.6, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": 0.03,
        "practical_margin": 0.40,
        "design": "a real but negligible difference, with enough units to resolve it",
    }
    return frame, truth


_EQUIVALENCE_MARGIN = {
    "mean_loss": {
        "lower": 0.40, "upper": 0.40, "scale": "absolute",
        "source": "declared in the scenario definition before any data was generated",
    }
}


def _demo_equivalence(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    result = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    cmp_ = result.get("mean_loss", "policy_b")
    eq = cmp_.equivalence

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    p = cmp_.difference.p_two_sided
    run.naive = {
        "analysis": "read a zero-containing interval as a finding of equivalence",
        "p_two_sided": p,
        "ci": [cmp_.difference.ci_low, cmp_.difference.ci_high],
        "why_wrong": (
            "An interval that includes zero is consistent with zero and with every other value "
            "it contains. Without a margin it cannot distinguish a negligible difference from "
            "an unresolved one."
        ),
    }
    run.correct = {
        "analysis": "TOST against a declared margin, with the decision caveat attached",
        "margin": eq.margin.as_dict() if eq else None,
        "tost_interval": [eq.ci_low, eq.ci_high] if eq else None,
        "conclusion": eq.conclusion if eq else None,
        "interpretation": eq.interpretation if eq else None,
        "caveats": eq.notes if eq else [],
        "verdict": cmp_.verdict.label,
    }
    run.trap_reproduced = not cmp_.difference.excludes_zero
    run.defence_worked = bool(eq and eq.conclusion == "statistically_equivalent")
    run.findings = [
        {
            "finding": "equivalence needs a margin, and with one it is concludable",
            "margin": eq.margin.describe() if eq else None,
            "conclusion": eq.conclusion if eq else None,
            "detail": eq.interpretation if eq else "no margin declared",
        },
        {
            "finding": "statistical equivalence is not operational interchangeability",
            "detail": (
                "The verdict states that the contrast lies inside a predeclared margin. "
                "Whether that margin marks a decision-relevant difference is a domain "
                "judgement the library does not make."
            ),
        },
    ]
    run.lines = [
        f"  true difference: {truth['true_mean_loss_difference']:+.3f}, declared margin: "
        f"{eq.margin.describe() if eq else 'none'}",
        f"  paired difference: {cmp_.difference.estimate:+.4f} "
        f"[{cmp_.difference.ci_low:+.4f}, {cmp_.difference.ci_high:+.4f}]"
        + (f"  (bootstrap p = {p:.3f})" if p is not None else ""),
        "",
        "  WRONG  'the interval contains zero, so the policies match.'",
        f"  RIGHT  {eq.interpretation if eq else 'no margin declared'}",
        f"  verdict: {cmp_.verdict.label}",
    ] + [f"  caveat: {n}" for n in (eq.notes if eq else [])]
    return run


# ---------------------------------------------------------------------------
# 5 -- missing units reverse the ranking
# ---------------------------------------------------------------------------

def _build_missing_units(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    full = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.5),
            PolicyBehaviour("policy_b", loss_shift=1.2, interaction_sd=0.5),
        ],
        WorldModel(n_units=60, events_per_unit=3, observations_per_event=25,
                   unit_sd=5.0, event_sd=0.8, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    hardest = hardest_units(full[full["policy_id"] == "policy_a"], 22)
    frame = drop_records(full, policy="policy_b", units=hardest)
    truth = {
        "true_mean_loss_difference": 1.2,
        "dropped_units": hardest,
        "n_dropped": len(hardest),
        "design": (
            "policy_b has no rows at all on the 22 hardest units -- the pattern left by runs "
            "that were never recorded"
        ),
    }
    return frame, truth


def _demo_missing_units(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    paired = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    unpaired = compare_policies(
        frame, _config(require_common_units=False, label="unpaired (wrong)"),
        validate=False, metrics=["mean_loss"],
    )
    p_cmp = paired.get("mean_loss", "policy_b")
    u_cmp = unpaired.get("mean_loss", "policy_b")
    overlap = p_cmp.diagnostics["overlap"]

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=paired)
    run.naive = {
        "analysis": "compare each policy over whatever units it has",
        "difference": u_cmp.difference.estimate,
        "ci": [u_cmp.difference.ci_low, u_cmp.difference.ci_high],
        "favours": u_cmp.verdict.favours,
    }
    run.correct = {
        "analysis": "paired comparison restricted to units both policies ran",
        "difference": p_cmp.difference.estimate,
        "ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
        "favours": p_cmp.verdict.favours,
        "n_common_units": p_cmp.difference.n_clusters,
        "excluded_units": truth["n_dropped"],
        "estimand_conditioning": p_cmp.diagnostics["estimand_conditioning"],
        "overlap_representativeness": overlap,
    }
    run.trap_reproduced = u_cmp.difference.estimate < 0 < p_cmp.difference.estimate
    run.defence_worked = bool(
        p_cmp.verdict.favours == "policy_a" and overlap.get("representativeness_questionable")
    )
    run.findings = [
        {
            "finding": "the missing units, not the policies, drive the unpaired ranking",
            "unpaired_difference": u_cmp.difference.estimate,
            "paired_difference": p_cmp.difference.estimate,
            "true_difference": truth["true_mean_loss_difference"],
            "detail": (
                f"policy_b is absent from the {truth['n_dropped']} hardest units. Comparing each "
                "policy over its own unit set credits policy_b with the easier material it "
                "happened to be given."
            ),
        },
        {
            "finding": "the paired estimate is correct on the shared units and not representative",
            "detail": overlap.get("message", ""),
        },
    ]
    run.lines = [
        f"  true difference (B - A): {truth['true_mean_loss_difference']:+.3f} "
        "(positive = B is worse)",
        f"  policy_b is missing from {truth['n_dropped']} of 60 units -- the hardest ones",
        "",
        f"  WRONG  unpaired over available units: {u_cmp.difference.estimate:+.3f} "
        f"[{u_cmp.difference.ci_low:+.3f}, {u_cmp.difference.ci_high:+.3f}] "
        f"-> favours {u_cmp.verdict.favours}",
        f"  RIGHT  paired on the {p_cmp.difference.n_clusters} shared units: "
        f"{p_cmp.difference.estimate:+.3f} "
        f"[{p_cmp.difference.ci_low:+.3f}, {p_cmp.difference.ci_high:+.3f}] "
        f"-> favours {p_cmp.verdict.favours}",
        "",
        f"  estimand: {p_cmp.diagnostics['estimand_conditioning']}",
        f"  {overlap.get('message', '')}",
    ]
    return run


# ---------------------------------------------------------------------------
# 6 -- a policy allocated easier units
# ---------------------------------------------------------------------------

def _build_easier_units(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    n_units = 60
    easy = [i for i in range(n_units) if i % 2 == 0]
    hard = [i for i in range(n_units) if i % 2 == 1]
    a_units = easy + hard[:6]
    b_units = hard + easy[:6]
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.4, unit_subset=a_units),
            PolicyBehaviour("policy_b", loss_shift=-1.5, interaction_sd=0.4, unit_subset=b_units),
        ],
        WorldModel(
            n_units=n_units, events_per_unit=3, observations_per_event=25,
            unit_sd=1.5, event_sd=0.7, observation_sd=1.0, strata=STRATA,
            stratum_effects={"difficulty": {"low": -4.0, "high": 4.0}},
        ),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": -1.5,
        "design": (
            "policy_a ran on 30 low-difficulty units and 6 high; policy_b on 30 high and 6 low. "
            "Only 12 units carry both."
        ),
        "n_common_units": 12,
    }
    return frame, truth


def _demo_easier_units(scenario: Scenario, seed: int) -> ScenarioRun:
    from wg_eval.stratify import compare_by_stratum

    frame, truth = scenario.build(seed)
    paired = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    unpaired = compare_policies(
        frame, _config(require_common_units=False, label="unpaired (wrong)"),
        validate=False, metrics=["mean_loss"],
    )
    strat = compare_by_stratum(
        frame, _config(metrics=[_BASE_METRICS[0]], label="stratified"), "difficulty",
        min_units=4, include_overall=False,
    )
    p_cmp = paired.get("mean_loss", "policy_b")
    u_cmp = unpaired.get("mean_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=paired)
    run.naive = {
        "analysis": "pool every unit each policy ran and compare the means",
        "difference": u_cmp.difference.estimate,
        "ci": [u_cmp.difference.ci_low, u_cmp.difference.ci_high],
        "favours": u_cmp.verdict.favours,
    }
    run.correct = {
        "analysis": "paired on shared units, plus the allocation ledger and per-stratum results",
        "difference": p_cmp.difference.estimate,
        "ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
        "favours": p_cmp.verdict.favours,
        "n_common_units": p_cmp.difference.n_clusters,
        "allocation_balance": strat.balance,
        "composition_shift": (strat.ledger.get("strata", {}).get("difficulty") or {}).get(
            "composition_shift"
        ),
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
                "The allocation ledger shows the policies were run on different material in "
                "different proportions. Pairing on shared units and reporting per stratum both "
                "recover the true direction. The ledger describes the allocation; it does not "
                "diagnose why the allocation happened."
            ),
        }
    ]
    run.lines = [
        f"  true difference (B - A): {truth['true_mean_loss_difference']:+.3f} "
        "(negative = B has lower loss)",
        f"  allocation: {truth['design']}",
        "",
        f"  WRONG  pooled over each policy's own units: {u_cmp.difference.estimate:+.3f} "
        f"[{u_cmp.difference.ci_low:+.3f}, {u_cmp.difference.ci_high:+.3f}] "
        f"-> favours {u_cmp.verdict.favours}",
        f"  RIGHT  paired on the {p_cmp.difference.n_clusters} shared units: "
        f"{p_cmp.difference.estimate:+.3f} "
        f"[{p_cmp.difference.ci_low:+.3f}, {p_cmp.difference.ci_high:+.3f}] "
        f"-> favours {p_cmp.verdict.favours}",
        "",
        f"  allocation imbalance on `difficulty`: "
        f"{strat.balance.get('max_share_gap', float('nan')):.0%} share gap",
    ] + [
        f"  stratum {k}: favours {v.get('mean_loss', 'policy_b').verdict.favours}"
        for k, v in sorted(strat.per_stratum.items())
    ]
    return run


# ---------------------------------------------------------------------------
# 7 -- dependence above the declared unit
# ---------------------------------------------------------------------------

def _build_shared_event_shock(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, truth = _nested_pair(
        seed, n_units=80, events_per_unit=1, observations_per_event=10,
        true_shift=0.6, interaction_sd=0.25,
        units_per_shared_event=4, shared_event_sd=3.0, group_interaction_sd=1.1,
    )
    truth.update(
        {
            "units_per_shared_event": 4,
            "n_independent_events": 20,
            "design": (
                "80 units, but each group of 4 shares one event. The event carries a shock that "
                "affects the two policies DIFFERENTLY, so it does not cancel in the paired "
                "difference. The event is a level ABOVE the unit: there are 20 independent "
                "replicates, not 80."
            ),
        }
    )
    return frame, truth


def _demo_shared_event_shock(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    run = ScenarioRun(scenario=scenario, records=frame, truth=truth)

    # The wrong declaration is refused outright rather than silently analysed.
    refused = ""
    try:
        compare_policies(frame, _config(primary_unit="world_id"), validate=False,
                         metrics=["mean_loss"])
    except Exception as exc:  # noqa: BLE001 - the refusal is the finding
        refused = str(exc)

    study_kwargs = dict(
        n_replications=150, n_resamples=250, n_units=80, events_per_unit=1,
        observations_per_event=10, true_shift=0.6, interaction_sd=0.25,
        units_per_shared_event=4, shared_event_sd=3.0, group_interaction_sd=1.1, seed=seed,
    )
    unit_study = coverage_study(**study_kwargs, cluster_key="world_id")
    event_study = coverage_study(**study_kwargs, cluster_key="event_id")

    correct_config = _config(primary_unit="event_id", nested_units=("world_id", "resident_id"),
                             aggregation={"resident_id->world_id": {"loss": "mean"},
                                          "world_id->event_id": {"loss": "mean"},
                                          "default_rule": "mean"},
                             metrics=[{"name": "mean_loss", "column": "loss", "estimator": "mean",
                                       "level": "world_id", "direction": "lower_is_better",
                                       "role": "primary"}],
                             label="event-level inference")
    correct = compare_policies(frame, correct_config, validate=False, metrics=["mean_loss"])
    cmp_ = correct.get("mean_loss", "policy_b")

    run.naive = {
        "analysis": "declare world_id as the independent unit",
        "refused_with": refused,
        "coverage_if_forced": unit_study["cluster_level"],
    }
    run.correct = {
        "analysis": "declare event_id as the independent unit (events contain units here)",
        "difference": cmp_.difference.estimate,
        "ci": [cmp_.difference.ci_low, cmp_.difference.ci_high],
        "n_units": cmp_.difference.n_clusters,
        "coverage": event_study["cluster_level"],
    }
    run.comparison = correct
    run.trap_reproduced = bool(refused) and not unit_study["cluster_level"].consistent_with_nominal
    run.defence_worked = bool(
        "inverted_nesting" in refused or "primary_unit" in refused
    ) and event_study["cluster_level"].empirical_coverage > unit_study["cluster_level"].empirical_coverage
    run.findings = [
        {
            "finding": "a unit-level bootstrap fails when units share a higher-level shock",
            "unit_level": unit_study["cluster_level"].as_dict(),
            "event_level": event_study["cluster_level"].as_dict(),
            "detail": (
                f"Resampling the 48 units gives {unit_study['cluster_level'].describe()}; "
                f"resampling the 8 shared events gives {event_study['cluster_level'].describe()}. "
                "Cluster resampling is only correct at the level where independence actually "
                "holds, and 'cluster bootstrap' is not a synonym for 'correct'."
            ),
        },
        {
            "finding": "the wrong declaration is refused, not silently analysed",
            "detail": refused[:400],
        },
    ]
    run.lines = [
        f"  design: {truth['design']}",
        f"  true difference: {truth['true_mean_loss_difference']:+.3f}",
        "",
        "  WRONG  inference.primary_unit: world_id",
        f"         refused: {refused.splitlines()[0][:150] if refused else 'NOT REFUSED'}",
        f"         if forced anyway: {unit_study['cluster_level'].describe()}",
        "",
        "  RIGHT  inference.primary_unit: event_id",
        f"         {cmp_.difference.estimate:+.3f} "
        f"[{cmp_.difference.ci_low:+.3f}, {cmp_.difference.ci_high:+.3f}] "
        f"over {cmp_.difference.n_clusters} events",
        f"         {event_study['cluster_level'].describe()}",
    ]
    return run


# ---------------------------------------------------------------------------
# 8 -- missingness that depends on the outcome
# ---------------------------------------------------------------------------

def _build_mnar_missing(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    full = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.5),
            PolicyBehaviour("policy_b", loss_shift=1.0, interaction_sd=0.5),
        ],
        WorldModel(n_units=50, events_per_unit=3, observations_per_event=20,
                   unit_sd=5.0, event_sd=0.8, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    hardest = hardest_units(full[full["policy_id"] == "policy_b"], 18)
    frame = mark_status(full, policy="policy_b", units=hardest, status="crashed")
    truth = {
        "true_mean_loss_difference": 1.0,
        "n_crashed_units": len(hardest),
        "crashed_units": hardest,
        "mechanism": "MNAR",
        "design": (
            "policy_b's runs crash on the 18 hardest units. The rows are still present with a "
            "`crashed` status and null outcomes, so the failure is visible rather than absent."
        ),
    }
    return frame, truth


def _demo_mnar_missing(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    result = compare_policies(frame, scenario.config(), validate=False, metrics=["mean_loss"])
    cmp_ = result.get("mean_loss", "policy_b")
    ledger = (result.run_status or {}).get("ledger", {})

    worst = compare_policies(
        frame,
        _config(label="worst-case imputation",
                status_handling={**_STATUS_HANDLING, "crashed": "missing"},
                assumed_mechanism="MNAR"),
        validate=False, metrics=["mean_loss"],
    )
    # Sensitivity bound: fill the crashed runs with the worst observed value.
    from wg_eval.config import config_from_mapping as _cfm

    bound_cfg = _cfm(
        {
            **worst.config.raw,
            "missing_data": {**worst.config.raw.get("missing_data", {}),
                             "policy": "impute_worst", "assumed_mechanism": "MNAR"},
        }
    )
    bounded = compare_policies(frame, bound_cfg, validate=False, metrics=["mean_loss"])
    b_cmp = bounded.get("mean_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    run.naive = {
        "analysis": "drop the crashed runs and analyse what is left",
        "difference": cmp_.difference.estimate,
        "ci": [cmp_.difference.ci_low, cmp_.difference.ci_high],
        "n_units": cmp_.difference.n_clusters,
        "why_wrong": (
            "The runs that crashed are the hard ones. Complete-case analysis then describes the "
            "units where policy_b happened to survive."
        ),
    }
    run.correct = {
        "analysis": "keep the failures visible, declare the mechanism, and bound the estimate",
        "run_status_ledger": ledger.get("counts"),
        "assumed_mechanism": ledger.get("assumed_mechanism"),
        "complete_case_difference": cmp_.difference.estimate,
        "worst_case_difference": b_cmp.difference.estimate,
        "overlap": cmp_.diagnostics["overlap"],
    }
    understates = cmp_.difference.estimate < truth["true_mean_loss_difference"]
    run.trap_reproduced = bool(understates)
    run.defence_worked = bool(
        ledger.get("available")
        and int(ledger.get("n_not_completed", 0)) > 0
        and b_cmp.difference.estimate > cmp_.difference.estimate
    )
    run.findings = [
        {
            "finding": "a crashed run is recorded, not absent",
            "n_not_completed": ledger.get("n_not_completed"),
            "detail": (
                "The run-status ledger counts every run by policy and status before any row is "
                "dropped, so a policy cannot improve its numbers by failing to produce them."
            ),
        },
        {
            "finding": "complete-case and worst-case bounds disagree, which is the result",
            "complete_case": cmp_.difference.estimate,
            "worst_case": b_cmp.difference.estimate,
            "detail": (
                f"Complete-case gives {cmp_.difference.estimate:+.3f}; imputing the worst "
                f"observed value for the crashed runs gives {b_cmp.difference.estimate:+.3f}. "
                f"The truth is {truth['true_mean_loss_difference']:+.3f}. When the bounds "
                "straddle the decision, the missing data decides it and the analysis does not."
            ),
        },
    ]
    run.lines = [
        f"  true difference (B - A): {truth['true_mean_loss_difference']:+.3f}",
        f"  {truth['n_crashed_units']} of 50 units have policy_b runs marked `crashed`; "
        f"mechanism declared `{truth['mechanism']}`",
        "",
        f"  WRONG  drop them: {cmp_.difference.estimate:+.3f} "
        f"[{cmp_.difference.ci_low:+.3f}, {cmp_.difference.ci_high:+.3f}] over "
        f"{cmp_.difference.n_clusters} units",
        f"  BOUND  impute worst: {b_cmp.difference.estimate:+.3f} "
        f"[{b_cmp.difference.ci_low:+.3f}, {b_cmp.difference.ci_high:+.3f}]",
        "",
        f"  ledger: {ledger.get('n_not_completed')} rows did not complete; they stay in the "
        "accounting",
        f"  {cmp_.diagnostics['overlap'].get('message', '')}",
    ]
    return run


# ---------------------------------------------------------------------------
# 9 -- too few independent units
# ---------------------------------------------------------------------------

def _build_tiny(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, truth = _nested_pair(seed, n_units=5, events_per_unit=4,
                                observations_per_event=40, true_shift=0.6)
    truth["design"] = "5 units, 160 observations each: plenty of rows, almost no replicates"
    return frame, truth


def _demo_tiny(scenario: Scenario, seed: int) -> ScenarioRun:
    sizes = (5, 10, 20, 50)
    rows: list[dict[str, Any]] = []
    flagged: dict[int, list[str]] = {}
    for n in sizes:
        frame, truth = _nested_pair(seed + n, n_units=n, events_per_unit=4,
                                    observations_per_event=40, true_shift=0.6)
        cfg = _config(n_resamples=800, label=f"n={n}")
        result = compare_policies(frame, cfg, validate=False,
                                  metrics=["mean_loss", "cvar90_loss", "p90_loss"])
        notes: list[str] = []
        for name in ("mean_loss", "cvar90_loss", "p90_loss"):
            c = result.get(name, "policy_b")
            rows.append(
                {
                    "n_units": n,
                    "metric": name,
                    "difference": c.difference.estimate,
                    "ci_low": c.difference.ci_low,
                    "ci_high": c.difference.ci_high,
                    "width": c.difference.ci_high - c.difference.ci_low,
                    "credible": bool(c.difference.credibility.get("credible", True)),
                    "covers_truth": bool(c.difference.ci_low <= 0.6 <= c.difference.ci_high)
                    if name == "mean_loss" else None,
                    "verdict": c.verdict.label,
                }
            )
            if not c.difference.credibility.get("credible", True):
                notes.append(name)
        flagged[n] = notes

    table = pd.DataFrame(rows)
    frame, truth = scenario.build(seed)
    run = ScenarioRun(scenario=scenario, records=frame, truth=truth)
    run.naive = {
        "analysis": "report a 90% CVaR and a 90th percentile from 5 units without comment",
        "flagged_at_5": flagged[5],
    }
    run.correct = {
        "analysis": "per-metric credibility thresholds, so the warning matches the statistic",
        "flagged_by_n": flagged,
        "stability": table.to_dict(orient="records"),
    }
    # The trap: tail statistics are computed happily at n=5 and are meaningless.
    run.trap_reproduced = bool(flagged[5] and "cvar90_loss" in flagged[5])
    # The defence: the mean survives to larger n without a credibility flag, and
    # the tail metrics stop being flagged only once there are enough units.
    run.defence_worked = bool("mean_loss" not in flagged[20] and flagged[10])
    widths = table.pivot_table(index="n_units", columns="metric", values="width")
    run.findings = [
        {
            "finding": "credibility is metric-specific, not a universal minimum n",
            "flagged_by_n": flagged,
            "detail": (
                "`mean` is flagged below 5 units, `cvar` and `quantile` below 20, `max`/`min` "
                "below 30. A single global threshold would either pass a meaningless CVaR or "
                "block a usable mean."
            ),
        },
        {
            "finding": "interval width shrinks with units, not with observations",
            "widths": widths.round(3).to_dict(),
            "detail": (
                "Every design here has 160 observations per unit. Only the unit count changes, "
                "and only the unit count narrows the intervals."
            ),
        },
    ]
    run.lines = [f"  {truth['design']}", ""]
    run.lines.append(f"  {'n':>4}  {'metric':<12}  {'difference':>22}  {'width':>7}  credible")
    for row in rows:
        run.lines.append(
            f"  {row['n_units']:>4}  {row['metric']:<12}  "
            f"{row['difference']:+.3f} [{row['ci_low']:+.3f}, {row['ci_high']:+.3f}]  "
            f"{row['width']:>7.3f}  {'yes' if row['credible'] else 'NO'}"
        )
    return run


# ---------------------------------------------------------------------------
# 10 -- the wrong tail
# ---------------------------------------------------------------------------

def _build_wrong_tail(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", success_shift=1.9, interaction_sd=0.3,
                            event_catastrophe_prob=0.10, event_catastrophe_magnitude=45.0),
            PolicyBehaviour("policy_b", success_shift=0.0, interaction_sd=0.3),
        ],
        WorldModel(n_units=70, events_per_unit=4, observations_per_event=20,
                   unit_sd=2.0, event_sd=0.5, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "design": (
            "policy_a has the higher mean success rate but a heavy LOW tail: one event in ten "
            "is a whole-event disaster in which success collapses."
        ),
        "direction": "higher_is_better",
        "harmful_tail": "lower",
    }
    return frame, truth


_SUCCESS_CVAR_RIGHT = {
    "name": "cvar_success", "column": "mission_success", "estimator": "cvar",
    "level": "event_id", "params": {"alpha": 0.9}, "direction": "higher_is_better",
    "tail": "harmful", "role": "primary", "bounds": {"lower": 0.0, "upper": 1.0},
}
_SUCCESS_CVAR_WRONG = {**_SUCCESS_CVAR_RIGHT, "tail": "upper", "role": "primary"}
_SUCCESS_MEAN = {
    "name": "mean_success", "column": "mission_success", "estimator": "mean",
    "level": "event_id", "direction": "higher_is_better", "role": "secondary",
    "bounds": {"lower": 0.0, "upper": 1.0},
}


def _demo_wrong_tail(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    agg = {"resident_id->event_id": {"mission_success": "mean"}, "default_rule": "mean"}
    right = compare_policies(
        frame, _config(metrics=[_SUCCESS_CVAR_RIGHT, _SUCCESS_MEAN], aggregation=agg,
                       label="harmful tail"),
        validate=False,
    )
    wrong = compare_policies(
        frame, _config(metrics=[_SUCCESS_CVAR_WRONG, _SUCCESS_MEAN], aggregation=agg,
                       label="beneficial tail"),
        validate=False,
    )
    r_cmp = right.get("cvar_success", "policy_b")
    w_cmp = wrong.get("cvar_success", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=right)
    run.naive = {
        "analysis": "CVaR with the upper tail on a higher-is-better metric",
        "tail_used": w_cmp.metric.tail_side,
        "difference": w_cmp.difference.estimate,
        "ci": [w_cmp.difference.ci_low, w_cmp.difference.ci_high],
        "favours": w_cmp.verdict.favours,
        "why_wrong": (
            "For a higher-is-better metric the harmful end is the LOW end. Averaging the best "
            "10% of events describes the good case and calls it risk."
        ),
    }
    run.correct = {
        "analysis": "`tail: harmful` resolved against the declared orientation",
        "tail_used": r_cmp.metric.tail_side,
        "difference": r_cmp.difference.estimate,
        "ci": [r_cmp.difference.ci_low, r_cmp.difference.ci_high],
        "favours": r_cmp.verdict.favours,
        "definition": r_cmp.metric.definition(),
    }
    run.trap_reproduced = bool(
        w_cmp.metric.tail_side == "upper"
        and r_cmp.metric.tail_side == "lower"
        and w_cmp.verdict.favours != r_cmp.verdict.favours
    )
    run.defence_worked = bool(r_cmp.metric.tail_side == "lower" and r_cmp.verdict.favours == "policy_b")
    run.findings = [
        {
            "finding": "tail side follows orientation, so it cannot be chosen by accident",
            "detail": (
                "`tail: harmful` with `direction: higher_is_better` resolves to the lower tail. "
                "Writing `params: {tail: upper}` alongside `tail: harmful` is rejected by the "
                "config loader as a contradiction; overriding requires saying `tail: upper` "
                "explicitly, which is then visible in the report and the provenance."
            ),
        }
    ]
    run.lines = [
        f"  metric orientation: {truth['direction']}; harmful tail is the "
        f"{truth['harmful_tail']} one",
        "",
        f"  WRONG  cvar(upper) on success: {w_cmp.difference.estimate:+.4f} "
        f"[{w_cmp.difference.ci_low:+.4f}, {w_cmp.difference.ci_high:+.4f}] "
        f"-> favours {w_cmp.verdict.favours}",
        f"  RIGHT  cvar(lower) on success: {r_cmp.difference.estimate:+.4f} "
        f"[{r_cmp.difference.ci_low:+.4f}, {r_cmp.difference.ci_high:+.4f}] "
        f"-> favours {r_cmp.verdict.favours}",
        "",
        f"  right-hand definition: {r_cmp.metric.definition()}",
    ]
    return run


# ---------------------------------------------------------------------------
# 11 -- an asymmetric margin
# ---------------------------------------------------------------------------

def _build_asymmetric(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.2),
            PolicyBehaviour("policy_b", loss_shift=0.25, interaction_sd=0.2),
        ],
        WorldModel(n_units=120, events_per_unit=4, observations_per_event=25,
                   unit_sd=3.0, event_sd=0.5, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "true_mean_loss_difference": 0.25,
        "symmetric_margin": 0.50,
        "asymmetric_margin": {"lower": 0.50, "upper": 0.15},
        "design": (
            "policy_b is 0.25 worse. A symmetric +/-0.50 margin calls that negligible; an "
            "asymmetric margin that tolerates only 0.15 of extra loss does not."
        ),
    }
    return frame, truth


def _demo_asymmetric(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    symmetric = compare_policies(
        frame,
        _config(margins={"mean_loss": {"lower": 0.5, "upper": 0.5,
                                       "source": "symmetric tolerance, declared in advance"}},
                label="symmetric margin"),
        validate=False, metrics=["mean_loss"],
    )
    asymmetric = compare_policies(
        frame,
        _config(margins={"mean_loss": {
            "lower": 0.5, "upper": 0.15,
            "source": "asymmetric tolerance: extra loss is less acceptable than less loss"}},
            label="asymmetric margin"),
        validate=False, metrics=["mean_loss"],
    )
    s_cmp = symmetric.get("mean_loss", "policy_b")
    a_cmp = asymmetric.get("mean_loss", "policy_b")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=asymmetric)
    run.naive = {
        "analysis": "a symmetric margin because symmetry is the default in most software",
        "margin": s_cmp.equivalence.margin.as_dict(),
        "conclusion": s_cmp.equivalence.conclusion,
        "verdict": s_cmp.verdict.label,
    }
    run.correct = {
        "analysis": "the margin the decision actually implies, asymmetric and documented",
        "margin": a_cmp.equivalence.margin.as_dict(),
        "conclusion": a_cmp.equivalence.conclusion,
        "verdict": a_cmp.verdict.label,
        "caveats": a_cmp.equivalence.notes,
    }
    run.trap_reproduced = s_cmp.equivalence.conclusion == "statistically_equivalent"
    run.defence_worked = a_cmp.equivalence.conclusion != "statistically_equivalent"
    run.findings = [
        {
            "finding": "the equivalence verdict depends on the margin, as it must",
            "symmetric": s_cmp.equivalence.conclusion,
            "asymmetric": a_cmp.equivalence.conclusion,
            "detail": (
                "Same data, same interval, opposite verdicts. This is not a defect: the margin "
                "carries the domain judgement, which is why it must be declared before the "
                "analysis and recorded with its source."
            ),
        }
    ]
    run.lines = [
        f"  true difference: {truth['true_mean_loss_difference']:+.3f} "
        f"(B has {truth['true_mean_loss_difference']:+.2f} more loss)",
        f"  90% TOST interval: [{a_cmp.equivalence.ci_low:+.4f}, "
        f"{a_cmp.equivalence.ci_high:+.4f}]",
        "",
        f"  SYMMETRIC  {s_cmp.equivalence.margin.describe()} -> "
        f"{s_cmp.equivalence.conclusion}",
        f"  ASYMMETRIC {a_cmp.equivalence.margin.describe()} -> "
        f"{a_cmp.equivalence.conclusion}",
        "",
        f"  {a_cmp.equivalence.interpretation}",
    ]
    return run


# ---------------------------------------------------------------------------
# 12 -- a bounded outcome with an impossible interval
# ---------------------------------------------------------------------------

def _build_bounded(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", success_shift=3.6, interaction_sd=0.1),
            PolicyBehaviour("policy_b", success_shift=3.4, interaction_sd=0.1),
        ],
        WorldModel(n_units=8, events_per_unit=2, observations_per_event=25,
                   unit_sd=0.8, event_sd=0.2, observation_sd=1.0,
                   success_intercept=4.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "design": (
            "a success rate pushed close to its upper bound of 1.0, with only 8 units. The "
            "sampling distribution is squeezed against the boundary and is left-skewed, so "
            "reflecting it about the estimate sends the upper endpoint past 1."
        ),
        "bounds": {"lower": 0.0, "upper": 1.0},
    }
    return frame, truth


_RATE_METRIC = {
    "name": "success_rate", "column": "mission_success", "estimator": "mean",
    "level": "event_id", "direction": "higher_is_better", "role": "primary",
    "bounds": {"lower": 0.0, "upper": 1.0},
}


def _demo_bounded(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    agg = {"resident_id->event_id": {"mission_success": "mean"}, "default_rule": "mean"}
    basic = compare_policies(
        frame, _config(metrics=[_RATE_METRIC], aggregation=agg, method="basic",
                       label="basic interval"),
        validate=False,
    )
    percentile = compare_policies(
        frame, _config(metrics=[_RATE_METRIC], aggregation=agg, method="percentile",
                       label="percentile interval"),
        validate=False,
    )
    b_cmp = basic.get("success_rate", "policy_b")
    p_cmp = percentile.get("success_rate", "policy_b")
    b_arm = basic.get("success_rate", "policy_b").candidate_result
    p_arm = percentile.get("success_rate", "policy_b").candidate_result

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=percentile)
    run.naive = {
        "analysis": "a basic (reflected) bootstrap interval on a rate near its bound",
        "arm_estimate": b_arm.estimate,
        "arm_ci": [b_arm.ci_low, b_arm.ci_high],
        "bounds_check": b_arm.bounds_check,
        "why_wrong": (
            "The basic interval reflects the bootstrap distribution about the estimate. Near a "
            "boundary the reflection lands outside the support, so the interval includes values "
            "the metric cannot take."
        ),
    }
    run.correct = {
        "analysis": "declare the support, report the violation, do not clamp",
        "arm_estimate": p_arm.estimate,
        "arm_ci": [p_arm.ci_low, p_arm.ci_high],
        "bounds_check": p_arm.bounds_check,
        "difference_ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
    }
    run.trap_reproduced = bool(b_arm.bounds_check.get("impossible"))
    run.defence_worked = bool(
        b_arm.bounds_check.get("impossible")
        and any("outside the metric's declared support" in n for n in b_arm.notes)
        and not p_arm.bounds_check.get("impossible")
    )
    run.findings = [
        {
            "finding": "an impossible endpoint is reported, never silently clamped",
            "detail": (
                "Truncating the endpoint at 1.0 would narrow the interval without making the "
                "construction appropriate, and would hide that the method does not suit a "
                "bounded outcome. The library reports the violation and leaves the number alone."
            ),
        },
        {
            "finding": "the interval construction is part of the result",
            "basic_ci": [b_arm.ci_low, b_arm.ci_high],
            "percentile_ci": [p_arm.ci_low, p_arm.ci_high],
            "detail": (
                "Percentile endpoints are order statistics of the resampled estimates, so they "
                "stay inside the observed support; basic endpoints need not. The two are not "
                "interchangeable, and which one was used belongs in the report."
            ),
        },
    ]
    run.lines = [
        f"  {truth['design']}",
        f"  declared support: [{truth['bounds']['lower']}, {truth['bounds']['upper']}]",
        "",
        f"  WRONG  basic:      {b_arm.estimate:.4f} [{b_arm.ci_low:.4f}, {b_arm.ci_high:.4f}]"
        f"  impossible={b_arm.bounds_check.get('impossible')}",
        f"  RIGHT  percentile: {p_arm.estimate:.4f} [{p_arm.ci_low:.4f}, {p_arm.ci_high:.4f}]"
        f"  impossible={p_arm.bounds_check.get('impossible')}",
        "",
    ] + [f"  note: {n}" for n in b_arm.notes if "support" in n]
    return run


# ---------------------------------------------------------------------------
# 13 -- a family of tests with no correction
# ---------------------------------------------------------------------------

def _add_noise_columns(frame: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Add ``n`` pure-noise outcome columns with no policy effect whatsoever."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    units = out["world_id"].astype("string")
    codes = pd.factorize(units)[0]
    for i in range(n):
        unit_effect = rng.normal(0.0, 1.0, size=int(codes.max()) + 1)
        out[f"noise_{i:02d}"] = unit_effect[codes] + rng.normal(0.0, 1.0, size=len(out))
    return out


def _build_multiplicity(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, _ = _nested_pair(seed, n_units=40, events_per_unit=2,
                            observations_per_event=15, true_shift=0.0, interaction_sd=0.3)
    frame = _add_noise_columns(frame, 20, seed)
    truth = {
        "n_noise_metrics": 20,
        "true_difference_every_metric": 0.0,
        "design": (
            "20 outcome columns generated with no policy effect at all, analysed as one "
            "declared exploratory family alongside a null primary metric."
        ),
    }
    return frame, truth


def _multiplicity_metrics() -> list[dict[str, Any]]:
    metrics = [
        {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
         "direction": "lower_is_better", "role": "primary"}
    ]
    for i in range(20):
        metrics.append(
            {"name": f"noise_{i:02d}", "column": f"noise_{i:02d}", "estimator": "mean",
             "level": "event_id", "direction": "lower_is_better", "role": "exploratory"}
        )
    return metrics


def _demo_multiplicity(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    agg = {"resident_id->event_id": {"loss": "mean"}, "default_rule": "mean"}
    uncorrected = compare_policies(
        frame,
        _config(metrics=_multiplicity_metrics(), aggregation=agg, n_resamples=1200,
                label="no correction"),
        validate=False,
    )
    corrected = compare_policies(
        frame,
        _config(metrics=[{**m, "role": "secondary"} if m["name"] != "mean_loss" else m
                         for m in _multiplicity_metrics()],
                aggregation=agg, n_resamples=1200, secondary_correction="holm",
                label="holm over the declared family"),
        validate=False,
    )
    naive_hits = [c.metric.name for c in uncorrected.comparisons if c.verdict.favours]
    survivors = [
        c.metric.name
        for c in corrected.comparisons
        if c.role == "secondary" and c.adjusted.get("survives")
    ]

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=corrected)
    run.naive = {
        "analysis": "run 21 comparisons and report whichever resolve",
        "resolved_metrics": naive_hits,
        "n_resolved": len(naive_hits),
        "expected_false_positives": 0.05 * truth["n_noise_metrics"],
    }
    run.correct = {
        "analysis": "declare the family by analysis role and apply Holm to it",
        "family_sizes": {f.role: f.family_size for f in corrected.families},
        "methods": {f.role: f.method for f in corrected.families},
        "survivors": survivors,
    }
    run.trap_reproduced = len(naive_hits) > 0
    run.defence_worked = len(survivors) < max(1, len(naive_hits))
    run.findings = [
        {
            "finding": "an uncorrected family of 20 null tests resolves some of them",
            "resolved": naive_hits,
            "detail": (
                f"Every noise column has a true difference of exactly 0. {len(naive_hits)} "
                "contrast(s) still resolved at the 95% level, which is what a family of 20 "
                "independent tests is expected to do."
            ),
        },
        {
            "finding": "correction requires a declared family, and the library will not guess one",
            "detail": (
                "The family here is the declared `secondary` role. Holm is applied across it and "
                "the primary metric is left uncorrected. The confidence intervals shown are "
                "marginal: adjusted p-values and unadjusted intervals appear side by side, "
                "labelled as such."
            ),
        },
    ]
    run.lines = [
        f"  {truth['design']}",
        f"  true difference on every metric: {truth['true_difference_every_metric']:+.1f}",
        "",
        f"  WRONG  21 uncorrected contrasts -> {len(naive_hits)} resolved: {naive_hits}",
        f"  RIGHT  Holm over the declared secondary family of "
        f"{[f.family_size for f in corrected.families if f.role == 'secondary']} -> "
        f"{len(survivors)} survive: {survivors}",
        "",
    ]
    for family in corrected.families:
        run.lines.extend(f"  {note}" for note in family.notes)
    return run


# ---------------------------------------------------------------------------
# 14 -- a failed run is not a missing value
# ---------------------------------------------------------------------------

def _build_failed_runs(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.2),
            PolicyBehaviour("policy_b", success_shift=0.55, interaction_sd=0.2,
                            run_failure_rates={"crashed": 0.22}),
        ],
        WorldModel(n_units=45, events_per_unit=4, observations_per_event=20,
                   unit_sd=1.5, event_sd=0.4, observation_sd=1.0, strata=STRATA),
        seed=seed,
    )
    truth = {
        "design": (
            "policy_b raises the success rate of the runs that complete, but 22% of its runs "
            "crash and produce nothing. Whether that is an improvement depends entirely on how "
            "a crash is counted."
        ),
        "crash_rate": 0.22,
    }
    return frame, truth


def _demo_failed_runs(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    agg = {"resident_id->event_id": {"mission_success": "mean"}, "default_rule": "mean"}
    dropped = compare_policies(
        frame,
        _config(metrics=[_RATE_METRIC], aggregation=agg, label="crashes dropped",
                status_handling={**_STATUS_HANDLING, "crashed": "missing"}),
        validate=False,
    )
    counted = compare_policies(
        frame,
        _config(metrics=[_RATE_METRIC], aggregation=agg, label="crashes counted as failures",
                status_handling={**_STATUS_HANDLING, "crashed": "failure"}),
        validate=False,
    )
    d_cmp = dropped.get("success_rate", "policy_b")
    c_cmp = counted.get("success_rate", "policy_b")
    ledger = (counted.run_status or {}).get("ledger", {})

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=counted)
    run.naive = {
        "analysis": "treat a crashed run as a missing value and drop it",
        "difference": d_cmp.difference.estimate,
        "ci": [d_cmp.difference.ci_low, d_cmp.difference.ci_high],
        "favours": d_cmp.verdict.favours,
        "why_wrong": (
            "The crashed runs leave the denominator, so a policy that fails to produce a result "
            "is scored only on the occasions when it did."
        ),
    }
    run.correct = {
        "analysis": "count a failed run as a failed observation, as declared",
        "difference": c_cmp.difference.estimate,
        "ci": [c_cmp.difference.ci_low, c_cmp.difference.ci_high],
        "favours": c_cmp.verdict.favours,
        "ledger": ledger.get("counts"),
        "note": (
            "Which handling is right is a domain question. The library's job is to make the "
            "choice explicit, record it, and show that the two answers differ."
        ),
    }
    run.trap_reproduced = bool(d_cmp.verdict.favours == "policy_b")
    run.defence_worked = bool(
        c_cmp.verdict.favours != "policy_b" and c_cmp.difference.estimate < d_cmp.difference.estimate
    )
    run.findings = [
        {
            "finding": "the sign of the finding depends on how a failed run is counted",
            "dropped": d_cmp.difference.estimate,
            "counted": c_cmp.difference.estimate,
            "detail": (
                f"Dropping crashes gives {d_cmp.difference.estimate:+.4f} favouring "
                f"{d_cmp.verdict.favours}; counting them as failures gives "
                f"{c_cmp.difference.estimate:+.4f} favouring {c_cmp.verdict.favours}. Same "
                "records, opposite conclusions, and the difference is a declaration rather than "
                "a computation."
            ),
        },
        {
            "finding": "failed runs never leave the accounting",
            "detail": (
                "The run-status ledger counts every run by policy and status before any row is "
                "handled, so the reader can see the crash rate even under the handling that "
                "drops them."
            ),
        },
    ]
    run.lines = [
        f"  {truth['design']}",
        "",
        f"  WRONG  crashes -> missing, then dropped: {d_cmp.difference.estimate:+.4f} "
        f"[{d_cmp.difference.ci_low:+.4f}, {d_cmp.difference.ci_high:+.4f}] "
        f"-> favours {d_cmp.verdict.favours}",
        f"  RIGHT  crashes -> counted as failures:   {c_cmp.difference.estimate:+.4f} "
        f"[{c_cmp.difference.ci_low:+.4f}, {c_cmp.difference.ci_high:+.4f}] "
        f"-> favours {c_cmp.verdict.favours}",
        "",
        "  run status ledger:",
    ] + [
        f"    {r['policy_id']:<10} {r['status']:<14} {r['handling']:<20} rows={r['n_rows']}"
        for r in (ledger.get("counts") or [])
    ]
    return run


# ---------------------------------------------------------------------------
# 15 -- paired data analysed unpaired
# ---------------------------------------------------------------------------

def _build_paired_mismatch(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, truth = _nested_pair(seed, n_units=30, events_per_unit=3,
                                observations_per_event=20, true_shift=0.9, interaction_sd=0.25)
    truth["design"] = (
        "a fully paired design: every unit carries both policies, and the unit effect (SD 4.0) "
        "is far larger than the policy effect (0.9)."
    )
    return frame, truth


def _demo_paired_mismatch(scenario: Scenario, seed: int) -> ScenarioRun:
    frame, truth = scenario.build(seed)
    paired = compare_policies(frame, _config(label="paired"), validate=False, metrics=["mean_loss"])
    unpaired_cfg = _config(label="unpaired")
    object.__setattr__(unpaired_cfg.comparison, "paired", False)
    unpaired = compare_policies(frame, unpaired_cfg, validate=False, metrics=["mean_loss"])
    p_cmp = paired.get("mean_loss", "policy_b")
    u_cmp = unpaired.get("mean_loss", "policy_b")
    p_width = p_cmp.difference.ci_high - p_cmp.difference.ci_low
    u_width = u_cmp.difference.ci_high - u_cmp.difference.ci_low

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=paired)
    run.naive = {
        "analysis": "ignore the pairing and compare two arms",
        "difference": u_cmp.difference.estimate,
        "ci": [u_cmp.difference.ci_low, u_cmp.difference.ci_high],
        "width": u_width,
        "verdict": u_cmp.verdict.label,
        "notes": [n for n in u_cmp.notes if "UNPAIRED" in n],
        "resample": "each arm drawn independently (comparison.paired: false)",
    }
    run.correct = {
        "analysis": "pair within unit, so the unit effect cancels",
        "difference": p_cmp.difference.estimate,
        "ci": [p_cmp.difference.ci_low, p_cmp.difference.ci_high],
        "width": p_width,
        "verdict": p_cmp.verdict.label,
    }
    run.trap_reproduced = bool(u_width > p_width and not u_cmp.difference.excludes_zero)
    run.defence_worked = bool(
        p_cmp.difference.excludes_zero
        and p_cmp.difference.ci_low <= truth["true_mean_loss_difference"] <= p_cmp.difference.ci_high
    )
    run.findings = [
        {
            "finding": "discarding the pairing discards the design's precision",
            "width_ratio": float(u_width / max(p_width, 1e-12)),
            "detail": (
                f"The unpaired interval is {u_width / max(p_width, 1e-12):.1f}x wider and no "
                "longer resolves a real effect. Nothing about the data changed; the analysis "
                "threw away the structure that made the comparison sharp."
            ),
        },
        {
            "finding": "an unpaired analysis of paired data is labelled, not silently accepted",
            "detail": (
                "Every result from `require_common_units: false` carries an UNPAIRED note "
                "through the panel, the interval, the console output and the report."
            ),
        },
    ]
    run.lines = [
        f"  {truth['design']}",
        f"  true difference: {truth['true_mean_loss_difference']:+.3f}",
        "",
        f"  WRONG  unpaired: {u_cmp.difference.estimate:+.3f} "
        f"[{u_cmp.difference.ci_low:+.3f}, {u_cmp.difference.ci_high:+.3f}]  "
        f"width {u_width:.3f}  -> {u_cmp.verdict.label}",
        f"  RIGHT  paired:   {p_cmp.difference.estimate:+.3f} "
        f"[{p_cmp.difference.ci_low:+.3f}, {p_cmp.difference.ci_high:+.3f}]  "
        f"width {p_width:.3f}  -> {p_cmp.verdict.label}",
        "",
        f"  width ratio: {u_width / max(p_width, 1e-12):.1f}x",
    ]
    return run


# ---------------------------------------------------------------------------
# 16 -- choosing the metric after seeing the results
# ---------------------------------------------------------------------------

def _build_metric_shopping(seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, _ = _nested_pair(seed, n_units=36, events_per_unit=2,
                            observations_per_event=15, true_shift=0.55, interaction_sd=0.3)
    frame = _add_noise_columns(frame, 40, seed + 5)
    truth = {
        "true_primary_difference": 0.55,
        "n_exploratory_metrics": 40,
        "design": (
            "policy_b is genuinely worse on the declared primary metric. Forty exploratory "
            "columns carry no effect at all, and some of them will look favourable."
        ),
    }
    return frame, truth


def _shopping_metrics() -> list[dict[str, Any]]:
    metrics = [
        {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
         "direction": "lower_is_better", "role": "primary"}
    ]
    for i in range(40):
        metrics.append(
            {"name": f"noise_{i:02d}", "column": f"noise_{i:02d}", "estimator": "mean",
             "level": "event_id", "direction": "lower_is_better", "role": "exploratory"}
        )
    return metrics


def _demo_metric_shopping(scenario: Scenario, seed: int) -> ScenarioRun:
    from wg_eval.report import render_markdown

    frame, truth = scenario.build(seed)
    agg = {"resident_id->event_id": {"loss": "mean"}, "default_rule": "mean"}
    result = compare_policies(
        frame, _config(metrics=_shopping_metrics(), aggregation=agg, n_resamples=1200,
                       secondary_correction="holm", exploratory_correction="holm",
                       label="roles declared"),
        validate=False,
    )
    primary = result.get("mean_loss", "policy_b")
    # Metric shopping does not wait for significance: an analyst scans the
    # exploratory metrics and reports whichever points the desired way and looks
    # most convincing. The trap is that selection, not a resolved interval.
    pointing_at_b = [
        c for c in result.comparisons
        if c.role == "exploratory" and c.difference.estimate < 0
    ]
    resolved = [c for c in pointing_at_b if c.verdict.favours == "policy_b"]
    best = (
        min(pointing_at_b, key=lambda c: c.difference.p_two_sided or 1.0)
        if pointing_at_b
        else None
    )
    favourable = pointing_at_b
    markdown = render_markdown(result, title="roles declared")
    primary_pos = markdown.find("### Primary outcome")
    exploratory_pos = markdown.find("### Exploratory outcomes")

    run = ScenarioRun(scenario=scenario, records=frame, truth=truth, comparison=result)
    run.naive = {
        "analysis": "report the most favourable metric as the headline",
        "chosen_metric": best.metric.name if best else None,
        "chosen_difference": best.difference.estimate if best else None,
        "chosen_p": best.difference.p_two_sided if best else None,
        "n_pointing_at_candidate": len(pointing_at_b),
        "n_resolved_at_95pct": len(resolved),
    }
    run.correct = {
        "analysis": "declared roles, primary reported first, exploratory labelled and corrected",
        "primary_metric": primary.metric.name,
        "primary_difference": primary.difference.estimate,
        "primary_ci": [primary.difference.ci_low, primary.difference.ci_high],
        "primary_favours": primary.verdict.favours,
        "primary_appears_before_exploratory": bool(
            primary_pos >= 0 and exploratory_pos > primary_pos
        ),
        "exploratory_family_size": next(
            (f.family_size for f in result.families if f.role == "exploratory"), 0
        ),
    }
    run.trap_reproduced = bool(
        best is not None and (best.difference.p_two_sided or 1.0) < 0.10
    )
    run.defence_worked = bool(
        primary.verdict.favours == "policy_a"
        and primary_pos >= 0
        and exploratory_pos > primary_pos
        and all(c.role == "exploratory" for c in favourable)
    )
    run.findings = [
        {
            "finding": "with enough exploratory metrics, one of them always looks good",
            "n_pointing_at_candidate": len(pointing_at_b),
            "n_resolved": len(resolved),
            "best_raw_p": best.difference.p_two_sided if best else None,
            "detail": (
                f"{len(pointing_at_b)} of {truth['n_exploratory_metrics']} pure-noise metrics "
                f"point towards policy_b, and the most convincing of them has raw p = "
                f"{best.difference.p_two_sided:.3f}" if best else "no metric pointed that way"
            ) + ". Choosing the headline after seeing them is the error; no correction applied "
                "afterwards repairs it.",
        },
        {
            "finding": "role discipline is structural, not advisory",
            "detail": (
                "Exactly one metric may be declared primary; the loader rejects a second. The "
                "report prints the primary outcome first, labels every other finding with its "
                "role, and records `analysis_status` so a reader can see whether any of this "
                "was declared in advance at all."
            ),
        },
    ]
    run.lines = [
        f"  {truth['design']}",
        "",
        f"  PRIMARY (declared)  {primary.metric.name}: {primary.difference.estimate:+.3f} "
        f"[{primary.difference.ci_low:+.3f}, {primary.difference.ci_high:+.3f}] "
        f"-> favours {primary.verdict.favours}",
    ]
    if best:
        run.lines.append(
            f"  SHOPPED (exploratory) {best.metric.name}: {best.difference.estimate:+.3f} "
            f"[{best.difference.ci_low:+.3f}, {best.difference.ci_high:+.3f}] "
            f"-> favours {best.verdict.favours}, raw p={best.difference.p_two_sided:.3f}, "
            f"adjusted p={best.adjusted.get('p_adjusted')}"
        )
    run.lines += [
        "",
        f"  {len(pointing_at_b)} of {truth['n_exploratory_metrics']} noise metrics point at "
        f"policy_b; {len(resolved)} of them resolve at the 95% level",
        f"  report order: primary section at char {primary_pos}, exploratory at "
        f"{exploratory_pos}",
    ]
    return run


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            key="observation_bootstrap_false_precision",
            title="Observation-level bootstrap manufactures precision",
            trap="Resampling observations treats 1,200 nested rows as 1,200 experiments and "
                 "returns an interval several times too narrow.",
            truth="The design supplies 20 independent units. The true mean loss difference "
                  "is +0.6.",
            defence="Resample whole units; observations travel with the unit they belong to.",
            can_conclude="That the observation-level interval under-covers by a margin larger "
                         "than Monte Carlo error at this number of replications.",
            cannot_conclude="That the unit-level interval is calibrated at 20 units -- measured "
                            "coverage there is below nominal too, just far less so.",
            build=_build_pseudoreplication,
            config=lambda: _config(label="pseudoreplication demo"),
            demonstrate=_demo_observation_bootstrap,
            audit_item="4, 5",
        ),
        Scenario(
            key="unit_bootstrap_calibration",
            title="Unit-level bootstrap coverage, with its Monte Carlo error",
            trap="A single interval proves nothing, and a bare coverage percentage from 100 "
                 "replications is read as if it were exact.",
            truth="The true mean loss difference is +0.6 in every replication.",
            defence="Report empirical coverage with its Monte Carlo SE and a Wilson interval, "
                    "and compare coverage at two unit counts.",
            can_conclude="That coverage improves with more units and is consistent with nominal "
                         "at 60 units.",
            cannot_conclude="That the percentile interval is calibrated at 20 units; the Wilson "
                            "interval there excludes the nominal level.",
            build=_build_pseudoreplication,
            config=lambda: _config(label="unit-level bootstrap"),
            demonstrate=_demo_unit_bootstrap,
            audit_item="5",
        ),
        Scenario(
            key="tail_risk_disagreement",
            title="Mean favours A while the harmful tail favours B",
            trap="policy_a has the lower average loss, so a mean-only report recommends it.",
            truth="policy_a carries a 4% per-observation chance of a +60 loss; its 90% CVaR is "
                  "far worse than policy_b's while its mean is 0.8 better.",
            defence="Declare tail metrics in advance, orient them by the metric's direction, and "
                    "report the disagreement with both intervals.",
            can_conclude="That the two contrasts point in opposite directions, each with its own "
                         "interval.",
            cannot_conclude="Which policy is preferable; that needs a risk preference declared "
                            "outside the analysis.",
            build=_build_tail_disagreement,
            config=lambda: _config(label="tail disagreement"),
            demonstrate=_demo_tail,
            audit_item="10",
        ),
        Scenario(
            key="practical_equivalence",
            title="Equivalent within a declared margin",
            trap="The interval includes zero, so the report is read as a finding that the "
                 "policies match.",
            truth="The true difference is +0.03 against a declared margin of +/-0.40 -- real, "
                  "and negligible at that margin.",
            defence="TOST against the declared margin, with the decision caveat attached.",
            can_conclude="That the contrast lies inside the predeclared margin.",
            cannot_conclude="That the policies are interchangeable in use; the margin's "
                            "adequacy is a domain judgement.",
            build=_build_equivalence,
            config=lambda: _config(margins=_EQUIVALENCE_MARGIN, label="equivalence"),
            demonstrate=_demo_equivalence,
            audit_item="11, 12",
        ),
        Scenario(
            key="missing_units_reverse_ranking",
            title="Missing units reverse the ranking",
            trap="policy_b has no rows on the 22 hardest units; comparing each policy over its "
                 "own units favours the worse one.",
            truth="policy_b is worse by +1.2 mean loss on every unit.",
            defence="Pair on the units both policies ran, record every exclusion, and test "
                    "whether the excluded units resemble the shared ones.",
            can_conclude="The paired contrast among units observed under both policies.",
            cannot_conclude="A contrast for the full set of observed units, because overlap "
                            "depends on unit difficulty.",
            build=_build_missing_units,
            config=lambda: _config(label="missing units"),
            demonstrate=_demo_missing_units,
            audit_item="15",
        ),
        Scenario(
            key="easier_units_confound",
            title="A policy allocated easier units",
            trap="policy_a ran mostly on low-difficulty units and policy_b mostly on high ones; "
                 "pooling favours policy_a.",
            truth="policy_b has 1.5 lower mean loss on identical units.",
            defence="Pair on shared units, print the allocation ledger, and report per stratum.",
            can_conclude="That the aggregate and within-stratum contrasts disagree, and how the "
                         "allocation differed.",
            cannot_conclude="Why the allocation differed, or that it was the cause; the ledger "
                            "describes, it does not diagnose.",
            build=_build_easier_units,
            config=lambda: _config(label="allocation confound"),
            demonstrate=_demo_easier_units,
            audit_item="16, 17",
        ),
        Scenario(
            key="dependence_above_declared_unit",
            title="Units that share a higher-level shock",
            trap="48 units look like 48 replicates, so a unit-level cluster bootstrap looks "
                 "correct and is not.",
            truth="The units come in 8 groups of 6 that share one event-level shock, so there "
                  "are 8 independent replicates.",
            defence="Declare the inference structure and check it against the records; an "
                    "event that contains units is refused as a nested level.",
            can_conclude="That resampling at the wrong level under-covers even though it is a "
                         "cluster bootstrap.",
            cannot_conclude="That the library can detect an unrecorded dependence; it can only "
                            "see groupings that appear in the records.",
            build=_build_shared_event_shock,
            config=lambda: _config(primary_unit="event_id",
                                   nested_units=("world_id", "resident_id"),
                                   metrics=[{"name": "mean_loss", "column": "loss",
                                             "estimator": "mean", "level": "world_id",
                                             "direction": "lower_is_better", "role": "primary"}],
                                   aggregation={"resident_id->world_id": {"loss": "mean"},
                                                "world_id->event_id": {"loss": "mean"},
                                                "default_rule": "mean"},
                                   label="event-level inference"),
            demonstrate=_demo_shared_event_shock,
            audit_item="3, 4",
        ),
        Scenario(
            key="outcome_dependent_missingness",
            title="Runs that crash on the hardest units",
            trap="policy_b's crashed runs are treated as missing values and dropped, so it is "
                 "scored only where it survived.",
            truth="policy_b is worse by +1.0, and its runs crash on the 18 hardest units.",
            defence="Keep failed runs in the records with a status, declare the mechanism, and "
                    "report a worst-case bound beside the complete-case estimate.",
            can_conclude="The complete-case contrast and a bound on how far the missing runs "
                         "could move it.",
            cannot_conclude="The target-population contrast; no method recovers outcomes that "
                            "were never produced.",
            build=_build_mnar_missing,
            config=lambda: _config(label="MNAR missingness", assumed_mechanism="MNAR"),
            demonstrate=_demo_mnar_missing,
            audit_item="14, 15",
        ),
        Scenario(
            key="too_few_units",
            title="Many observations, almost no replicates",
            trap="A 90% CVaR computed from 5 units is reported like any other number.",
            truth="Every design here has 160 observations per unit; only the unit count varies, "
                  "from 5 to 50.",
            defence="Per-estimator credibility thresholds, so the warning matches the statistic "
                    "instead of a universal minimum n.",
            can_conclude="Which statistics the available unit count can support.",
            cannot_conclude="A universal minimum sample size; the threshold depends on the "
                            "estimator.",
            build=_build_tiny,
            config=lambda: _config(label="small n"),
            demonstrate=_demo_tiny,
            audit_item="7",
        ),
        Scenario(
            key="wrong_cvar_tail",
            title="CVaR summarising the beneficial tail",
            trap="A higher-is-better metric analysed with an upper-tail CVaR describes the best "
                 "events and calls the result risk.",
            truth="policy_a has the higher mean success rate and a much heavier low tail.",
            defence="`tail: harmful` resolves against the declared direction; a contradictory "
                    "`params.tail` is rejected at config load.",
            can_conclude="The contrast in the harmful tail, with the tail side printed beside it.",
            cannot_conclude="Anything about the harmful tail from a beneficial-tail statistic, "
                            "however confident its interval looks.",
            build=_build_wrong_tail,
            config=lambda: _config(
                metrics=[_SUCCESS_CVAR_RIGHT, _SUCCESS_MEAN],
                aggregation={"resident_id->event_id": {"mission_success": "mean"},
                             "default_rule": "mean"},
                label="harmful tail"),
            demonstrate=_demo_wrong_tail,
            audit_item="8, 9",
        ),
        Scenario(
            key="asymmetric_margin",
            title="A symmetric margin that hides an unacceptable loss",
            trap="A symmetric +/-0.50 margin declares a 0.25 increase in loss negligible.",
            truth="policy_b is 0.25 worse, and only 0.15 of extra loss was acceptable.",
            defence="Support asymmetric margins, and record each margin's source.",
            can_conclude="Whether the contrast lies inside the margin that was actually declared.",
            cannot_conclude="Which margin is correct; the library checks arithmetic against a "
                            "declaration, not the declaration itself.",
            build=_build_asymmetric,
            config=lambda: _config(
                margins={"mean_loss": {"lower": 0.5, "upper": 0.15,
                                       "source": "asymmetric operational tolerance"}},
                label="asymmetric margin"),
            demonstrate=_demo_asymmetric,
            audit_item="11",
        ),
        Scenario(
            key="bounded_outcome_interval",
            title="An interval that runs past the end of the scale",
            trap="A basic bootstrap interval on a rate near 1.0 includes values above 1.0 and "
                 "is reported without comment.",
            truth="The metric is a proportion with declared support [0, 1].",
            defence="Declare the support, check the endpoints, report the violation, and do not "
                    "clamp.",
            can_conclude="That the interval construction does not suit this metric here.",
            cannot_conclude="A corrected interval; the library flags the problem rather than "
                            "silently substituting a different method.",
            build=_build_bounded,
            config=lambda: _config(
                metrics=[_RATE_METRIC],
                aggregation={"resident_id->event_id": {"mission_success": "mean"},
                             "default_rule": "mean"},
                method="percentile", label="bounded outcome"),
            demonstrate=_demo_bounded,
            audit_item="13, 6",
        ),
        Scenario(
            key="uncorrected_family",
            title="Twenty null metrics, uncorrected",
            trap="Twenty exploratory contrasts on pure noise are reported individually and some "
                 "of them resolve.",
            truth="Every one of the twenty columns has a true difference of exactly zero.",
            defence="Assign each metric a role, declare the family, and apply Holm across it.",
            can_conclude="Which findings survive correction over the family that was declared.",
            cannot_conclude="Anything about a family the configuration did not declare; the "
                            "library will not infer one.",
            build=_build_multiplicity,
            config=lambda: _config(
                metrics=_multiplicity_metrics(),
                aggregation={"resident_id->event_id": {"loss": "mean"}, "default_rule": "mean"},
                secondary_correction="holm", label="multiplicity"),
            demonstrate=_demo_multiplicity,
            audit_item="19",
        ),
        Scenario(
            key="failed_runs_as_missing",
            title="A crashed run counted as a missing value",
            trap="policy_b crashes on 22% of runs; dropping those runs makes it look better.",
            truth="policy_b raises the success rate of the runs that finish while failing to "
                  "finish more than a fifth of them.",
            defence="Declare how each run status is handled; counting a crash as a failure keeps "
                    "it in the denominator.",
            can_conclude="That the finding's sign depends on the declared handling, and what "
                         "each handling gives.",
            cannot_conclude="Which handling is right; that is a domain question the library "
                            "forces into the open rather than answering.",
            build=_build_failed_runs,
            config=lambda: _config(
                metrics=[_RATE_METRIC],
                aggregation={"resident_id->event_id": {"mission_success": "mean"},
                             "default_rule": "mean"},
                label="failed runs"),
            demonstrate=_demo_failed_runs,
            audit_item="14",
        ),
        Scenario(
            key="paired_data_analysed_unpaired",
            title="Paired data analysed as two independent arms",
            trap="A fully paired design compared arm-to-arm loses a real effect in unit "
                 "variance.",
            truth="Every unit carries both policies; the unit SD is 4.0 and the effect is 0.9.",
            defence="Pair within unit by default, and label any unpaired comparison as such "
                    "everywhere it appears.",
            can_conclude="That discarding the pairing widens the interval enough to change the "
                         "finding.",
            cannot_conclude="That pairing is always available; when units are not shared, the "
                            "estimand changes rather than the precision.",
            build=_build_paired_mismatch,
            config=lambda: _config(label="paired"),
            demonstrate=_demo_paired_mismatch,
            audit_item="2, 15",
        ),
        Scenario(
            key="metric_selected_after_the_fact",
            title="Choosing the headline metric after seeing the results",
            trap="Twelve exploratory metrics carry no effect, one of them looks favourable, and "
                 "it becomes the headline.",
            truth="policy_b is genuinely worse on the declared primary metric by +0.55.",
            defence="Exactly one primary metric, reported first; every other finding labelled "
                    "with its role and corrected within its declared family.",
            can_conclude="The primary contrast, and that the favourable exploratory ones are "
                         "consistent with noise.",
            cannot_conclude="That the analysis was preregistered; `analysis_status` records the "
                            "claim and the existence of a config file is not evidence for it.",
            build=_build_metric_shopping,
            config=lambda: _config(
                metrics=_shopping_metrics(),
                aggregation={"resident_id->event_id": {"loss": "mean"}, "default_rule": "mean"},
                secondary_correction="holm", exploratory_correction="holm",
                label="roles declared"),
            demonstrate=_demo_metric_shopping,
            audit_item="20, 21",
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
