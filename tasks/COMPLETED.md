# Completed

## 2026-09-20 — v0.1.0 scientific audit

An adversarial review of the initial build, correcting what it found, and
freezing `v0.1.0`. ~11,400 lines across 22 modules, 325 tests.

Release artefact: [`../reports/V0_1_SCIENTIFIC_AUDIT.md`](../reports/V0_1_SCIENTIFIC_AUDIT.md).

### Defects the audit found

Five, each found by a different mechanism, each recorded as a decision.

1. **`comparison.paired` was parsed and never read.** A config declaring
   `paired: false` received a paired analysis under an unpaired label. Found by
   the paired/unpaired red-team scenario, which could not make the "unpaired"
   analysis behave differently. Fixed; D14. **Rule adopted: every configuration
   key must be read by something.**
2. **CVaR's `k` was floating-point fragile.** `(1-0.7)*10` is
   `3.0000000000000004`, so `ceil` gave 4 and the estimator averaged one value
   more than its own documented definition, for many ordinary `(alpha, n)`
   pairs. Found by a hand-computed reference test; review could not have caught
   it because the code matched the spec exactly. Fixed; D16. **Rule adopted:
   production code is not its own oracle.**
3. **The previous red-team's coverage claims were overstated.** "90% at 20
   worlds (nominal 95%)" from R=100 has a Wilson interval of [82.6%, 94.5%],
   which excludes 95%; the "98% at 60 worlds" figure came from R=50 and was far
   too imprecise for the comparison built on it. The numbers were right, the
   claims were not. Fixed; D15.
4. **The generic `stratum` column was auto-added to declared strata**, so the
   new redundancy detector correctly reported it as nested inside every real
   stratum. True and useless; now checked only when nothing else is declared.
5. **The redundancy detector reported nesting direction backwards.** Found by a
   test that asserted on the direction rather than on the finding's presence.

### What was added

| Area | Delivered |
|---|---|
| Estimands | `docs/ESTIMANDS.md`: six parts, the paired formalisation, the conditioned complete-case estimand, pooled functionals, weights refused with reasons |
| Inference structure | `hierarchy.py`: declared primary unit and nested levels, checked against the records; inverted, crossed, undeclared-coarser and degenerate findings |
| Resampling | two-stage bootstrap with its estimand stated; an actually-unpaired resample; studentized refused by name; per-method "do not trust when" table |
| Coverage | `coverage.py`: Monte Carlo SE, Wilson intervals, `consistent_with_nominal`, `replications_for_precision`, `compare_coverage` |
| Small samples | per-estimator credibility thresholds and reasons, surfaced on every result and in reports |
| Orientation | `direction` drives `harmful_side`; `tail: harmful` resolves against it; contradictory `params.tail` rejected at load |
| Tail definitions | interpolation, ties, weighting, finite-sample `k`, minimum samples — all documented and hand-checked |
| Equivalence | asymmetric margins, `source` provenance, `scale`, the decision-equivalence caveat on every finding |
| Bounds | declared support per metric; values outside are errors; interval endpoints outside are reported, never clamped |
| Missing data | run-status taxonomy, declared handling, mechanisms, ledger before handling, overlap/representativeness check |
| Allocation | ledger with units per policy, shared/unique, nested counts, strata composition, status and failure counts, composition shift |
| Strata | redundancy diagnostics; reversal wording that describes arithmetic rather than diagnosing confounding; support thresholds |
| Multiplicity | `multiplicity.py`: declared families by analysis role, Holm, Bonferroni, declared none; intervals left marginal and labelled |
| Roles | one primary metric enforced; report ordered primary → secondary → exploratory |
| Preregistration | `analysis_status` plus protocol hash/commit/timestamp; never inferred from a config file |
| Reproducibility | two fingerprints, five-hash manifest, seed/order/fingerprint audits |
| Wording | `audit_wording` with a phrase-level list, enforced in tests and by `wg-eval report` |
| Red team | 16 scenarios (10 new), each stating what the defence *cannot* conclude |
| Mutation | `mutation.py`: 11 deliberate statistical errors, all detected |
| Domain independence | a vocabulary scan over every module, plus generic strata (`difficulty`, `scale`, `regime`, `capacity`) |

### Test suites

| File | Covers |
|---|---|
| `test_hierarchy.py` | declared structure, containment detection, refusals |
| `test_schema_and_validation.py` | 19 error codes, 21 warnings, 5 info |
| `test_metrics_and_aggregation.py` | estimators, roll-up, filters, missing data, config rules |
| `test_pairing_and_bootstrap.py` | pairing, cluster/two-stage/unpaired resampling, credibility |
| `test_equivalence.py` | TOST, asymmetric margins, non-inferiority, verdict vocabulary |
| `test_compare_and_stratify.py` | end to end, allocation ledger, redundancy, disagreement |
| `test_coverage_uncertainty.py` | Wilson against scipy, MC SE, precision planning |
| `test_reference_calculations.py` | hand-computed and scipy-checked references |
| `test_reproducibility.py` | seeds, ordering, fingerprint audit, version semantics |
| `test_domain_independence.py` | vocabulary scan over the statistical core |
| `test_mutation.py` | 11 mutants, all required to be detected |
| `test_io_provenance_report.py` | round trips, manifest, wording audit |
| `test_cli.py`, `test_fixtures.py`, `test_redteam.py` | surfaces and fixtures |

### The sixteen red-team scenarios

All pass in both directions: the trap fires under the wrong analysis, and does
not under the right one.

| Scenario | Wrong analysis says | Right analysis says |
|---|---|---|
| `observation_bootstrap_false_precision` | interval ~4× too narrow, ~60% coverage | correct width, coverage measurably better |
| `unit_bootstrap_calibration` | a bare percentage read as exact | coverage with its Wilson interval, improving with units |
| `tail_risk_disagreement` | "recommend A" | mean favours A, CVaR favours B, reported as a conflict |
| `practical_equivalence` | "no significant difference" | equivalent within ±0.40, with the interchangeability caveat |
| `missing_units_reverse_ranking` | −1.86 favouring B | +0.95 favouring A, conditioned and flagged |
| `easier_units_confound` | +3.22 favouring A | −1.69 favouring B; every stratum agrees |
| `dependence_above_declared_unit` | refused; 78.7% [71.4%, 84.5%] if forced | 93.3% [88.2%, 96.3%] at the event level |
| `outcome_dependent_missingness` | +0.97 over 32 of 50 units | bounded by +4.17 worst case against a truth of +1.0 |
| `too_few_units` | a 90% CVaR from 5 units | flagged not credible; the mean is not |
| `wrong_cvar_tail` | −0.043 favouring A (upper tail) | +0.536 favouring B (harmful lower tail) |
| `asymmetric_margin` | equivalent at ±0.50 | not equivalent at (−0.50, +0.15) |
| `bounded_outcome_interval` | basic interval reaching 1.0025 | violation reported, never clamped |
| `uncorrected_family` | 1 of 20 null metrics resolves | 0 survive Holm over the declared family |
| `failed_runs_as_missing` | +0.064 favouring B | −0.159 favouring A when crashes count as failures |
| `paired_data_analysed_unpaired` | inconclusive, 7× wider | resolved, and covering the truth |
| `metric_selected_after_the_fact` | best of 40 noise metrics, raw p = 0.052 | primary resolves the other way; roles enforced |

### Not done, deliberately

- **No Guardian output integrated.** Out of scope by instruction; ROADMAP R8.
- **No multiplicity-adjusted intervals.** A Holm-adjusted region is not an
  interval; shipping one means choosing a simultaneous construction and saying
  which. ROADMAP R5.
- **No studentized bootstrap.** Needs a nested resample for CVaR; rejected by
  name rather than substituted. ROADMAP R9.
- **No crossed-design support.** `crossed_levels` is an error, and the right
  answer is an open question. ROADMAP R10.
- **No weighted estimands.** Refused until a sampling design is declared.
  ROADMAP R11.

---

## 2026-09-19 — Initial build

The library, its documentation, its adversarial suite and its demonstration.
~6,200 lines across 19 modules, 152 tests.

Delivered the definition of done: generic schema validator, event-level
aggregation, paired comparisons, cluster bootstrap, equivalence and
non-inferiority, tail metrics, stratification, six red-team scenarios, and
reproducible report generation.

Three defects were found during that build and recorded at the time: a tail
scenario whose trap did not fire, successes counted as a failure mode
([`DECISIONS.md`](../docs/DECISIONS.md) D10), and perfectly collinear declared
strata (D12). The v0.1.0 audit above found five more that this build had
shipped.
