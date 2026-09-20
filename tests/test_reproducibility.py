"""Seeds, fingerprints and version semantics, audited adversarially.

Two properties are checked in both directions:

* things that should change a result **do** change it, and change its
  scientific fingerprint;
* things that should not change a result -- row order, policy order,
  presentation -- change neither.
"""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from wg_eval.compare import compare_policies
from wg_eval.config import config_from_mapping
from wg_eval.dataio import write_records
from wg_eval.provenance import Provenance, provenance_for, text_checksum
from wg_eval.report import render_markdown, reproducibility_manifest
from wg_eval.version import REPORT_GENERATOR_VERSION, code_version


def run(frame, mapping, source=None, metrics=("mean_loss",)):
    return compare_policies(
        frame, config_from_mapping(mapping), source=source, validate=False,
        metrics=list(metrics),
    )


def fingerprint(result, metric="mean_loss", candidate="policy_b") -> str:
    return result.get(metric, candidate).provenance.scientific_fingerprint()


# ---------------------------------------------------------------------------
# seeds and ordering
# ---------------------------------------------------------------------------

def test_the_same_seed_gives_the_same_interval(records, config_mapping):
    a = run(records, config_mapping)
    b = run(records, config_mapping)
    assert a.get("mean_loss", "policy_b").difference.ci == b.get("mean_loss", "policy_b").difference.ci


def test_a_different_seed_gives_a_different_interval(records, config_mapping):
    other = copy.deepcopy(config_mapping)
    other["bootstrap"]["seed"] = config_mapping["bootstrap"]["seed"] + 1
    a = run(records, config_mapping).get("mean_loss", "policy_b")
    b = run(records, other).get("mean_loss", "policy_b")
    assert a.difference.ci != b.difference.ci
    # ... and the point estimate, which is deterministic, does not move.
    assert a.difference.estimate == pytest.approx(b.difference.estimate)


def test_row_order_does_not_change_anything(records, config_mapping):
    shuffled = records.sample(frac=1.0, random_state=7).reset_index(drop=True)
    a = run(records, config_mapping).get("mean_loss", "policy_b")
    b = run(shuffled, config_mapping).get("mean_loss", "policy_b")
    assert a.difference.estimate == pytest.approx(b.difference.estimate)
    assert a.difference.ci == pytest.approx(b.difference.ci)


def test_policy_order_in_the_table_does_not_change_anything(records, config_mapping):
    reordered = pd.concat(
        [records[records["policy_id"] == "policy_b"], records[records["policy_id"] == "policy_a"]],
        ignore_index=True,
    )
    a = run(records, config_mapping).get("mean_loss", "policy_b")
    b = run(reordered, config_mapping).get("mean_loss", "policy_b")
    assert a.difference.estimate == pytest.approx(b.difference.estimate)
    assert a.difference.ci == pytest.approx(b.difference.ci)


def test_swapping_baseline_and_candidate_flips_the_sign_and_nothing_else(records, config_mapping):
    flipped = copy.deepcopy(config_mapping)
    flipped["comparison"] = {"baseline": "policy_b", "candidates": ["policy_a"]}
    forward = run(records, config_mapping).get("mean_loss", "policy_b")
    backward = run(records, flipped).get("mean_loss", "policy_a")
    assert backward.difference.estimate == pytest.approx(-forward.difference.estimate)


def test_deterministic_estimators_stay_deterministic(records, config_mapping):
    a = run(records, config_mapping).get("mean_loss", "policy_b")
    b = run(records, config_mapping).get("mean_loss", "policy_b")
    assert a.baseline_result.estimate == b.baseline_result.estimate
    assert a.candidate_result.estimate == b.candidate_result.estimate


def test_the_rng_algorithm_is_recorded(records, config_mapping):
    prov = run(records, config_mapping).get("mean_loss", "policy_b").provenance
    assert "PCG64" in prov.environment["rng"]


# ---------------------------------------------------------------------------
# fingerprint audit
# ---------------------------------------------------------------------------

def test_source_content_changes_the_fingerprint(records, config_mapping, tmp_path):
    original = write_records(records, tmp_path / "a.parquet")
    edited = records.copy()
    edited.loc[0, "loss"] = float(edited.loc[0, "loss"]) + 1.0
    changed = write_records(edited, tmp_path / "b.parquet")
    assert fingerprint(run(records, config_mapping, source=original)) != fingerprint(
        run(edited, config_mapping, source=changed)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c["bootstrap"].__setitem__("seed", 999), id="seed"),
        pytest.param(lambda c: c["bootstrap"].__setitem__("confidence_level", 0.9),
                     id="confidence_level"),
        pytest.param(lambda c: c["bootstrap"].__setitem__("method", "basic"), id="method"),
        pytest.param(lambda c: c["bootstrap"].__setitem__("n_resamples", 500), id="n_resamples"),
        pytest.param(lambda c: c.__setitem__("filters", {"exclude": {"action": ["action_1"]}}),
                     id="filters"),
        pytest.param(lambda c: c.__setitem__(
            "filters", {"exclusions": {"big": "loss > 1e9"}}), id="exclusions"),
        pytest.param(lambda c: c["aggregation"].__setitem__(
            "resident_id->event_id", {"loss": "median"}), id="aggregation_rule"),
        pytest.param(lambda c: c["metrics"][0].__setitem__("estimator", "median"),
                     id="metric_definition"),
        pytest.param(lambda c: c["metrics"][0].__setitem__("level", "world_id"),
                     id="metric_level"),
        pytest.param(lambda c: c["metrics"][0].__setitem__("direction", "higher_is_better"),
                     id="metric_direction"),
        pytest.param(lambda c: c["missing_data"].__setitem__("policy", "impute_worst"),
                     id="missing_data_policy"),
        pytest.param(lambda c: c["equivalence"]["margins"]["mean_loss"].__setitem__("upper", 3.0),
                     id="margin"),
    ],
)
def test_every_scientific_input_changes_the_fingerprint(records, config_mapping, mutate):
    base = copy.deepcopy(config_mapping)
    base.setdefault("missing_data", {"policy": "drop_record"})
    base.setdefault("filters", {})
    variant = copy.deepcopy(base)
    mutate(variant)
    assert fingerprint(run(records, base)) != fingerprint(run(records, variant))


def test_the_declared_hierarchy_changes_the_fingerprint_and_the_number():
    """Rolling up through events is a different estimand from pooling observations.

    The two coincide only when every event is the same size, so this fixture
    deliberately varies event size.
    """
    from wg_eval.synth.generators import PolicyBehaviour, WorldModel, generate_experiment

    frame = generate_experiment(
        [
            PolicyBehaviour("policy_a", interaction_sd=0.5),
            PolicyBehaviour("policy_b", loss_shift=0.8, interaction_sd=0.5),
        ],
        WorldModel(n_units=24, events_per_unit=3, observations_per_event=(3, 25),
                   unit_sd=3.0),
        seed=4,
    )

    def mapping(nested):
        return {
            "inference": {"primary_unit": "world_id", "nested_units": nested},
            "metrics": [{"name": "mean_loss", "column": "loss", "level": "world_id",
                         "role": "primary"}],
            "aggregation": {"default_rule": "mean"},
            "comparison": {"baseline": "policy_a", "candidates": ["policy_b"]},
            "bootstrap": {"n_resamples": 300, "seed": 5},
            "strata": [],
        }

    two_step = run(frame, mapping(["event_id", "resident_id"]))
    one_step = run(frame, mapping(["resident_id"]))
    assert fingerprint(two_step) != fingerprint(one_step)
    assert two_step.get("mean_loss", "policy_b").baseline_result.estimate != pytest.approx(
        one_step.get("mean_loss", "policy_b").baseline_result.estimate
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c.__setitem__("label", "a different title"), id="label"),
        pytest.param(lambda c: c["metrics"][0].__setitem__("description", "prose"),
                     id="metric_description"),
    ],
)
def test_presentation_only_changes_leave_the_scientific_fingerprint_alone(
    records, config_mapping, mutate
):
    base = copy.deepcopy(config_mapping)
    variant = copy.deepcopy(base)
    mutate(variant)
    assert fingerprint(run(records, base)) == fingerprint(run(records, variant))


def test_the_full_fingerprint_does_move_for_presentation_changes(records, config_mapping):
    """The two fingerprints exist to be different; this is the difference."""
    base = copy.deepcopy(config_mapping)
    variant = copy.deepcopy(base)
    variant["label"] = "another label"
    a = run(records, base).get("mean_loss", "policy_b").provenance
    b = run(records, variant).get("mean_loss", "policy_b").provenance
    assert a.scientific_fingerprint() == b.scientific_fingerprint()
    assert a.fingerprint() != b.fingerprint()


def test_source_path_alone_does_not_change_the_scientific_fingerprint(
    records, config_mapping, tmp_path
):
    """Moving a file does not re-analyse it; the content hash is what matters."""
    here = write_records(records, tmp_path / "here.parquet")
    there = write_records(records, tmp_path / "elsewhere.parquet")
    assert here.checksum == there.checksum
    assert fingerprint(run(records, config_mapping, source=here)) == fingerprint(
        run(records, config_mapping, source=there)
    )


def test_code_version_is_part_of_the_scientific_payload(records, config_mapping):
    prov = run(records, config_mapping).get("mean_loss", "policy_b").provenance
    assert prov.scientific_payload()["code_version"] == prov.code_version


# ---------------------------------------------------------------------------
# version semantics
# ---------------------------------------------------------------------------

def test_the_manifest_separates_the_five_things_that_move_independently(
    records, config_mapping, tmp_path
):
    source = write_records(records, tmp_path / "records.parquet")
    result = run(records, config_mapping, source=source)
    manifest = reproducibility_manifest(result)["manifest"]
    assert manifest["code_version"] == code_version()
    assert manifest["report_generator_version"] == REPORT_GENERATOR_VERSION
    assert manifest["source_data_hash"] == source.checksum
    assert manifest["analysis_config_hash"].startswith("sha256:")
    assert manifest["protocol_hash"] == "(none declared)"


def test_a_declared_protocol_hash_reaches_the_manifest(records, config_mapping):
    declared = copy.deepcopy(config_mapping)
    declared["analysis_status"] = "preregistered"
    declared["protocol"] = {"hash": "sha256:deadbeef", "commit": "abc1234",
                            "timestamp": "2026-09-01T00:00:00Z"}
    result = run(records, declared)
    manifest = reproducibility_manifest(result)["manifest"]
    assert manifest["protocol_hash"] == "sha256:deadbeef"
    assert result.config.protocol.is_preregistered


def test_preregistration_cannot_be_claimed_without_evidence(config_mapping):
    from wg_eval.config import ConfigError

    bad = copy.deepcopy(config_mapping)
    bad["analysis_status"] = "preregistered"
    with pytest.raises(ConfigError, match="cannot be checked"):
        config_from_mapping(bad)


def test_regenerating_a_report_does_not_change_the_scientific_fingerprints(
    records, config_mapping, tmp_path
):
    source = write_records(records, tmp_path / "records.parquet")
    result = run(records, config_mapping, source=source)
    first = reproducibility_manifest(result)["scientific_fingerprints"]
    render_markdown(result, title="one")
    render_markdown(result, title="two")
    assert reproducibility_manifest(result)["scientific_fingerprints"] == first


def test_provenance_fingerprints_ignore_the_wall_clock():
    a = Provenance(bootstrap={"seed": 1}, created_at="2026-01-01T00:00:00+00:00")
    b = Provenance(bootstrap={"seed": 1}, created_at="2027-06-06T12:00:00+00:00")
    assert a.fingerprint() == b.fingerprint()
    assert a.scientific_fingerprint() == b.scientific_fingerprint()


def test_text_checksum_is_stable_and_sensitive():
    assert text_checksum("abc") == text_checksum("abc")
    assert text_checksum("abc") != text_checksum("abd")


def test_provenance_for_records_the_estimand_and_the_structure():
    prov = provenance_for(
        inference={"primary_unit": "event_id", "nested_units": ["world_id"]},
        estimand={"contrast": "b - a", "conditioning": "shared units"},
        bootstrap={"seed": 3},
    )
    assert prov.inference["primary_unit"] == "event_id"
    assert "shared units" in prov.to_text()
