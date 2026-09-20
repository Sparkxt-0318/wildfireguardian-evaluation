"""Pairing policies on the experimental units they share.

Two policies run on the same unit share that unit's difficulty and every other
unmodelled nuisance.  Comparing them *within* unit removes all of it.  Comparing
them across differing unit sets does not, and is the easiest way to publish a
ranking that is an artefact of which units each policy happened to see.

The restriction to shared units changes the estimand, from the mean paired
difference over the target population of units to the mean paired difference
*among units observed under every compared policy*.  That conditioning is
carried on the panel and reported; see ``docs/ESTIMANDS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from wg_eval.config import MetricSpec
from wg_eval.hierarchy import InferenceSpec, unit_labels


@dataclass
class ClusteredValues:
    """Values grouped by resampling unit, stored flat for fast resampling.

    When ``sub_starts`` is present the unit is further divided into the level
    below it, which is what a two-stage (hierarchical) bootstrap resamples.
    """

    flat: np.ndarray
    starts: np.ndarray
    lengths: np.ndarray
    sub_starts: list[np.ndarray] | None = None
    sub_lengths: list[np.ndarray] | None = None

    @classmethod
    def from_groups(
        cls,
        groups: Sequence[np.ndarray],
        subgroups: Sequence[Sequence[np.ndarray]] | None = None,
    ) -> "ClusteredValues":
        lengths = np.array([len(g) for g in groups], dtype="int64")
        flat = (
            np.concatenate([np.asarray(g, dtype="float64") for g in groups])
            if len(groups)
            else np.array([], dtype="float64")
        )
        starts = (
            np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype("int64")
            if len(groups)
            else np.array([], dtype="int64")
        )
        sub_starts = sub_lengths = None
        if subgroups is not None:
            sub_starts, sub_lengths = [], []
            for unit_index, parts in enumerate(subgroups):
                part_lengths = np.array([len(p) for p in parts], dtype="int64")
                offsets = int(starts[unit_index]) + np.concatenate(
                    [[0], np.cumsum(part_lengths)[:-1]]
                ).astype("int64") if len(parts) else np.array([], dtype="int64")
                sub_starts.append(offsets)
                sub_lengths.append(part_lengths)
        return cls(flat=flat, starts=starts, lengths=lengths,
                   sub_starts=sub_starts, sub_lengths=sub_lengths)

    @property
    def n_clusters(self) -> int:
        return int(len(self.lengths))

    @property
    def has_substructure(self) -> bool:
        return self.sub_starts is not None and any(len(s) > 1 for s in self.sub_starts)

    def all_values(self) -> np.ndarray:
        return self.flat

    def take(self, cluster_indices: np.ndarray) -> np.ndarray:
        """Concatenated values of the selected units, with repetition."""
        return self._gather(self.starts[cluster_indices], self.lengths[cluster_indices])

    def take_hierarchical(self, cluster_indices: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Resample units, then resample the level below within each drawn unit."""
        if self.sub_starts is None or self.sub_lengths is None:
            return self.take(cluster_indices)
        offsets: list[np.ndarray] = []
        lengths: list[np.ndarray] = []
        for i in cluster_indices:
            starts_i, lengths_i = self.sub_starts[i], self.sub_lengths[i]
            n_sub = len(starts_i)
            if n_sub == 0:
                continue
            pick = rng.integers(0, n_sub, size=n_sub)
            offsets.append(starts_i[pick])
            lengths.append(lengths_i[pick])
        if not offsets:
            return np.array([], dtype="float64")
        return self._gather(np.concatenate(offsets), np.concatenate(lengths))

    def _gather(self, offsets: np.ndarray, lengths: np.ndarray) -> np.ndarray:
        total = int(lengths.sum())
        if total == 0:
            return np.array([], dtype="float64")
        cumulative = np.concatenate([[0], np.cumsum(lengths)[:-1]])
        idx = np.repeat(offsets - cumulative, lengths) + np.arange(total)
        return self.flat[idx]

    def cluster_values(self, index: int) -> np.ndarray:
        start = int(self.starts[index])
        return self.flat[start : start + int(self.lengths[index])]


@dataclass
class PairedPanel:
    """A metric's values for several policies over a common set of units."""

    metric: MetricSpec
    inference: InferenceSpec
    policies: tuple[str, ...]
    clusters: tuple[str, ...]
    values: dict[str, ClusteredValues]
    paired: bool
    #: Units present for some policy but not all; excluded from the paired set.
    dropped_clusters: dict[str, list[str]] = field(default_factory=dict)
    #: Every unit seen for any compared policy, before the intersection.
    union_clusters: tuple[str, ...] = ()
    counts: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def cluster_level(self) -> str:
        return self.inference.primary_unit

    @property
    def n_clusters(self) -> int:
        return len(self.clusters)

    @property
    def n_union_clusters(self) -> int:
        return len(self.union_clusters) or len(self.clusters)

    @property
    def coverage_fraction(self) -> float:
        """Share of observed units that survive the pairing restriction."""
        total = self.n_union_clusters
        return float(self.n_clusters / total) if total else float("nan")

    @property
    def estimand_conditioning(self) -> str:
        """What population the paired estimand actually refers to."""
        if not self.paired:
            return "unpaired: each policy on its own unit set (policy is confounded with unit)"
        if self.n_clusters == self.n_union_clusters:
            return f"all {self.n_clusters} observed {self.cluster_level}s carry every policy"
        return (
            f"{self.cluster_level}s observed under every compared policy "
            f"({self.n_clusters} of {self.n_union_clusters})"
        )

    def n_observations(self, policy: str) -> int:
        return int(len(self.values[policy].flat))

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.name,
            "cluster_level": self.cluster_level,
            "inference": self.inference.as_dict(),
            "paired": self.paired,
            "policies": list(self.policies),
            "n_clusters": self.n_clusters,
            "n_union_clusters": self.n_union_clusters,
            "coverage_fraction": self.coverage_fraction,
            "estimand_conditioning": self.estimand_conditioning,
            "n_observations": {p: self.n_observations(p) for p in self.policies},
            "n_dropped_clusters": {p: len(v) for p, v in self.dropped_clusters.items()},
            "dropped_clusters": {p: v[:10] for p, v in self.dropped_clusters.items() if v},
            "notes": list(self.notes),
        }


def build_paired_panel(
    panel_frame: pd.DataFrame,
    metric: MetricSpec,
    policies: Iterable[str],
    inference: InferenceSpec,
    *,
    require_common_clusters: bool = True,
    substructure: bool = False,
) -> PairedPanel:
    """Restrict a metric panel to the units every policy shares.

    Parameters
    ----------
    panel_frame:
        Output of :func:`wg_eval.aggregate.panel` -- one row per unit at the
        metric's level, with ``policy_id`` and ``value``.
    policies:
        The policies to align.  Order is preserved and becomes the panel order.
    inference:
        The declared structure; its ``primary_unit`` is the resampling unit.
    require_common_clusters:
        When True (the default) the panel is the intersection of each policy's
        units and everything dropped is recorded, which conditions the estimand
        on being observed under every policy.  When False the policies keep
        their own unit sets and the comparison is *unpaired*.
    substructure:
        Also group each unit by the level below it, so a two-stage bootstrap is
        possible.
    """
    policies = tuple(str(p) for p in policies)
    if len(policies) < 2:
        raise ValueError("a paired panel needs at least two policies")

    unit = inference.primary_unit
    work = panel_frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work["__cluster__"] = unit_labels(work, inference, unit)
    sub_level = _sub_level(inference, metric)
    if substructure and sub_level is not None:
        work["__sub__"] = unit_labels(work, inference, sub_level)
    numeric = pd.to_numeric(work["value"], errors="coerce")
    work = work[np.isfinite(numeric.to_numpy(dtype="float64", na_value=np.nan))]

    per_policy_clusters = {
        p: set(work.loc[work["policy_id"] == p, "__cluster__"].tolist()) for p in policies
    }
    empty = [p for p, c in per_policy_clusters.items() if not c]
    if empty:
        raise ValueError(
            f"policy/policies {empty} have no usable rows for metric {metric.name!r}; "
            "check the filters, the run-status handling and the missing-data policy"
        )

    union = set().union(*per_policy_clusters.values())
    intersection = set.intersection(*per_policy_clusters.values())
    dropped = {p: sorted(union - per_policy_clusters[p]) for p in policies}

    notes: list[str] = []
    if require_common_clusters:
        keep = sorted(intersection)
        n_lost = len(union) - len(intersection)
        if n_lost:
            notes.append(
                f"{n_lost} of {len(union)} {unit}s were not evaluated under every policy and "
                f"are excluded from the paired comparison. The estimand is therefore the mean "
                f"paired difference among {unit}s observed under every compared policy, which "
                f"equals the target-population estimand only if overlap is unrelated to the "
                f"outcome."
            )
        paired = True
    else:
        keep = sorted(union)
        paired = False
        notes.append(
            f"UNPAIRED (require_common_units=False): the policies are compared on DIFFERENT "
            f"{unit} sets. Any difference confounds policy with {unit} difficulty and may "
            f"reverse under pairing."
        )

    if not keep:
        raise ValueError(f"no {unit} is shared by policies {list(policies)}; there is nothing to pair")

    index = {c: i for i, c in enumerate(keep)}
    values: dict[str, ClusteredValues] = {}
    for policy in policies:
        sub = work[work["policy_id"] == policy]
        buckets: list[list[float]] = [[] for _ in keep]
        sub_keys: list[list[str]] = [[] for _ in keep]
        clusters = sub["__cluster__"].to_numpy()
        vals = pd.to_numeric(sub["value"], errors="coerce").to_numpy(dtype="float64")
        subs = sub["__sub__"].to_numpy() if "__sub__" in sub.columns else None
        for row, (cluster, value) in enumerate(zip(clusters, vals)):
            slot = index.get(cluster)
            if slot is None:
                continue
            buckets[slot].append(float(value))
            if subs is not None:
                sub_keys[slot].append(str(subs[row]))
        groups = [np.asarray(b, dtype="float64") for b in buckets]
        subgroups = None
        if subs is not None:
            subgroups = [
                _split_by_key(np.asarray(b, dtype="float64"), keys)
                for b, keys in zip(buckets, sub_keys)
            ]
        values[policy] = ClusteredValues.from_groups(groups, subgroups)

    counts = (
        work[work["__cluster__"].isin(keep)]
        .groupby(["__cluster__", "policy_id"], dropna=False, observed=True)
        .size()
        .rename("n")
        .reset_index()
        .rename(columns={"__cluster__": unit})
    )

    return PairedPanel(
        metric=metric,
        inference=inference,
        policies=policies,
        clusters=tuple(keep),
        values=values,
        paired=paired,
        dropped_clusters=dropped,
        union_clusters=tuple(sorted(union)),
        counts=counts,
        notes=notes,
    )


def _sub_level(inference: InferenceSpec, metric: MetricSpec) -> str | None:
    """The level immediately below the resampling unit, if the metric reaches it."""
    primary_index = inference.index_of(inference.primary_unit)
    metric_index = inference.index_of(metric.level)
    if metric_index <= primary_index + 0:
        return None
    return inference.levels[primary_index + 1]


def _split_by_key(values: np.ndarray, keys: list[str]) -> list[np.ndarray]:
    """Split a unit's values into the sub-groups named by ``keys``."""
    if not keys:
        return [values] if values.size else []
    order: dict[str, list[float]] = {}
    for key, value in zip(keys, values):
        order.setdefault(key, []).append(float(value))
    return [np.asarray(v, dtype="float64") for v in order.values()]


def single_policy_values(
    panel_frame: pd.DataFrame,
    policy: str,
    inference: InferenceSpec,
) -> tuple[ClusteredValues, tuple[str, ...]]:
    """One policy's values grouped by unit, for a marginal (unpaired) bootstrap.

    Marginal per-policy intervals are wider than the paired difference interval
    and overlapping ones say nothing about the difference; this exists to report
    each arm, not to compare them by eye.
    """
    unit = inference.primary_unit
    work = panel_frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work["__cluster__"] = unit_labels(work, inference, unit)
    work = work[work["policy_id"] == str(policy)]
    values = pd.to_numeric(work["value"], errors="coerce")
    work = work[np.isfinite(values.to_numpy(dtype="float64", na_value=np.nan))]
    if work.empty:
        raise ValueError(f"policy {policy!r} has no usable values in this panel")
    clusters = sorted(set(work["__cluster__"].tolist()))
    grouped = {
        str(k): pd.to_numeric(v["value"], errors="coerce").to_numpy(dtype="float64")
        for k, v in work.groupby("__cluster__", observed=True)
    }
    return ClusteredValues.from_groups([grouped[c] for c in clusters]), tuple(clusters)


def per_cluster_statistics(panel: PairedPanel, estimator) -> pd.DataFrame:
    """Within-unit value of the estimator for each policy, one row per unit."""
    rows: dict[str, list[float]] = {p: [] for p in panel.policies}
    for i in range(panel.n_clusters):
        for policy in panel.policies:
            rows[policy].append(estimator(panel.values[policy].cluster_values(i)))
    out = pd.DataFrame(rows)
    out.insert(0, panel.cluster_level, list(panel.clusters))
    return out


def imbalance_report(panel: PairedPanel, max_imbalance: float = 0.2) -> dict[str, Any]:
    """Flag units where policies contributed very different observation counts."""
    if panel.counts is None or panel.counts.empty:
        return {"checked": False}
    wide = panel.counts.pivot_table(
        index=panel.cluster_level, columns="policy_id", values="n", aggfunc="sum"
    ).fillna(0)
    present = [p for p in panel.policies if p in wide.columns]
    if len(present) < 2:
        return {"checked": False}
    hi = wide[present].max(axis=1)
    lo = wide[present].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(hi > 0, (hi - lo) / hi, 0.0)
    flagged = wide.index[rel > max_imbalance].astype(str).tolist()
    return {
        "checked": True,
        "max_relative_imbalance": float(np.nanmax(rel)) if len(rel) else 0.0,
        "threshold": max_imbalance,
        "n_flagged_clusters": len(flagged),
        "flagged_clusters": flagged[:10],
    }
