"""wg_eval -- an independent statistical evaluation library.

This package knows nothing about wildfires, forecasting, routing, rescue or
simulation.  It knows about *nested experiment records*: observations grouped
into units, produced under policies.

Its single purpose is to prevent misleading conclusions from such data.

The load-bearing ideas:

1. The unit of inference is **declared**, never guessed, and the declaration is
   checked against the records.
2. Policies evaluated on the same unit are compared **paired**, and the
   restriction to shared units is a change of estimand that is reported.
3. Uncertainty comes from a cluster bootstrap over the declared unit, and the
   interval construction is named rather than assumed interchangeable.
4. "No significant difference" is not a conclusion; equivalence requires an
   explicitly declared, possibly asymmetric practical margin, and statistical
   equivalence is not operational interchangeability.
5. Metric orientation is machine-readable, so a tail statistic summarises the
   harmful tail by construction.
"""

from wg_eval.version import __version__, REPORT_GENERATOR_VERSION, code_version
from wg_eval.schema import (
    CORE_COLUMNS,
    ID_COLUMNS,
    OUTCOME_COLUMNS,
    ColumnSpec,
    SCHEMA,
)
from wg_eval.hierarchy import (
    InferenceSpec,
    InferenceStructureError,
    check_structure,
    nesting_relation,
    require_valid_structure,
    unit_labels,
)
from wg_eval.validate import (
    ValidationIssue,
    ValidationReport,
    validate_for_config,
    validate_records,
)
from wg_eval.dataio import DataSource, load_records, file_checksum
from wg_eval.config import (
    AnalysisConfig,
    Bounds,
    ConfigError,
    Margin,
    MetricSpec,
    config_from_mapping,
    load_config,
)
from wg_eval.aggregate import aggregate_to_level, apply_run_status, panel, run_status_ledger
from wg_eval.metrics import METRIC_REGISTRY, credibility, estimate, register_estimator
from wg_eval.pairing import PairedPanel, build_paired_panel
from wg_eval.bootstrap import (
    BootstrapResult,
    cluster_bootstrap,
    naive_iid_bootstrap,
    paired_cluster_bootstrap,
)
from wg_eval.coverage import CoverageEstimate, estimate_coverage, wilson_interval
from wg_eval.equivalence import (
    EquivalenceResult,
    MarginRequired,
    classify_difference,
    non_inferiority,
    tost,
)
from wg_eval.multiplicity import adjust, bonferroni, holm
from wg_eval.compare import ComparisonResult, MetricComparison, compare_policies
from wg_eval.stratify import (
    StratifiedResult,
    allocation_ledger,
    compare_by_stratum,
    stratum_redundancy,
)
from wg_eval.failures import FailureSummary, summarize_failures
from wg_eval.provenance import Provenance, ReproducibilityManifest, provenance_for
from wg_eval.report import audit_wording, render_markdown, reproducibility_manifest, write_report

__all__ = [
    "__version__",
    "REPORT_GENERATOR_VERSION",
    "code_version",
    "CORE_COLUMNS",
    "ID_COLUMNS",
    "OUTCOME_COLUMNS",
    "ColumnSpec",
    "SCHEMA",
    "InferenceSpec",
    "InferenceStructureError",
    "check_structure",
    "nesting_relation",
    "require_valid_structure",
    "unit_labels",
    "ValidationIssue",
    "ValidationReport",
    "validate_records",
    "validate_for_config",
    "DataSource",
    "load_records",
    "file_checksum",
    "AnalysisConfig",
    "Bounds",
    "ConfigError",
    "Margin",
    "MetricSpec",
    "config_from_mapping",
    "load_config",
    "aggregate_to_level",
    "apply_run_status",
    "run_status_ledger",
    "panel",
    "METRIC_REGISTRY",
    "credibility",
    "estimate",
    "register_estimator",
    "PairedPanel",
    "build_paired_panel",
    "BootstrapResult",
    "cluster_bootstrap",
    "naive_iid_bootstrap",
    "paired_cluster_bootstrap",
    "CoverageEstimate",
    "estimate_coverage",
    "wilson_interval",
    "EquivalenceResult",
    "MarginRequired",
    "classify_difference",
    "non_inferiority",
    "tost",
    "adjust",
    "bonferroni",
    "holm",
    "ComparisonResult",
    "MetricComparison",
    "compare_policies",
    "StratifiedResult",
    "allocation_ledger",
    "compare_by_stratum",
    "stratum_redundancy",
    "FailureSummary",
    "summarize_failures",
    "Provenance",
    "ReproducibilityManifest",
    "provenance_for",
    "audit_wording",
    "render_markdown",
    "reproducibility_manifest",
    "write_report",
]
