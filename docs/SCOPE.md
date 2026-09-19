# Scope

## In scope

| Area | What is included |
|---|---|
| Schema validation | Structure, dtypes, keys, nesting, cluster counts, policy coverage, strata |
| Aggregation | Declared two-step roll-up: observation → event → world |
| Point estimation | Means, medians, sums, quantiles, CVaR, trimmed means, exceedance rates, counts |
| Uncertainty | Cluster bootstrap (percentile, basic, BCa) at world or event level |
| Comparison | Paired differences between policies on shared clusters |
| Hypothesis framing | Superiority, equivalence (TOST), non-inferiority |
| Stratification | Per-stratum comparison, allocation ledger, heterogeneity detection |
| Failure analysis | Failure-mode composition and paired per-world shifts |
| Reporting | Markdown, JSON and CSV, with full provenance |
| Adversarial testing | Synthetic scenarios with known truth |
| CLI | `validate-results`, `compare`, `bootstrap`, `report`, plus support commands |

## Out of scope

### Permanently out — domain knowledge

Nothing in `src/wg_eval/` may know about fire spread, weather, routing,
evacuation, rescue, or simulation. No wildfire-specific defaults, thresholds,
column semantics or metric names. A reader of the source should not be able to
tell what domain the data came from.

This is the property that makes the library trustworthy as an evaluator: it
cannot be tuned, even unconsciously, toward the conclusion the producing system
would prefer.

### Permanently out — producing data

The library evaluates records. It does not run experiments, call models, or
simulate anything. The synthetic generators exist only to create data with a
*known statistical truth* for testing; they model no real process and make no
claim to.

### Out for now — with reasons

| Not included | Why | Where it would go |
|---|---|---|
| Parametric mixed models (`lmer`-style) | The bootstrap answers the same questions with fewer distributional assumptions. Worth adding as a cross-check, not as the primary method. | ROADMAP R4 |
| Multiplicity correction | Requires a declared family the library cannot infer. The family is made visible instead. | Protocol §9 |
| Bayesian estimation | A coherent alternative, but mixing two inferential frameworks in one report invites cherry-picking between them. | ROADMAP R5 |
| Sequential / group-sequential designs | Needs a stopping rule declared before data collection, which is a property of the producer's process, not of this library. | ROADMAP R6 |
| Causal identification beyond pairing | Pairing handles world-level confounding. Within-world confounding needs a design this library cannot see. | ASSUMPTIONS A3 |
| Power and sample-size planning | Natural and useful, and it needs a variance-component model this library does not yet fit. | ROADMAP R3 |
| Plots | The report is deliberately text-first so it diffs and reviews cleanly. | ROADMAP R7 |
| Integration with real Guardian output | Explicitly deferred by the current brief. | ROADMAP R8 |

## Boundary rules

Three questions decide whether something belongs here.

**1. Would this make sense for a drug trial or an A/B test?**
If no, it is domain knowledge and belongs in the producer.

**2. Does it help prevent a misleading conclusion, or does it produce a number?**
Producing a number is not enough. Every feature should close a specific failure
mode, and the failure mode should be named in
[`FAILURE_MODES.md`](FAILURE_MODES.md).

**3. Can it be tested against a known truth?**
If a feature cannot be checked on synthetic data whose answer is known by
construction, it cannot be red-teamed, and an untestable statistical claim is
not one this library should make.

## Interface contract

Input is a file on disk — Parquet, CSV, TSV or JSONL — conforming to the schema
printed by `wg-eval schema`, plus a YAML analysis configuration.

Output is a `ComparisonResult` (in Python), or Markdown, JSON and CSV (from the
CLI). Every output carries the provenance needed to regenerate it.

The library never reaches back to the producer. If an analysis needs something
the file does not contain, the answer is a clear error naming the missing
column, not an inference.
