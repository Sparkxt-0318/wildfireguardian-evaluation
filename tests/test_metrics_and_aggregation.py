"""Estimators, roll-up rules and missing-data handling."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from wg_eval.aggregate import (
    aggregate_to_level,
    apply_filters,
    apply_missing_policy,
    cluster_labels,
    observation_counts,
)
from wg_eval.config import (
    AggregationSpec,
    ConfigError,
    FilterSpec,
    MissingDataSpec,
    config_from_mapping,
)
from wg_eval.hierarchy import InferenceSpec
from wg_eval.metrics import build_estimator, credibility, definition_of, estimate

INFERENCE = InferenceSpec()


def test_mean_median_and_quantile():
    x = np.arange(1.0, 101.0)
    assert estimate(x, "mean") == pytest.approx(50.5)
    assert estimate(x, "median") == pytest.approx(50.5)
    assert estimate(x, "quantile", {"q": 0.9}) == pytest.approx(np.quantile(x, 0.9))
    assert estimate(x, "max") == 100.0
    assert estimate(x, "count") == 100.0


def test_cvar_is_the_mean_of_the_worst_k():
    x = np.arange(1.0, 101.0)
    # alpha=0.9 over 100 values -> the worst 10, i.e. 91..100.
    assert estimate(x, "cvar", {"alpha": 0.9}) == pytest.approx(np.arange(91.0, 101.0).mean())
    # The lower tail picks the best 10 instead.
    assert estimate(x, "cvar", {"alpha": 0.9, "tail": "lower"}) == pytest.approx(
        np.arange(1.0, 11.0).mean()
    )


def test_cvar_always_keeps_at_least_one_observation():
    x = np.array([3.0, 1.0, 2.0])
    assert estimate(x, "cvar", {"alpha": 0.99}) == 3.0


def test_cvar_is_at_least_the_quantile_for_the_upper_tail():
    rng = np.random.default_rng(0)
    x = rng.lognormal(size=500)
    assert estimate(x, "cvar", {"alpha": 0.8}) >= estimate(x, "quantile", {"q": 0.8})


def test_estimators_ignore_non_finite_values():
    x = np.array([1.0, 2.0, np.nan, np.inf, 3.0])
    assert estimate(x, "mean") == pytest.approx(2.0)
    assert estimate(x, "count") == 3.0


def test_empty_input_gives_nan_not_an_exception():
    assert math.isnan(estimate(np.array([]), "mean"))


def test_trimmed_mean_drops_both_tails():
    x = np.array([0.0, 1.0, 2.0, 3.0, 100.0])
    assert estimate(x, "trimmed_mean", {"proportion": 0.2}) == pytest.approx(2.0)


def test_invalid_estimator_parameters_are_rejected():
    with pytest.raises(ValueError):
        build_estimator("quantile", {"q": 1.5})
    with pytest.raises(ValueError):
        build_estimator("cvar", {"alpha": 1.0})
    with pytest.raises(KeyError):
        build_estimator("not_an_estimator")


def test_definitions_render_their_parameters():
    text = definition_of("cvar", column="loss", level="event", params={"alpha": 0.95})
    assert "0.95" in text and "loss" in text and "event" in text


def test_roll_up_goes_through_the_hierarchy():
    """world-level = mean over events of mean over residents, not a pooled mean."""
    frame = pd.DataFrame(
        {
            "world_id": ["w0"] * 5,
            "event_id": ["e0", "e0", "e0", "e1", "e1"],
            "resident_id": [f"r{i}" for i in range(5)],
            "policy_id": ["p"] * 5,
            "loss": [0.0, 0.0, 30.0, 10.0, 20.0],
        }
    )
    agg = AggregationSpec()
    event = aggregate_to_level(frame, "loss", "event_id", agg, INFERENCE)
    assert sorted(event["value"].tolist()) == [10.0, 15.0]
    world = aggregate_to_level(frame, "loss", "world_id", agg, INFERENCE)
    # Two-step: (10 + 15) / 2 = 12.5. A pooled resident mean would be 12.0.
    assert world["value"].iloc[0] == pytest.approx(12.5)
    assert frame["loss"].mean() == pytest.approx(12.0)


def test_sum_rule_is_honoured_per_column():
    frame = pd.DataFrame(
        {
            "world_id": ["w0"] * 4,
            "event_id": ["e0"] * 4,
            "resident_id": [f"r{i}" for i in range(4)],
            "policy_id": ["p"] * 4,
            "resource_use": [1.0, 2.0, 3.0, 4.0],
        }
    )
    agg = AggregationSpec(by_step={"resident_id->event_id": {"resource_use": "sum"}})
    out = aggregate_to_level(frame, "resource_use", "event_id", agg, INFERENCE)
    assert out["value"].iloc[0] == 10.0


def test_resident_level_panel_keeps_every_row(records):
    out = aggregate_to_level(records, "loss", "resident_id", AggregationSpec(), INFERENCE)
    assert len(out) == len(records)
    assert {"world_id", "event_id", "resident_id", "policy_id", "value"} <= set(out.columns)


def test_filters_record_rows_and_units_removed(records):
    worlds = sorted(records["world_id"].unique())[:4]
    spec = FilterSpec(exclude={"world_id": worlds})
    out, log = apply_filters(records, spec)
    assert out["world_id"].nunique() == records["world_id"].nunique() - 4
    entry = log.filters_applied[0]
    assert entry["units_removed"] == 4
    assert entry["rows_removed"] == len(records) - len(out)


def test_named_exclusions_are_recorded(records):
    spec = FilterSpec(exclusions={"very_high_loss": "loss > 1e9"})
    out, log = apply_filters(records, spec)
    assert len(out) == len(records)
    assert log.exclusions_applied[0]["name"] == "very_high_loss"


def test_filter_on_unknown_column_raises(records):
    with pytest.raises(KeyError):
        apply_filters(records, FilterSpec(include={"nope": ["x"]}))


def test_missing_policy_drop_removes_only_missing_rows(records):
    holed = records.copy()
    holed.loc[holed.index[:10], "loss"] = np.nan
    out, info = apply_missing_policy(holed, "loss", MissingDataSpec(policy="drop_record"))
    assert len(out) == len(records) - 10
    assert info["n_missing"] == 10
    assert info["missing_by_policy"]


def test_missing_policy_fail_refuses_to_guess(records):
    holed = records.copy()
    holed.loc[holed.index[:3], "loss"] = np.nan
    with pytest.raises(ValueError, match="missing"):
        apply_missing_policy(holed, "loss", MissingDataSpec(policy="fail"))


def test_impute_worst_respects_the_metric_direction(records):
    holed = records.copy()
    holed.loc[holed.index[:3], "loss"] = np.nan
    worst, _ = apply_missing_policy(
        holed, "loss", MissingDataSpec(policy="impute_worst"), direction="lower_is_better"
    )
    best, _ = apply_missing_policy(
        holed, "loss", MissingDataSpec(policy="impute_best"), direction="lower_is_better"
    )
    assert worst["loss"].iloc[0] == records["loss"].max()
    assert best["loss"].iloc[0] == records["loss"].min()


def test_cluster_labels_and_counts(records):
    panel = aggregate_to_level(records, "loss", "event_id", AggregationSpec(), INFERENCE)
    labels = cluster_labels(panel, INFERENCE)
    assert labels.nunique() == records["world_id"].nunique()
    with pytest.raises(KeyError):
        cluster_labels(panel.drop(columns=["event_id"]), INFERENCE, "event_id")
    counts = observation_counts(records, INFERENCE)
    assert {"world_id", "policy_id", "n_event_id", "n_observations"} <= set(counts.columns)


def test_config_rejects_observation_level_resampling(config_mapping):
    bad = {**config_mapping, "inference": {"primary_unit": "resident_id"}}
    with pytest.raises(ConfigError, match="pseudoreplication"):
        config_from_mapping(bad)


def test_config_rejects_duplicate_metric_names(config_mapping):
    bad = {**config_mapping, "metrics": config_mapping["metrics"] + [config_mapping["metrics"][0]]}
    with pytest.raises(ConfigError, match="duplicate"):
        config_from_mapping(bad)


def test_config_rejects_margins_for_unknown_metrics(config_mapping):
    bad = {**config_mapping, "equivalence": {"margins": {"ghost_metric": 1.0}}}
    with pytest.raises(ConfigError, match="unknown metric"):
        config_from_mapping(bad)


def test_config_rejects_non_positive_margin(config_mapping):
    bad = {**config_mapping, "equivalence": {"margins": {"mean_loss": -1.0}}}
    with pytest.raises(ConfigError, match="positive"):
        config_from_mapping(bad)


def test_config_refuses_unit_weights(config_mapping):
    """A sampling weight and a within-unit probability are different things."""
    with pytest.raises(ConfigError, match="not a supported configuration key"):
        config_from_mapping({**config_mapping, "weights": {"w0000": 2.0}})


def test_config_rejects_more_than_one_primary_metric(config_mapping):
    metrics = [dict(m) for m in config_mapping["metrics"]]
    metrics[1]["role"] = "primary"
    with pytest.raises(ConfigError, match="more than one metric is declared primary"):
        config_from_mapping({**config_mapping, "metrics": metrics})


def test_config_rejects_a_tail_that_contradicts_the_orientation(config_mapping):
    metrics = [dict(m) for m in config_mapping["metrics"]]
    metrics[1] = {**metrics[1], "tail": "harmful", "params": {"alpha": 0.9, "tail": "lower"}}
    with pytest.raises(ConfigError, match="contradicts"):
        config_from_mapping({**config_mapping, "metrics": metrics})


def test_cvar_tail_follows_metric_orientation(config_mapping):
    cfg = config_from_mapping(config_mapping)
    lower_is_better = cfg.metric("cvar90_loss")
    assert lower_is_better.harmful_side == "upper"
    assert lower_is_better.resolved_params()["tail"] == "upper"

    flipped = [dict(m) for m in config_mapping["metrics"]]
    flipped[1] = {**flipped[1], "direction": "higher_is_better"}
    cfg2 = config_from_mapping({**config_mapping, "metrics": flipped})
    assert cfg2.metric("cvar90_loss").harmful_side == "lower"
    assert cfg2.metric("cvar90_loss").resolved_params()["tail"] == "lower"


def test_credibility_is_per_estimator_not_a_global_minimum():
    assert credibility("mean", n_units=10, n_values=30)["credible"]
    assert not credibility("cvar", n_units=10, n_values=30)["credible"]
    assert not credibility("max", n_units=25, n_values=75)["credible"]


def test_config_round_trips_through_yaml(config):
    text = config.to_yaml()
    assert "inference" in text
    margin = config.margin_for("mean_loss")
    assert margin.lower == margin.upper == 2.0
    assert config.margin_for("cvar90_loss") is None
