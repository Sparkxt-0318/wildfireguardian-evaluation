"""The suite must be able to fail.

Each mutant introduces one deliberate statistical error and asserts that
something catches it.  A mutant nothing detects is a hole in the library's
checks, and this file fails rather than reporting a clean bill of health.
"""

from __future__ import annotations

import pytest

from wg_eval.mutation import MUTANTS, run_mutation_audit, summary

REQUIRED_KEYS = {
    "observation_bootstrap",
    "unpaired_analysis_of_paired_data",
    "equivalence_without_a_margin",
    "p_value_read_as_equivalence",
    "reversed_metric_orientation",
    "cvar_wrong_tail",
    "failed_runs_dropped_silently",
    "uneven_unit_reuse_unreported",
    "multiplicity_family_ignored",
    "strata_incorrectly_merged",
    "inference_structure_unchecked",
}


def test_every_required_mutant_is_defined():
    assert {m.key for m in MUTANTS} == REQUIRED_KEYS


def test_every_mutant_documents_what_it_breaks():
    for mutant in MUTANTS:
        assert mutant.description.strip()
        assert mutant.wrong_behaviour.strip()
        assert mutant.audit_item.strip()


@pytest.mark.slow
@pytest.mark.parametrize("key", sorted(REQUIRED_KEYS))
def test_the_mutant_is_detected(key: str):
    result = run_mutation_audit([key])[0]
    assert result.detected, (
        f"{key}: {result.mutant.wrong_behaviour} -- and nothing in the library caught it. "
        f"{result.detail}"
    )


@pytest.mark.slow
def test_the_audit_summary_reports_no_holes():
    report = summary(run_mutation_audit())
    assert report["n_detected"] == report["n_mutants"]
    assert report["undetected"] == []
