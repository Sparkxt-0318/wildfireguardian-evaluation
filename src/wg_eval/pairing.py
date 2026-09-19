"""Pairing policies on the clusters they share.

Two policies run on the same world share that world's difficulty, weather,
terrain and every other unmodelled nuisance.  Comparing them *within* world
removes all of it.  Comparing them across differing world sets does not, and is
the single easiest way to publish a ranking that is an artefact of which worlds
each policy happened to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from wg_eval.aggregate import cluster_labels
from wg_eval.config import MetricSpec


@dataclass
class ClusteredValues:
    """Values grouped by cluster, stored flat for fast resampling."""

    flat: np.ndarray
    starts: np.ndarray
    lengths: np.ndarray

    @classmethod
    def from_groups(cls, groups: Sequence[np.ndarray]) -> "ClusteredValues":
        lengths = np.array([len(g) for g in groups], dtype="int64")
        flat = (
            np.concatenate([np.asarray(g, dtype="float64") for g in groups])
            if len(groups)
            else np.array([], dtype="float64")
        )
        starts = np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype("int64") if len(groups) else np.array([], dtype="int64")
        return cls(flat=flat, starts=starts, lengths=lengths)

    @property
    def n_clusters(self) -> int:
        return int(len(self.lengths))

    def all_values(self) -> np.ndarray:
        return self.flat

    def take(self, cluster_indices: np.ndarray) -> np.ndarray:
        """Concatenated values of the selected clusters, with repetition."""
        lengths = self.lengths[cluster_indices]
        total = int(lengths.sum())
        if total == 0:
            return np.array([], dtype="float64")
        offsets = self.starts[cluster_indices]
        cumulative = np.concatenate([[0], np.cumsum(lengths)[:-1]])
        idx = np.repeat(offsets - cumulative, lengths) + np.arange(total)
        return self.flat[idx]

    def cluster_values(self, index: int) -> np.ndarray:
        start = int(self.starts[index])
        return self.flat[start : start + int(self.lengths[index])]


@dataclass
class PairedPanel:
    """A metric's values for several policies over a common set of clusters."""

    metric: MetricSpec
    cluster_level: str
    policies: tuple[str, ...]
    clusters: tuple[str, ...]
    values: dict[str, ClusteredValues]
    paired: bool
    #: Clusters present for some policy but not all; excluded from the paired set.
    dropped_clusters: dict[str, list[str]] = field(default_factory=dict)
    #: Per (cluster, policy) observation counts, for imbalance diagnostics.
    counts: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def n_clusters(self) -> int:
        return len(self.clusters)

    def n_observations(self, policy: str) -> int:
        return int(len(self.values[policy].flat))

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.name,
            "cluster_level": self.cluster_level,
            "paired": self.paired,
            "policies": list(self.policies),
            "n_clusters": self.n_clusters,
            "n_observations": {p: self.n_observations(p) for p in self.policies},
            "n_dropped_clusters": {p: len(v) for p, v in self.dropped_clusters.items()},
            "dropped_clusters": {p: v[:10] for p, v in self.dropped_clusters.items() if v},
            "notes": list(self.notes),
        }


def build_paired_panel(
    panel_frame: pd.DataFrame,
    metric: MetricSpec,
    policies: Iterable[str],
    *,
    cluster_level: str = "world",
    require_common_clusters: bool = True,
) -> PairedPanel:
    """Restrict a metric panel to the clusters every policy shares.

    Parameters
    ----------
    panel_frame:
        Output of :func:`wg_eval.aggregate.panel` -- one row per unit at the
        metric's level, with ``policy_id`` and ``value``.
    policies:
        The policies to align.  Order is preserved and becomes the panel order.
    cluster_level:
        The unit of inference (``"world"`` or ``"event"``).
    require_common_clusters:
        When True (the default) the panel is the intersection of each policy's
        clusters and everything dropped is recorded.  When False the policies
        keep their own cluster sets and the comparison is *unpaired*, which is
        recorded as an explicit note.
    """
    policies = tuple(str(p) for p in policies)
    if len(policies) < 2:
        raise ValueError("a paired panel needs at least two policies")

    work = panel_frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work["__cluster__"] = cluster_labels(work, cluster_level)
    work = work[np.isfinite(pd.to_numeric(work["value"], errors="coerce").to_numpy(dtype="float64", na_value=np.nan))]

    per_policy_clusters = {
        p: set(work.loc[work["policy_id"] == p, "__cluster__"].tolist()) for p in policies
    }
    missing_policies = [p for p, c in per_policy_clusters.items() if not c]
    if missing_policies:
        raise ValueError(
            f"policy/policies {missing_policies} have no usable rows for metric "
            f"{metric.name!r}; check the filters and the missing-data policy"
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
                f"{n_lost} of {len(union)} {cluster_level}(s) were not evaluated under every "
                f"policy and are excluded from the paired comparison. Excluding them is the "
                f"conservative choice; the ranking on the full, unequal sets may differ and "
                f"that difference is a property of the missing data, not of the policies."
            )
        paired = True
    else:
        keep = sorted(union)
        paired = False
        notes.append(
            "UNPAIRED (require_common_clusters=False): the policies are compared on "
            f"DIFFERENT {cluster_level} sets. Any difference confounds policy with "
            f"{cluster_level} difficulty and may reverse under pairing."
        )

    if not keep:
        raise ValueError(
            f"no {cluster_level} is shared by policies {list(policies)}; "
            "there is nothing to pair"
        )

    index = {c: i for i, c in enumerate(keep)}
    values: dict[str, ClusteredValues] = {}
    for policy in policies:
        sub = work[work["policy_id"] == policy]
        buckets: list[list[float]] = [[] for _ in keep]
        clusters = sub["__cluster__"].to_numpy()
        vals = pd.to_numeric(sub["value"], errors="coerce").to_numpy(dtype="float64")
        for cluster, value in zip(clusters, vals):
            slot = index.get(cluster)
            if slot is not None:
                buckets[slot].append(float(value))
        values[policy] = ClusteredValues.from_groups([np.asarray(b, dtype="float64") for b in buckets])

    counts = (
        work[work["__cluster__"].isin(keep)]
        .groupby(["__cluster__", "policy_id"], dropna=False, observed=True)
        .size()
        .rename("n")
        .reset_index()
        .rename(columns={"__cluster__": cluster_level})
    )

    return PairedPanel(
        metric=metric,
        cluster_level=cluster_level,
        policies=policies,
        clusters=tuple(keep),
        values=values,
        paired=paired,
        dropped_clusters={p: v for p, v in dropped.items()},
        counts=counts,
        notes=notes,
    )


def single_policy_values(
    panel_frame: pd.DataFrame,
    policy: str,
    *,
    cluster_level: str = "world",
) -> tuple[ClusteredValues, tuple[str, ...]]:
    """One policy's values grouped by cluster, for a marginal (unpaired) bootstrap.

    Marginal per-policy intervals are wider than the paired difference interval
    and overlapping ones say nothing about the difference; this exists to report
    each arm, not to compare them by eye.
    """
    work = panel_frame.copy()
    work["policy_id"] = work["policy_id"].astype("string")
    work["__cluster__"] = cluster_labels(work, cluster_level)
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
    """Within-cluster value of the estimator for each policy, one row per cluster.

    Only meaningful when each cluster holds enough observations for the
    estimator; for a world-level metric each cluster holds exactly one value and
    this is simply the paired data.
    """
    rows: dict[str, list[float]] = {p: [] for p in panel.policies}
    for i in range(panel.n_clusters):
        for policy in panel.policies:
            rows[policy].append(estimator(panel.values[policy].cluster_values(i)))
    out = pd.DataFrame(rows)
    out.insert(0, panel.cluster_level, list(panel.clusters))
    return out


def imbalance_report(panel: PairedPanel, max_imbalance: float = 0.2) -> dict[str, Any]:
    """Flag clusters where policies contributed very different observation counts.

    A paired difference computed from 3 observations under A and 300 under B is
    still *paired*, but the two sides estimate the cluster's outcome with very
    different precision, and the difference inherits the noisier one.
    """
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
