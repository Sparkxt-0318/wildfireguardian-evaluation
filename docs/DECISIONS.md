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

---

## D13 — The unit of inference is declared, then checked against the records

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A · **Supersedes** part of D1

**Decision.** `inference.primary_unit` and `inference.nested_units` are declared
in the configuration and verified against the data by
`wg_eval.hierarchy.check_structure`. A declaration the records contradict is a
hard failure. `world_id` remains the default; it is no longer an assumption.

**Considered and rejected**

- *Keep `world_id` hardwired as the outermost level.* D1 did that, and it
  cannot express a design where one event spans many worlds — a shock shared
  above the unit. Scenario `dependence_above_declared_unit` shows a unit-level
  cluster bootstrap covering 78.7% [71.4%, 84.5%] on such data while the
  event-level one covers 93.3% [88.2%, 96.3%]. "Cluster bootstrap" is not a
  synonym for "correct".
- *Infer the structure from the data alone.* Tempting, and wrong in the unsafe
  direction: identifiers that differ are not evidence of independence, and an
  inference that silently picked the finest available level would reintroduce
  pseudoreplication under a new name. The library requires a declaration and
  then tries to falsify it.
- *Warn instead of raising.* The error makes the output look better than the
  truth warrants, so D11's rule applies: refuse.

**Reverses if.** Never as a structure. The default primary unit is open to
revision.

---

## D14 — `comparison.paired` is honoured, and is distinct from unit-set overlap

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent B, ratified by A

**Decision.** Two separate switches, both labelled wherever they appear:

- `comparison.require_common_units` controls *which units are used*. False
  means the arms run on different unit sets, marked `UNPAIRED UNIT SETS`.
- `comparison.paired` controls *how the resample is drawn*. False means each
  arm is drawn independently, marked `UNPAIRED RESAMPLE`.

**Why this record exists.** In v0.1.0-pre, `comparison.paired` was parsed and
never read. A configuration that declared `paired: false` received a paired
analysis carrying an unpaired label. The red-team scenario for
paired/unpaired mismatch found it, because the scenario could not make the
"unpaired" analysis behave any differently from the paired one.

**Reverses if.** Never. A configuration key that is read by nothing is worse
than an absent one.

---

## D15 — Coverage figures carry their Monte Carlo uncertainty

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A

**Decision.** Every simulation-based coverage estimate reports replications,
nominal level, empirical coverage, Monte Carlo standard error and a Wilson
interval. `CoverageEstimate.consistent_with_nominal` is the honest summary, and
it means "the Monte Carlo interval does not exclude nominal", not "calibrated".

**Why this record exists.** The v0.1.0-pre red-team wrote "90% at 20 worlds,
98% at 60 worlds (nominal 95%)" from R=100 and R=50. The first has a Wilson
interval of [82.6%, 94.5%], which *excludes* 95% — so "approximately
calibrated" was an overstatement — and the second, from 50 replications, was far
too imprecise to support the comparison it was used for. Both numbers were
right; the claims built on them were not.

**Considered and rejected**

- *Report the point estimate and let readers judge.* They do not, and neither
  did this project's own first write-up.
- *Raise the replication count until the interval is narrow.* Useful, and not a
  substitute: ±2 points around 0.95 needs 457 replications, which is worth
  spending when the claim matters and worth stating when it is not spent.

**Reverses if.** Never.

---

## D16 — CVaR's `k` is computed with explicit rounding

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent B

**Decision.** `k = max(1, ceil(round((1 - alpha) * n, 9)))`.

**Why this record exists.** `(1 - 0.7) * 10` evaluates to `3.0000000000000004`
in binary floating point, whose ceiling is 4. The unrounded implementation
averaged one value more than the documented definition stated, for many ordinary
`(alpha, n)` pairs. Found by a hand-computed reference test, not by review: the
code looked exactly like its specification.

**Consequence.** Production code must not be its own oracle. Every estimator now
has a hand-computed case in `tests/test_reference_calculations.py` with the
arithmetic written out in the docstring.

**Reverses if.** Never. The rounding tolerance may be revisited for `n` beyond
about 10^6.

---

## D17 — Metric orientation is machine-readable and governs tail selection

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A

**Decision.** `direction` determines `harmful_side`; `tail: harmful` resolves
against it; a `params.tail` contradicting `tail` is rejected at config load.

**Considered and rejected**

- *Leave the tail to the analyst.* A CVaR on a higher-is-better metric with the
  default upper tail averages the *best* events and presents the result as risk.
  Scenario `wrong_cvar_tail` gives opposite verdicts from the two tails on the
  same records, both with intervals excluding zero.
- *Infer orientation from the column name.* Domain knowledge, and wrong for any
  column whose name does not happen to say.

**Reverses if.** Never.

---

## D18 — Statistical equivalence is reported as distinct from interchangeability

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A

**Decision.** Every equivalence finding carries a caveat stating that
`STATISTICALLY_EQUIVALENT` is not `OPERATIONALLY_INTERCHANGEABLE`, and margins
may be asymmetric with a recorded `source`.

**Considered and rejected**

- *Leave the distinction to the reader.* The whole point of the verdict
  vocabulary is that readers compress; a verdict called "equivalent" with no
  caveat compresses to "interchangeable".
- *Symmetric margins only.* Tolerance for harm and tolerance for benefit are
  rarely equal. Scenario `asymmetric_margin` shows the same interval concluding
  equivalence under one declaration and not the other; neither is wrong, which
  is precisely why the declaration must precede the data.

**Reverses if.** Never.

---

## D19 — Failed runs are records, not absences

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A

**Decision.** A `run_status` column with a declared `status_handling` per value.
`failure` keeps the run in the denominator; `excluded_documented` removes it and
conditions the estimand on feasibility; `missing` defers to the missing-data
policy. The run-status ledger counts every run *before* any handling.

**Rationale.** A policy that fails to produce a result must not improve its
score by doing so. Scenario `failed_runs_as_missing` gives opposite findings
from the same records under two handlings — which is the finding, and the
library's job is to force the choice into the open rather than to make it.

**Reverses if.** Never.

---

## D20 — Reversals are reported as arithmetic, not diagnosed as confounding

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A · **Supersedes** D9's wording

**Decision.** The detector's findings are named
`direction_differs_between_strata` and `aggregate_contradicts_strata`, and the
prose says "aggregate and within-stratum contrasts disagree". The word
"confounding" does not appear, and a stratum contrast is not reported at all
below `min_units` shared units.

**Rationale.** A reversal between an aggregate and its strata can arise from
unequal allocation, from genuine heterogeneity, or from noise in thin strata.
Calling it confounding asserts a causal explanation the library has no way to
check, and invites a reader to stop looking.

**Reverses if.** Never.

---

## D21 — Unit weights are refused rather than applied

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent A

**Decision.** A `weights` key raises; a weight-looking column is reported and
ignored.

**Rationale.** A sampling weight across units and a scenario probability inside
one unit are different quantities that change different parts of the estimand,
and they cannot be told apart from a column of numbers. Applying either
silently would change what the number means without saying so. See
[`ESTIMANDS.md`](ESTIMANDS.md) "Weighted units".

**Reverses if.** A design declaration distinguishes the two, at which point the
weighted estimand can be defined and stated.

---

## D22 — Two fingerprints, and five hashes

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent B, ratified by A

**Decision.** `scientific_fingerprint` covers only what could change a number;
`fingerprint` covers the whole provenance block. The reproducibility manifest
keeps `code_version`, `analysis_config_hash`, `source_data_hash`,
`protocol_hash` and `report_generator_version` separate.

**Rationale.** Regenerating a report must not look like re-analysed science, and
re-analysed science must not hide behind an unchanged report. One string cannot
do both jobs. Note that the raw config checksum is deliberately *excluded* from
the scientific payload: it hashes the whole file including its label, so a
retitled analysis would otherwise look like a different one.

**Reverses if.** Never.

---

## D23 — The wording list is phrase-level, and enforced

**Date** 2026-09-20 · **Status** Accepted · **Owner** Agent B

**Decision.** `audit_wording` matches phrases, not words. `wg-eval report` exits
non-zero when a generated report matches one, and the test suite runs the audit
over every report the package can produce.

**Considered and rejected**

- *Ban the words outright.* "the same units", "the same seed" and "paired on the
  same worlds" are all legitimate, and a word-level ban makes reports
  unreadable while catching no additional error.
- *Document the rule without enforcing it.* A style rule nothing checks is a
  style rule that drifts. The mutation audit's `p_value_read_as_equivalence`
  mutant is detected precisely by this check.

**Reverses if.** Never as a mechanism; the phrase list will grow.
