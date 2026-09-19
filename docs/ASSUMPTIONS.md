# Assumptions

Each assumption states what must be true, what breaks if it is not, whether the
library can detect the violation, and what to do about it.

---

## A1 — Worlds are independent replicates

**Required.** Worlds are drawn from, and generalise to, a population of worlds.
Two different worlds share no unmodelled structure.

**If violated.** Intervals are too narrow again, one level up. Worlds generated
from the same base map, the same weather day, or the same random seed family
are correlated, and the cluster bootstrap will not see it.

**Detectable?** **No.** The library sees identifiers, not provenance. It cannot
know that `w0001` and `w0002` came from the same underlying map.

**What to do.** Make the true replicate the `world_id`. If ten worlds are
variations on one base map, either give all ten the same `world_id` and treat
the variations as events, or accept that the effective replicate count is ten
times smaller than it appears and say so beside the result.

---

## A2 — Events are nested in exactly one world

**Required.** An `event_id` belongs to one `world_id`.

**If violated.** The hierarchy is undefined and cluster membership is
ambiguous.

**Detectable?** **Yes** — `broken_nesting`, an error. Analysis stops.

**What to do.** Make event identifiers unique across worlds, e.g. `w0007-e03`.

---

## A3 — Policy is assigned at the world or event level, not within events

**Required.** A policy applies to a whole world (or a whole event). The library
pairs on worlds and assumes both policies faced the *same* world.

**If violated.** If policies are assigned to individual residents within an
event, the pairing is at the wrong level and the residents under different
policies are not exchangeable in the way the analysis assumes.

**Detectable?** **Partially.** The library can see that policies share worlds;
it cannot see how assignment happened inside them.

**What to do.** For within-event assignment, treat the event as the unit of
inference and pair at that level — and check that the within-event allocation
was actually randomised. This design is not otherwise supported today.

---

## A4 — Missingness is unrelated to the outcome, unless declared

**Required.** For `missing_data.policy: drop_record`, observations with missing
values are missing for reasons unrelated to what their value would have been.

**If violated.** Dropping them biases the estimate, in an unknown direction.

**Detectable?** **Partially.** Missing counts per policy are recorded and
warned about, and *missing clusters* are always reported. Whether missingness
depends on the unobserved outcome cannot be seen from the data that remains.

**What to do.** Run `impute_worst` and `impute_best` and report both. If the
verdict survives both bounds, the missing data did not decide it. Scenario
`missing_worlds_reverse_ranking` shows what happens when it does.

---

## A5 — The declared metric is the decision-relevant one

**Required.** The metric compared is the one the decision depends on.

**If violated.** Every number is correct and the conclusion is still wrong. A
policy that improves mean loss while tripling catastrophic loss "wins" on the
declared metric.

**Detectable?** **No.** The library cannot know what the decision is. It
mitigates: tail metrics are first-class, and `detect_disagreements` refuses to
let a central-vs-tail conflict pass silently.

**What to do.** Declare the primary metric before the analysis. Always declare
at least one tail metric alongside a mean. Scenario `tail_risk_disagreement` is
the demonstration.

---

## A6 — Strata are world-level and constant within a world

**Required.** A world has one value of each declared stratum.

**If violated.** Assigning a world to a stratum becomes arbitrary and the
paired structure inside the stratum is broken.

**Detectable?** **Yes** — `stratum_varies_within_world`, a warning. The modal
value is used.

**What to do.** Either the stratum is genuinely world-level, or it is an
event-level covariate and needs the event as the unit of inference.

---

## A7 — Enough clusters for the bootstrap

**Required.** Roughly 20 or more clusters for an interval to be trusted at
face value.

**If violated.** The percentile cluster bootstrap under-covers. Measured on the
demonstration design: ~90% actual coverage for a nominal 95% interval at 20
worlds, ~98% at 60.

**Detectable?** **Yes** — `few_clusters` warning below 20, `single_cluster`
error below 2. The cluster count appears next to every interval.

**What to do.** More worlds. Not more residents — the missing variance is
*between* worlds, and no number of residents supplies it. If more worlds are
impossible, report the cluster count next to every interval and treat the
result as provisional.

---

## A8 — Records within a world are exchangeable given the policy

**Required.** For the cluster bootstrap, observations within a cluster are
exchangeable; the cluster's internal structure is not itself the estimand.

**If violated.** Ordering effects, time trends or dependence *between* events
in a world are not captured by resampling whole worlds. The interval remains
valid for the cluster-level estimand but may not mean what is intended.

**Detectable?** **No.** The library has no time or ordering column.

**What to do.** If order matters, encode it as a declared stratum or make each
ordered block its own world.

---

## A9 — The estimator is smooth enough for the bootstrap

**Required.** The statistic is not a badly non-smooth functional of the
empirical distribution.

**If violated.** Extreme quantiles and maxima converge slowly and their
bootstrap intervals can be poorly calibrated, especially with few clusters.

**Detectable?** **Partially.** Degenerate replicates are counted and reported
in the result's notes; slow convergence is not detectable from one dataset.

**What to do.** Prefer CVaR to a raw extreme quantile — it averages the tail
rather than picking one point out of it. For `max`, treat the interval as
indicative and say so.

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
| A1 | Worlds are independent replicates | No | Critical |
| A2 | Events nested in one world | Yes (error) | Critical |
| A3 | Policy assigned at world/event level | Partial | Critical |
| A4 | Missingness unrelated to outcome | Partial | High |
| A5 | Declared metric is decision-relevant | No | High |
| A6 | Strata are world-level | Yes (warning) | Medium |
| A7 | Enough clusters | Yes (warning) | Medium |
| A8 | Within-cluster exchangeability | No | Medium |
| A9 | Estimator smooth enough | Partial | Low |
| A10 | Records are faithful | No | Critical |

The four undetectable-and-critical rows are why this library reports its
assumptions rather than only its results.
