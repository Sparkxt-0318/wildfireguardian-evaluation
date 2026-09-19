# wildfireguardian-evaluation

An independent statistical evaluation library for experiments whose
observations are **nested** — many observations inside one event, many events
inside one world — and whose policies are evaluated on the **same** worlds.

It knows nothing about wildfires. It knows nothing about forecasting, routing,
rescue, or simulation. It reads a generic table of experiment records and its
only job is to stop that table producing a misleading conclusion.

```bash
pip install -e ".[dev]"

wg-eval validate-results tests/fixtures/paired_balanced.parquet
wg-eval compare          tests/fixtures/paired_balanced.parquet tests/fixtures/analysis.yaml
wg-eval bootstrap        tests/fixtures/paired_balanced.parquet tests/fixtures/analysis.yaml
wg-eval report           tests/fixtures/paired_balanced.parquet tests/fixtures/analysis.yaml --out reports/
```

## The research question

> How should WildfireGuardian experiments be evaluated so that comparisons are
> paired, reproducible, event-level, resistant to pseudoreplication, and capable
> of supporting both superiority and equivalence conclusions?

The answer this repository implements is set out in
[`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md) and made binding by
[`docs/STATISTICAL_PROTOCOL.md`](docs/STATISTICAL_PROTOCOL.md).

## The fundamental rule

**Residents within one event are nested observations. They are not independent
wildfire experiments.**

A study with 20 worlds, 3 events each and 40 residents per event has 2,400
rows and 20 replicates. Analysing it as though it had 2,400 gives confidence
intervals several times too narrow and a nominal-95% interval that covers the
truth about 60% of the time. That is measured, not asserted —
see [the demonstration](experiments/output/DEMONSTRATION.md).

So the default resampling unit is the **world**, and
`bootstrap.cluster_level: resident` is rejected by the config loader rather
than merely discouraged.

## What the library refuses to do

| It will not | Because |
|---|---|
| Resample residents as replicates | That is pseudoreplication; the config loader rejects it |
| Conclude "no difference" from a large p-value | Absence of evidence is not evidence of absence |
| Conclude equivalence without a declared margin | The smallest difference that still matters is a domain judgement, not a statistical one |
| Compare policies across different world sets silently | That confounds policy with world difficulty; it is possible, but it is labelled `UNPAIRED` everywhere it appears |
| Report a number without its provenance | A result that cannot be reproduced cannot be checked |
| Resolve a mean-vs-tail disagreement for you | The disagreement *is* the finding |

## Input schema

One row per observation. Only the four identifiers are required; everything
else is optional, and extra columns are carried through and may be declared as
strata.

```
world_id  event_id  policy_id  resident_id
action  mission_success  loss  travel_time  resource_use  responder_exposure
failure_reason  stratum
```

`wg-eval schema` prints the full description. The nesting is
`world → event → observation`, and `policy_id` is the treatment label.

## What it does

- **Validation** — schema, dtypes, duplicate keys, broken nesting, cluster
  counts, policy coverage, and an explicit pseudoreplication ledger.
  ([`docs/VALIDATION.md`](docs/VALIDATION.md))
- **Event-level aggregation** — declared roll-up rules per column, applied
  step by step through the hierarchy, never by implicit pooling.
- **Paired comparison** — policies compared on the worlds they share, with
  every excluded world recorded.
- **Cluster bootstrap** — percentile, basic or BCa intervals from resampling
  whole worlds (or events), with the same resample applied to both arms so the
  pairing survives into the interval.
- **Equivalence and non-inferiority** — TOST against an explicit practical
  margin; one-sided non-inferiority with a direction.
- **Tail metrics** — quantiles, CVaR, trimmed means, exceedance rates, applied
  at a declared level.
- **Stratification** — per-stratum comparison plus an *allocation ledger* that
  shows whether the policies were even given comparable material.
- **Failure analysis** — failure-mode composition and the paired per-world
  shift in each mode, so an improved success rate cannot hide a worsened
  failure.
- **Provenance** — source checksum, filters, exclusions, aggregation rules,
  metric definition, bootstrap seed, confidence level and code version on
  every result.

## Red-team scenarios

Six adversarial datasets, each built so that a plausible analysis reaches a
wrong conclusion:

```bash
wg-eval redteam                 # run all six
wg-eval redteam --scenario tail_risk_disagreement
```

1. `resident_bootstrap_false_precision` — resident-level resampling manufactures precision
2. `world_bootstrap_correct` — world-level resampling is calibrated (measured over replications)
3. `tail_risk_disagreement` — the mean favours A while CVaR favours B
4. `practical_equivalence` — a real but negligible difference, provably equivalent
5. `missing_worlds_reverse_ranking` — missing worlds flip the ranking
6. `easier_worlds_confound` — a policy wins only because it was allocated easier worlds

Each scenario asserts two things: the trap fires under the wrong analysis, and
it does not fire under the right one.

## The demonstration

```bash
python experiments/run_demonstration.py --out experiments/output
```

Writes [`experiments/output/DEMONSTRATION.md`](experiments/output/DEMONSTRATION.md)
(pseudoreplication, the correct paired analysis, equivalence testing, tail
disagreement), a full routine `report.md`, and `redteam.md`.

## Documentation

| File | What it settles |
|---|---|
| [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) | What this repository is and is not |
| [`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md) | The question and the shape of the answer |
| [`docs/SCOPE.md`](docs/SCOPE.md) | Boundaries, including what stays out |
| [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) | What must be true for the numbers to mean anything |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Dated decision records with their alternatives |
| [`docs/STATISTICAL_PROTOCOL.md`](docs/STATISTICAL_PROTOCOL.md) | The binding analysis protocol |
| [`docs/METRIC_REGISTRY.md`](docs/METRIC_REGISTRY.md) | Every estimator and its exact definition |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | Every validation check and its severity |
| [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) | The errors this library exists to prevent |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | Terms, used consistently |
| [`AGENTS.md`](AGENTS.md) | How the three working roles divide the work |

## Status

No Guardian output is integrated. The library is exercised entirely on
synthetic fixtures whose truth is known by construction — see
[`tasks/CURRENT.md`](tasks/CURRENT.md) and
[`tasks/ROADMAP.md`](tasks/ROADMAP.md).

## Development

```bash
pip install -e ".[dev]"
pytest                      # full suite (~45s)
pytest -m "not slow"        # skip coverage studies and scenario replications
python tests/fixtures/generate_fixtures.py   # regenerate committed fixtures
```
