"""The declared inference structure, and refusing declarations the data denies."""

from __future__ import annotations

import pandas as pd
import pytest

from wg_eval.config import ConfigError, config_from_mapping
from wg_eval.hierarchy import (
    InferenceSpec,
    InferenceStructureError,
    candidate_unit_columns,
    check_structure,
    infer_default,
    nesting_relation,
    require_valid_structure,
    unit_labels,
)
from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment


def codes(findings) -> set[str]:
    return {f["code"] for f in findings}


def toy(event_spans_units: bool = False) -> pd.DataFrame:
    if event_spans_units:
        events = ["e1", "e1", "e1", "e1", "e2", "e2"]
    else:
        events = ["w1e1", "w1e2", "w2e1", "w2e2", "w3e1", "w3e2"]
    return pd.DataFrame(
        {
            "world_id": ["w1", "w1", "w2", "w2", "w3", "w3"],
            "event_id": events,
            "resident_id": list("abcdef"),
            "policy_id": ["p", "q"] * 3,
            "loss": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )


def test_default_spec_describes_the_usual_hierarchy():
    spec = InferenceSpec()
    assert spec.levels == ("world_id", "event_id", "resident_id")
    assert spec.keys_for("event_id") == ("world_id", "event_id")
    assert spec.steps() == (("resident_id", "event_id"), ("event_id", "world_id"))
    assert "world_id > event_id > resident_id" in spec.describe()


def test_short_level_names_resolve_to_columns():
    spec = InferenceSpec(primary_unit="world", nested_units=("event", "resident"))
    assert spec.levels == ("world_id", "event_id", "resident_id")


def test_outcomes_and_the_policy_label_are_not_units():
    with pytest.raises(InferenceStructureError, match="not an experimental unit"):
        InferenceSpec(primary_unit="policy_id")
    with pytest.raises(InferenceStructureError, match="not levels of the experimental design"):
        InferenceSpec(primary_unit="world_id", nested_units=("loss",))


def test_a_unit_cannot_be_nested_inside_itself():
    with pytest.raises(InferenceStructureError, match="not nested inside itself"):
        InferenceSpec(primary_unit="world_id", nested_units=("world_id",))


def test_nesting_relation_reads_containment_from_the_data():
    flat = toy()
    assert nesting_relation(flat, "event_id", "world_id") == "a_within_b"
    assert nesting_relation(flat, "world_id", "event_id") == "b_within_a"
    spanning = toy(event_spans_units=True)
    assert nesting_relation(spanning, "world_id", "event_id") == "a_within_b"


def test_one_to_one_levels_are_reported_as_degenerate():
    frame = toy()
    frame["event_id"] = frame["world_id"] + "-only"
    findings = check_structure(frame, InferenceSpec())
    assert "degenerate_level" in codes(findings)
    assert all(f["severity"] == "warning" for f in findings if f["code"] == "degenerate_level")


def test_an_event_that_contains_units_makes_the_default_declaration_an_error():
    frame = toy(event_spans_units=True)
    findings = check_structure(frame, InferenceSpec())
    assert "inverted_nesting" in codes(findings)
    with pytest.raises(InferenceStructureError, match="primary_unit: event_id"):
        require_valid_structure(frame, InferenceSpec())


def test_the_same_records_pass_under_the_correct_declaration():
    frame = toy(event_spans_units=True)
    spec = InferenceSpec(primary_unit="event_id", nested_units=("world_id", "resident_id"))
    assert require_valid_structure(frame, spec) == []


def test_an_undeclared_coarser_grouping_is_an_error():
    """Distinct identifiers are not evidence of independence."""
    frame = toy()
    frame["batch_id"] = ["b1"] * 4 + ["b2"] * 2
    findings = check_structure(frame, InferenceSpec())
    assert "undeclared_coarser_grouping" in codes(findings)
    detail = next(f for f in findings if f["code"] == "undeclared_coarser_grouping")["detail"]
    assert detail["column"] == "batch_id" and detail["n_groups"] == 2


def test_crossed_levels_are_refused():
    frame = pd.DataFrame(
        {
            "world_id": ["w1", "w1", "w2", "w2"],
            "event_id": ["e1", "e2", "e1", "e2"],
            "resident_id": list("abcd"),
            "policy_id": ["p"] * 4,
            "loss": [1.0, 2.0, 3.0, 4.0],
        }
    )
    findings = check_structure(frame, InferenceSpec())
    assert "crossed_levels" in codes(findings)


def test_a_single_unit_is_refused():
    frame = toy()
    frame["world_id"] = "w1"
    findings = check_structure(frame, InferenceSpec())
    assert "single_primary_unit" in codes(findings)


def test_missing_declared_columns_are_reported_before_anything_else():
    findings = check_structure(toy().drop(columns=["event_id"]), InferenceSpec())
    assert codes(findings) == {"undeclared_unit_column_missing"}


def test_candidate_unit_columns_ignores_outcomes_and_declared_levels():
    frame = toy()
    frame["batch_id"] = "b1"
    assert candidate_unit_columns(frame, declared=InferenceSpec().levels) == ["batch_id"]


def test_unit_labels_compose_the_keys_above_a_level():
    frame = toy()
    spec = InferenceSpec()
    labels = unit_labels(frame, spec, "event_id")
    assert labels.nunique() == 6
    with pytest.raises(KeyError, match="needed because"):
        unit_labels(frame.drop(columns=["world_id"]), spec, "event_id")


def test_infer_default_uses_only_the_columns_present():
    frame = toy().drop(columns=["resident_id"])
    spec = infer_default(frame)
    assert spec.levels == ("world_id", "event_id")
    with pytest.raises(InferenceStructureError, match="declare inference.primary_unit"):
        infer_default(frame.drop(columns=["world_id", "event_id"]))


def test_config_refuses_observation_level_inference():
    with pytest.raises(ConfigError, match="pseudoreplication"):
        config_from_mapping(
            {
                "inference": {"primary_unit": "resident_id"},
                "metrics": [{"name": "m", "column": "loss", "level": "resident_id"}],
            }
        )


def test_generated_records_with_shared_events_need_the_event_declaration():
    frame = generate_experiment(
        [PolicyBehaviour("policy_a"), PolicyBehaviour("policy_b", loss_shift=0.4)],
        WorldModel(n_units=16, events_per_unit=1, observations_per_event=3,
                   units_per_shared_event=4, shared_event_sd=2.0),
        seed=1,
    )
    with pytest.raises(InferenceStructureError):
        require_valid_structure(frame, InferenceSpec())
    spec = InferenceSpec(primary_unit="event_id", nested_units=("world_id", "resident_id"))
    assert require_valid_structure(frame, spec) == []
    assert spec.keys_for("world_id") == ("event_id", "world_id")
