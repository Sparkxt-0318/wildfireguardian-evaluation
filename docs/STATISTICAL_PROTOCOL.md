# Statistical protocol

This protocol is binding. Where the library and this document disagree, the
document is right and the library has a bug. Changes require a decision record
in [`DECISIONS.md`](DECISIONS.md).

Companion documents: [`ESTIMANDS.md`](ESTIMANDS.md) (what the numbers mean),
[`EQUIVALENCE_AND_NONINFERIORITY.md`](EQUIVALENCE_AND_NONINFERIORITY.md)
(section 6 in full), [`MULTIPLICITY.md`](MULTIPLICITY.md) (section 9 in full).

---

## 1. The unit of inference is declared, and then checked

The independent experimental unit is **declared** in the configuration and
**verified against the records**. It is never inferred from a column name.

```yaml
inference:
  primary_unit: world_id
  nested_units: [event_id, resident_id]
```

`primary_unit` is the replicate: the thing resampled, and the index the
estimand's expectation runs over. `nested_units` are the dependent levels
inside it, ordered coarse to fine.

Nothing assumes `world_id` sits above `event_id`. A design in which one event
spans many units declares the reverse:

```yaml
inference:
  primary_unit: event_id
  nested_units: [world_id, resident_id]
```

### What is checked, and why each check is fatal

`wg_eval.hierarchy.check_structure` compares the declaration against the data:

| Finding | Severity | Meaning |
|---|---|---|
| `inverted_nesting` | error | a declared nested level in fact *contains* the primary unit |
| `undeclared_coarser_grouping` | error | an id-like column outside the declaration strictly contains the primary unit |
| `crossed_levels` | error | two declared levels neither contain nor nest in one another |
| `single_primary_unit` | error | fewer than two replicates |
| `degenerate_level` | warning | a nested level one-to-one with the level above it |

**Distinct identifiers are not evidence of independence.** If units come in
batches and the batch column is present, resampling units is wrong and the
library says so rather than trusting the declaration.

`primary_unit: resident_id` does not exist. The config loader raises, naming the
error.

### Consequence for precision

Precision comes from *units*. Doubling observations per unit narrows the
interval only through the within-unit variance term; doubling units narrows it
by roughly √2. A study that needs more power needs more units.

The narrowing that pseudoreplication buys is bounded, and the bound is worth
stating because the folk version of this rule is too strong. With `sigma_I` the
unit-by-policy interaction, `V_E` the variance of a paired sub-unit difference
and `E` sub-units per unit, the ratio of variances is
`(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)`. It tends to `1/E` when the
interaction dominates, and to **1** when there is none: on a design where units
do not differ in how they respond to the policies, unit-level and sub-unit-level
paired bootstraps estimate the same thing.

## 2. Nesting, and what may never be a replicate

Observations within one sub-unit share everything about it; sub-units within a
unit share everything about the unit. Both correlations are usually large.

- **Resampling** draws whole primary units with replacement. Every observation
  inside a drawn unit travels with it, including into a repeated draw.
- **Aggregation** proceeds one declared step at a time.
- **Counting** never reports observations as a sample size without also
  reporting the unit count and the design effect.

## 3. Aggregation

Rules are keyed by the transition they apply to:

```yaml
aggregation:
  resident_id->event_id: {loss: mean, resource_use: sum}
  event_id->world_id:    {loss: mean}
  default_rule: mean
```

Rules: `mean`, `sum`, `median`, `max`, `min`, `any`, `all`, `first`. The legacy
spellings `resident_to_event` and `event_to_world` are accepted and translated.

The default `mean` is a *choice*: it weights every sub-unit equally regardless
of size. For an extensive quantity, `sum` is usually right at the first step.
Getting this wrong changes the estimand, not just the number — see
[`ESTIMANDS.md`](ESTIMANDS.md) §3.

A metric's `level` must be a declared level, and may not be coarser than the
resampling unit.

## 4. Paired comparison

Policies are compared on the units they share.

1. Compute the metric panel at the metric's level.
2. Intersect the unit sets of the compared policies.
3. Record every unit dropped, and from which policy.
4. Estimate on the shared set only.

**This changes the estimand**, from the mean paired difference over the target
population to the mean paired difference *among units observed under every
compared policy*. Every panel carries `estimand_conditioning` saying so, and
every report prints it beside the finding.

`comparison.require_common_units: false` disables step 2 and marks every
downstream result `UNPAIRED UNIT SETS`.

`comparison.paired: false` is a different switch: it resamples each arm
independently, which is the two-independent-samples analysis. It is honoured
rather than ignored, and it is labelled `UNPAIRED RESAMPLE`. On a paired design
it discards the design's precision — scenario `paired_data_analysed_unpaired`
measures a 7× widening.

### Representativeness of the complete case

A paired complete-case estimate is internally correct on the units it uses.
Whether it stands in for the target population is a separate question, and it is
checkable in one direction: compare each arm's values on the shared units
against its values on the units the pairing excluded. A standardized shift above
0.2 means the complete-case population is not a stand-in, and the result says so
(`diagnostics.overlap`). Passing the check is not proof; failing it is disproof.

## 5. Bootstrap design

| Parameter | Default | Note |
|---|---|---|
| resampling unit | `inference.primary_unit` | `resident_id` refused |
| `n_resamples` | 2000 | minimum 100 enforced |
| `method` | `percentile` | `basic` and `bca` also available |
| `confidence_level` | 0.95 | |
| `hierarchical` | false | two-stage; see below |
| `seed` | required in practice | an absent seed makes the result irreproducible |

### Procedure for a paired difference

For replicate *b*: draw *K* unit indices with replacement, evaluate the
estimator on the baseline arm restricted to those units, evaluate it on the
candidate arm restricted to **the same** units, record the difference. Applying
one draw to both arms is what preserves the pairing.

### The methods are not interchangeable

| Method | Assumes | Use when | Do not trust when |
|---|---|---|---|
| `percentile` | the bootstrap distribution of the statistic is a usable stand-in for its sampling distribution | default; transformation-respecting; endpoints stay inside the observed support | units are few (under-covers); the statistic is strongly biased |
| `basic` | the distribution of `theta_hat - theta` is pivotal | comparison with percentile | the parameter is bounded — reflection can put an endpoint outside the support |
| `bca` | a monotone transformation makes the statistic normal with constant bias | skewed statistics such as CVaR, if the jackknife is stable | the leave-one-unit-out jackknife is degenerate (it then falls back to percentile and says so) |

**Studentized is not implemented.** It has the best coverage in theory and needs
a variance estimate per replicate, which for CVaR means a nested bootstrap. The
method name is rejected rather than silently substituted.

### Where the selected bootstrap should not be trusted

| Situation | What goes wrong | What the library does |
|---|---|---|
| Few units | percentile endpoints are order statistics of few distinct resamples and under-cover; for tail statistics the width is not even monotone in n | `few_units` warning below 20; unit count beside every interval; per-estimator credibility flags, because width alone cannot tell you a statistic is degenerate |
| Heavy tails | extreme quantiles converge slowly | per-estimator credibility thresholds; `cvar` and `quantile` need ~20 units |
| Skewed unit-level effects | percentile and BCa disagree materially | both available; BCa records any fallback |
| Zero-inflated or bounded outcomes | reflected intervals leave the support | `bounds` declared per metric; violations reported, never clamped |
| `max` / `min` | the bootstrap is not consistent for an extreme order statistic | flagged as not credible below 30 units; documented as descriptive |

### Hierarchical (two-stage) bootstrap

`bootstrap.hierarchical: true` resamples units, then resamples the level below
each drawn unit. It is **off by default and is not a free improvement**: it
estimates the uncertainty of a quantity defined over *both* a population of
units and a population of sub-units within them, which is a different estimand
from the one-stage interval. It widens intervals, and a wider interval is not
automatically a better one. Every result produced this way carries a note
saying it is not comparable with a one-stage interval for the same statistic.
Requesting it on a design with no level below the unit raises.

### Bootstrap p-values

Reported as `2 * min(P(theta* <= 0), P(theta* >= 0))`, floored at `1/(B+1)`.
A coarse inversion of the percentile interval, not an exact test. No verdict in
this library is based on one; they exist to feed the multiplicity correction and
to be reported alongside it.

## 6. Coverage claims

Any claim that an interval is calibrated must be backed by a simulation, and
every simulation-based coverage figure must report:

```
number of Monte Carlo replications
nominal confidence level
empirical coverage
Monte Carlo standard error
Wilson confidence interval for the coverage
```

Two uncertainties are kept apart and must not be conflated:

* **Bootstrap interval coverage** — the probability that the procedure's
  nominal 95% interval contains the truth. The property under study.
* **Monte Carlo uncertainty** — how precisely a finite number of replications
  pins that probability down. The noise in the study of the property.

At `R = 100`, an empirical coverage of 0.90 has a Wilson interval of about
`[0.83, 0.95]`. Reporting "90%" alone invites a reader to over-read the gap from
95%. `wg_eval.coverage.replications_for_precision` says how many replications a
target precision needs (±2 points around 0.95 needs 457).

A coverage estimate *consistent with* nominal is the absence of evidence of
miscalibration at that number of replications, not a demonstration of
calibration.

## 7. Missing data and run status

Three different things are recorded separately, because they fail differently.

### 7a. Failed runs

A run that did not complete is **not an absent row**. The producer records it
with a `run_status`, and the configuration declares how each status is handled:

```yaml
missing_data:
  status_column: run_status
  status_handling:
    completed: completed
    crashed: failure               # counted as a failed observation
    timeout: failure
    infeasible: excluded_documented
    not_evaluated: missing
  assumed_mechanism: MNAR
  mechanism_justification: "runs fail on the hardest units"
```

| Handling | Behaviour | Effect on the estimand |
|---|---|---|
| `completed` | ordinary observation | none |
| `failure` | kept; the binary outcome is forced to 0 so the run stays in the denominator | over all attempted runs |
| `excluded_documented` | removed, with counts recorded | conditional on feasibility |
| `missing` | kept with a missing value, for the missing-data policy | complete-case |

**A policy may not improve its numbers by failing to produce them.** The
run-status ledger counts every run by policy and status *before* any row is
handled, and it appears in every report.

### 7b. Missingness mechanisms

`assumed_mechanism` is one of `MCAR`, `MAR`, `MNAR`, `unknown`. It is an
assertion by the analyst, recorded in the provenance and printed in the report.
The library cannot verify it; what it can do is check the observable
consequence — whether the excluded units differ from the shared ones (§4).

| Mechanism | Complete-case analysis is | The library's contribution |
|---|---|---|
| MCAR | unbiased | records the counts |
| MAR given observed covariates | biased unless adjusted | stratified results; allocation ledger |
| MNAR / outcome-dependent | biased, direction unknown | overlap check; worst/best-case bounds |
| Simulation crash | depends on why it crashed | status ledger; the crash is visible, not absent |
| Policy infeasibility | may itself be the outcome | `excluded_documented`, with the estimand conditioned |
| Intentional non-evaluation | a design fact, not missing data | `not_evaluated`, reported |

### 7c. Missing values within a present record

| Policy | Behaviour | When |
|---|---|---|
| `drop_record` | drop the observation | default; missingness unrelated to the outcome |
| `fail` | raise | when missingness must be explained first |
| `impute_worst` | fill with the observed extreme in the bad direction | sensitivity bound |
| `impute_best` | fill with the observed extreme in the good direction | the other bound |

**Running `impute_worst` and `impute_best` and reporting both is the honest
treatment of substantial missingness.** If the finding survives both, the
missing data did not decide it.

### 7d. Count imbalance

A unit where one policy contributed far more observations than the other is
flagged above `max_count_imbalance`: the paired difference there is estimated
with unequal precision on the two sides.

## 8. Metric orientation and tails

Every metric declares `direction: lower_is_better | higher_is_better`. This is
machine-readable and load-bearing:

| It determines | How |
|---|---|
| which policy a resolved interval favours | sign of the difference against the direction |
| which side non-inferiority tests | the harmful side |
| which extreme `impute_worst`/`impute_best` use | the harmful/beneficial extreme |
| **which tail a tail statistic summarises** | `harmful_side` = `upper` for lower-is-better, `lower` for higher-is-better |

`tail: harmful` (the default) resolves against `direction`, so a CVaR
summarises the harmful tail by construction. `tail: upper` / `tail: lower`
override it deliberately and appear in the report. A `params.tail` that
contradicts `tail` is **rejected at config load** rather than silently winning.

Orientation mistakes that are legal but almost always unintended — a
harmful-tail quantile with `q < 0.5` on a lower-is-better metric, a `max` where
the harmful extreme is the `min` — are reported as `metric_orientation`
warnings.

Scenario `wrong_cvar_tail` shows the two tails giving opposite verdicts on the
same records.

## 9. Tail-statistic definitions

For a higher-is-worse quantity `L`, the 100·alpha% CVaR is conceptually
`E[L | L >= VaR_alpha(L)]`. The finite-sample convention implemented here is:

```
    k      = max(1, ceil((1 - alpha) * n))      # (1-alpha)*n rounded to 9 dp first
    CVaR   = unweighted mean of the k most extreme values in the declared tail
```

Specifics, all checked by hand in `tests/test_reference_calculations.py`:

| Question | Answer |
|---|---|
| Interpolation | none; CVaR averages order statistics, it does not interpolate |
| Ties | included by position in the sorted order, not by value |
| Weighting | unweighted; every retained value counts once |
| Tail | from `direction` and `tail` (§8), never implicit |
| Minimum sample | `k >= 1` always; below ~20 units the statistic is flagged as not credible |
| Why not "mean of values beyond the quantile" | undefined when nothing exceeds the quantile, which happens at high alpha and small n |

**The rounding is not cosmetic.** `(1 - 0.7) * 10` evaluates to
`3.0000000000000004` in binary floating point, whose ceiling is 4: an unrounded
implementation averages one value more than its own definition states, for many
ordinary `(alpha, n)` pairs. This was a live defect found by the hand-computed
reference tests during the v0.1.0 audit.

Quantiles use numpy's `linear` method: the quantile sits at position
`q*(n-1)` in the zero-indexed sorted values, interpolating linearly between
neighbours. `p_exceed` is strict: a value equal to the threshold does not count.

## 10. Bounded outcomes

A metric may declare its support:

```yaml
    bounds: {lower: 0.0, upper: 1.0}
```

Consequences:

* values outside the support are a **validation error** — either the bound or
  the data is wrong, and both cannot be right;
* interval endpoints outside the support are **reported, never clamped**.

Clamping would narrow the interval without making the construction appropriate,
and would hide that the method does not suit a bounded outcome. Scenario
`bounded_outcome_interval` shows a basic interval reaching 1.0025 on a rate.
The honest responses are to report it, to prefer percentile endpoints, or to
work on a transformed scale — and the library does the first two and documents
the third rather than silently doing any of them.

## 11. Analysis roles and preregistration

Exactly one metric may be `role: primary`; a second raises. Reports print the
primary outcome first, then secondary, then exploratory, and label every finding
with its role.

`analysis_status` is `preregistered`, `exploratory` or `unspecified`.
**Preregistration is never inferred from the existence of a config file.**
Claiming `preregistered` requires at least one of `protocol.hash`,
`protocol.commit` or `protocol.path`, so the claim is checkable by someone else;
the loader raises otherwise.

## 12. Multiplicity

Families are the declared analysis roles; the primary family is never
corrected; the secondary family takes the declared correction. p-values are
adjusted and **intervals are not**, because a Holm-adjusted interval is not a
rescaling of an unadjusted one. See [`MULTIPLICITY.md`](MULTIPLICITY.md).

## 13. Stratification

Strata are unit-level design variables. A unit belongs to exactly one level of
each; disagreement inside a unit is warned about and resolved to the modal
value, because splitting a unit across strata would break the pairing.

Every stratified analysis produces:

1. **Per-stratum contrasts**, skipping strata with fewer than `min_units`
   *shared* units, with the reason recorded.
2. **An allocation ledger**: units per policy, shared and unique units, counts
   at every nested level, strata composition, run-status and failure-reason
   counts, and the composition shift between all observed units and the shared
   ones.
3. **Redundancy diagnostics**: identical strata, deterministic nesting, thin
   cells, levels evaluated under only one policy, single-level strata.

Two arithmetic disagreements are surfaced:

* `direction_differs_between_strata` — the contrast points different ways in
  different strata, so an aggregate describes no single stratum.
* `aggregate_contradicts_strata` — the aggregate and every resolved stratum
  disagree.

**Neither is called confounding.** A reversal is a fact about the arithmetic; it
can arise from unequal allocation, from genuine heterogeneity, or from noise in
thin strata, and this library cannot distinguish those. The wording is
"aggregate and within-stratum contrasts disagree", and the reader is pointed at
the allocation ledger.

Imbalance is **reported, not corrected**. `stratified_estimate` provides a
unit-weighted combination only when explicitly requested.

## 14. Weighted units

Not supported. A `weights` key raises; a weight-looking column is reported and
ignored. See [`ESTIMANDS.md`](ESTIMANDS.md) "Weighted units" for why the two
things called "weight" cannot be told apart from a column of numbers.

## 15. Reproducibility

Five hashes, kept separate because they move independently:

| Field | Covers | Changes when |
|---|---|---|
| `code_version` | the package source at a commit | `src/` or `pyproject.toml` differs from the commit |
| `analysis_config_hash` | the whole config file | any config edit, including its label |
| `source_data_hash` | the input bytes | the data changes |
| `protocol_hash` | the preregistered protocol | supplied by the analyst |
| `report_generator_version` | the rendering | the report's layout changes, its numbers do not |

Two fingerprints, also kept separate:

* `scientific_fingerprint` — everything that could change a number. Two results
  with the same scientific fingerprint must agree; if they do not, that is a bug.
* `fingerprint` — the whole provenance block including presentation.

Regenerating a report changes the second and not the first. Renaming an analysis
changes the second and not the first. Changing a seed, a filter, an aggregation
rule, a margin or the declared structure changes both.

`code_version` reports `+dirty` when the package source differs from the named
commit, scoped to `src/` and `pyproject.toml`: rewriting a tracked report does
not make the scientific code look changed.

## 16. What a report must contain

Enforced by `render_markdown`:

1. Source path and checksum, row count, format.
2. Declared inference structure, and that it was checked.
3. Resampling design, seed, confidence level, method, whether two-stage.
4. Whether the comparison is paired, on how many shared units, and the estimand
   conditioning.
5. Analysis status and protocol hash, or an explicit statement that none was
   asserted.
6. The run-status ledger and the declared mechanism.
7. Every filter and exclusion, with rows *and units* removed by each.
8. Findings ordered primary, secondary, exploratory, each labelled.
9. Point estimates, intervals, unit counts, margins, adjusted p-values.
10. Metric disagreements, with both intervals and no ranking.
11. Design effects, credibility flags, bounds violations.
12. Failure-mode composition and paired per-unit shifts.
13. Per-stratum results, allocation ledger, composition shift.
14. A provenance block per metric with both fingerprints and the manifest.

## 17. Wording

Generated prose may not claim that a comparison *proves* anything, that two
options *are the same* or show *no difference*, that either *is safe*, or that
one is *better overall*. `wg_eval.report.audit_wording` enforces the list over
every report the package can produce, the test suite runs it, and `wg-eval
report` exits non-zero if a generated report ever violates it.

The list is phrase-level rather than word-level on purpose: "the same units" is
a fact about the design, "the policies are the same" is an unsupported
conclusion, and a word-level ban cannot tell them apart without making reports
unreadable.

## 18. Checklist before reporting a conclusion

- [ ] `wg-eval validate-results --strict` passes, and the warnings have been read.
- [ ] The inference structure is declared and matches the design.
- [ ] At least 20 units, or the unit count is stated beside every interval.
- [ ] The comparison is paired, or the reason it is not is written down.
- [ ] The estimand is written out, including what it is conditioned on.
- [ ] The primary metric was declared before the analysis.
- [ ] Margins were declared before the analysis, with a recorded source.
- [ ] Metric orientations are correct, and tail statistics use the harmful tail.
- [ ] Every excluded unit and every failed run is accounted for.
- [ ] Tail metrics were examined, not only means.
- [ ] Metric disagreements are reported, not resolved after the fact.
- [ ] The declared family and correction are stated.
- [ ] Any coverage claim carries its Monte Carlo interval.
- [ ] The reproducibility manifest is recorded with the conclusion.
