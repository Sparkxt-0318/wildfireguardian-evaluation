"""wg_eval -- an independent statistical evaluation library.

This package knows nothing about wildfires, forecasting, routing, rescue or
simulation.  It knows about *nested experiment records*: observations grouped
into events, grouped into worlds, produced under policies.

Its single purpose is to prevent misleading conclusions from such data.

The four load-bearing ideas:

1. The unit of inference is the world (or event), never the resident.
2. Policies evaluated on the same world are compared **paired**.
3. Uncertainty comes from a cluster bootstrap over the unit of inference.
4. "No significant difference" is not a conclusion; equivalence requires an
   explicitly declared practical margin.
"""

from wg_eval.version import __version__, code_version
from wg_eval.schema import (
    CORE_COLUMNS,
    ID_COLUMNS,
    OUTCOME_COLUMNS,
    ColumnSpec,
    SCHEMA,
)
from wg_eval.validate import ValidationIssue, ValidationReport, validate_records
from wg_eval.dataio import DataSource, load_records, file_checksum
from wg_eval.config import AnalysisConfig, MetricSpec, load_config
from wg_eval.aggregate import aggregate_to_level, panel
from wg_eval.metrics import METRIC_REGISTRY, estimate, register_estimator
from wg_eval.pairing import PairedPanel, build_paired_panel
from wg_eval.bootstrap import BootstrapResult, cluster_bootstrap, paired_cluster_bootstrap
from wg_eval.equivalence import EquivalenceResult, non_inferiority, tost
from wg_eval.compare import ComparisonResult, compare_policies
from wg_eval.stratify import StratifiedResult, compare_by_stratum
from wg_eval.failures import FailureSummary, summarize_failures
from wg_eval.provenance import Provenance, provenance_for

__all__ = [
    "__version__",
    "code_version",
    "CORE_COLUMNS",
    "ID_COLUMNS",
    "OUTCOME_COLUMNS",
    "ColumnSpec",
    "SCHEMA",
    "ValidationIssue",
    "ValidationReport",
    "validate_records",
    "DataSource",
    "load_records",
    "file_checksum",
    "AnalysisConfig",
    "MetricSpec",
    "load_config",
    "aggregate_to_level",
    "panel",
    "METRIC_REGISTRY",
    "estimate",
    "register_estimator",
    "PairedPanel",
    "build_paired_panel",
    "BootstrapResult",
    "cluster_bootstrap",
    "paired_cluster_bootstrap",
    "EquivalenceResult",
    "non_inferiority",
    "tost",
    "ComparisonResult",
    "compare_policies",
    "StratifiedResult",
    "compare_by_stratum",
    "FailureSummary",
    "summarize_failures",
    "Provenance",
    "provenance_for",
]
