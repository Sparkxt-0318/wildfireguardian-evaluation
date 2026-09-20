"""The command line surface."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from wg_eval.cli import DEFAULT_CONFIG, main
from wg_eval.dataio import write_records


@pytest.fixture
def workspace(records, tmp_path):
    results = tmp_path / "results.parquet"
    write_records(records, results)
    config = tmp_path / "analysis.yaml"
    config.write_text(DEFAULT_CONFIG.replace("[difficulty, scale, regime, capacity]",
                                             "[difficulty, scale]"))
    return tmp_path, results, config


def test_schema_and_metrics_commands(capsys):
    assert main(["schema"]) == 0
    assert "world_id" in capsys.readouterr().out
    assert main(["metrics"]) == 0
    assert "cvar" in capsys.readouterr().out


def test_init_config_writes_and_refuses_to_clobber(tmp_path, capsys):
    out = tmp_path / "analysis.yaml"
    assert main(["init-config", "--out", str(out)]) == 0
    assert out.exists()
    assert main(["init-config", "--out", str(out)]) == 2
    assert main(["init-config", "--out", str(out), "--force"]) == 0


def test_validate_results_passes_and_emits_json(workspace, capsys):
    _, results, config = workspace
    assert main(["validate-results", str(results), "--config", str(config)]) == 0
    assert "validation: PASS" in capsys.readouterr().out
    assert main(["validate-results", str(results), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source"]["checksum"].startswith("sha256:")


def test_validate_results_fails_on_broken_records(records, tmp_path, capsys):
    broken = pd.concat([records, records.iloc[:3]], ignore_index=True)
    path = tmp_path / "broken.parquet"
    write_records(broken, path)
    assert main(["validate-results", str(path)]) == 1
    assert "duplicate_records" in capsys.readouterr().out


def test_validate_strict_treats_warnings_as_failures(records, tmp_path, capsys):
    worlds = sorted(records["world_id"].unique())[:8]
    small = records[records["world_id"].isin(worlds)]
    path = tmp_path / "small.parquet"
    write_records(small, path)
    assert main(["validate-results", str(path)]) == 0
    assert main(["validate-results", str(path), "--strict"]) == 1


def test_compare_prints_findings(workspace, capsys):
    _, results, config = workspace
    assert main(["compare", str(results), str(config), "--metric", "mean_loss"]) == 0
    out = capsys.readouterr().out
    assert "world_id > event_id > resident_id" in out
    assert "mean_loss" in out
    assert "analysis status" in out


def test_compare_json_and_markdown(workspace, capsys):
    _, results, config = workspace
    assert main(["compare", str(results), str(config), "--metric", "mean_loss", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["comparisons"][0]["metric"]["name"] == "mean_loss"
    assert main(["compare", str(results), str(config), "--metric", "mean_loss", "--markdown"]) == 0
    assert "## 2. Findings" in capsys.readouterr().out


def test_compare_with_stratification(workspace, capsys):
    _, results, config = workspace
    assert main(["compare", str(results), str(config), "--metric", "mean_loss",
                 "--stratify", "difficulty"]) == 0
    assert "by difficulty" in capsys.readouterr().out


def test_bootstrap_reports_per_policy_intervals(workspace, capsys):
    _, results, config = workspace
    assert main(["bootstrap", str(results), str(config), "--metric", "mean_loss"]) == 0
    out = capsys.readouterr().out
    assert "n_units" in out
    assert "must not be eyeballed for overlap" in out


def test_report_writes_every_artifact_and_passes_the_wording_audit(workspace, tmp_path, capsys):
    _, results, config = workspace
    outdir = tmp_path / "reports"
    assert main(["report", str(results), str(config), "--out", str(outdir)]) == 0
    assert (outdir / "report.md").exists()
    assert (outdir / "report.json").exists()
    assert (outdir / "report_summary.csv").exists()
    assert (outdir / "report_manifest.json").exists()
    out = capsys.readouterr().out
    assert "reproducibility manifest" in out
    assert "analysis_config_hash" in out


def test_ledger_command_shows_what_each_policy_was_given(workspace, capsys):
    _, results, config = workspace
    assert main(["ledger", str(results), str(config)]) == 0
    out = capsys.readouterr().out
    assert "unit of inference: world_id" in out
    assert "shared by every policy" in out
    assert "Reported, not corrected" in out


def test_synth_writes_records_and_their_truth(tmp_path, capsys):
    out = tmp_path / "fixture.parquet"
    assert main(["synth", "--scenario", "practical_equivalence", "--out", str(out)]) == 0
    assert out.exists()
    truth = json.loads((tmp_path / "fixture.truth.json").read_text())
    assert truth["scenario"] == "practical_equivalence"
    assert "true_mean_loss_difference" in truth["truth"]


def test_synth_rejects_an_unknown_scenario(tmp_path):
    assert main(["synth", "--scenario", "nope", "--out", str(tmp_path / "x.parquet")]) == 2


def test_redteam_rejects_an_unknown_scenario():
    assert main(["redteam", "--scenario", "nope"]) == 2


def test_missing_file_exits_nonzero_without_a_traceback(tmp_path, capsys):
    assert main(["validate-results", str(tmp_path / "absent.parquet")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err
