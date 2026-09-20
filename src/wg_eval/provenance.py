"""Provenance: what a number is, and what would have to change to change it.

A result without provenance cannot be reproduced, and a result that cannot be
reproduced cannot be checked.  Every result this library emits carries the
checksum of the bytes it came from, the declared inference structure, the
filters and exclusions applied, the aggregation rules, the metric definition,
the bootstrap seed, the confidence level and the code version.

Two fingerprints are computed, and the distinction matters:

* :meth:`Provenance.scientific_fingerprint` covers everything that could change
  a number.  Two analyses with the same scientific fingerprint must produce the
  same results; if they do not, the difference is a bug.
* :meth:`Provenance.fingerprint` additionally covers presentation -- titles,
  labels, file locations.  Rewriting a report changes this and not the former.

Keeping them apart is what stops a regenerated report from looking like changed
science, and what stops changed science from hiding behind an unchanged report.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from wg_eval.version import REPORT_GENERATOR_VERSION, code_version

#: Keys excluded from the scientific fingerprint: they describe how a result is
#: presented or where it lives, never what it is.
PRESENTATION_KEYS: frozenset[str] = frozenset(
    {"created_at", "label", "title", "path", "source_path", "description", "basename"}
)


@dataclass
class ReproducibilityManifest:
    """The five hashes needed to say whether two runs are the same run.

    Kept as separate fields rather than one blob because they fail differently:
    a changed ``source_data_hash`` means new data, a changed
    ``analysis_config_hash`` means a new question, and a changed
    ``report_generator_version`` means neither.
    """

    code_version: str = ""
    analysis_config_hash: str = ""
    source_data_hash: str = ""
    protocol_hash: str = ""
    report_generator_version: str = ""
    environment: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code_version": self.code_version,
            "analysis_config_hash": self.analysis_config_hash,
            "source_data_hash": self.source_data_hash,
            "protocol_hash": self.protocol_hash or "(none declared)",
            "report_generator_version": self.report_generator_version,
            "environment": dict(self.environment),
        }

    def to_text(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.as_dict().items() if k != "environment")


@dataclass
class Provenance:
    """The full record of how one analysis result was produced."""

    source: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    inference: dict[str, Any] = field(default_factory=dict)
    estimand: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    aggregation: dict[str, Any] = field(default_factory=dict)
    missing_data: dict[str, Any] = field(default_factory=dict)
    run_status: dict[str, Any] = field(default_factory=dict)
    metric: dict[str, Any] = field(default_factory=dict)
    bootstrap: dict[str, Any] = field(default_factory=dict)
    unit_of_inference: str = "world_id"
    confidence_level: float = 0.95
    code_version: str = field(default_factory=code_version)
    manifest: ReproducibilityManifest = field(default_factory=ReproducibilityManifest)
    environment: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        if not self.environment:
            self.environment = environment_fingerprint()
        if not self.manifest.code_version:
            self.manifest = ReproducibilityManifest(
                code_version=self.code_version,
                analysis_config_hash=str(self.config.get("checksum", "")),
                source_data_hash=str(self.source.get("checksum", "")),
                protocol_hash=str(
                    (self.config.get("resolved") or {}).get("protocol", {}).get("protocol_hash", "")
                ),
                report_generator_version=REPORT_GENERATOR_VERSION,
                environment=dict(self.environment),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "config": self.config,
            "inference": self.inference,
            "estimand": self.estimand,
            "filters": self.filters,
            "exclusions": self.exclusions,
            "aggregation": self.aggregation,
            "missing_data": self.missing_data,
            "run_status": self.run_status,
            "metric": self.metric,
            "bootstrap": self.bootstrap,
            "unit_of_inference": self.unit_of_inference,
            "confidence_level": self.confidence_level,
            "code_version": self.code_version,
            "manifest": self.manifest.as_dict(),
            "environment": self.environment,
            "created_at": self.created_at,
            **({"extra": self.extra} if self.extra else {}),
        }

    # -- fingerprints ------------------------------------------------------
    def scientific_payload(self) -> dict[str, Any]:
        """Everything that could change a number, and nothing that could not."""
        return _strip_presentation(
            {
                "source_checksum": self.source.get("checksum"),
                "source_rows": self.source.get("n_rows"),
                # NOT the raw config checksum: that hashes the whole file,
                # including its label and prose, so a retitled analysis would
                # look like a different one. The stripped resolved config below
                # carries every choice that moves a number.
                "config": self.config.get("resolved", self.config),
                "inference": self.inference,
                "estimand": self.estimand,
                "filters": self.filters,
                "exclusions": self.exclusions,
                "aggregation": self.aggregation,
                "missing_data": self.missing_data,
                "metric": self.metric,
                "bootstrap": self.bootstrap,
                "unit_of_inference": self.unit_of_inference,
                "confidence_level": self.confidence_level,
                "code_version": self.code_version,
            }
        )

    def scientific_fingerprint(self) -> str:
        """Checksum of the inputs that determine the numbers."""
        return _sha256_of(self.scientific_payload())

    def fingerprint(self) -> str:
        """Checksum of the whole provenance block except the wall-clock time."""
        payload = self.as_dict()
        payload.pop("created_at", None)
        return _sha256_of(payload)

    def to_text(self) -> str:
        lines = [f"code_version: {self.code_version}", f"created_at: {self.created_at}"]
        if self.source:
            lines.append(f"source: {self.source.get('path')}")
            lines.append(f"source_checksum: {self.source.get('checksum')}")
        if self.config:
            lines.append(f"config: {self.config.get('path')} ({self.config.get('checksum')})")
        if self.inference:
            lines.append(
                f"inference: primary_unit={self.inference.get('primary_unit')} "
                f"nested={self.inference.get('nested_units')}"
            )
        if self.estimand:
            lines.append(f"estimand: {self.estimand.get('contrast')} at "
                         f"{self.estimand.get('aggregation_level')} level over "
                         f"{self.estimand.get('unit')}")
            lines.append(f"conditioning: {self.estimand.get('conditioning')}")
        if self.metric:
            lines.append(f"metric: {self.metric.get('name')} = {self.metric.get('definition')}")
        if self.bootstrap:
            lines.append(
                f"bootstrap: {self.bootstrap.get('n_resamples')} resamples of "
                f"{self.unit_of_inference}s, seed={self.bootstrap.get('seed')}, "
                f"method={self.bootstrap.get('method')}, "
                f"hierarchical={self.bootstrap.get('hierarchical', False)}"
            )
        lines.append(f"confidence_level: {self.confidence_level}")
        lines.append(f"filters: {self.filters or 'none'}")
        lines.append(f"exclusions: {self.exclusions or 'none'}")
        lines.append("--- reproducibility manifest ---")
        lines.append(self.manifest.to_text())
        lines.append(f"scientific_fingerprint: {self.scientific_fingerprint()}")
        lines.append(f"full_fingerprint: {self.fingerprint()}")
        return "\n".join(lines)


def _strip_presentation(payload: Any) -> Any:
    """Recursively drop keys that describe presentation rather than content."""
    if isinstance(payload, dict):
        return {
            k: _strip_presentation(v)
            for k, v in payload.items()
            if k not in PRESENTATION_KEYS
        }
    if isinstance(payload, list):
        return [_strip_presentation(v) for v in payload]
    return payload


def _sha256_of(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=_stable_default).encode("utf-8")
    ).hexdigest()


def _stable_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def environment_fingerprint() -> dict[str, str]:
    """Versions of everything whose behaviour could move a decimal point."""
    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "rng": "numpy.random.Generator(PCG64)",
    }
    for module in ("numpy", "pandas", "pyarrow", "yaml"):
        try:
            mod = __import__(module)
            versions[module] = str(getattr(mod, "__version__", "unknown"))
        except ImportError:  # pragma: no cover - optional at runtime
            versions[module] = "absent"
    return versions


def text_checksum(text: str, algorithm: str = "sha256") -> str:
    """Checksum of a string, used for configs supplied in memory."""
    digest = hashlib.new(algorithm)
    digest.update(text.encode("utf-8"))
    return f"{algorithm}:{digest.hexdigest()}"


def provenance_for(
    *,
    source: Any = None,
    config: Any = None,
    metric: Any = None,
    preparation: Any = None,
    bootstrap: Any = None,
    unit_of_inference: str = "world_id",
    confidence_level: float = 0.95,
    inference: dict[str, Any] | None = None,
    estimand: dict[str, Any] | None = None,
    **extra: Any,
) -> Provenance:
    """Assemble a :class:`Provenance` from the objects an analysis already has."""
    source_d = _to_dict(source)
    config_d = _to_dict(config)
    metric_d = _to_dict(metric)
    prep_d = _to_dict(preparation)
    boot_d = _to_dict(bootstrap)

    protocol_hash = ""
    if config is not None and hasattr(config, "to_yaml"):
        protocol_hash = getattr(getattr(config, "protocol", None), "protocol_hash", "") or ""
        config_d = {
            "path": getattr(config, "source_path", None),
            "checksum": text_checksum(config.to_yaml()),
            "label": getattr(config, "label", ""),
            "resolved": config_d,
        }

    prov = Provenance(
        source=source_d,
        config=config_d,
        inference=dict(inference or {}),
        estimand=dict(estimand or {}),
        filters=list(prep_d.get("filters_applied", [])),
        exclusions=list(prep_d.get("exclusions_applied", [])),
        aggregation=dict(prep_d.get("aggregation_rules", {})),
        missing_data=dict(prep_d.get("missing_data", {})),
        run_status=dict(prep_d.get("run_status", {})),
        metric=metric_d,
        bootstrap=boot_d,
        unit_of_inference=unit_of_inference,
        confidence_level=confidence_level,
        extra=dict(extra),
    )
    if protocol_hash:
        prov.manifest = ReproducibilityManifest(
            code_version=prov.code_version,
            analysis_config_hash=str(config_d.get("checksum", "")),
            source_data_hash=str(source_d.get("checksum", "")),
            protocol_hash=protocol_hash,
            report_generator_version=REPORT_GENERATOR_VERSION,
            environment=dict(prov.environment),
        )
    return prov


def _to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "as_dict"):
        return dict(obj.as_dict())
    return {"repr": repr(obj)}
