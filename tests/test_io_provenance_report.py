"""Loading, checksums, provenance and report rendering."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from wg_eval.compare import compare_policies
from wg_eval.dataio import file_checksum, load_records, write_records
from wg_eval.provenance import (
    Provenance,
    environment_fingerprint,
    provenance_for,
    text_checksum,
)
from wg_eval.report import (
    audit_wording,
    render_markdown,
    render_text,
    reproducibility_manifest,
    result_to_json,
    write_report,
)
from wg_eval.stratify import compare_by_stratum


def test_parquet_round_trip_preserves_the_records(records, tmp_path):
    path = tmp_path / "records.parquet"
    source = write_records(records, path)
    loaded, loaded_source = load_records(path)
    assert source.checksum == loaded_source.checksum
    assert len(loaded) == len(records)
    assert set(loaded.columns) == set(records.columns)


def test_csv_round_trip_coerces_back_to_the_schema_types(records, tmp_path):
    path = tmp_path / "records.csv"
    write_records(records, path)
    loaded, source = load_records(path)
    assert source.format == "csv"
    assert pd.api.types.is_numeric_dtype(loaded["loss"])
    assert pd.api.types.is_numeric_dtype(loaded["mission_success"])
    assert str(loaded["world_id"].dtype) == "string"


def test_checksum_changes_with_content(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("x\n1\n")
    b.write_text("x\n2\n")
    assert file_checksum(a) != file_checksum(b)
    assert file_checksum(a) == file_checksum(a)


def test_unsupported_format_is_reported_clearly(tmp_path):
    path = tmp_path / "records.xlsx"
    path.write_bytes(b"not really a spreadsheet")
    with pytest.raises(ValueError, match="unsupported results format"):
        load_records(path)


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_records(tmp_path / "absent.parquet")


def test_provenance_fingerprint_ignores_time_but_not_content():
    first = provenance_for(bootstrap={"seed": 1}, confidence_level=0.95)
    second = provenance_for(bootstrap={"seed": 1}, confidence_level=0.95)
    third = provenance_for(bootstrap={"seed": 2}, confidence_level=0.95)
    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != third.fingerprint()


def test_provenance_records_everything_a_rerun_would_need(records, config, tmp_path):
    path = tmp_path / "records.parquet"
    source = write_records(records, path)
    result = compare_policies(records, config, source=source, metrics=["mean_loss"])
    prov = result.get("mean_loss", "policy_b").provenance.as_dict()
    assert prov["source"]["checksum"] == source.checksum
    assert prov["config"]["checksum"].startswith("sha256:")
    assert prov["bootstrap"]["n_resamples"] == config.bootstrap.n_resamples
    assert prov["metric"]["definition"]
    assert prov["missing_data"]["policy"] == "drop_record"
    assert "filters" in prov and "exclusions" in prov
    assert prov["code_version"].startswith("wg-eval")
    assert prov["environment"]["python"]
    assert prov["inference"]["primary_unit"] == "world_id"
    assert prov["estimand"]["contrast"] == "policy_b - policy_a"
    assert "observed under every" in prov["estimand"]["conditioning"] or prov["estimand"][
        "conditioning"
    ].startswith("all ")
    manifest = prov["manifest"]
    assert manifest["analysis_config_hash"].startswith("sha256:")
    assert manifest["source_data_hash"] == source.checksum
    assert manifest["report_generator_version"]


def test_provenance_text_is_human_readable():
    prov = Provenance(bootstrap={"n_resamples": 10, "seed": 1, "method": "percentile"})
    text = prov.to_text()
    assert "scientific_fingerprint:" in text
    assert "full_fingerprint:" in text
    assert "reproducibility manifest" in text


def test_text_checksum_and_environment_helpers():
    assert text_checksum("abc") == text_checksum("abc")
    assert text_checksum("abc") != text_checksum("abd")
    env = environment_fingerprint()
    assert "python" in env and "numpy" in env


def test_report_manifest_separates_the_five_hashes(records, config, tmp_path):
    path = tmp_path / "records.parquet"
    source = write_records(records, path)
    result = compare_policies(records, config, source=source, metrics=["mean_loss"])
    manifest = reproducibility_manifest(result)
    keys = set(manifest["manifest"])
    assert {"code_version", "analysis_config_hash", "source_data_hash", "protocol_hash",
            "report_generator_version"} <= keys


def test_generated_reports_make_no_forbidden_claim(records, config, tmp_path):
    """The wording audit runs over every surface the package can produce."""
    source = write_records(records, tmp_path / "records.parquet")
    result = compare_policies(records, config, source=source)
    strat = {"difficulty": compare_by_stratum(records, config, "difficulty", min_units=5)}
    for text in (
        render_markdown(result, title="Demo", stratified=strat),
        render_text(result),
    ):
        assert audit_wording(text) == []


def test_the_wording_audit_catches_the_claims_it_is_for():
    assert audit_wording("the analysis proves the policies are the same")
    assert audit_wording("there was no significant difference")
    assert audit_wording("policy_b is better overall")
    assert audit_wording("policy_a beats policy_b")
    # Legitimate uses of the same words survive.
    assert audit_wording("paired on the same units, with the same seed") == []


def test_markdown_report_contains_the_audit_trail(records, config, tmp_path):
    path = tmp_path / "records.parquet"
    source = write_records(records, path)
    result = compare_policies(records, config, source=source)
    strat = {"difficulty": compare_by_stratum(records, config, "difficulty", min_units=5)}
    text = render_markdown(result, title="Demo", stratified=strat)
    for needle in [
        "# Demo",
        source.checksum,
        "Inference structure",
        "bootstrap resamples of whole",
        "### Primary outcome",
        "Allocation ledger",
        "## 9. Provenance and reproducibility",
        "scientific_fingerprint",
        "undetermined",
        "Analysis families and multiplicity",
    ]:
        assert needle in text or needle.lower() in text.lower(), needle


def test_report_files_are_written_and_parseable(records, config, tmp_path):
    result = compare_policies(records, config)
    paths = write_report(result, tmp_path, title="Demo")
    assert paths.markdown.exists() and paths.json.exists() and paths.summary_csv.exists()
    assert paths.manifest.exists()
    payload = json.loads(paths.json.read_text())
    assert payload["comparisons"]
    assert payload["config"]["inference"]["primary_unit"] == "world_id"
    summary = pd.read_csv(paths.summary_csv)
    assert {"role", "metric", "difference", "ci_low", "ci_high", "verdict"} <= set(summary.columns)


def test_json_serialisation_handles_numpy_and_nan(records, config):
    result = compare_policies(records, config)
    payload = json.loads(result_to_json(result))
    assert isinstance(payload["comparisons"][0]["difference"]["estimate"], float)


def test_console_rendering_mentions_the_resampling_unit(records, config):
    result = compare_policies(records, config, metrics=["mean_loss"])
    text = render_text(result)
    assert "world_id > event_id > resident_id" in text
    assert "bootstrap:" in text


def test_code_version_identifies_the_package_and_its_revision():
    from wg_eval.version import __version__, code_version

    text = code_version()
    assert text.startswith(f"wg-eval {__version__}")
    # In a git checkout the revision is appended; outside one it is omitted.
    assert text == f"wg-eval {__version__}" or "(git " in text


def test_console_rendering_surfaces_validation_warnings(records, config):
    """A comparison that hid the coverage warning would hide why it matters."""
    worlds = sorted(records["world_id"].unique())[:6]
    partial = records[~((records["policy_id"] == "policy_b") & records["world_id"].isin(worlds))]
    result = compare_policies(partial, config, metrics=["mean_loss"])
    text = render_text(result)
    assert "not evaluated on the same set of world_ids" in text
    assert "! " in text
