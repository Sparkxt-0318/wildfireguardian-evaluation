# wildfireguardian-evaluation

An independent statistical evaluation library for experiments whose
observations are **nested** — many observations inside one grouping, many
groupings inside one experimental unit — and whose policies are evaluated on
**shared or partially shared** units.

It knows nothing about the process that produced its input. It reads a generic
table of experiment records, and its only job is to stop that table producing a
misleading conclusion.

```bash
pip install -e ".[dev]"

wg-eval validate-results tests/fixtures/paired_balanced.parquet --config tests/fixtures/analysis.yaml
wg-eval compare          tests/fixtures/paired_balanced.parquet tests/fixtures/analysis.yaml
wg-eval ledger           tests/fixtures/failed_runs.parquet     tests/fixtures/analysis.yaml
wg-eval report           tests/fixtures/paired_balanced.parquet tests/fixtures/analysis.yaml --out reports/
```

## The research question

> How should WildfireGuardian experiments be evaluated so that comparisons are
> paired, reproducible, event-level, resistant to pseudoreplication, and capable
> of supporting both superiority and equivalence conclusions?

The answer is set out in [`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md)
and made binding by
[`docs/STATISTICAL_PROTOCOL.md`](docs/STATISTICAL_PROTOCOL.md).

## Two rules that shape everything else

**Nested observations are not replicates.** A study with 20 units, 3 groupings
each and 40 observations per grouping has 2,400 rows and 20 replicates.
Resampling rows instead of units gives intervals several times too narrow.

**The unit of inference is declared, and then checked against the data.**

```yaml
inference:
  primary_unit: world_id
  nested_units: [event_id, resident_id]
```

Distinct identifiers are not evidence of independence. If a column strictly
contains the declared unit — units that share a batch, a seed family, or a
higher-level shock — that is a hard failure, not a warning. A design where one
event spans many units is supported; it just has to be declared the other way
round. See [`docs/ESTIMANDS.md`](docs/ESTIMANDS.md) for what each declaration
makes the numbers mean.

## What the library refuses to do

| It will not | Because |
|---|---|
| Resample observations as replicates | That is pseudoreplication; the config loader rejects it |
| Accept a declared structure the records contradict | Distinct ids are not evidence of independence |
| Conclude "no difference" from a large p-value | Absence of evidence is not evidence of absence |
| Conclude equivalence without a declared margin | The smallest difference that matters is a domain judgement |
| Imply that statistical equivalence means interchangeability | Those are different claims with different falsifiers |
| Pick a tail for you | A CVaR's tail follows the metric's declared orientation, or is stated explicitly |
| Apply a multiplicity correction you did not declare | Correcting over the wrong family is worse than not correcting |
| Apply unit weights | A sampling weight and an intra-unit probability cannot be told apart from a column of numbers |
| Clamp an interval into a metric's bounds | That narrows the interval without fixing the construction |
| Let a failed run leave the denominator silently | A policy must not improve its score by failing to produce results |
| Call a reversal "confounding" | A reversal is arithmetic; its cause is not established by the arithmetic |
| Report a number without its provenance | A result that cannot be reproduced cannot be checked |
| Name an overall winner | It compares one metric at a time |

## Input schema

One row per observation. Four identifiers are required; everything else is
optional, and extra columns are carried through and may be declared as strata.

```
world_id  event_id  policy_id  resident_id
run_status
action  mission_success  loss  travel_time  resource_use  responder_exposure
failure_reason  stratum
```

`wg-eval schema` prints the full description. Column names are part of the
interface contract and are treated as opaque identifiers throughout.

## What it does

- **Declared inference structure** — the primary unit and its nested levels,
  verified against the records; inverted, crossed and undeclared coarser
  groupings are errors.
- **Validation** — 19 error codes, 21 warnings, 5 info, including run status,
  metric bounds, orientation and stratum redundancy.
- **Aggregation** — declared roll-up rules per column per step, never an
  implicit pool.
- **Paired comparison** — on shared units, with the estimand's conditioning
  stated, plus a check of whether the excluded units resemble the shared ones.
- **Cluster bootstrap** — percentile, basic or BCa at the declared unit, with an
  optional two-stage variant; the methods are documented as *not*
  interchangeable, and studentized is refused rather than substituted.
- **Coverage validation** — every simulated coverage figure carries its
  replication count, Monte Carlo SE and Wilson interval.
- **Equivalence and non-inferiority** — TOST against a required, possibly
  asymmetric margin with a recorded source; every finding carries the
  interchangeability caveat.
- **Orientation-aware tails** — quantiles, CVaR and exceedance rates whose tail
  follows the metric's declared direction.
- **Small-sample honesty** — per-estimator credibility thresholds, not a
  universal minimum n.
- **Multiplicity** — families declared by analysis role; Holm, Bonferroni or a
  declared none; p-values adjusted, intervals marginal and labelled.
- **Run status** — failed, timed-out, infeasible and unevaluated runs recorded,
  handled as declared, and counted in a ledger before any handling.
- **Stratification** — per-stratum contrasts, an allocation ledger, redundancy
  diagnostics, and disagreement reporting that does not claim a cause.
- **Provenance** — two fingerprints and five hashes, so a regenerated report and
  a re-analysis are distinguishable.
- **Enforced wording** — generated prose may not claim that a comparison proves
  anything, that options are the same, that either is safe, or that one is
  better overall.

## Red-team scenarios

Sixteen adversarial datasets, each built so a plausible analysis reaches a wrong
conclusion, and each asserting both that the trap fires under the wrong analysis
and that it does not under the right one:

```bash
wg-eval redteam
wg-eval redteam --scenario wrong_cvar_tail
```

| Scenario | What it traps |
|---|---|
| `observation_bootstrap_false_precision` | resampling observations manufactures precision |
| `unit_bootstrap_calibration` | a bare coverage percentage read as exact |
| `tail_risk_disagreement` | the mean favours A while the harmful tail favours B |
| `practical_equivalence` | a zero-containing interval read as sameness |
| `missing_units_reverse_ranking` | missing units flip the ranking |
| `easier_units_confound` | a policy allocated easier material |
| `dependence_above_declared_unit` | units sharing a higher-level shock |
| `outcome_dependent_missingness` | runs that crash on the hardest units |
| `too_few_units` | a 90% CVaR from five units |
| `wrong_cvar_tail` | a risk statistic describing the good case |
| `asymmetric_margin` | a symmetric margin hiding an unacceptable loss |
| `bounded_outcome_interval` | an interval running past the end of the scale |
| `uncorrected_family` | twenty null metrics, uncorrected |
| `failed_runs_as_missing` | a crashed run counted as a missing value |
| `paired_data_analysed_unpaired` | paired data compared arm-to-arm |
| `metric_selected_after_the_fact` | the headline chosen after seeing the results |

## Mutation audit

The suite must be able to fail. Eleven deliberate statistical errors are
introduced and each must be caught:

```bash
python experiments/run_mutation_audit.py --out reports
```

Results in [`reports/MUTATION_TEST_AUDIT.md`](reports/MUTATION_TEST_AUDIT.md).

## The demonstration

```bash
python experiments/run_demonstration.py --out experiments/output
```

Writes [`experiments/output/DEMONSTRATION.md`](experiments/output/DEMONSTRATION.md),
a full routine `report.md`, and `redteam.md`.

## Documentation

| File | What it settles |
|---|---|
| [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) | What this repository is and is not |
| [`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md) | The question and the shape of the answer |
| [`docs/ESTIMANDS.md`](docs/ESTIMANDS.md) | What each number actually estimates |
| [`docs/STATISTICAL_PROTOCOL.md`](docs/STATISTICAL_PROTOCOL.md) | The binding analysis protocol |
| [`docs/EQUIVALENCE_AND_NONINFERIORITY.md`](docs/EQUIVALENCE_AND_NONINFERIORITY.md) | Difference, equivalence and non-inferiority, kept apart |
| [`docs/MULTIPLICITY.md`](docs/MULTIPLICITY.md) | Declared families and corrections |
| [`docs/METRIC_REGISTRY.md`](docs/METRIC_REGISTRY.md) | Every estimator, with hand-checkable definitions |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | Every validation check and its severity |
| [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) | The twenty errors this library prevents |
| [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) | What must be true, and what is detectable |
| [`docs/SCOPE.md`](docs/SCOPE.md) | Boundaries, including what stays out |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Dated decision records with rejected alternatives |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | Terms, used consistently |
| [`reports/V0_1_SCIENTIFIC_AUDIT.md`](reports/V0_1_SCIENTIFIC_AUDIT.md) | The v0.1.0 release audit |
| [`AGENTS.md`](AGENTS.md) | How the three working roles divide the work |

## Status

`v0.1.0`. No Guardian output is integrated; the library is exercised entirely on
synthetic fixtures whose truth is known by construction. See
[`reports/V0_1_SCIENTIFIC_AUDIT.md`](reports/V0_1_SCIENTIFIC_AUDIT.md) for what
the library does and does not support, and
[`tasks/ROADMAP.md`](tasks/ROADMAP.md) for what comes next.

## Development

```bash
pip install -e ".[dev]"
pytest                      # full suite
pytest -m "not slow"        # skip coverage studies and scenario replications
python tests/fixtures/generate_fixtures.py   # regenerate committed fixtures
```
