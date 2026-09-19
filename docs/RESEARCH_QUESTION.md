# Research question

> **How should WildfireGuardian experiments be evaluated so that comparisons
> are paired, reproducible, event-level, resistant to pseudoreplication, and
> capable of supporting both superiority and equivalence conclusions?**

The question contains five requirements. Each has a precise meaning here, a
concrete implementation, and a test that it is actually met.

---

## 1. Paired

**Requirement.** Policies evaluated on the same world must be compared within
that world.

**Why.** Worlds differ enormously — the demonstration generator uses a world
standard deviation of 3–5 loss units against policy effects of 0.03–1.5. If two
policies are compared across *different* worlds, the world differences swamp
the policy differences and the comparison measures allocation, not quality.
Comparing within world removes every world-level nuisance exactly, without
modelling any of it.

**Implementation.** `build_paired_panel` intersects each policy's cluster set
and records every excluded cluster. The paired bootstrap draws one set of
clusters per replicate and evaluates *both* arms on it, so the pairing survives
into the interval and not just the point estimate.

**Test.** `test_paired_interval_is_narrower_than_the_marginal_ones`;
scenarios `missing_worlds_reverse_ranking` and `easier_worlds_confound`, where
the unpaired ranking is the reverse of the true one.

---

## 2. Reproducible

**Requirement.** Any result can be regenerated exactly, and any two results can
be compared to see what differs.

**Why.** A number that cannot be reproduced cannot be checked, and a number
that cannot be checked cannot be argued with.

**Implementation.** Every result carries a `Provenance` block: source file
checksum, config checksum, filters, exclusions, aggregation rules, metric
definition, bootstrap seed, confidence level, code version and environment
versions. `Provenance.fingerprint()` hashes all of it except the wall-clock
time, so two analyses that should agree can be compared by one string.

**Test.** `test_provenance_records_everything_a_rerun_would_need`,
`test_provenance_fingerprint_ignores_time_but_not_content`,
`test_comparison_is_deterministic_for_a_fixed_seed`.

---

## 3. Event-level

**Requirement.** The analysis operates on events and worlds, not on raw
observations, and the roll-up from observations to events is declared.

**Why.** A per-observation mean weights events by their size. An event with 200
residents then counts fifty times an event with 4, even though both are one
fire. Whether that is wanted is a real choice, so it is a declared one.

**Implementation.** `aggregation.resident_to_event` and
`aggregation.event_to_world` give a rule per column. The roll-up proceeds one
step at a time through the hierarchy and never pools levels implicitly. Metrics
declare the level they pool over, and the level travels with the metric into
the report and the provenance.

**Test.** `test_roll_up_goes_through_the_hierarchy` — which checks explicitly
that the two-step world mean (12.5) differs from the pooled observation mean
(12.0) on the same data.

---

## 4. Resistant to pseudoreplication

**Requirement.** Nested observations are never treated as independent
replicates, and the design cannot be configured to do so by accident.

**Why.** It is the single most consequential error available here, it is easy
to commit, and the resulting intervals look *better* — narrower, more
decisive — than correct ones.

**Implementation.** Three layers:

1. `bootstrap.cluster_level: resident` raises `ConfigError` at load time. The
   error names the error and points at the red-team demonstration.
2. Every bootstrap resamples whole clusters; all observations inside a drawn
   cluster travel with it.
3. Every validation report carries a `nesting_ratio` note stating how large the
   overstatement would be, and every analysis report prints the design effect
   and the effective sample size.

**Test.** `test_config_rejects_resident_level_resampling`;
`test_resident_bootstrap_is_measurably_over_confident`, which measures coverage
of the wrong interval (~62%) against the right one (~90% at 20 worlds, ~98% at
60).

---

## 5. Capable of superiority *and* equivalence

**Requirement.** The analysis can conclude "A beats B", "A and B are
practically the same", or "we cannot tell" — and it can tell the last two
apart.

**Why.** The failure this prevents is the most common in applied evaluation: a
wide interval containing zero reported as "no significant difference" and read
by everyone downstream as "they are the same". Those are opposite claims.

**Implementation.** `classify_difference` emits one of five labels —
`superior`, `worse`, `equivalent`, `statistically_different_practically_equivalent`,
`inconclusive`. There is no "no difference" label, and the inconclusive
sentence says "This is 'undetermined', NOT 'no difference'" in the report text.
Equivalence requires a margin: `tost` raises `MarginRequired` without one.

**Test.** `test_no_verdict_ever_claims_no_difference`,
`test_wide_interval_around_zero_is_inconclusive_never_equivalent`, and the
`practical_equivalence` scenario.

---

## The shape of the answer

| Requirement | Mechanism | Refuses |
|---|---|---|
| Paired | Common-cluster panel, shared resample | Silent unpaired comparison |
| Reproducible | Provenance + fingerprint on every result | Unseeded resampling |
| Event-level | Declared two-step roll-up, levelled metrics | Implicit pooling |
| Anti-pseudoreplication | Cluster bootstrap; resident level rejected at load | Observation-level resampling |
| Superiority + equivalence | Five-label verdict; TOST with a required margin | "No significant difference" |

## What would falsify the answer

- A cluster bootstrap whose measured coverage is not approximately nominal at
  realistic cluster counts. Measured in `coverage_study`.
- A red-team scenario that the protocol does not defend against. Each new
  scenario is a falsification attempt; `test_scenario_traps_fire_and_defences_hold`
  fails loudly when a defence stops working.
- A real design where the world is not the right unit of inference — for
  example, policies allocated *within* events rather than across worlds. That
  design is out of scope today; see [`ASSUMPTIONS.md`](ASSUMPTIONS.md) A3.
