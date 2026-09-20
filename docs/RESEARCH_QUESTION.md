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
`test_unpaired_resample_is_wider_and_labelled`; scenarios
`missing_units_reverse_ranking`, `easier_units_confound` and
`paired_data_analysed_unpaired`.

**What pairing costs.** Restricting to shared units changes the estimand to one
conditioned on being observed under every policy. That is reported wherever the
finding is, and the excluded units are compared against the shared ones to see
whether the conditioning matters here. See [`ESTIMANDS.md`](ESTIMANDS.md).

---

## 2. Reproducible

**Requirement.** Any result can be regenerated exactly, and any two results can
be compared to see what differs.

**Why.** A number that cannot be reproduced cannot be checked, and a number
that cannot be checked cannot be argued with.

**Implementation.** Every result carries a `Provenance` block and a
reproducibility manifest with five separately-moving hashes. Two fingerprints
are computed: `scientific_fingerprint` covers everything that could change a
number, and `fingerprint` covers presentation as well, so a regenerated report
and a re-analysis are distinguishable.

**Test.** `tests/test_reproducibility.py` audits both directions: thirteen
parametrized cases assert that each scientific input moves the fingerprint, and
further cases assert that row order, policy order, file location and
presentation do not.

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

1. `inference.primary_unit: resident_id` raises `ConfigError` at load time, and
   a declared structure the records contradict raises `InferenceStructureError`.
2. Every bootstrap resamples whole clusters; all observations inside a drawn
   cluster travel with it.
3. Every validation report carries a `nesting_ratio` note stating how large the
   overstatement would be, and every analysis report prints the design effect
   and the effective sample size.

**Test.** `test_config_rejects_observation_level_resampling`;
`test_observation_bootstrap_is_measurably_over_confident`, which measures the
wrong interval's coverage with its Monte Carlo interval and asserts the gap from
the right one exceeds Monte Carlo error. Also
`tests/test_hierarchy.py`, which refuses declarations the records contradict,
and the `observation_bootstrap` mutation test.

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

**Test.** `test_no_verdict_makes_a_forbidden_claim` (which runs the wording
audit over every verdict),
`test_wide_interval_around_zero_is_inconclusive_never_equivalent`, and the
`practical_equivalence` and `asymmetric_margin` scenarios.

---

## The shape of the answer

| Requirement | Mechanism | Refuses |
|---|---|---|
| Paired | Shared-unit panel, shared resample, conditioning reported | Silent unpaired comparison |
| Reproducible | Two fingerprints, five hashes, seed audit | Unseeded resampling |
| Event-level | Declared step-by-step roll-up, levelled metrics | Implicit pooling |
| Anti-pseudoreplication | Declared unit, checked against the records | Observation-level resampling; unchecked declarations |
| Superiority + equivalence | Five-label verdict; TOST with a required, possibly asymmetric margin | "No significant difference"; equivalence read as interchangeability |

## What would falsify the answer

- A cluster bootstrap whose measured coverage is not approximately nominal at
  realistic unit counts. Measured in `coverage_study`, and reported with a
  Monte Carlo interval so the claim is falsifiable rather than decorative.
- A red-team scenario that the protocol does not defend against. Each new
  scenario is a falsification attempt; `test_scenario_traps_fire_and_defences_hold`
  fails loudly when a defence stops working.
- A real design the declared-hierarchy machinery cannot express. Crossed random
  effects are the known case: `crossed_levels` is an error rather than a
  supported design.
