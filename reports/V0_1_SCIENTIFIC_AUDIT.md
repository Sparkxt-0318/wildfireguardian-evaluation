# v0.1.0 scientific audit

An adversarial review of `wildfireguardian-evaluation` before freezing
`v0.1.0`, answering thirteen questions about what the library supports, what it
refuses, and what it cannot do.

The library's role, stated once:

> Given repeated experimental outcomes from policies evaluated on shared or
> partially shared experimental units, produce statistically defensible
> comparisons without allowing common simulation-analysis mistakes to
> masquerade as evidence.

No WildfireGuardian data is integrated. Everything below is exercised on
synthetic fixtures whose truth is known by construction.

---

## Defects found and corrected during this audit

Five are worth naming, because each survived the previous review and each was
found by a different mechanism.

### 1. `comparison.paired` was parsed and never read

A configuration declaring `paired: false` received a **paired** analysis,
labelled unpaired. Found by the red-team scenario for paired/unpaired mismatch:
the scenario could not make the "unpaired" analysis behave differently from the
paired one, which is what a silently-ignored flag looks like from outside.

Fixed: `comparison.paired` now selects an independent resample per arm, and the
two switches are distinguished — `require_common_units` controls *which units*,
`paired` controls *how the resample is drawn*
([`DECISIONS.md`](../docs/DECISIONS.md) D14).

**Rule adopted:** every configuration key must be read by something. A key that
is parsed and ignored is worse than an absent one.

### 2. CVaR's `k` was floating-point fragile

`(1 - 0.7) * 10` evaluates to `3.0000000000000004`, whose ceiling is 4. The
implementation averaged **one value more than its own documented definition**
for many ordinary `(alpha, n)` pairs. The code looked exactly like the
specification; review could not have caught it.

Found by a hand-computed reference test. Fixed by rounding to 9 decimal places
before the ceiling ([`DECISIONS.md`](../docs/DECISIONS.md) D16).

**Rule adopted:** production code is not its own oracle. Every estimator now has
a hand-computed case with the arithmetic written out in the docstring.

### 3. The coverage claims in the v0.1.0-pre red-team were overstated

The previous write-up reported "90% at 20 worlds, 98% at 60 worlds (nominal
95%)" from R=100 and R=50 replications. The first has a Wilson interval of
**[82.6%, 94.5%]**, which *excludes* 95% — so "approximately calibrated" was an
overstatement — and the second, from 50 replications, was far too imprecise to
support the comparison built on it.

Both numbers were correct. The claims were not. Every coverage figure now
carries its replication count, Monte Carlo standard error and Wilson interval
([`DECISIONS.md`](../docs/DECISIONS.md) D15).

### 4. The generic `stratum` column was silently added to declared strata

The validator appended the convenience `stratum` column to any explicitly
declared stratum list, and the new redundancy detector then correctly reported
it as deterministically nested inside every real stratum. True, and useless.
Now it is checked only when nothing else was declared.

### 5. The nesting direction in the redundancy detector was inverted

`stratum_redundancy` reported the coarse and fine columns the wrong way round.
Found by a test that asserted on the *direction*, not merely on the finding's
presence.

---

## The thirteen questions

### 1. What estimands does the library support?

A comparison is specified by six parts: target population of units, observed and
shared populations, per-unit aggregation, policy contrast, the estimand they
imply, and the estimator. [`ESTIMANDS.md`](../docs/ESTIMANDS.md) sets them out.

**Per-unit contrasts.** With `Delta_i = g(Y_{i,A}) - g(Y_{i,B})` for aggregation
`g`, the estimand is `theta = E[Delta_i]`, the expectation taken over **the
population of units**, not of observations.

**Pooled functionals.** For statistics that do not decompose per unit — CVaR,
quantiles, exceedance rates — the estimand is `T(F_A) - T(F_B)`, with `F_P` the
distribution of level-`L` values under policy `P`. Pairing enters through the
resampling: one draw of units, both arms evaluated on it.

**What is actually estimated when pairing restricts to shared units:**

```
theta_shared = E[ Delta_i | unit i observed under every compared policy ]
```

This is **not** `theta`, and the library never lets it be read as `theta`. Every
panel, report and provenance block carries the conditioning in words. The two
coincide when overlap is unrelated to the outcome — an assumption, checkable in
one direction, and checked (`diagnostics.overlap`).

Supported levels: any declared level at or below the resampling unit. A metric
coarser than the resampling unit is refused by name.

### 2. What independence structures does it support?

The inference structure is **declared** and **checked**:

```yaml
inference:
  primary_unit: world_id
  nested_units: [event_id, resident_id]
```

Supported:

- observations nested in sub-units nested in units (the common case);
- **units nested inside a higher-level grouping** — declare
  `primary_unit: event_id, nested_units: [world_id, resident_id]`;
- sub-unit-level inference, when sub-units are the true replicates;
- any declared chain whose containment the records confirm.

Refused: `primary_unit: resident_id` (pseudoreplication, rejected at config
load); a declared nested level that in fact contains the primary unit
(`inverted_nesting`); an undeclared id-like column that strictly contains the
primary unit (`undeclared_coarser_grouping`); two declared levels that cross
(`crossed_levels`); fewer than two replicates.

**Distinct identifiers are never taken as evidence of independence.**

Not supported: crossed random effects, and dependence that appears in no column.
The second is stated as [`ASSUMPTIONS.md`](../docs/ASSUMPTIONS.md) A1 rather
than papered over.

### 3. Which bootstrap methods are implemented?

| Method | Status | Where it should not be trusted |
|---|---|---|
| `percentile` | default | few units (under-covers); strongly biased statistics |
| `basic` | available | bounded parameters — reflection can leave the support |
| `bca` | available | degenerate leave-one-unit-out jackknife (falls back to percentile and says so) |
| two-stage (`hierarchical: true`) | available, off by default | it estimates a **different** quantity and is not comparable with a one-stage interval |
| studentized | **not implemented** | rejected by name, not silently substituted |

The methods are documented as *not interchangeable*
([`STATISTICAL_PROTOCOL.md`](../docs/STATISTICAL_PROTOCOL.md) §5), with a table
of pathological cases: skewed unit-level effects, few units, heavy tails,
zero-inflated and bounded outcomes.

The two-stage bootstrap is offered with a stated estimand rather than as a free
improvement: it covers variation in both a population of units and a population
of sub-units within them, and it widens intervals for that reason, not because
it is more careful.

### 4. What small-sample warnings exist?

Per estimator, not a universal minimum n:

| Estimator | Credible from | Why |
|---|---|---|
| `mean`, `rate`, `sum` | 5 units | unbiased at any n; the interval degrades |
| `median`, `std`, `trimmed_mean` | 8–10 units | jumps between order statistics |
| `quantile`, `cvar`, `p_exceed` | 20 units | at small n, `k` is 1 or 2 and the statistic is an extreme order statistic |
| `max`, `min` | 30 units | the bootstrap is **not consistent** for an extreme; descriptive only |

Below 20 resampling units, every interval carries a note that percentile
endpoints under-cover. `few_units` fires at validation; `single_primary_unit` is
an error.

Scenario `too_few_units` sweeps 5, 10, 20 and 50 units at a fixed 160
observations each: at 5 units the mean is credible and the CVaR and quantile are
flagged, and only the unit count moves the interval widths.

### 5. How are equivalence margins handled?

- **Required.** `tost` and `non_inferiority` raise `MarginRequired` without one.
  There is no default and there will not be.
- **Possibly asymmetric.** `{lower: 0.50, upper: 0.15}` gives the region
  `(-0.50, +0.15)`. Non-inferiority uses only the harmful side, chosen by the
  metric's orientation.
- **In the metric's own units**, or explicitly `scale: standardized` with a
  warning that the scaling constant comes from the same data.
- **Provenance recorded.** `source` is carried everywhere, and its absence is
  flagged in the result, the report and validation.
- **Part of the scientific fingerprint**, so changing a margin changes the
  identity of the result.

Scenario `asymmetric_margin` shows the same interval concluding equivalence
under a symmetric margin and failing to under an asymmetric one.

### 6. How is metric orientation handled?

`direction` is machine-readable and load-bearing. It determines which policy a
resolved interval favours, which side non-inferiority tests, which extreme
imputation uses, which side of an asymmetric margin governs, and **which tail a
tail statistic summarises**.

`harmful_side` is `upper` for `lower_is_better` and `lower` for
`higher_is_better`. `tail: harmful` resolves against it. A `params.tail` that
contradicts the declaration is **rejected at config load**, so the harmful tail
cannot be selected by accident; overriding requires saying so, and the override
appears in the report and the provenance.

Scenario `wrong_cvar_tail`: on a higher-is-better rate, the upper tail gives
−0.043 favouring policy_a, the harmful lower tail gives +0.536 favouring
policy_b, both intervals excluding zero.

### 7. How are missing units handled?

Distinguished, not merged:

| Case | Handling |
|---|---|
| Missing value in a present row | `missing_data.policy`: drop, fail, or impute worst/best |
| Unit absent for one policy | excluded from the paired set, recorded, and the estimand conditioned |
| Composition shift | the overlap check compares shared against excluded units on the metric |

`assumed_mechanism` (`MCAR` / `MAR` / `MNAR` / `unknown`) is declared by the
analyst, recorded and printed. The library cannot verify it; it checks the
observable consequence.

Scenario `outcome_dependent_missingness`: truth +1.0, complete-case +0.97 over
32 of 50 units, worst-case bound +4.17. The bounds are far apart, and that is
the finding.

### 8. How are failed runs handled?

**A failed run is a record, not an absence.** `run_status` takes `completed`,
`crashed`, `timeout`, `infeasible`, `not_evaluated` or `excluded`, and the
configuration declares what each means:

| Handling | Effect | Estimand becomes |
|---|---|---|
| `completed` | ordinary observation | unchanged |
| `failure` | kept, outcome forced to 0 | over all attempted runs |
| `excluded_documented` | removed, counted | conditional on feasibility |
| `missing` | deferred to the missing-data policy | complete-case |

The run-status ledger counts every run by policy and status **before** any
handling, so a policy cannot improve its numbers by failing to produce them.

Scenario `failed_runs_as_missing`: dropping crashes gives +0.064 favouring
policy_b; counting them as failures gives −0.159 favouring policy_a. Same
records, opposite findings. The library does not choose; it makes the choice
explicit and shows that it matters.

### 9. How is multiplicity represented?

Families are the **declared analysis roles**. Exactly one metric may be
`primary`; a second raises. The primary family is never corrected; the secondary
family takes the declared correction (`none`, `holm`, `bonferroni`).

**p-values are adjusted; confidence intervals are not.** A Holm-adjusted
interval is not a rescaling of an unadjusted one, and silently widening the
displayed intervals would misrepresent what was computed. Reports show marginal
intervals beside adjusted p-values and say so.

Scenario `uncorrected_family`: 20 pure-noise metrics with a true difference of
exactly zero; 1 resolves uncorrected, 0 survive Holm.

Correction cannot repair selection. Scenario `metric_selected_after_the_fact`:
40 noise metrics, 13 pointing the desired way, best at raw p = 0.052, against a
declared primary that resolves the other way. The defences there are structural
— one primary metric, reported first, roles labelled, `analysis_status` and a
protocol hash — and they are described as partial.

### 10. How are tail metrics defined?

```
k    = max(1, ceil((1 - alpha) * n))      # rounded to 9 dp before the ceiling
CVaR = unweighted mean of the k most extreme values in the declared tail
```

Fully specified: no interpolation (CVaR averages order statistics); ties
included by position in the sorted order; unweighted; the tail from the declared
orientation; `k >= 1` always.

Quantiles use numpy's `linear` method (position `q*(n-1)`, interpolating between
neighbours). `p_exceed` is strict.

Every convention has a hand-computed example in the registry documentation and a
matching test: for `x = 1..10` and `alpha = 0.7`, `k = 3`, upper CVaR = 9, lower
CVaR = 2; for `q = 0.9`, the quantile is 9.1.

### 11. Which red-team failures are now detected?

Sixteen scenarios, all passing both directions (the trap fires under the wrong
analysis; it does not under the right one), covering twenty documented failure
modes. The table is in
[`FAILURE_MODES.md`](../docs/FAILURE_MODES.md); the ten added by this audit are
dependence above the declared unit, outcome-dependent missingness, too few
units, the wrong tail, asymmetric margins, bounded outcomes, uncorrected
families, failed runs read as missing values, paired data analysed unpaired, and
metric selection after the fact.

Eleven mutation tests additionally verify that the suite can fail: each
introduces a deliberate statistical error and asserts something catches it. All
eleven are detected ([`MUTATION_TEST_AUDIT.md`](MUTATION_TEST_AUDIT.md)).

Two findings from building those mutants are worth keeping:

- **The pseudoreplication error is bounded.** With `sigma_I` the unit-by-policy
  interaction, `V_E` the paired sub-unit variance and `E` sub-units per unit,
  the variance ratio is `(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)`. It tends
  to `1/E` when the interaction dominates and to **1** when there is none. "The
  naive interval is always narrower" is false as stated.
- **A naive analysis can make two errors that cancel.** Ignoring clustering
  narrows the interval; ignoring pairing widens it. On a strongly paired design
  the second dominates and the doubly-wrong interval looks conservative. The two
  are therefore separate mutants.

### 12. Which statistical problems remain explicitly out of scope?

| Not supported | Why | Where |
|---|---|---|
| Weighted units | A sampling weight and an intra-unit probability cannot be told apart from a column of numbers | `ESTIMANDS.md`; `weights` raises |
| Studentized bootstrap | Needs a per-replicate variance estimate; for CVaR a nested bootstrap | rejected by name |
| Multiplicity-adjusted intervals | A Holm-adjusted interval is not a rescaling of an unadjusted one | `MULTIPLICITY.md` |
| Parametric mixed models | The bootstrap answers the same questions with fewer assumptions; valuable as a cross-check | ROADMAP R4 |
| Bayesian estimation | Mixing frameworks in one report invites choosing between them afterwards | SCOPE |
| Sequential / interim analysis | Needs a stopping rule declared before collection | ROADMAP R6 |
| Power and sample-size planning | Needs a fitted variance-components model | ROADMAP R3 |
| Crossed random effects | `crossed_levels` is an error, not a supported design | `hierarchy.py` |
| Causal adjustment for allocation imbalance | The ledger reports it; correcting needs a model of the allocation | protocol §13 |
| Any domain knowledge | The property that makes it usable as an evaluator | SCOPE; enforced by a vocabulary scan |

### 13. Which conclusions is the library forbidden from making?

Structurally, in the verdict vocabulary — `superior`, `worse`,
`statistically_equivalent`, `statistically_different_within_margin`,
`inconclusive` — and mechanically, by `audit_wording`, which is run over every
report the package can produce and makes `wg-eval report` exit non-zero:

| Forbidden | Instead |
|---|---|
| that a comparison *proves* anything | what the interval supports |
| *no difference* / *no significant difference* | `inconclusive`, "undetermined" |
| that two options *are the same* | "equivalent within the declared margin" |
| that either *is safe* | safety is a domain judgement, not an interval |
| that one is *better overall* | one metric at a time |
| that there is an *overall winner* | the disagreement between metrics is the finding |
| that a reversal is *confounding* | "aggregate and within-stratum contrasts disagree" |
| that statistical equivalence means *interchangeability* | the caveat on every equivalence finding |

---

## What this audit does not establish

- **That the library is correct.** It establishes that sixteen specific
  misleading analyses are caught, that eleven specific implementation errors are
  caught, and that the documented conventions match the code on hand-computed
  cases. A seventeenth trap may exist.
- **That a reader will act on the warnings.** Every defence here is a message.
  Whether a message is read, understood and acted on is a property of the report
  and the reader, and no test in this repository observes it.
- **That the declared assumptions hold.** A1 (units are independent), A5 (the
  declared metric is decision-relevant) and A10 (the records are faithful)
  remain undetectable and critical. The library reports its assumptions because
  it cannot check them.
- **Anything about WildfireGuardian.** No Guardian output has been analysed. The
  protocol is settled before it meets data anyone has a stake in, which is the
  intended order.

---

## Verification performed for this release

```
pytest                                          # full suite, including slow tests
python experiments/run_mutation_audit.py        # 11/11 mutants detected
python experiments/run_demonstration.py         # regenerated demonstration
python tests/fixtures/generate_fixtures.py      # fixtures reproduce byte-for-byte
wg-eval redteam                                 # 16/16 scenarios pass both directions
wg-eval validate-results ... --strict           # committed fixtures validate clean
ruff check src tests experiments --select F,E9  # clean
```

Counts and the exact test total are in [`../tasks/COMPLETED.md`](../tasks/COMPLETED.md).
