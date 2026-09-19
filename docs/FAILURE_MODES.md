# Failure modes

The errors this library exists to prevent. Each has a red-team scenario, a
defence, and a measurement showing the defence works.

Run them all:

```bash
wg-eval redteam
python experiments/run_demonstration.py --out experiments/output
```

---

## F1 — Pseudoreplication

**The error.** Treating nested observations as independent replicates.

**What it looks like.** "We evaluated across 2,400 residents." Narrow
intervals, decisive verdicts, a study that appears far better powered than it is.

**Measured consequence.** On the demonstration design — 20 worlds, 2 events
each, 30 residents per event — resampling residents gives an interval **3.9×
too narrow**, and a nominal-95% interval covers the true difference **62% of
the time**. The world-level interval covers it 90% at 20 worlds and 98% at 60.

**Why it is dangerous.** The wrong answer looks *better*. Nobody scrutinises an
interval for being too narrow.

**Defence.** `bootstrap.cluster_level: resident` raises at config load. All
resampling draws whole clusters. Design effect, ICC and effective sample size
appear in every report; `nesting_ratio` appears in every validation.

**Scenarios.** `resident_bootstrap_false_precision`, `world_bootstrap_correct`.

---

## F2 — Unpaired comparison across different worlds

**The error.** Comparing policies over whichever worlds each happened to run.

**What it looks like.** A confident ranking with no mention of which worlds
each policy saw.

**Measured consequence.** Scenario `missing_worlds_reverse_ranking`: policy_b is
truly **+1.2 worse**, but is missing from the 22 hardest of 60 worlds. The
unpaired comparison reports **−1.86 in policy_b's favour**. The paired
comparison on the 38 shared worlds reports **+0.95**, recovering the direction.

**Defence.** Pairing on shared clusters by default. Every excluded world
recorded. `require_common_worlds: false` marks everything downstream `UNPAIRED`.
`unbalanced_policy_coverage` fires at validation.

**Scenario.** `missing_worlds_reverse_ranking`.

---

## F3 — Confounded allocation

**The error.** A policy wins because it was tested on easier material.

**What it looks like.** A large effect, consistent across metrics, that
evaporates or reverses when you ask which worlds each policy ran.

**Measured consequence.** Scenario `easier_worlds_confound`: policy_b is truly
**1.5 better**, but ran mostly on steep worlds while policy_a ran mostly on
flat ones. Pooled, policy_a appears **+3.2 better**. Paired on the 12 shared
worlds: **−1.69**, favouring policy_b. Every stratum agrees with the paired
result.

**Defence.** Pairing; the allocation ledger (worlds per policy per stratum);
`allocation_balance`, which flags a share gap above 10%; per-stratum
comparison; `pooled_contradicts_strata` detection.

**Scenario.** `easier_worlds_confound`.

---

## F4 — "No significant difference" read as "the same"

**The error.** Reporting a large p-value or a zero-containing interval as
evidence of equivalence.

**What it looks like.** "No significant difference was found (p = 0.41),
so the simpler policy is preferred."

**Why it is dangerous.** It is a claim the data does not support, phrased so
that it sounds like one it does. A wide interval around zero is consistent with
zero *and with every other value it contains*, including differences that
matter enormously.

**Defence.** No `no_difference` verdict exists. `inconclusive` says "This is
'undetermined', NOT 'no difference'". `tost` raises `MarginRequired` without a
margin. Metrics with no margin are annotated. When the interval is much wider
than the margin, the result says so and says that more *clusters* are needed.

**Scenario.** `practical_equivalence` — a true difference of +0.03 against a
declared margin of 0.40, correctly concluded equivalent rather than merely
"not significant".

---

## F5 — Mean-only reporting of a heavy-tailed outcome

**The error.** Summarising a skewed loss distribution by its mean.

**What it looks like.** "Policy A reduces mean loss by 0.85. Recommend A."

**Measured consequence.** Scenario `tail_risk_disagreement`: policy_a has a 4%
per-observation chance of a +60 catastrophe. Its mean loss is **0.85 better**;
its 90% CVaR is **2.7 worse**. Both intervals exclude zero. A mean-only report
confidently recommends the policy with the catastrophic tail.

**Defence.** Tail metrics are first-class. `detect_disagreements` compares
central and tail metrics on the same column and reports a `central_vs_tail`
conflict, plus a `metric_split` note when declared metrics favour different
policies. The report prints the disagreement rather than resolving it.

**Scenario.** `tail_risk_disagreement`.

---

## F6 — Outcome-dependent missingness

**The error.** Dropping failed, crashed or timed-out runs and analysing what
remains.

**What it looks like.** A clean dataset. That is exactly the problem: the runs
that would have shown the weakness are the ones that did not finish.

**Defence.** Missing clusters are always reported, never silently dropped.
Missing values are counted per policy in the provenance. `impute_worst` and
`impute_best` bound the effect, and running both is the protocol's
recommendation for substantial missingness. Pairing prevents the specific
failure of comparing over unequal world sets.

**Residual risk.** No method recovers information that was never recorded. If a
policy crashes on hard worlds, the honest statement is about the worlds it
completed. [`ASSUMPTIONS.md`](ASSUMPTIONS.md) A4.

**Scenario.** `missing_worlds_reverse_ranking`.

---

## F7 — Metric shopping

**The error.** Running many comparisons and reporting the flattering one.

**Defence, partial.** The library cannot prevent this — a determined analyst
can delete metrics from a config. It makes it *visible*: every declared metric
is reported including unresolved ones; disagreements are flagged; per-stratum
results are printed together; the provenance records the config that produced
the numbers, so a later reader can see what was declared.

The protocol requires declaring the primary metric in advance
([`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) §9). That is a process
control, not a code control, and it is labelled as one.

---

## F8 — Irreproducible results

**The error.** A number that cannot be regenerated: unseeded resampling, an
undocumented filter, a since-modified input file.

**Defence.** Every result carries source checksum, config checksum, filters,
exclusions, aggregation rules, metric definition, seed, confidence level, code
version and environment versions. `Provenance.fingerprint()` reduces all of it
except the timestamp to one string, so two results that should match can be
compared directly.

---

## F9 — Aggregation that changes the estimand silently

**The error.** Pooling observations across events of very different sizes, so a
200-resident event counts fifty times a 4-resident one.

**Measured consequence.** On the worked example in
`test_roll_up_goes_through_the_hierarchy`, the two-step world mean is **12.5**
and the pooled observation mean is **12.0** on identical data. Neither is
wrong; they are different quantities, and only one of them is what was meant.

**Defence.** Declared per-column rules for each step, applied one level at a
time. Never an implicit pool. Rules are recorded in provenance.

---

## F10 — Success rates that hide a worsened failure mode

**The error.** Reporting overall success and stopping.

**What it looks like.** "Success improved from 82% to 84%." Meanwhile the
`unreachable` failures — the ones with the worst consequences — doubled.

**Defence.** Failure composition per policy, plus a **paired per-world shift**
in each mode's absolute rate. The summary names the mode that got worse:

```
policy_b raises the per-world rate of 'late_arrival' by +6.94% on average
relative to policy_a; an improved overall rate can still hide a worsened
failure mode.
```

Rates are per observation, not per failure, so a mode cannot appear to worsen
merely because the total failure count fell ([`DECISIONS.md`](DECISIONS.md) D10).

---

## Coverage summary

| Failure mode | Scenario | Prevention |
|---|---|---|
| F1 Pseudoreplication | `resident_bootstrap_false_precision`, `world_bootstrap_correct` | Refused at config load |
| F2 Unpaired comparison | `missing_worlds_reverse_ranking` | Paired by default; `UNPAIRED` labelling |
| F3 Confounded allocation | `easier_worlds_confound` | Allocation ledger; per-stratum results |
| F4 "No difference" | `practical_equivalence` | Verdict vocabulary; margin required |
| F5 Mean-only reporting | `tail_risk_disagreement` | Tail metrics; disagreement detection |
| F6 Missingness | `missing_worlds_reverse_ranking` | Reported; imputation bounds |
| F7 Metric shopping | — | Visibility; process control |
| F8 Irreproducibility | — | Provenance on every result |
| F9 Silent aggregation | — | Declared two-step rules |
| F10 Hidden failure modes | — | Paired per-world failure shifts |
