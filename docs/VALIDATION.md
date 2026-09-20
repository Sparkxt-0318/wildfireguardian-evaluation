# Validation

```bash
wg-eval validate-results results.parquet --config analysis.yaml
wg-eval validate-results results.parquet --json
wg-eval validate-results results.parquet --strict     # warnings fail too
```

Exit code 0 when nothing blocks analysis, 1 when something does (or, with
`--strict`, when anything at all was found).

Severity follows one rule, from [`DECISIONS.md`](DECISIONS.md) D11:

> **If the mistake makes the output look *better* than the truth warrants,
> refuse. If it makes it noisier or harder to interpret, warn.**

---

## Errors — analysis stops

| Code | Trigger | Why it is fatal |
|---|---|---|
| `missing_required_columns` | a required identifier is absent | the nesting cannot be established |
| `empty_table` | no rows | nothing to analyse |
| `null_key` | a null in any identifier | the row cannot be placed in the hierarchy |
| `duplicate_records` | two rows share `(world, event, policy, resident)` | duplicated observations inflate the apparent sample size |
| `broken_nesting` | an `event_id` under more than one `world_id` while the default hierarchy is declared | either the ids are wrong or the hierarchy is the other way round |
| `non_numeric_column` | a numeric column that will not coerce | a silent `nan` column would produce quiet nonsense |
| `non_binary_outcome` | a boolean column with values outside {0, 1} | a "rate" would not be a rate |
| `non_finite_value` | `inf` in a numeric column | every downstream statistic becomes `inf` or `nan` |
| `no_common_units` | no unit carries every policy | a paired comparison is impossible |
| `missing_stratum_column` | a declared stratum is not in the table | the config and the data disagree |
| `outcome_used_as_stratum` | a declared stratum is an outcome or an identifier | subsetting on something the policy influenced selects on the result |
| `undeclared_unit_column_missing` | a declared inference level is not a column | the structure cannot be checked at all |
| `inverted_nesting` | a declared nested level in fact contains the primary unit | resampling would break up a dependent group |
| `undeclared_coarser_grouping` | an id-like column strictly contains the primary unit | the units inside one group are not replicates |
| `crossed_levels` | two declared levels neither nest nor contain | there is no hierarchy to resample |
| `single_primary_unit` | fewer than two replicates | no between-unit variation to estimate |
| `value_outside_declared_bounds` | a value outside a metric's declared support | either the bound or the data is wrong |

## Warnings — analysis proceeds, loudly

| Code | Trigger | What it means |
|---|---|---|
| `few_units` | fewer than 20 units | intervals under-cover; the count is attached to the warning |
| `unbalanced_policy_coverage` | policies ran different unit sets | the paired analysis excludes units, which conditions the estimand |
| `degenerate_level` | a nested level one-to-one with the one above it | the level adds no structure and buys no replicates |
| `incomplete_runs` | rows whose run did not complete | a failed run may be an outcome; declare its handling |
| `unknown_run_status` | a status outside the known vocabulary | it defaults to `missing` unless declared |
| `metric_orientation` | a tail statistic pointing at the beneficial end | legal, and almost always unintended |
| `weight_column_ignored` | a column that looks like a sampling weight | weights are not applied; see docs/ESTIMANDS.md |
| `identical_strata` | two strata partition the units the same way | one dimension under two names |
| `nested_strata` | one stratum is a deterministic refinement of another | the two do not give independent evidence |
| `thin_stratum_cell` | a stratum level with fewer than 5 units | a contrast inside it is not supportable |
| `stratum_confounded_with_policy` | a stratum level ran under one policy only | no contrast exists inside that level |
| `single_level_stratum` | a stratum with one value | it partitions nothing |
| `undocumented_margin` | an equivalence margin with no recorded source | a margin justified after the fact is not predeclared |
| `no_primary_metric` | no metric declared primary | the headline becomes whichever metric is noticed first |
| `missing_outcome` | nulls in a numeric column | the missing-data policy will decide their fate |
| `implausible_value` | outside a column's plausible range (e.g. negative loss) | possible unit or sign error |
| `failure_without_reason` | `mission_success == 0` with no reason | bucketed as `unspecified`, never dropped |
| `success_with_failure_reason` | `mission_success == 1` with a reason | the two columns disagree |
| `stratum_varies_within_unit` | a unit's rows disagree on a stratum | modal value used; pairing would break otherwise |
| `null_stratum` | nulls in a stratum column | those units form an explicit `__missing__` stratum |
| `single_policy` | fewer than two policies | no comparison is possible |

## Info — always present

| Code | Content |
|---|---|
| `nesting_ratio` | observations per unit, and the factor by which treating them as independent would overstate the sample size |
| `run_status_present` | the run-status breakdown, before anything is handled |
| `extra_columns` | columns outside the core schema, carried through |
| `absent_optional_columns` | optional schema columns not supplied |
| `analysis_status_unspecified` | the analysis does not claim preregistration, and a config file is not evidence that it was |

`nesting_ratio` is emitted unconditionally, even on clean data. Being told
"2,400 observations in 20 units — 120 per unit" before reading any result is the
cheapest available protection against the error this library exists to prevent.

---

## The summary block

```
validation: PASS (0 errors, 0 warnings)
  n_rows: 7200
  primary_unit: world_id
  hierarchy: world_id > event_id > resident_id
  n_units: 60
  n_policies: 2
  policies: ['policy_a', 'policy_b']
  n_common_units: 60
  observations_per_unit: 120.0
```

`hierarchy` is the structure the configuration declared *and that the records
were checked against*. `n_common_units` is the number that matters for a paired
comparison; when it is below `n_units`, `unbalanced_policy_coverage` says how
many are missing and from which policy.

---

## What validation cannot check

Validation is structural. It cannot see:

- whether the declared units are genuinely independent replicates, when the
  dependence is not recorded in any column ([`ASSUMPTIONS.md`](ASSUMPTIONS.md) A1);
- whether missingness depends on the unobserved outcome (A4);
- whether the declared metric is the decision-relevant one (A5);
- whether the records faithfully represent the experiment (A10).

A passing validation means the table is *analysable*, not that the analysis
will be *meaningful*. The protocol checklist
([`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) §11) covers the rest.

---

## Using it in a pipeline

```bash
wg-eval validate-results results.parquet --config analysis.yaml --strict || exit 1
wg-eval report results.parquet analysis.yaml --out reports/
```

`--strict` is the right setting for an automated gate: a `few_clusters` or
`unbalanced_policy_coverage` warning is exactly the kind of thing that gets
scrolled past in a log and turns into a published ranking.

For a programmatic check:

```python
from wg_eval import load_config, load_records, validate_for_config

frame, source = load_records("results.parquet")
config = load_config("analysis.yaml")

# validate_for_config checks everything the analysis declares: the inference
# structure, metric bounds and orientations, margins, strata and roles.
report = validate_for_config(frame, config)
report.raise_if_errors()
for warning in report.warnings:
    log.warning("%s: %s", warning.code, warning.message)
```

`validate_records` is the lower-level entry point for a table with no config.
It still checks the inference structure: when none is declared it assumes the
default and checks *that*, so an undeclared coarser grouping is an error either
way.
