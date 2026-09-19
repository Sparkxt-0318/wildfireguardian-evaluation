# Completed

## 2026-09-19 — Initial build

The library, its documentation, its adversarial suite and its demonstration.
~6,200 lines across 19 modules, 152 tests.

### Definition of done

| Requirement | Delivered | Where |
|---|---|---|
| Generic schema validator | 12 error codes, 9 warnings, 3 info, plus a structural summary | `validate.py`, `docs/VALIDATION.md` |
| Event-level aggregation | Declared two-step roll-up with per-column rules | `aggregate.py` |
| Paired comparisons | Shared-cluster panel; every exclusion recorded; `UNPAIRED` labelling | `pairing.py`, `compare.py` |
| Bootstrap | Cluster bootstrap at world/event level; percentile, basic, BCa; one resample shared by both arms | `bootstrap.py` |
| Equivalence / non-inferiority | TOST and one-sided NI, margin required, five-label verdict vocabulary | `equivalence.py` |
| Tail metrics | `quantile`, `cvar`, `trimmed_mean`, `p_exceed`, `max`, `min` | `metrics.py`, `docs/METRIC_REGISTRY.md` |
| Stratification | Per-stratum comparison, allocation ledger, heterogeneity detection, weighted estimate | `stratify.py` |
| Red-team examples | Six scenarios, each asserting trap-fires and defence-holds | `synth/redteam.py` |
| Reproducible reports | Markdown, JSON, CSV with provenance on every result | `report.py`, `provenance.py` |

### The six red-team scenarios

Each was tuned until the misleading analysis actually reaches the wrong
conclusion — a scenario whose trap does not fire tests nothing.

| Scenario | Truth | Wrong analysis says | Right analysis says |
|---|---|---|---|
| `resident_bootstrap_false_precision` | +0.60, 20 worlds | interval 3.9× too narrow, 62% coverage | correct width, 90% coverage |
| `world_bootstrap_correct` | +0.60 | (calibration check) | 90% coverage at 20 worlds, 98% at 60 |
| `tail_risk_disagreement` | A better on mean, worse on tail | "recommend A" | mean favours A, CVaR favours B — reported as a conflict |
| `practical_equivalence` | +0.03, margin 0.40 | "no significant difference" | equivalent within ±0.40 |
| `missing_worlds_reverse_ranking` | B is +1.2 worse | −1.86, favouring B | +0.95, favouring A |
| `easier_worlds_confound` | B is 1.5 better | +3.22, favouring A | −1.69, favouring B; every stratum agrees |

### What was built

**Library** — `schema`, `validate`, `dataio`, `config`, `metrics`, `aggregate`,
`pairing`, `bootstrap`, `equivalence`, `compare`, `stratify`, `failures`,
`provenance`, `report`, `cli`, `version`, `synth/generators`, `synth/redteam`.

**CLI** — `validate-results`, `compare`, `bootstrap`, `report`, plus `schema`,
`metrics`, `init-config`, `synth`, `redteam`.

**Docs** — `PROJECT_CONTEXT`, `RESEARCH_QUESTION`, `SCOPE`, `ASSUMPTIONS`,
`DECISIONS` (12 records), `STATISTICAL_PROTOCOL` (11 sections),
`METRIC_REGISTRY`, `VALIDATION`, `FAILURE_MODES` (10 modes), `GLOSSARY`, plus
`README` and `AGENTS`.

**Fixtures** — `paired_balanced.parquet`, `missing_worlds.csv`,
`invalid_records.csv`, `analysis.yaml`, `MANIFEST.json`, regenerable from
`generate_fixtures.py` and checked for drift by `test_fixtures.py`.

**Demonstration** — `experiments/run_demonstration.py` produces
`DEMONSTRATION.md`, a full `report.md` and `redteam.md`.

### Things that went wrong during the build

Recorded because each is a live example of a failure this library is meant to
catch, and each was caught by measurement rather than by reading the code.

1. **The tail scenario did not trap.** The first parameterisation gave policy_a
   a +2.1 expected catastrophe cost against a −2.2 shift, so its mean was
   *worse* and the mean-only analysis reached the right answer by accident.
   Found by `trap_reproduced=False`, not by inspection. Retuned to a −3.2 shift
   with a 4%×60 catastrophe, giving a genuine 0.85 mean advantage against a 2.7
   CVaR disadvantage.

2. **Successes were counted as a failure mode.** The paired failure-shift table
   bucketed successful observations as `unspecified` and reported them as an
   83% "failure mode". Fixed so the numerator counts only failures while the
   denominator counts all observations — recorded as
   [`DECISIONS.md`](../docs/DECISIONS.md) D10.

3. **Declared strata were perfectly collinear.** Round-robin assignment made
   every `flat` world also `high` mobility, so a per-stratum result looked
   informative and was not. Replaced with a full-factorial layout —
   [`DECISIONS.md`](../docs/DECISIONS.md) D12.

### Not done, deliberately

- **No real Guardian output integrated.** Out of scope by instruction; see
  [`ROADMAP.md`](ROADMAP.md) R8.
- **No multiplicity correction.** Requires a declared family the library cannot
  infer; visibility is provided instead —
  [`STATISTICAL_PROTOCOL.md`](../docs/STATISTICAL_PROTOCOL.md) §9,
  [`DECISIONS.md`](../docs/DECISIONS.md) D8.
- **No parametric models, Bayesian estimation, power analysis or plots.**
  Reasons and conditions in [`SCOPE.md`](../docs/SCOPE.md) and
  [`ROADMAP.md`](ROADMAP.md).
