# Current work

_Last updated: 2026-09-19_

## In flight

Nothing. The initial build is complete against its definition of done; see
[`COMPLETED.md`](COMPLETED.md).

## Blocked, deliberately

**Integration with real Guardian output** — out of scope by instruction. The
library is exercised entirely on synthetic fixtures whose truth is known by
construction. Unblocking requires a decision that integration should begin;
see [`ROADMAP.md`](ROADMAP.md) R8 for what it would involve.

## Open questions for Agent A

These are unresolved specification questions, not bugs. Each blocks a roadmap
item.

1. **Event-level stratification.** Strata are world-level today
   ([`DECISIONS.md`](../docs/DECISIONS.md) D9). Event-level covariates —
   weather at ignition, time of day — would need the event as the unit of
   inference. Is that a supported design, or does it belong to a different
   analysis entirely? Blocks R2.

2. **Standard margins per metric family.** No global default will ever exist
   (D3). Should a *project* be able to declare standard margins that configs
   inherit, and if so what stops that becoming a de-facto default nobody
   chose? Blocks R1.

3. **Declared families for multiplicity.** §9 of the protocol argues against
   correcting over a family the library cannot infer. If a config could declare
   its family explicitly, correction becomes well defined. What is the right
   syntax, and what is the default when no family is declared? Blocks R5.

4. **More than two arms.** The comparison API is baseline-vs-candidates and
   runs each contrast independently. A genuine multi-arm design wants a
   declared contrast structure. What should that look like? Blocks R2.

## Known limitations, accepted for now

| Limitation | Effect | Mitigation in place |
|---|---|---|
| Percentile bootstrap under-covers at few clusters | ~90% actual coverage for a nominal 95% interval at 20 worlds | `few_clusters` warning; cluster count printed next to every interval; measured and documented |
| `max`/`min` intervals are poorly calibrated | Unreliable extreme-value inference | Documented in [`ASSUMPTIONS.md`](../docs/ASSUMPTIONS.md) A9; `cvar` recommended instead |
| Bootstrap p-values are a coarse interval inversion | Not an exact test | Reported for completeness only; no verdict uses one |
| Red-team scenarios use one seed by default | A scenario could pass by luck | Coverage studies run 50–100 replications; scenario seed is a CLI argument |
| Full suite takes ~45s | Slower iteration | `pytest -m "not slow"` runs in ~5s |

## Next session

Pick up from [`ROADMAP.md`](ROADMAP.md). R1 and R2 are the highest value and
both need an Agent A decision first — see the open questions above.
