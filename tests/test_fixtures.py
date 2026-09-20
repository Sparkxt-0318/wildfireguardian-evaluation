"""The committed fixtures must stay reproducible and keep their stated properties."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from wg_eval.cli import main
from wg_eval.compare import compare_policies
from wg_eval.config import load_config
from wg_eval.dataio import load_records
from wg_eval.validate import validate_records

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURES / "MANIFEST.json").read_text())


def test_manifest_lists_every_data_fixture(manifest):
    on_disk = {p.name for p in FIXTURES.iterdir() if p.suffix in {".parquet", ".csv"}}
    assert set(manifest["fixtures"]) == on_disk


def test_fixtures_are_reproducible_from_the_generator(manifest, tmp_path, monkeypatch):
    """Regenerating must give byte-identical CSVs and content-identical parquet."""
    import tests.fixtures.generate_fixtures as gen

    monkeypatch.setattr(gen, "HERE", tmp_path)
    gen.main()
    for name in manifest["fixtures"]:
        fresh, committed = tmp_path / name, FIXTURES / name
        if name.endswith(".csv"):
            assert fresh.read_bytes() == committed.read_bytes(), name
        else:
            # Parquet embeds writer metadata, so compare the records themselves.
            pd.testing.assert_frame_equal(pd.read_parquet(fresh), pd.read_parquet(committed))


def test_paired_balanced_fixture_validates_and_recovers_its_truth(manifest):
    frame, source = load_records(FIXTURES / "paired_balanced.parquet")
    config = load_config(FIXTURES / "analysis.yaml")
    report = validate_records(frame, strata=config.strata, inference=config.inference)
    assert report.ok and not report.warnings

    truth = manifest["fixtures"]["paired_balanced.parquet"]["truth"]
    result = compare_policies(frame, config, source=source, metrics=["mean_loss"])
    c = result.get("mean_loss", "policy_b")
    assert c.difference.n_clusters == truth["n_worlds"]
    assert c.difference.ci_low <= truth["true_mean_loss_difference"] <= c.difference.ci_high


def test_failed_runs_fixture_keeps_crashed_runs_visible(manifest):
    frame, source = load_records(FIXTURES / "failed_runs.parquet")
    config = load_config(FIXTURES / "analysis.yaml")
    report = validate_records(frame, strata=config.strata, inference=config.inference)
    assert report.ok
    assert "incomplete_runs" in {i.code for i in report.warnings}

    result = compare_policies(frame, config, source=source, metrics=["mean_loss"])
    ledger = result.run_status["ledger"]
    assert ledger["available"]
    assert ledger["n_not_completed"] > 0
    crashed = [r for r in ledger["counts"] if r["status"] == "crashed"]
    assert crashed and crashed[0]["handling"] == "failure"


def test_missing_worlds_fixture_warns_and_reverses_under_pairing(manifest):
    frame, source = load_records(FIXTURES / "missing_worlds.csv")
    config = load_config(FIXTURES / "analysis.yaml")
    report = validate_records(frame, strata=config.strata, inference=config.inference)
    assert report.ok
    assert "unbalanced_policy_coverage" in {i.code for i in report.warnings}

    truth = manifest["fixtures"]["missing_worlds.csv"]["truth"]
    n_dropped = len(truth["policy_b_missing_from"])

    paired = compare_policies(frame, config, source=source, metrics=["mean_loss"])
    unpaired_config = load_config(FIXTURES / "analysis.yaml")
    object.__setattr__(unpaired_config.comparison, "require_common_units", False)
    unpaired = compare_policies(frame, unpaired_config, source=source, metrics=["mean_loss"])

    p = paired.get("mean_loss", "policy_b")
    u = unpaired.get("mean_loss", "policy_b")
    assert p.difference.n_clusters == truth["n_worlds"] - n_dropped
    assert p.difference.estimate > 0          # paired recovers "policy_b is worse"
    assert u.difference.estimate < 0          # unpaired reverses it
    assert any("UNPAIRED" in n for n in u.notes)


def test_invalid_fixture_fails_validation_with_the_expected_codes(manifest):
    frame, _ = load_records(FIXTURES / "invalid_records.csv")
    report = validate_records(frame)
    assert not report.ok
    expected = set(manifest["fixtures"]["invalid_records.csv"]["expected_validation_errors"])
    assert expected <= {i.code for i in report.errors}


def test_cli_runs_against_the_committed_fixtures(tmp_path):
    assert main(["validate-results", str(FIXTURES / "paired_balanced.parquet"),
                 "--config", str(FIXTURES / "analysis.yaml")]) == 0
    assert main(["ledger", str(FIXTURES / "failed_runs.parquet"),
                 str(FIXTURES / "analysis.yaml")]) == 0
    assert main(["validate-results", str(FIXTURES / "invalid_records.csv")]) == 1
    assert main(["report", str(FIXTURES / "paired_balanced.parquet"),
                 str(FIXTURES / "analysis.yaml"), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.md").exists()
