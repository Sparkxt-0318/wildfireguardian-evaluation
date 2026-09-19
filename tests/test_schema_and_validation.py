"""The validator must refuse structures that make conclusions misleading."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wg_eval.schema import describe_schema, level_keys
from wg_eval.validate import MIN_CLUSTERS_FOR_BOOTSTRAP, validate_records


def codes(report) -> set[str]:
    return {i.code for i in report.issues}


def test_clean_records_pass(records):
    report = validate_records(records, strata=["landscape", "mobility"])
    assert report.ok
    assert not report.errors
    assert report.summary["n_policies"] == 2
    assert report.summary["n_common_worlds"] == report.summary["n_worlds"]


def test_missing_required_columns_is_an_error(records):
    report = validate_records(records.drop(columns=["world_id"]))
    assert not report.ok
    assert "missing_required_columns" in codes(report)


def test_empty_table_is_an_error(records):
    report = validate_records(records.iloc[0:0])
    assert not report.ok
    assert "empty_table" in codes(report)


def test_duplicate_observation_keys_are_an_error(records):
    doubled = pd.concat([records, records.iloc[:5]], ignore_index=True)
    report = validate_records(doubled)
    assert not report.ok
    assert "duplicate_records" in codes(report)


def test_event_shared_across_worlds_breaks_nesting(records):
    broken = records.copy()
    first_event = broken.loc[0, "event_id"]
    other_world = broken.loc[broken["world_id"] != broken.loc[0, "world_id"], "world_id"].iloc[0]
    idx = broken.index[broken["world_id"] == other_world][0]
    broken.loc[idx, "event_id"] = first_event
    report = validate_records(broken)
    assert not report.ok
    assert "broken_nesting" in codes(report)


def test_non_binary_outcome_is_an_error(records):
    bad = records.copy()
    bad.loc[0, "mission_success"] = 7
    report = validate_records(bad)
    assert not report.ok
    assert "non_binary_outcome" in codes(report)


def test_non_finite_value_is_an_error(records):
    bad = records.copy()
    bad.loc[0, "loss"] = np.inf
    report = validate_records(bad)
    assert not report.ok
    assert "non_finite_value" in codes(report)


def test_negative_loss_is_a_warning_not_an_error(records):
    odd = records.copy()
    odd.loc[0, "loss"] = -1.0
    report = validate_records(odd)
    assert report.ok
    assert "implausible_value" in codes(report)


def test_single_cluster_is_refused(records):
    one = records[records["world_id"] == records["world_id"].iloc[0]]
    report = validate_records(one)
    assert not report.ok
    assert "single_cluster" in codes(report)


def test_few_clusters_warns_with_the_count(records):
    worlds = sorted(records["world_id"].unique())[: MIN_CLUSTERS_FOR_BOOTSTRAP - 5]
    small = records[records["world_id"].isin(worlds)]
    report = validate_records(small)
    assert report.ok
    issue = next(i for i in report.issues if i.code == "few_clusters")
    assert issue.detail["n_clusters"] == len(worlds)


def test_unbalanced_policy_coverage_warns_and_counts_common_worlds(records):
    worlds = sorted(records["world_id"].unique())
    dropped = set(worlds[:6])
    partial = records[~((records["policy_id"] == "policy_b") & records["world_id"].isin(dropped))]
    report = validate_records(partial)
    assert report.ok
    issue = next(i for i in report.issues if i.code == "unbalanced_policy_coverage")
    assert issue.detail["n_common_worlds"] == len(worlds) - len(dropped)


def test_no_common_worlds_is_an_error(records):
    worlds = sorted(records["world_id"].unique())
    half = set(worlds[: len(worlds) // 2])
    disjoint = pd.concat(
        [
            records[(records["policy_id"] == "policy_a") & records["world_id"].isin(half)],
            records[(records["policy_id"] == "policy_b") & ~records["world_id"].isin(half)],
        ],
        ignore_index=True,
    )
    report = validate_records(disjoint)
    assert not report.ok
    assert "no_common_worlds" in codes(report)


def test_stratum_varying_within_world_warns(records):
    bad = records.copy()
    bad.loc[0, "landscape"] = "moon"
    report = validate_records(bad, strata=["landscape"])
    assert report.ok
    assert "stratum_varies_within_world" in codes(report)


def test_missing_stratum_column_is_an_error(records):
    report = validate_records(records, strata=["not_a_column"])
    assert not report.ok
    assert "missing_stratum_column" in codes(report)


def test_nesting_ratio_is_always_reported(records):
    report = validate_records(records)
    issue = next(i for i in report.issues if i.code == "nesting_ratio")
    assert issue.detail["observations_per_world"] > 1


def test_failure_reason_consistency_warnings(records):
    odd = records.copy()
    successes = odd.index[odd["mission_success"] == 1][:3]
    odd.loc[successes, "failure_reason"] = "late_arrival"
    failures = odd.index[odd["mission_success"] == 0][:3]
    odd.loc[failures, "failure_reason"] = pd.NA
    report = validate_records(odd)
    assert {"success_with_failure_reason", "failure_without_reason"} <= codes(report)


def test_raise_if_errors_mentions_every_error(records):
    report = validate_records(records.drop(columns=["world_id"]))
    with pytest.raises(ValueError, match="missing_required_columns"):
        report.raise_if_errors()


def test_level_keys_and_schema_description():
    assert level_keys("event") == ("world_id", "event_id")
    assert level_keys("world") == ("world_id",)
    with pytest.raises(ValueError):
        level_keys("resident_group")
    assert "world_id" in describe_schema()


def test_outcome_column_cannot_be_used_as_a_stratum(records):
    """Subsetting on something the policy influenced selects on the result."""
    report = validate_records(records, strata=["loss"])
    assert not report.ok
    assert "outcome_used_as_stratum" in codes(report)
    assert "mission_success" not in codes(report)


def test_identifier_cannot_be_used_as_a_stratum(records):
    report = validate_records(records, strata=["resident_id"])
    assert not report.ok
    assert "outcome_used_as_stratum" in codes(report)
