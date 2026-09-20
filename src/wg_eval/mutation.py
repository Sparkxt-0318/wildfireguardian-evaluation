"""Mutation testing for the statistics themselves.

A test suite that passes proves nothing until you know it can fail.  This
module introduces deliberate statistical errors -- the exact errors the library
exists to prevent -- and checks that something catches each one.

Each mutant is a context manager that monkey-patches one behaviour, plus a
"detector": the assertion a reader would expect to fire.  A mutant that nothing
detects is a hole in the suite, and it is reported as such rather than quietly
passing.

This is deliberately not a generic mutation-testing tool.  Random operator
mutations mostly produce crashes; the interesting mutants here are the ones
that produce *plausible numbers that are wrong*, and those have to be written
by hand.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

from wg_eval import bootstrap as boot_mod
from wg_eval import compare as compare_mod
from wg_eval import equivalence as eq_mod
from wg_eval import metrics as metrics_mod
from wg_eval import multiplicity as mult_mod
from wg_eval import stratify as strat_mod
from wg_eval.config import AnalysisConfig, Margin, config_from_mapping


@dataclass
class Mutant:
    """One deliberate statistical error, and how a reader would notice it."""

    key: str
    description: str
    #: What the mutation makes the library do wrong.
    wrong_behaviour: str
    apply: Callable[[], contextlib.AbstractContextManager]
    #: Returns True when the mutation is detected. Raising also counts.
    detect: Callable[[], bool]
    audit_item: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "wrong_behaviour": self.wrong_behaviour,
            "audit_item": self.audit_item,
        }


@dataclass
class MutationResult:
    mutant: Mutant
    detected: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {**self.mutant.as_dict(), "detected": self.detected, "detail": self.detail}


@contextlib.contextmanager
def _patch(module: Any, name: str, value: Any) -> Iterator[None]:
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


# ---------------------------------------------------------------------------
# the data and config every mutant is exercised on
# ---------------------------------------------------------------------------

def sample_records(
    seed: int = 5,
    *,
    true_shift: float = 0.9,
    crash_rate: float = 0.12,
    events_per_unit: int = 3,
    interaction_sd: float = 0.6,
) -> pd.DataFrame:
    from wg_eval.synth.generators import (
        DEFAULT_STRATA,
        PolicyBehaviour,
        WorldModel,
        generate_experiment,
    )

    return generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=interaction_sd),
            PolicyBehaviour("policy_b", loss_shift=true_shift, interaction_sd=interaction_sd,
                            run_failure_rates=({"crashed": crash_rate} if crash_rate else {})),
        ],
        WorldModel(n_units=30, events_per_unit=events_per_unit, observations_per_event=12,
                   unit_sd=4.0, strata=DEFAULT_STRATA),
        seed=seed,
    )


def sample_config(**overrides: Any) -> AnalysisConfig:
    mapping: dict[str, Any] = {
        "inference": {"primary_unit": "world_id", "nested_units": ["event_id", "resident_id"]},
        "metrics": [
            {"name": "mean_loss", "column": "loss", "estimator": "mean", "level": "event_id",
             "direction": "lower_is_better", "role": "primary"},
            {"name": "cvar90_loss", "column": "loss", "estimator": "cvar", "level": "event_id",
             "params": {"alpha": 0.9}, "direction": "lower_is_better", "role": "secondary"},
        ],
        "aggregation": {"resident_id->event_id": {"loss": "mean"}, "default_rule": "mean"},
        "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
        "bootstrap": {"n_resamples": 400, "seed": 11},
        "equivalence": {"margins": {"mean_loss": {"lower": 0.2, "upper": 0.2,
                                                  "source": "mutation harness"}}},
        "missing_data": {"policy": "drop_record",
                         "status_handling": {"completed": "completed", "crashed": "failure"}},
        "strata": ["difficulty", "scale"],
    }
    mapping.update(overrides)
    return config_from_mapping(mapping)


def _difference(
    config: AnalysisConfig | None = None,
    metric: str = "mean_loss",
    records: pd.DataFrame | None = None,
):
    frame = sample_records() if records is None else records
    result = compare_mod.compare_policies(
        frame, config or sample_config(), validate=False, metrics=[metric]
    )
    return result.get(metric, "policy_b")


# ---------------------------------------------------------------------------
# mutants
# ---------------------------------------------------------------------------

def _observation_bootstrap() -> contextlib.AbstractContextManager:
    """Resample observations instead of whole units, keeping the pairing.

    Isolating pseudoreplication requires holding the pairing fixed. A naive
    bootstrap that ALSO drops the pairing makes two errors that push in
    opposite directions -- ignoring clustering narrows the interval, ignoring
    pairing widens it -- and on a strongly paired design the second dominates,
    so such a mutant can look conservative. This mutant keeps the paired draw
    and changes only the unit that is drawn.
    """
    original = boot_mod.paired_cluster_bootstrap

    def patched(panel, estimator, baseline, candidate, **kwargs):
        base, cand, diff = original(panel, estimator, baseline, candidate, **kwargs)
        base_flat = panel.values[baseline].all_values()
        cand_flat = panel.values[candidate].all_values()
        n = min(base_flat.size, cand_flat.size)
        n_resamples = kwargs.get("n_resamples", 2000)
        level = kwargs.get("confidence_level", 0.95)
        rng = np.random.default_rng(kwargs.get("seed", 0))
        draws = rng.integers(0, n, size=(n_resamples, n))
        reps = np.array(
            [estimator(cand_flat[d]) - estimator(base_flat[d]) for d in draws], dtype="float64"
        )
        mutated = boot_mod.BootstrapResult(
            estimate=diff.estimate,
            ci_low=float(np.quantile(reps, (1 - level) / 2)),
            ci_high=float(np.quantile(reps, 1 - (1 - level) / 2)),
            confidence_level=level, method="percentile", n_resamples=n_resamples,
            n_effective=len(reps), seed=kwargs.get("seed", 0),
            cluster_level="observation (PSEUDOREPLICATION)", n_clusters=n,
            standard_error=float(reps.std(ddof=1)), replicates=reps,
        )
        return base, cand, mutated

    return _patch(compare_mod, "paired_cluster_bootstrap", patched)


def _detect_observation_bootstrap() -> bool:
    """Every arm must align, and there must be real clustering to destroy.

    The narrowing an observation-level bootstrap buys is not unbounded. Writing
    ``sigma_I`` for the unit-by-policy interaction, ``V_E`` for the variance of
    a paired sub-unit difference and ``E`` for sub-units per unit, the ratio of
    variances is ``(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)``: it tends to
    ``1/E`` when the interaction dominates and to **1** when there is none. With
    no interaction the two bootstraps estimate the same thing, so a fixture
    without one cannot detect this mutant at all. The fixture therefore has a
    substantial interaction and 12 sub-units per unit, and no crashed runs --
    those would misalign the two arms and reintroduce the separate unpairing
    error, which pushes the width the other way.
    """
    records = sample_records(crash_rate=0.0, events_per_unit=12, interaction_sd=2.5)
    baseline = _difference(records=records).difference
    with _observation_bootstrap():
        mutated = _difference(records=records).difference
    return (mutated.ci_high - mutated.ci_low) < 0.5 * (baseline.ci_high - baseline.ci_low)


def _unpaired_resample() -> contextlib.AbstractContextManager:
    """Draw each arm independently while still calling the result paired."""
    original = boot_mod.paired_cluster_bootstrap

    def patched(*args, **kwargs):
        kwargs["paired"] = False
        return original(*args, **kwargs)

    return _patch(compare_mod, "paired_cluster_bootstrap", patched)


def _detect_unpaired_resample() -> bool:
    baseline = _difference().difference
    with _unpaired_resample():
        mutated = _difference().difference
    return (mutated.ci_high - mutated.ci_low) > 2.0 * (baseline.ci_high - baseline.ci_low)


def _equivalence_without_margin() -> contextlib.AbstractContextManager:
    """Invent a margin when none was declared."""

    def patched(replicates, difference, margin, **kwargs):
        if margin is None:
            margin = Margin(lower=1e9, upper=1e9, source="invented by the mutant")
        return eq_mod.tost.__wrapped__(replicates, difference, margin, **kwargs) \
            if hasattr(eq_mod.tost, "__wrapped__") else _real_tost(replicates, difference,
                                                                   margin, **kwargs)

    return _patch(eq_mod, "tost", patched)


_real_tost = eq_mod.tost


def _detect_equivalence_without_margin() -> bool:
    config = sample_config(equivalence={"margins": {}, "alpha": 0.05})
    with _equivalence_without_margin():
        try:
            comparison = _difference(config)
        except eq_mod.MarginRequired:
            return True
        # Undetected if a verdict of equivalence appears with no declared margin.
        return comparison.equivalence is None
    return False


def _p_value_means_equivalence() -> contextlib.AbstractContextManager:
    """Turn a large p-value into a finding of equivalence."""
    original = eq_mod.classify_difference

    def patched(**kwargs):
        verdict = original(**kwargs)
        if verdict.label == "inconclusive":
            verdict.label = "statistically_equivalent"
            verdict.sentence = "there was no significant difference, so the policies match"
        return verdict

    return _patch(compare_mod, "classify_difference", patched)


def _detect_p_value_means_equivalence() -> bool:
    from wg_eval.report import audit_wording, render_markdown, render_text

    # A null effect and no declared margin, so the honest verdict is inconclusive
    # and the mutant has something to relabel.
    records = sample_records(true_shift=0.0)
    config = sample_config(
        equivalence={"margins": {}, "alpha": 0.05},
        metrics=[{"name": "mean_loss", "column": "loss", "estimator": "mean",
                  "level": "event_id", "direction": "lower_is_better", "role": "primary"}],
    )
    honest = compare_mod.compare_policies(records, config, validate=False, metrics=["mean_loss"])
    if honest.get("mean_loss", "policy_b").verdict.label != "inconclusive":
        return False  # the fixture is wrong, not the library
    with _p_value_means_equivalence():
        result = compare_mod.compare_policies(
            records, config, validate=False, metrics=["mean_loss"]
        )
        return bool(audit_wording(render_text(result))) and bool(
            audit_wording(render_markdown(result))
        )


def _flipped_orientation() -> contextlib.AbstractContextManager:
    """Read every metric as higher-is-better."""
    from wg_eval.config import MetricSpec

    original = MetricSpec.harmful_side

    def patched(self):  # noqa: ANN001
        return "lower" if original.fget(self) == "upper" else "upper"

    return _patch(MetricSpec, "harmful_side", property(patched))


def _detect_flipped_orientation() -> bool:
    config = sample_config()
    baseline = _difference(config, "cvar90_loss")
    baseline_tail = baseline.metric.tail_side
    baseline_estimate = baseline.difference.estimate
    with _flipped_orientation():
        # Read the orientation INSIDE the patched context: a property reverts
        # the moment the patch is lifted, and a detector that reads it after
        # would report a clean bill of health for a broken library.
        mutated = _difference(config, "cvar90_loss")
        mutated_tail = mutated.metric.tail_side
        mutated_estimate = mutated.difference.estimate
    return baseline_tail != mutated_tail or baseline_estimate != mutated_estimate


def _cvar_wrong_tail() -> contextlib.AbstractContextManager:
    """Average the beneficial tail and call it risk."""
    original = metrics_mod.METRIC_REGISTRY["cvar"].factory

    def factory(alpha: float = 0.9, tail: str = "upper"):
        return original(alpha=alpha, tail="lower" if tail == "upper" else "upper")

    spec = metrics_mod.METRIC_REGISTRY["cvar"]
    mutated = type(spec)(
        name=spec.name, factory=factory, definition=spec.definition, is_tail=spec.is_tail,
        min_values=spec.min_values, min_units=spec.min_units,
        small_sample_note=spec.small_sample_note, estimand_params=spec.estimand_params,
    )
    registry = dict(metrics_mod.METRIC_REGISTRY)
    registry["cvar"] = mutated
    return _patch(metrics_mod, "METRIC_REGISTRY", registry)


def _detect_cvar_wrong_tail() -> bool:
    x = np.arange(1.0, 11.0)
    correct = metrics_mod.estimate(x, "cvar", {"alpha": 0.7, "tail": "upper"})
    with _cvar_wrong_tail():
        mutated = metrics_mod.estimate(x, "cvar", {"alpha": 0.7, "tail": "upper"})
    return correct != mutated


def _drop_failed_runs() -> contextlib.AbstractContextManager:
    """Silently discard runs that did not complete."""

    def patched(frame, spec, **kwargs):
        column = spec.status_column
        if column in frame.columns:
            frame = frame[frame[column].astype("string") == "completed"].reset_index(drop=True)
        return frame, {"applied": False, "column": column}

    return _patch(compare_mod, "apply_run_status", patched)


def _detect_drop_failed_runs() -> bool:
    baseline = _difference()
    with _drop_failed_runs():
        mutated = _difference()
    ledger = mutated.provenance.run_status.get("applied", {})
    return not ledger.get("applied", False) or baseline.difference.estimate != pytest_approx(
        mutated.difference.estimate
    )


def pytest_approx(value: float, tol: float = 1e-12) -> Any:
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - value) <= tol  # type: ignore[arg-type]

    return _Approx()


def _uneven_reuse_unreported() -> contextlib.AbstractContextManager:
    """Stop reporting unit-count imbalance between arms."""
    return _patch(compare_mod, "imbalance_report", lambda panel, threshold: {"checked": False})


def _detect_uneven_reuse_unreported() -> bool:
    records = sample_records()
    first_unit = sorted(records["world_id"].unique())[0]
    mask = (records["world_id"] == first_unit) & (records["policy_id"] == "policy_b")
    thinned = records.drop(index=records.index[mask][2:]).reset_index(drop=True)
    result = compare_mod.compare_policies(
        thinned, sample_config(), validate=False, metrics=["mean_loss"]
    )
    baseline_flags = result.get("mean_loss", "policy_b").diagnostics["imbalance"]
    with _uneven_reuse_unreported():
        mutated = compare_mod.compare_policies(
            thinned, sample_config(), validate=False, metrics=["mean_loss"]
        )
    mutated_flags = mutated.get("mean_loss", "policy_b").diagnostics["imbalance"]
    return bool(baseline_flags.get("checked")) and not mutated_flags.get("checked")


def _no_multiplicity_correction() -> contextlib.AbstractContextManager:
    """Ignore the declared family and leave every p-value raw."""
    return _patch(mult_mod, "holm", lambda pvalues: {k: float(v) for k, v in pvalues.items()})


def _detect_no_multiplicity_correction() -> bool:
    raw = {"a": 0.01, "b": 0.02, "c": 0.03}
    correct = mult_mod.adjust(raw, "holm")
    with _no_multiplicity_correction():
        mutated = mult_mod.adjust(raw, "holm")
    return correct != mutated


def _merge_strata() -> contextlib.AbstractContextManager:
    """Collapse every unit into one stratum."""
    original = strat_mod.unit_stratum_map

    def patched(frame, column, *, unit="world_id"):
        out = original(frame, column, unit=unit)
        out["stratum_value"] = "all"
        return out

    return _patch(strat_mod, "unit_stratum_map", patched)


def _detect_merge_strata() -> bool:
    records = sample_records()
    config = sample_config()
    correct = strat_mod.compare_by_stratum(records, config, "difficulty", min_units=4,
                                           include_overall=False)
    with _merge_strata():
        mutated = strat_mod.compare_by_stratum(records, config, "difficulty", min_units=4,
                                               include_overall=False)
    return len(correct.per_stratum) > len(mutated.per_stratum)


def _accept_any_inference_structure() -> contextlib.AbstractContextManager:
    """Stop checking the declared structure against the records."""
    return _patch(compare_mod, "require_valid_structure", lambda frame, inference: [])


def _detect_accept_any_inference_structure() -> bool:
    from wg_eval.hierarchy import InferenceStructureError
    from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment

    frame = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.5)],
        WorldModel(n_units=16, events_per_unit=1, observations_per_event=4,
                   units_per_shared_event=4, shared_event_sd=2.0),
        seed=2,
    )
    config = sample_config(
        metrics=[{"name": "mean_loss", "column": "loss", "estimator": "mean",
                  "level": "event_id", "direction": "lower_is_better", "role": "primary"}],
        strata=[],
    )
    try:
        compare_mod.compare_policies(frame, config, validate=False, metrics=["mean_loss"])
    except InferenceStructureError:
        refused = True
    else:
        refused = False
    with _accept_any_inference_structure():
        try:
            compare_mod.compare_policies(frame, config, validate=False, metrics=["mean_loss"])
            accepted = True
        except InferenceStructureError:
            accepted = False
    return refused and accepted


MUTANTS: list[Mutant] = [
    Mutant("observation_bootstrap",
           "resample observations instead of whole units",
           "confidence intervals several times too narrow",
           _observation_bootstrap, _detect_observation_bootstrap, "4, 27"),
    Mutant("unpaired_analysis_of_paired_data",
           "draw each arm independently while the design is paired",
           "the unit effect stops cancelling and a real effect is lost in noise",
           _unpaired_resample, _detect_unpaired_resample, "2, 27"),
    Mutant("equivalence_without_a_margin",
           "invent a margin when the config declared none",
           "an equivalence verdict nobody declared the basis for",
           _equivalence_without_margin, _detect_equivalence_without_margin, "11, 27"),
    Mutant("p_value_read_as_equivalence",
           "relabel an inconclusive result as equivalent",
           "'no significant difference' presented as a finding of sameness",
           _p_value_means_equivalence, _detect_p_value_means_equivalence, "12, 30"),
    Mutant("reversed_metric_orientation",
           "treat lower-is-better metrics as higher-is-better",
           "tail statistics summarise the beneficial tail and verdicts invert",
           _flipped_orientation, _detect_flipped_orientation, "8, 27"),
    Mutant("cvar_wrong_tail",
           "CVaR averages the opposite tail from the one requested",
           "a risk statistic that describes the good case",
           _cvar_wrong_tail, _detect_cvar_wrong_tail, "9, 27"),
    Mutant("failed_runs_dropped_silently",
           "discard runs that did not complete, with no ledger",
           "a policy improves its score by failing to produce results",
           _drop_failed_runs, _detect_drop_failed_runs, "14, 27"),
    Mutant("uneven_unit_reuse_unreported",
           "stop reporting unequal observation counts between arms",
           "a paired difference estimated with wildly unequal precision looks clean",
           _uneven_reuse_unreported, _detect_uneven_reuse_unreported, "16, 27"),
    Mutant("multiplicity_family_ignored",
           "leave every p-value in a declared family uncorrected",
           "a family of twenty null tests produces a headline",
           _no_multiplicity_correction, _detect_no_multiplicity_correction, "19, 27"),
    Mutant("strata_incorrectly_merged",
           "collapse every unit into a single stratum",
           "heterogeneity and allocation imbalance become invisible",
           _merge_strata, _detect_merge_strata, "18, 27"),
    Mutant("inference_structure_unchecked",
           "accept a declared structure the records contradict",
           "resampling at a level where independence does not hold",
           _accept_any_inference_structure, _detect_accept_any_inference_structure, "3, 27"),
]


def run_mutation_audit(keys: list[str] | None = None) -> list[MutationResult]:
    """Apply every mutant and record whether anything caught it."""
    wanted = set(keys) if keys else None
    results: list[MutationResult] = []
    for mutant in MUTANTS:
        if wanted and mutant.key not in wanted:
            continue
        try:
            detected = bool(mutant.detect())
            detail = "" if detected else "no check distinguished the mutant from the original"
        except Exception as exc:  # noqa: BLE001 - a raised error is a detection
            detected, detail = True, f"detected by raising {type(exc).__name__}: {exc}"
        results.append(MutationResult(mutant=mutant, detected=detected, detail=detail))
    return results


def summary(results: list[MutationResult]) -> dict[str, Any]:
    return {
        "n_mutants": len(results),
        "n_detected": sum(1 for r in results if r.detected),
        "undetected": [r.mutant.key for r in results if not r.detected],
        "results": [r.as_dict() for r in results],
    }
