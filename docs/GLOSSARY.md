# Glossary

Terms as this repository uses them. Where a term is contested elsewhere, the
usage here is stated and stuck to.

---

**Allocation ledger** — A machine-readable account of what each policy was
actually given: units per policy, shared and unique units, counts at every
nested level, strata composition, run-status and failure-reason counts, and the
composition shift between all observed units and the shared ones. Reported, never
corrected.

**Analysis role** — `primary`, `secondary` or `exploratory`. Determines report
order and family membership for multiplicity. At most one metric may be primary.

**Analysis status** — `preregistered`, `exploratory` or `unspecified`, asserted
by the analyst. Never inferred from the existence of a config file; claiming
preregistration requires a protocol hash, commit or path.

**BCa** — Bias-corrected and accelerated bootstrap interval. Adjusts percentile
endpoints for median bias (`z0`) and for how the statistic's variance changes
with its value (`a`, from a leave-one-cluster-out jackknife). Falls back to
percentile endpoints, with a note, when either correction is degenerate.

**Cluster** — A group of observations resampled as a unit. Here, whatever
`inference.primary_unit` declares. Synonyms in context: *primary unit*, *unit of
inference*, *replicate*.

**Credibility (of a statistic)** — Whether the available unit count can support
the estimator in question. Per estimator, not a universal minimum n: 5 units for
a mean, 20 for a CVaR or quantile, 30 for an extreme.

**Cluster bootstrap** — Resampling whole clusters with replacement. Every
observation inside a drawn cluster travels with it, so within-cluster
correlation is preserved and the interval reflects the precision the design
actually has.

**Coverage** — The probability that a procedure's nominal-95% interval contains
the true value. Estimated by simulation, and therefore reported with its
replication count, Monte Carlo standard error and Wilson interval. An estimate
*consistent with* nominal is the absence of evidence of miscalibration at that
number of replications, not a demonstration of calibration.

**Monte Carlo uncertainty** — How precisely a finite number of simulation
replications pins down a coverage probability. Distinct from the coverage itself:
one is the property under study, the other the noise in studying it.

**CVaR (conditional value at risk)** — The mean of the worst
`k = max(1, ceil((1 − α) · n))` values. Unlike a quantile it is sensitive to
*how bad* the tail is, not only where it starts. Also called expected
shortfall.

**Design effect** — `1 + (m − 1) · ICC`, with `m` the mean cluster size. The
factor by which clustering inflates the variance of an estimate relative to an
independent sample of the same size. A design effect of 4 means the data carry
the information of a quarter as many independent observations.

**Equivalence (statistical)** — The positive claim that a contrast lies inside a
declared practical margin. Established by TOST. Not the same as failing to
detect a difference, and **not** the same as operational interchangeability:
whether the margin marks a decision-relevant difference is a domain judgement
outside this library.

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
negligible. May be **asymmetric** (`lower` and `upper` differ), and carries a
`source` recording where the number came from. No default exists
([`DECISIONS.md`](DECISIONS.md) D3).

**Harmful side / harmful tail** — The end of a metric's distribution that is bad:
`upper` for a `lower_is_better` metric, `lower` for a `higher_is_better` one.
Tail statistics resolve against it, so a CVaR summarises risk by construction.

**Inference structure** — The declared chain of levels, coarsest first, with the
primary unit at its head. Checked against the records rather than assumed.

**Reproducibility manifest** — Five separately-moving hashes: `code_version`,
`analysis_config_hash`, `source_data_hash`, `protocol_hash`,
`report_generator_version`.

**Run status** — How a run ended: `completed`, `crashed`, `timeout`,
`infeasible`, `not_evaluated`, `excluded`. A failed run is a record, not an
absence, and the declared `status_handling` decides whether it counts as a
failure, an exclusion, or a missing value.

**Scientific fingerprint** — A checksum over everything that could change a
number, excluding presentation. Distinct from the full provenance fingerprint,
which includes titles and paths.

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
most attractive. Its magnitude is bounded: it vanishes when units do not differ
in how they respond to the policies, and approaches a factor of `sqrt(E)` in the
interval width when they differ strongly, with `E` sub-units per unit.

**Replicate** — An independent realisation of the experiment. A world.
Observations are *not* replicates, however many of them there are.

**Resident** — One nested observation. The name comes from the interface
contract; the library treats it as an opaque identifier and attaches no meaning
to it.

**Stratum** — A declared unit-level design variable used to subset the
comparison. A unit belongs to exactly one level of each. Redundant strata —
identical, deterministically nested, thin, or confounded with policy — are
detected and reported, because they look like extra evidence and are not.

**Superiority** — The claim that one policy beats another: the interval for the
difference excludes zero in that policy's favour.

**TOST (two one-sided tests)** — The standard equivalence procedure. Equivalence
at level `α` is concluded exactly when the central `1 − 2α` interval lies
strictly inside `(−margin, +margin)`.

**Unit of inference** — The level at which replication occurs and resampling is
performed. The world by default; the event where events are the true
replicates; never the observation.

**Verdict** — One of `superior`, `worse`, `statistically_equivalent`,
`statistically_different_within_margin`, `inconclusive`. "No difference" is not
among them, and no generated sentence may contain it.

**World** — The default name of the primary unit. One independent replicate,
drawn from the population the study generalises to. Which column is the primary
unit is declared, not assumed.
