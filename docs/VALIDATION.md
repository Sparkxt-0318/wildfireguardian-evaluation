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
| `broken_nesting` | an `event_id` under more than one `world_id` | cluster membership is ambiguous |
| `non_numeric_column` | a numeric column that will not coerce | a silent `nan` column would produce quiet nonsense |
| `non_binary_outcome` | a boolean column with values outside {0, 1} | a "rate" would not be a rate |
| `non_finite_value` | `inf` in a numeric column | every downstream statistic becomes `inf` or `nan` |
| `single_cluster` | fewer than two clusters | no between-cluster variation, so no inference |
| `no_common_worlds` | no world carries every policy | a paired comparison is impossible |
| `missing_stratum_column` | a declared stratum is not in the table | the config and the data disagree |
| `outcome_used_as_stratum` | a declared stratum is an outcome or an identifier | subsetting on something the policy influenced selects on the result |

## Warnings — analysis proceeds, loudly

| Code | Trigger | What it means |
|---|---|---|
| `few_clusters` | fewer than 20 clusters | intervals under-cover; the count is attached to the warning |
| `unbalanced_policy_coverage` | policies ran different world sets | the paired analysis will exclude worlds; the count is given |
| `missing_outcome` | nulls in a numeric column | the missing-data policy will decide their fate |
| `implausible_value` | outside a column's plausible range (e.g. negative loss) | possible unit or sign error |
| `failure_without_reason` | `mission_success == 0` with no reason | bucketed as `unspecified`, never dropped |
| `success_with_failure_reason` | `mission_success == 1` with a reason | the two columns disagree |
| `stratum_varies_within_world` | a world's rows disagree on a stratum | modal value used; pairing would break otherwise |
| `null_stratum` | nulls in a stratum column | those worlds form an explicit `__missing__` stratum |
| `single_policy` | fewer than two policies | no comparison is possible |

## Info — always present

| Code | Content |
|---|---|
| `nesting_ratio` | observations per world, and the factor by which treating them as independent would overstate the sample size |
| `extra_columns` | columns outside the core schema, carried through |
| `absent_optional_columns` | optional schema columns not supplied |

`nesting_ratio` is emitted unconditionally, even on clean data. Being told
"2,400 observations in 20 worlds — 120 per world" before reading any result is
the cheapest available protection against the error this library exists to
prevent.

---

## The summary block

```
validation: PASS (0 errors, 0 warnings)
  n_rows: 7200
  n_worlds: 60
  n_events: 240
  n_policies: 2
  policies: ['policy_a', 'policy_b']
  n_common_worlds: 60
  observations_per_world: 120.0
```

`n_common_worlds` is the number that matters for a paired comparison. When it
is below `n_worlds`, some policy is missing from some world, and
`unbalanced_policy_coverage` will say how many.

---

## What validation cannot check

Validation is structural. It cannot see:

- whether worlds are genuinely independent replicates ([`ASSUMPTIONS.md`](ASSUMPTIONS.md) A1);
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
from wg_eval import load_records, validate_records

frame, source = load_records("results.parquet")
report = validate_records(frame, strata=["landscape", "mobility"])
report.raise_if_errors()
for warning in report.warnings:
    log.warning("%s: %s", warning.code, warning.message)
```
