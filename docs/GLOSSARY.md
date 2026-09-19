# Glossary

Terms as this repository uses them. Where a term is contested elsewhere, the
usage here is stated and stuck to.

---

**Allocation ledger** — A table of how many worlds of each stratum each policy
actually ran. The check for [F3](FAILURE_MODES.md#f3--confounded-allocation):
if two policies were given different material, a pooled comparison measures the
material.

**BCa** — Bias-corrected and accelerated bootstrap interval. Adjusts percentile
endpoints for median bias (`z0`) and for how the statistic's variance changes
with its value (`a`, from a leave-one-cluster-out jackknife). Falls back to
percentile endpoints, with a note, when either correction is degenerate.

**Cluster** — A group of observations resampled as a unit. Here, a world (or an
event). Synonym in context: *unit of inference*, *replicate*.

**Cluster bootstrap** — Resampling whole clusters with replacement. Every
observation inside a drawn cluster travels with it, so within-cluster
correlation is preserved and the interval reflects the precision the design
actually has.

**Coverage** — The proportion of repeated experiments whose nominal-95%
interval contains the true value. An honest 95% interval covers ~95% of the
time. Measured by `coverage_study`; the library's central empirical claim.

**CVaR (conditional value at risk)** — The mean of the worst
`k = max(1, ceil((1 − α) · n))` values. Unlike a quantile it is sensitive to
*how bad* the tail is, not only where it starts. Also called expected
shortfall.

**Design effect** — `1 + (m − 1) · ICC`, with `m` the mean cluster size. The
factor by which clustering inflates the variance of an estimate relative to an
independent sample of the same size. A design effect of 4 means the data carry
the information of a quarter as many independent observations.

**Equivalence** — The positive claim that two policies differ by less than a
declared practical margin. Established by TOST. Not the same as failing to
detect a difference.

**Estimand** — The quantity being estimated. "Mean loss" is not an estimand;
"the mean, over events, of the per-event mean loss, over the population of
worlds the sample represents" is. Changing a metric's `level` changes the
estimand.

**Event** — A grouping of observations nested inside a world. One fire, one
incident, one episode. May be the unit of inference when there is no meaningful
world-level grouping.

**Full factorial** — An assignment where every combination of stratum levels
occurs equally often, so the stratum columns are crossed rather than collinear.
How the synthetic generator lays out declared strata
([`DECISIONS.md`](DECISIONS.md) D12).

**ICC (intraclass correlation)** — The share of total variance that lies
between clusters. High ICC means observations within a cluster are similar and
the effective sample size is close to the cluster count.

**Inconclusive** — The study could not resolve the question: the interval
includes zero and does not fit inside the margin. Explicitly **not** "no
difference".

**Margin** — A positive distance on the difference scale, in the metric's own
units, declared before analysis: the largest difference that would still be
practically the same. No default exists ([`DECISIONS.md`](DECISIONS.md) D3).

**Non-inferiority** — The one-sided claim that a candidate is not worse than a
baseline by more than the margin. Direction-dependent.

**Paired comparison** — Comparing policies within the clusters they share, so
cluster-level nuisance variation cancels.

**Policy** — The treatment arm under evaluation. A system configuration,
algorithm or intervention. Domain-free by design.

**Provenance** — The record of how a result was produced: source checksum,
config checksum, filters, exclusions, aggregation rules, metric definition,
seed, confidence level, code version, environment. Present on every result.

**Pseudoreplication** — Treating nested observations as independent replicates.
The central error this library prevents, and the one whose wrong answer looks
most attractive.

**Replicate** — An independent realisation of the experiment. A world.
Observations are *not* replicates, however many of them there are.

**Resident** — One nested observation inside an event. The name is historical;
the library treats it as an opaque observation identifier and attaches no
meaning to it.

**Stratum** — A declared world-level design variable used to subset the
comparison: `landscape`, `mobility`, `fire_regime`, `resource_level`, or any
other. A world belongs to exactly one level of each.

**Superiority** — The claim that one policy beats another: the interval for the
difference excludes zero in that policy's favour.

**TOST (two one-sided tests)** — The standard equivalence procedure. Equivalence
at level `α` is concluded exactly when the central `1 − 2α` interval lies
strictly inside `(−margin, +margin)`.

**Unit of inference** — The level at which replication occurs and resampling is
performed. The world by default; the event where events are the true
replicates; never the observation.

**Verdict** — One of `superior`, `worse`, `equivalent`,
`statistically_different_practically_equivalent`, `inconclusive`. "No
difference" is not among them.

**World** — One independent replicate: one scenario instance, drawn from the
population the study generalises to. The default unit of inference.
