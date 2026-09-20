# Assumptions

Each assumption states what must be true, what breaks if it is not, whether the
library can detect the violation, and what to do about it.

---

## A1 — The declared primary unit is an independent replicate

**Required.** Units are drawn from, and generalise to, a population of units.
Two different units share no unmodelled structure that affects the *contrast*.

**If violated.** Intervals are too narrow, one level up. Units generated from
the same base configuration, the same day, or the same seed family are
correlated.

**Detectable?** **Partially, and this changed in v0.1.0.** If the shared
grouping appears as a column, the library detects it: a column that strictly
contains the primary unit is an `undeclared_coarser_grouping` error, and a
declared nested level that in fact contains the primary unit is
`inverted_nesting`. If the dependence is recorded nowhere, the library cannot
see it.

Note the qualifier "that affects the contrast". A shock shared by *both*
policies within a unit cancels in a paired difference and does no harm; what
breaks a unit-level bootstrap is a group-by-policy interaction. Scenario
`dependence_above_declared_unit` measures it: 78.7% [71.4%, 94.2%] coverage at
the wrong level against 93.3% [88.2%, 96.3%] at the right one.

**What to do.** Record the grouping as a column and declare it:
`inference.primary_unit: <the grouping>`. If it cannot be recorded, state the
effective replicate count beside the result.

---

## A2 — The declared hierarchy matches the records

**Required.** Each declared nested level sits inside the one above it.

**If violated.** Cluster membership is ambiguous, or the resampling unit is the
wrong one.

**Detectable?** **Yes** — `inverted_nesting`, `crossed_levels`,
`undeclared_coarser_grouping` and `broken_nesting` are all errors. Analysis
stops, and the message names the declaration that would be correct.

**What to do.** Either make the identifiers unique within their parent
(`w0007-e03`), or declare the hierarchy the way the data actually is. A design
where one event spans many units is supported; it just has to be declared.

---

## A3 — Policy is assigned at the unit or sub-unit level, not within sub-units

**Required.** A policy applies to a whole unit (or a whole sub-unit). The
library pairs on units and assumes both policies faced the *same* unit.

**If violated.** If policies are assigned to individual observations within a
sub-unit, the pairing is at the wrong level and the observations under different
policies are not exchangeable in the way the analysis assumes.

**Detectable?** **Partially.** The library can see that policies share units;
it cannot see how assignment happened inside them.

**What to do.** For within-sub-unit assignment, declare the sub-unit as the
primary unit and pair at that level — the declared-hierarchy machinery supports
it — and check that the within-sub-unit allocation was actually randomised.

---

## A4 — Missingness is unrelated to the outcome, unless declared

**Required.** For `missing_data.policy: drop_record`, observations with missing
values are missing for reasons unrelated to what their value would have been.

**If violated.** Dropping them biases the estimate, in an unknown direction.

**Detectable?** **Partially, and more than in v0.1.0-pre.** Missing counts per
policy are recorded; missing units are always reported; the run-status ledger
counts failed runs before any handling; and the overlap check compares each
arm's values on shared versus excluded units, flagging a standardized shift
above 0.2. Whether missingness depends on the *unobserved* outcome still cannot
be seen from the data that remains.

**What to do.** Declare `assumed_mechanism` and justify it. Run `impute_worst`
and `impute_best` and report both. If the finding survives both bounds, the
missing data did not decide it; scenario `outcome_dependent_missingness` shows
bounds of +0.97 and +4.17 around a truth of +1.0, which is a case where it did.

---

## A5 — The declared metric is the decision-relevant one

**Required.** The metric compared is the one the decision depends on.

**If violated.** Every number is correct and the conclusion is still wrong. A
policy that improves mean loss while tripling catastrophic loss "wins" on the
declared metric.

**Detectable?** **No.** The library cannot know what the decision is. It
mitigates: exactly one metric may be primary and it is reported first; tail
metrics are first-class and oriented by the metric's direction; and
`detect_disagreements` refuses to let a central-versus-tail conflict pass.

**What to do.** Declare the primary metric before the analysis, and record that
you did with `analysis_status` and a protocol hash. Always declare at least one
harmful-tail metric alongside a mean. Scenarios `tail_risk_disagreement` and
`metric_selected_after_the_fact` are the demonstrations.

---

## A6 — Strata are unit-level and constant within a unit

**Required.** A unit has one value of each declared stratum, and the declared
strata carry independent information.

**If violated.** Assigning a world to a stratum becomes arbitrary and the
paired structure inside the stratum is broken.

**Detectable?** **Yes** — `stratum_varies_within_unit`, a warning, with the
modal value used. Redundancy is also detected: `identical_strata`,
`nested_strata`, `thin_stratum_cell`, `stratum_confounded_with_policy` and
`single_level_stratum`.

**What to do.** Either the stratum is genuinely unit-level, or it is a
sub-unit covariate and needs a different unit of inference. Two strata that
partition the units identically are one dimension, not two pieces of evidence.

---

## A7 — Enough units for the bootstrap

**Required.** Enough independent units for the statistic in question. The
threshold is per estimator, not universal: 5 for a mean, 20 for a CVaR or
quantile, 30 for an extreme.

**If violated.** The percentile cluster bootstrap under-covers, and tail
statistics become extreme order statistics in disguise.

**Detectable?** **Yes** — `few_units` below 20, `single_primary_unit` below 2,
and a per-estimator credibility flag with its reason on every result. The unit
count appears next to every interval.

**What to do.** More units. Not more observations per unit — the missing
variance is *between* units, and no number of observations supplies it.
Scenario `too_few_units` sweeps 5, 10, 20 and 50 units at a fixed 160
observations each and shows only the unit count moving the intervals.

---

## A8 — Records within a unit are exchangeable given the policy

**Required.** For the cluster bootstrap, observations within a unit are
exchangeable; the unit's internal structure is not itself the estimand.

**If violated.** Ordering effects, time trends or dependence *between*
sub-units in a unit are not captured by resampling whole units. The interval
remains valid for the unit-level estimand but may not mean what is intended.

**Detectable?** **No.** The library has no time or ordering column.

**What to do.** If order matters, encode it as a declared stratum or make each
ordered block its own unit.

---

## A9 — The estimator is smooth enough for the bootstrap

**Required.** The statistic is not a badly non-smooth functional of the
empirical distribution.

**If violated.** Extreme quantiles and maxima converge slowly and their
bootstrap intervals can be poorly calibrated, especially with few clusters.

**Detectable?** **Partially.** Degenerate replicates are counted and reported;
the per-estimator credibility thresholds fire below 20 units for a quantile or
CVaR and below 30 for an extreme. Slow convergence is not detectable from one
dataset.

**What to do.** Prefer CVaR to a raw extreme quantile — it averages the tail
rather than picking one point out of it. For `max` and `min` the bootstrap is
not consistent at any n; treat those intervals as descriptive and say so.

---

## A10 — The records are what the producer says they are

**Required.** The file faithfully represents the experiment that was run.

**If violated.** Everything downstream is wrong, and no statistical method
detects it.

**Detectable?** **No.** The library checks structure, not truthfulness. What it
*can* do is make tampering visible after the fact: the source checksum in every
provenance block means a result and the file it came from can always be
matched.

**What to do.** Record the checksum with the conclusion. Treat a mismatch as
invalidating the result.

---

## Summary

| # | Assumption | Detectable | Severity if violated |
|---|---|---|---|
| A1 | The declared unit is an independent replicate | Partial (error when the grouping is a column) | Critical |
| A2 | The declared hierarchy matches the records | Yes (error) | Critical |
| A3 | Policy assigned at unit or sub-unit level | Partial | Critical |
| A4 | Missingness unrelated to the outcome | Partial (overlap check, status ledger) | High |
| A5 | The declared metric is decision-relevant | No | High |
| A6 | Strata are unit-level and non-redundant | Yes (warning) | Medium |
| A7 | Enough units for the statistic | Yes (warning, per estimator) | Medium |
| A8 | Within-unit exchangeability | No | Medium |
| A9 | The estimator is smooth enough | Partial | Low |
| A10 | The records are faithful | No | Critical |
| A11 | The margin was declared before the data | Partial (source recorded, absence flagged) | High |

A1 and A4 moved from "no" to "partial" in the v0.1.0 audit, and the reasons are
in [`DECISIONS.md`](DECISIONS.md) D13 and D19. The rows that remain
undetectable-and-critical are why this library reports its assumptions rather
than only its results.

---

## A11 — The margin was declared before the data were seen

**Required.** For any equivalence or non-inferiority claim, the margin reflects
a domain judgement fixed in advance.

**If violated.** The verdict is a restatement of the interval, not a test
against an independent standard. A margin chosen after the interval is known can
always be made to produce the desired conclusion.

**Detectable?** **Partially.** `margin.source` is recorded and its absence is
flagged in the result, the report and validation; `analysis_status` and the
protocol hash make a preregistration claim checkable by someone else. Whether
the recorded justification is honest is not checkable from the file.

**What to do.** Record where the margin came from, in enough detail that a
sceptical reader can check it. "Agreed in advance" is weaker than a reference to
where.
