# Roadmap

Ordered by value, not by effort. Each item states what it adds, why it is not
already here, and what must be decided before it can start.

---

## R1 — Config inheritance and project-level margins

**Adds.** A project-level config that analysis configs extend, so standard
margins and metric definitions are declared once and reused.

**Why not yet.** [`DECISIONS.md`](../docs/DECISIONS.md) D3 forbids a default
margin. Inheritance is the legitimate way to standardise margins without
creating a global default — but only if the inherited margin is still a choice
somebody made and can be seen in the provenance.

**Blocked on.** Agent A open question 3 in [`CURRENT.md`](CURRENT.md).

**Definition of done.** `extends:` in a config; resolved values visible in
provenance; a test that an inherited margin is distinguishable from an
explicitly declared one.

---

## R2 — Multi-arm comparisons with declared contrasts

**Adds.** More than two policies with a declared contrast structure — all
pairwise, all-vs-baseline, or a named set — rather than independent
baseline-vs-candidate runs.

**Why not yet.** Multi-arm comparison and multiplicity are the same problem
wearing different clothes; doing arms without families would produce more
comparisons with no accounting.

**Blocked on.** Agent A open questions 2 and 4.

**Definition of done.** `comparison.contrasts`; each contrast paired on its own
shared cluster set; a red-team scenario where a three-arm winner is an artefact
of picking the best of three.

---

## R3 — Power and sample-size planning

**Adds.** "How many worlds would you need to resolve a difference of X?" from
variance components estimated on pilot data.

**Why not yet.** Needs a fitted variance-components model (world, world×policy,
event, residual), which the library does not have. `design_effect` computes a
one-way ICC but not the full decomposition.

**Definition of done.** `wg-eval power --config analysis.yaml --effect 0.5`;
validated against simulation, not just formulae.

---

## R4 — Mixed-model cross-check

**Adds.** A variance-components model alongside the bootstrap, as a
disagreement check rather than a replacement.

**Why not yet.** [`DECISIONS.md`](../docs/DECISIONS.md) D2 and
[`SCOPE.md`](../docs/SCOPE.md): the bootstrap answers the same questions with
fewer assumptions. The value is in the *disagreement* — when the two methods
differ, something about the data is worth looking at.

**Definition of done.** Optional `statsmodels` dependency; both estimates
reported side by side; a note when they disagree materially; explicitly never
the default.

---

## R5 — Multiplicity across contrasts, and adjusted intervals

**Adds.** Families spanning policy contrasts as well as metrics, and confidence
intervals adjusted for multiplicity.

**Why not yet.** Declared families by analysis role shipped in v0.1.0, with Holm
and Bonferroni, and the red-team scenario `uncorrected_family` demonstrates the
error. Two pieces remain. Contrast-level families need the multi-arm design of
R2. Adjusted intervals are genuinely hard: a Holm-adjusted region is not
generally an interval, so shipping one would mean choosing a specific
simultaneous-confidence construction and saying which.

**Definition of done.** A declared family spanning contrasts; a named
simultaneous-interval construction with its assumptions documented; the
uncorrected and corrected results both shown.

---

## R6 — Sequential and interim analysis support

**Adds.** Support for designs where data arrives in batches and analysis may
stop early.

**Why not yet.** Needs a stopping rule declared before data collection, which
is a property of the producer's process. Analysing a sequentially-stopped
experiment as if it were fixed-sample inflates effects.

**Definition of done.** A declared stopping rule in the config; alpha spending;
validation that refuses a fixed-sample analysis of a sequentially-stopped
dataset when the config declares one.

---

## R7 — Visual output

**Adds.** Forest plots of paired differences with margins drawn; per-stratum
panels; loss distributions with the CVaR region shaded.

**Why not yet.** [`SCOPE.md`](../docs/SCOPE.md): the report is text-first so it
diffs and reviews cleanly. Plots are additive, not a replacement.

**Definition of done.** Optional `matplotlib` dependency; `--plots` on
`wg-eval report`; every plot showing the margin and the cluster count, because
a forest plot without a margin invites the F4 error.

---

## R8 — Integration with real Guardian output

**Adds.** An adapter from Guardian's native output to the generic schema.

**Why not yet.** Explicitly out of scope by instruction, and the sequencing is
right: the evaluation protocol should be settled before it meets data that
someone has a stake in.

**Definition of done.** An adapter living **outside** `src/wg_eval/` — the
library stays domain-free ([`SCOPE.md`](../docs/SCOPE.md)); a mapping document
stating what each Guardian concept becomes in the schema, especially what
counts as a world; validation run on real output with the warnings addressed
before any comparison is reported.

---

## R9 — Studentized bootstrap

**Adds.** A studentized interval, which has the best small-sample coverage of
the standard constructions.

**Why not yet.** It needs a variance estimate for the statistic within each
replicate. For a mean that is cheap; for CVaR it means a nested bootstrap, whose
cost is quadratic in resamples. The method name is currently *rejected* rather
than silently substituted, which is the honest interim position.

**Definition of done.** Available per metric rather than globally, with the
nested-resample cost stated; a coverage study comparing it against percentile
and BCa at 10, 20 and 50 units, each with Monte Carlo intervals.

---

## R10 — Crossed designs

**Adds.** Support for two groupings that neither nest nor contain — the same
policies evaluated across, say, scenario families and hardware configurations.

**Why not yet.** `crossed_levels` is an error today. A crossed design needs
either a crossed-effects model or a declared choice of which factor is the
resampling unit with the other as a stratum, and that choice changes the
estimand.

**Blocked on.** Agent A open question 1 in [`CURRENT.md`](CURRENT.md).

**Definition of done.** A declared crossed structure; a stated estimand for it;
a red-team scenario where treating one crossed factor as nested under-covers.

---

## R11 — Weighted estimands with a declared design

**Adds.** Unit weights, once the producer declares a sampling design that makes
the weighted estimand well defined.

**Why not yet.** [`DECISIONS.md`](../docs/DECISIONS.md) D21: the two things
called "weight" change different parts of the estimand and cannot be told apart
from a column of numbers.

**Definition of done.** A `sampling_design` declaration that distinguishes a
unit-level sampling weight from an intra-unit probability; the weighted estimand
written out in `ESTIMANDS.md`; a red-team scenario where applying the wrong one
silently re-weights the population.

---

## Not planned

| | Why |
|---|---|
| Bayesian estimation | A coherent alternative, but mixing frameworks in one report invites choosing between them after the fact |
| Weighted units without a declared design | A sampling weight and an intra-unit probability cannot be told apart from a column of numbers (D21) |
| A web UI | The CLI and the Python API are the interface; a UI would need a server and an audience this library does not have |
| Automatic metric selection | Metric shopping with extra steps ([`FAILURE_MODES.md`](../docs/FAILURE_MODES.md) F7) |
| Domain knowledge of any kind | [`SCOPE.md`](../docs/SCOPE.md) — this is the property that makes the library trustworthy |
