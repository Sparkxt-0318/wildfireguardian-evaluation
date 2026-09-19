# Decision records

Each record states the decision, when it was taken, what was decided against,
and what would reverse it. Superseding a decision means adding a new record,
not editing an old one.

---

## D1 — The world is the default unit of inference

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** Resampling and inference default to the world. `event` is
available when events are the true replicates. `resident` does not exist as an
option; the config loader raises.

**Considered and rejected**

- *Resident-level resampling.* Gives intervals 3–4× too narrow on the
  demonstration design, with ~62% coverage for a nominal 95% interval. The
  error is easy to commit and the wrong answer looks better than the right one,
  so a warning is not enough — it must be unrepresentable.
- *Event level by default.* Events within a world are correlated through the
  world. Defaulting to events reintroduces the same error one level down and is
  harder to notice.
- *Configurable with no restriction.* Rejected on the same "wrong answer looks
  better" grounds.

**Reverses if.** A design appears where residents genuinely are independent
replicates. That design has no nesting and should not be described with this
schema.

---

## D2 — Paired comparison restricted to shared clusters, by default

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** `require_common_worlds: true` by default. Every excluded cluster
is recorded. Setting it false is allowed and marks every downstream result
`UNPAIRED`.

**Considered and rejected**

- *Unpaired by default.* World variance (SD 3–5) dwarfs policy effects
  (0.03–1.5). Scenario `missing_worlds_reverse_ranking` shows a true +1.2
  disadvantage reported as a −1.9 advantage.
- *Model the world effect instead of excluding.* A mixed model could use the
  unpaired worlds. It buys information at the cost of a distributional
  assumption and, more importantly, it hides the problem — the analyst no
  longer sees that half the worlds are one-armed. Deferred to ROADMAP R4 as a
  cross-check.
- *Forbid unpaired comparison entirely.* Sometimes there is no alternative.
  Loud labelling beats an obstruction people route around.

**Reverses if.** A variance-components model is added and shown, by red-team
scenario, to recover the truth on the missing-worlds data at least as reliably
as exclusion.

---

## D3 — Equivalence requires an explicit margin; there is no default

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** `tost` and `non_inferiority` raise `MarginRequired` without a
margin. Metrics without one are annotated to say equivalence cannot be
concluded for them.

**Considered and rejected**

- *A default margin* (0.1σ, 5% of the baseline, Cohen-style conventions). A
  default margin is a claim about what matters in a domain the library does not
  know. It would be adopted silently and defended by nobody.
- *Infer the margin from the data*, e.g. a fraction of the observed SD. Ties
  the practical threshold to the noise level: a noisier experiment would get a
  wider margin and find equivalence more easily. Exactly backwards.

**Reverses if.** Never, for a global default. A *project* may declare standard
margins in its own config; that is the right place for them.

---

## D4 — "No difference" is not an available conclusion

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** The verdict vocabulary is `superior`, `worse`, `equivalent`,
`statistically_different_practically_equivalent`, `inconclusive`. The
`inconclusive` sentence states explicitly that it is not "no difference".

**Considered and rejected**

- *Report p-values and let readers interpret.* Fifty years of evidence say they
  will interpret p > 0.05 as "the same".
- *A `no_significant_difference` label.* The phrase is the failure mode.

**Reverses if.** Never.

---

## D5 — Percentile bootstrap by default, BCa available

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** `percentile` default; `basic` and `bca` available. BCa falls back
to percentile endpoints when the jackknife or bias correction is degenerate,
and records the fallback in the result's notes.

**Considered and rejected**

- *BCa by default.* Better small-sample properties, but it costs a
  leave-one-cluster-out jackknife pass and it fails in more ways. A default
  should be the method that is hardest to get silently wrong.
- *Normal-theory intervals.* Assume symmetry, which CVaR and quantiles
  violate badly.
- *Studentised bootstrap.* Best coverage in theory, needs a variance estimate
  per replicate, which for CVaR means a nested bootstrap. Deferred.

**Reverses if.** Coverage studies show BCa materially better across the
estimator registry at realistic cluster counts.

---

## D6 — Two-step, per-column declared aggregation

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** `resident_to_event` and `event_to_world` rules per column,
default `mean`, applied one step at a time.

**Considered and rejected**

- *Pool all observations and take one statistic.* Weights events by size, so a
  200-resident event counts fifty times a 4-resident one. Sometimes right,
  never right by default, and invisible when implicit.
- *A single rule for all columns.* `sum` is right for resource use and wrong
  for loss. One rule forces one of them to be wrong.

**Reverses if.** Never as a structure; the defaults are open to revision.

---

## D7 — CVaR is the mean of the worst `ceil((1−α)·n)` values

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** Empirical CVaR takes `k = max(1, ceil((1−α)·n))` and averages the
`k` most extreme values.

**Considered and rejected**

- *Mean of values beyond the α-quantile.* Undefined when no value exceeds the
  quantile — which happens at high α and small n, exactly where tail statistics
  matter.
- *A parametric tail fit.* Better in the far tail, but it imports a
  distributional assumption into a library whose selling point is not having
  any.

**Reverses if.** A tail-fitting estimator is added as an explicitly named
alternative, never as a redefinition of `cvar`.

---

## D8 — No multiplicity correction; visibility instead

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** No family-wise or FDR correction. Instead: every comparison is
reported, disagreements are flagged, per-stratum results are shown together,
and the protocol requires declaring a primary metric in advance.

**Considered and rejected**

- *Bonferroni over all declared metrics.* Requires a family the library cannot
  infer — metrics, policies and strata are all candidate dimensions — and
  correcting over the wrong family is worse than not correcting.
- *FDR over everything.* Same problem, with a false air of rigour.

**Reverses if.** A project declares its family in the config. Then the
correction is well defined and can be applied to that family.

---

## D9 — Strata are world-level, resolved by modal value

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent A

**Decision.** A world gets one stratum value: the modal value of its rows.
Disagreement inside a world is warned about.

**Considered and rejected**

- *Split the world across strata.* Breaks the pairing — the same world would
  appear in two strata with different subsets of its events.
- *Error on any disagreement.* Too brittle for a single mislabelled row; the
  warning plus modal resolution loses nothing and blocks nothing.

**Reverses if.** Event-level stratification is added with the event as the unit
of inference.

---

## D10 — Failure-mode rates are per observation, not per failure

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent B, ratified by A

**Decision.** In the paired per-world failure shift table, the numerator counts
failures of a given reason and the denominator counts **all** observations in
that world. Successes never appear as a failure mode.

**Considered and rejected**

- *Share of failures.* Composition shares move when the total failure rate
  moves, so a policy that halves failures can appear to "worsen" a mode whose
  absolute rate fell. Composition is still reported in the main table; the
  paired shift uses absolute rates.
- *Only among failures.* Same problem, and it drops the information that
  matters most: whether the mode got rarer.

**Reverses if.** Never. (This record exists because the first implementation got
it wrong: successes were bucketed as `unspecified` and appeared in the shift
table as an 83% "failure mode".)

---

## D11 — The library refuses rather than warns, where refusal is possible

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent B

**Decision.** Errors that would produce a confidently wrong number raise:
resident-level resampling, equivalence without a margin, a metric coarser than
the resampling unit, a single cluster, duplicate observation keys, broken
nesting. Problems that only *degrade* a number warn: few clusters, unbalanced
coverage, implausible values.

**Rule of thumb.** If the mistake makes the output *look better* than the truth
warrants, refuse. If it makes it noisier or harder to interpret, warn.

**Reverses if.** A refusal blocks a legitimate design. Then the design gets a
supported expression, not an exemption.

---

## D12 — Synthetic strata are laid out as a full factorial

**Date** 2026-09-19 · **Status** Accepted · **Owner** Agent C

**Decision.** The generator assigns stratum column *j* by cycling at the
product of the widths of the preceding columns, so declared strata are crossed
and balanced.

**Considered and rejected**

- *Round-robin on every column.* The original implementation. Two 2-level
  columns became perfectly collinear — every `flat` world was also `high`
  mobility — which made a per-stratum result silently uninformative.
- *Random assignment per column.* Independent in expectation, imbalanced in any
  finite sample, and not reproducible by hand.

**Reverses if.** A scenario needs deliberately confounded strata — in which
case it should say so, as `easier_worlds_confound` does through `world_subset`.
