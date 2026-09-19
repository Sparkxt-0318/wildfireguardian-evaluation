"""Provenance: what a number is, and what would have to change to change it.

A result without provenance cannot be reproduced, and a result that cannot be
reproduced cannot be checked.  Every result this library emits carries the
checksum of the bytes it came from, the filters and exclusions applied, the
aggregation rules, the metric definition, the bootstrap seed, the confidence
level and the code version.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
from dataclasses import dataclass, field
from typing import Any

from wg_eval.version import code_version


@dataclass
class Provenance:
    """The full record of how one analysis result was produced."""

    source: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    aggregation: dict[str, Any] = field(default_factory=dict)
    missing_data: dict[str, Any] = field(default_factory=dict)
    metric: dict[str, Any] = field(default_factory=dict)
    bootstrap: dict[str, Any] = field(default_factory=dict)
    unit_of_inference: str = "world"
    confidence_level: float = 0.95
    code_version: str = field(default_factory=code_version)
    environment: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        if not self.environment:
            self.environment = environment_fingerprint()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "config": self.config,
            "filters": self.filters,
            "exclusions": self.exclusions,
            "aggregation": self.aggregation,
            "missing_data": self.missing_data,
            "metric": self.metric,
            "bootstrap": self.bootstrap,
            "unit_of_inference": self.unit_of_inference,
            "confidence_level": self.confidence_level,
            "code_version": self.code_version,
            "environment": self.environment,
            "created_at": self.created_at,
            **({"extra": self.extra} if self.extra else {}),
        }

    def fingerprint(self) -> str:
        """Checksum of everything except the wall-clock timestamp.

        Two analyses with the same fingerprint must produce the same numbers;
        if they do not, the difference is in the code, not the configuration.
        """
        payload = self.as_dict()
        payload.pop("created_at", None)
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def to_text(self) -> str:
        lines = [f"code_version: {self.code_version}", f"created_at: {self.created_at}"]
        if self.source:
            lines.append(f"source: {self.source.get('path')}")
            lines.append(f"source_checksum: {self.source.get('checksum')}")
        if self.config:
            lines.append(f"config: {self.config.get('path')} ({self.config.get('checksum')})")
        if self.metric:
            lines.append(f"metric: {self.metric.get('name')} = {self.metric.get('definition')}")
        lines.append(f"unit_of_inference: {self.unit_of_inference}")
        if self.bootstrap:
            lines.append(
                f"bootstrap: {self.bootstrap.get('n_resamples')} resamples at "
                f"{self.bootstrap.get('cluster_level')} level, seed={self.bootstrap.get('seed')}, "
                f"method={self.bootstrap.get('method')}"
            )
        lines.append(f"confidence_level: {self.confidence_level}")
        lines.append(f"filters: {self.filters or 'none'}")
        lines.append(f"exclusions: {self.exclusions or 'none'}")
        lines.append(f"fingerprint: {self.fingerprint()}")
        return "\n".join(lines)


def environment_fingerprint() -> dict[str, str]:
    """Versions of everything whose behaviour could move a decimal point."""
    versions = {"python": platform.python_version(), "platform": platform.platform()}
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
    unit_of_inference: str = "world",
    confidence_level: float = 0.95,
    **extra: Any,
) -> Provenance:
    """Assemble a :class:`Provenance` from the objects an analysis already has.

    Accepts a :class:`wg_eval.dataio.DataSource`, an
    :class:`wg_eval.config.AnalysisConfig`, a
    :class:`wg_eval.config.MetricSpec`, a
    :class:`wg_eval.aggregate.PreparationLog` and a ``BootstrapSpec`` -- or
    plain dicts standing in for any of them.
    """
    source_d = _to_dict(source)
    config_d = _to_dict(config)
    metric_d = _to_dict(metric)
    prep_d = _to_dict(preparation)
    boot_d = _to_dict(bootstrap)

    if config_d and config is not None and hasattr(config, "to_yaml"):
        config_d = {
            "path": getattr(config, "source_path", None),
            "checksum": text_checksum(config.to_yaml()),
            "label": getattr(config, "label", ""),
            "resolved": config_d,
        }

    return Provenance(
        source=source_d,
        config=config_d,
        filters=list(prep_d.get("filters_applied", [])),
        exclusions=list(prep_d.get("exclusions_applied", [])),
        aggregation=dict(prep_d.get("aggregation_rules", {})),
        missing_data=dict(prep_d.get("missing_data", {})),
        metric=metric_d,
        bootstrap=boot_d,
        unit_of_inference=unit_of_inference,
        confidence_level=confidence_level,
        extra=dict(extra),
    )


def _to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "as_dict"):
        return dict(obj.as_dict())
    return {"repr": repr(obj)}
