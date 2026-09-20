# Failure modes

The errors this library exists to prevent. Each has a red-team scenario, a
defence, and a measurement showing the defence works — or, where the defence is
partial, an honest statement of what it does not cover.

```bash
wg-eval redteam                      # all sixteen scenarios
wg-eval redteam --scenario wrong_cvar_tail
python experiments/run_demonstration.py --out experiments/output
python experiments/run_mutation_audit.py --out reports
```

Quoted numbers are from seed `20260919` and are reproduced by the commands above.

---

## F1 — Pseudoreplication

**The error.** Treating nested observations as independent replicates.

**What it looks like.** "We evaluated across 2,400 observations." Narrow
intervals, decisive verdicts, a study that appears far better powered than it is.

**Measured.** On the demonstration design — 20 units, 2 sub-units each, 30
observations per sub-unit — resampling observations gives an interval about 4×
too narrow, and its nominal-95% coverage is measured at roughly 60% with a
Monte Carlo interval that excludes 95%. The unit-level interval covers
materially more often, and the gap between the two coverage estimates is larger
than their combined Monte Carlo error.

**How large the error can be.** Not unbounded, and the folk version of this rule
is too strong. With `sigma_I` the unit-by-policy interaction, `V_E` the variance
of a paired sub-unit difference and `E` sub-units per unit, the variance ratio is
`(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)` — it tends to `1/E` when the
interaction dominates and to **1** when there is none. On a design where units
do not differ in how they respond to the policies, the two bootstraps estimate
the same thing.

**Defence.** `inference.primary_unit: resident_id` raises at config load. All
resampling draws whole units. Design effect, ICC and effective sample size in
every report; `nesting_ratio` in every validation.

**Scenarios.** `observation_bootstrap_false_precision`, `unit_bootstrap_calibration`.

---

## F2 — Unpaired comparison across different unit sets

**The error.** Comparing policies over whichever units each happened to run.

**Measured.** Scenario `missing_units_reverse_ranking`: policy_b is truly
**+1.2 worse** and missing from the 22 hardest of 60 units. Unpaired reports
**−1.86 favouring policy_b**; paired on the 38 shared units reports **+0.95**,
recovering the direction.

**Defence.** Pairing on shared units by default; every excluded unit recorded;
`require_common_units: false` marks everything downstream `UNPAIRED UNIT SETS`;
`unbalanced_policy_coverage` at validation.

**What it cannot fix.** The paired estimand is conditioned on being observed
under every policy. See F11.

---

## F3 — Confounded allocation

**The error.** A policy wins because it was tested on easier material.

**Measured.** Scenario `easier_units_confound`: policy_b is truly **1.5 better**
but ran mostly on high-difficulty units. Pooled, policy_a appears **+3.2**
better; paired on the 12 shared units, **−1.69** favouring policy_b, and every
stratum agrees with the paired result.

**Defence.** Pairing; the allocation ledger; `allocation_balance`; per-stratum
contrasts; composition shift between all observed units and the shared ones.

**What it does not claim.** The ledger describes the allocation. It does not
diagnose why the allocation happened, and the library never calls it confounding
(see F13).

---

## F4 — "No significant difference" read as "the same"

**Measured.** Scenario `practical_equivalence`: a true difference of **+0.03**
against a declared margin of **±0.40**, correctly concluded equivalent rather
than merely "not significant".

**Defence.** No `no_difference` verdict exists; `inconclusive` says
"undetermined"; `tost` raises `MarginRequired` without a margin; equivalence
findings carry the interchangeability caveat (F12); `audit_wording` refuses the
phrase in any generated report.

---

## F5 — Mean-only reporting of a heavy-tailed outcome

**Measured.** Scenario `tail_risk_disagreement`: policy_a's mean loss is
**0.85 better**, its 90% CVaR **2.7 worse**, both intervals excluding zero.

**Defence.** Tail metrics are first-class; `detect_disagreements` reports the
conflict with orientation, both contrasts, both intervals and both unit counts,
and names no winner.

---

## F6 — Outcome-dependent missingness

**Measured.** Scenario `outcome_dependent_missingness`: policy_b is truly
**+1.0 worse** and its runs crash on the 18 hardest of 50 units. Complete-case
gives **+0.97 over 32 units**; imputing the worst observed value for the crashed
runs gives **+4.17**. The bounds are far apart, which is the finding.

**Defence.** Failed runs stay in the records with a status; the ledger counts
them before any handling; the declared mechanism is recorded and printed;
`impute_worst`/`impute_best` bound the estimate; the overlap check (F11) fires.

**Residual risk.** No method recovers outcomes that were never produced.

---

## F7 — Metric shopping

**Measured.** Scenario `metric_selected_after_the_fact`: forty pure-noise
metrics, **13 point at policy_b**, and the most convincing has raw **p = 0.052**
— while the declared primary metric resolves against policy_b.

**Defence, partial and labelled as such.** Exactly one primary metric, reported
first; every finding labelled with its role; declared families with corrections;
`analysis_status` and the protocol hash; the config in the provenance. None of
these stop a determined analyst. They stop an accidental one and leave a record
for everyone else.

---

## F8 — Irreproducible results

**Defence.** Two fingerprints and five hashes
([`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) §15). Every scientific
input is tested to move the scientific fingerprint; presentation changes are
tested not to. Row order, policy order and file location are tested to change
nothing.

---

## F9 — Aggregation that changes the estimand silently

**Measured.** On the worked example in `tests/test_reference_calculations.py`,
the two-step unit mean is **12.5** and the pooled observation mean is **12.0** on
identical data. Neither is wrong; they are different quantities.

**Defence.** Declared per-column rules for each step, applied one level at a
time, recorded in provenance and in the scientific fingerprint.

---

## F10 — Success rates that hide a worsened failure mode

**Defence.** Failure composition per policy, plus a paired per-unit shift in each
mode's **absolute** rate, so a mode cannot appear to worsen merely because the
total failure count fell ([`DECISIONS.md`](DECISIONS.md) D10).

---

## F11 — Complete-case bias

**The error.** Reporting a paired estimate, correct on the units it used, as
though it described the target population.

**Measured.** In `missing_units_reverse_ranking` the excluded units differ from
the shared ones by well over the 0.2 standardized-shift threshold, and the
result says so in words.

**Defence.** `panel.estimand_conditioning` states what the estimand is
conditioned on, in the panel, the report and the provenance;
`diagnostics.overlap` compares each arm's values on shared versus excluded units.

**Direction of evidence.** Passing the check is not proof of
representativeness; failing it is disproof. [`ESTIMANDS.md`](ESTIMANDS.md) says
why.

---

## F12 — Statistical equivalence read as interchangeability

**The error.** "Equivalent within ±0.4" taken to mean the options can be swapped.

**Defence.** Every equivalence finding carries the caveat; the verdict label is
`statistically_equivalent`, not `equivalent`; the margin's `source` is recorded
and its absence flagged.

**What it cannot fix.** Whether the margin marks a decision-relevant difference
is a domain judgement. The library states the limit rather than closing it.

---

## F13 — Reversals mistaken for diagnosed confounding

**The error.** Reporting "Simpson's paradox detected" or "confounding detected"
when an aggregate and its strata disagree.

**Why it matters.** A reversal can arise from unequal allocation, from genuine
heterogeneity, or from noise in thin strata. Naming a cause asserts something
unchecked and invites the reader to stop looking.

**Defence.** The findings are `direction_differs_between_strata` and
`aggregate_contradicts_strata`; the prose says the contrasts disagree and points
at the allocation ledger; a stratum with fewer than `min_units` shared units is
not given a contrast at all.

---

## F14 — Redundant strata read as independent evidence

**The error.** Two stratification dimensions that partition the units the same
way, presented as two confirmations.

**Defence.** `stratum_redundancy` reports identical strata, deterministic
nesting, thin cells, levels evaluated under only one policy, and single-level
strata. Found first by the red team in v0.1.0-pre, where two declared strata were
perfectly collinear; the generator now lays strata out as a full factorial
([`DECISIONS.md`](DECISIONS.md) D12).

---

## F15 — Dependence above the declared unit

**The error.** Units that share a higher-level shock, resampled as if
independent. A cluster bootstrap at the wrong level looks rigorous and is not.

**Measured.** Scenario `dependence_above_declared_unit`: 80 units in 20 groups
of 4 sharing a group-by-policy shock. Resampling units covers **78.7%
[71.4%, 84.5%]**; resampling the 20 shared events covers **93.3%
[88.2%, 96.3%]**.

**Defence.** The declared structure is checked against the records:
`inverted_nesting` and `undeclared_coarser_grouping` are hard failures.

**What it cannot fix.** The library sees only groupings that appear in the
records. A dependence nobody recorded is invisible — [`ASSUMPTIONS.md`](ASSUMPTIONS.md) A1.

---

## F16 — The wrong tail

**The error.** A tail statistic that summarises the beneficial end.

**Measured.** Scenario `wrong_cvar_tail`: on a higher-is-better rate, the upper
tail gives **−0.043 favouring policy_a** and the harmful lower tail gives
**+0.536 favouring policy_b** — both with intervals excluding zero.

**Defence.** `tail: harmful` resolves against `direction`; a contradictory
`params.tail` is rejected at config load; the tail side is printed beside every
tail finding; suspect orientations raise `metric_orientation` warnings.

---

## F17 — Too few independent units

**Measured.** Scenario `too_few_units`, at 5 units with 160 observations each:
`mean_loss` is reported as credible, while `cvar90_loss` and `p90_loss` are
flagged as not credible. Only the unit count changes across the sweep; the
observation count per unit does not, and neither do the interval widths shrink
with it.

**Defence.** Per-estimator credibility thresholds — 5 units for a mean, 20 for a
CVaR or quantile, 30 for an extreme — rather than a universal minimum n, which
would either pass a meaningless CVaR or block a usable mean.

---

## F18 — Impossible intervals on bounded outcomes

**Measured.** Scenario `bounded_outcome_interval`: a basic bootstrap interval on
a success rate near 1.0 reaches **1.0025**. The percentile interval on the same
data stays inside the support.

**Defence.** Declared `bounds`; values outside them are a validation error;
interval endpoints outside them are reported and **never clamped**, because
clamping narrows the interval without making the construction appropriate.

---

## F19 — An uncorrected family of tests

**Measured.** Scenario `uncorrected_family`: 20 pure-noise metrics with a true
difference of exactly zero. Uncorrected, **1 contrast resolves**; Holm over the
declared family leaves **0**.

**Defence.** Families are the declared analysis roles; the primary family is
never corrected; p-values are adjusted and intervals are not, and the report
says so.

**What it cannot fix.** A correction applied after the metric was chosen does
not repair the choosing (F7).

---

## F20 — A failed run counted as a missing value

**Measured.** Scenario `failed_runs_as_missing`: policy_b raises the success
rate of the runs that finish while crashing on 22% of them. Dropping the crashes
gives **+0.064 favouring policy_b**; counting them as failures gives **−0.159
favouring policy_a**. Same records, opposite findings.

**Defence.** Declared `status_handling`; the run-status ledger before any
handling; the estimand's conditioning stated per handling.

**Whose choice it is.** Which handling is right is a domain question. The
library forces it into the open rather than answering it.

---

## Coverage summary

| # | Failure mode | Scenario | Prevention |
|---|---|---|---|
| F1 | Pseudoreplication | `observation_bootstrap_false_precision`, `unit_bootstrap_calibration` | refused at config load |
| F2 | Unpaired unit sets | `missing_units_reverse_ranking` | paired by default; labelled otherwise |
| F3 | Confounded allocation | `easier_units_confound` | allocation ledger; per-stratum results |
| F4 | "No difference" | `practical_equivalence` | verdict vocabulary; margin required |
| F5 | Mean-only reporting | `tail_risk_disagreement` | tail metrics; disagreement reported |
| F6 | Outcome-dependent missingness | `outcome_dependent_missingness` | status ledger; imputation bounds |
| F7 | Metric shopping | `metric_selected_after_the_fact` | roles; report order; protocol hash |
| F8 | Irreproducibility | — | two fingerprints; five hashes |
| F9 | Silent aggregation | — | declared steps |
| F10 | Hidden failure modes | — | paired per-unit failure shifts |
| F11 | Complete-case bias | `missing_units_reverse_ranking` | estimand conditioning; overlap check |
| F12 | Equivalence read as interchangeability | `practical_equivalence` | decision caveat on every finding |
| F13 | Reversal called confounding | `easier_units_confound` | wording; support thresholds |
| F14 | Redundant strata | — | `stratum_redundancy` |
| F15 | Dependence above the unit | `dependence_above_declared_unit` | structure checked against records |
| F16 | Wrong tail | `wrong_cvar_tail` | orientation-derived tails |
| F17 | Too few units | `too_few_units` | per-estimator credibility |
| F18 | Impossible intervals | `bounded_outcome_interval` | bounds reported, not clamped |
| F19 | Uncorrected family | `uncorrected_family` | declared families; Holm |
| F20 | Failed run as missing value | `failed_runs_as_missing` | status handling; ledger |

Two further scenarios test the library's own machinery rather than a domain
failure: `asymmetric_margin` (F12/F4) and `paired_data_analysed_unpaired`
(F2/F8, and the defect that found the unread `comparison.paired` flag).
