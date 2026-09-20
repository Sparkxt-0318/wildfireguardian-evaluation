# Current work

_Last updated: 2026-09-20 — `v0.1.0` frozen_

## In flight

Nothing. The v0.1.0 scientific audit is complete; see
[`../reports/V0_1_SCIENTIFIC_AUDIT.md`](../reports/V0_1_SCIENTIFIC_AUDIT.md)
and [`COMPLETED.md`](COMPLETED.md).

## Blocked, deliberately

**Integration with real Guardian output** — out of scope by instruction, and the
sequencing is the point: the protocol is settled before it meets data anyone has
a stake in. Unblocking is [`ROADMAP.md`](ROADMAP.md) R8.

## Open questions for Agent A

Unresolved specification questions, not bugs. Each blocks a roadmap item.

1. **Crossed designs.** `crossed_levels` is currently an error. A genuinely
   crossed design — two groupings that neither nest nor contain — needs either a
   crossed-effects model or an explicit statement that it is unsupported. Which?
   Blocks R10.

2. **Sub-unit-level strata.** Strata are unit-level ([`DECISIONS.md`](../docs/DECISIONS.md) D9).
   A sub-unit covariate would need the sub-unit as the primary unit, which the
   declared-hierarchy machinery now supports. Is that the answer, or do
   sub-unit strata deserve first-class support at a coarser inference level?
   Blocks R2.

3. **Standard margins per metric family.** No global default will ever exist
   (D3). Should a *project* config declare standard margins that analyses
   inherit, and what stops that becoming a de-facto default nobody chose?
   Blocks R1.

4. **More than two arms.** The comparison API is baseline-vs-candidates and runs
   each contrast independently. A genuine multi-arm design wants a declared
   contrast structure, and the multiplicity family should probably span the
   contrasts rather than only the metrics. Blocks R2.

5. **Weighted estimands.** Weights are refused (D21). If a producer declares its
   sampling design, the weighted estimand becomes well defined. What is the
   minimum declaration that would make it safe to support? Blocks R11.

## Known limitations, accepted for now

| Limitation | Effect | Mitigation in place |
|---|---|---|
| Percentile bootstrap under-covers below ~20 units | intervals narrower than their label | `few_units` warning; unit count printed beside every interval; per-estimator credibility flags |
| `max` / `min` intervals are not consistent | unreliable extreme-value inference | flagged below 30 units; documented as descriptive; `cvar` recommended |
| Bootstrap p-values are a coarse interval inversion | not an exact test | reported for multiplicity only; no verdict uses one |
| Multiplicity-adjusted intervals are not produced | adjusted p-values sit beside marginal intervals | stated in the table footnote, the family notes and `MULTIPLICITY.md` |
| Two-stage bootstrap estimates a different quantity | not comparable with one-stage intervals | off by default; every such result carries the note |
| Crossed designs are refused | a real design shape is unsupported | `crossed_levels` error names the problem; open question 1 |
| Dependence recorded in no column is invisible | A1 remains undetectable | stated in `ASSUMPTIONS.md`; detected when the grouping *is* a column |
| Full suite takes ~2 minutes | slower iteration | `pytest -m "not slow"` runs in ~25s |

## Next session

Pick up from [`ROADMAP.md`](ROADMAP.md). R2 and R10 are the highest value and
both need an Agent A decision first.
