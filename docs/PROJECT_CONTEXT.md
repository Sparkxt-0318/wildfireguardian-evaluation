# Project context

## What this repository is

`wildfireguardian-evaluation` is a standalone statistics library. It takes a
table of experiment records with a declared nesting structure and produces
comparisons, intervals, equivalence verdicts and reports that are hard to
misread.

It exists because the interesting failures in an evaluation are almost never
arithmetic. They are structural: the wrong unit of analysis, an unpaired
comparison, a ranking driven by which runs happened to finish, a mean that
hides a tail, a large p-value read as "the same". Those failures survive
correct code and careful review, because every individual step looks right.

## What it is not

It is **not** part of WildfireGuardian. It has no model of fire spread, no
routing, no rescue logic, no simulation. It cannot produce experiment records;
it can only evaluate them. If a reader of `src/wg_eval/` can tell what domain
the data came from, that is a defect.

This independence is the point. An evaluator that shares assumptions with the
system it evaluates will reproduce those assumptions in its conclusions.

## The relationship to the systems it evaluates

```
   (any experiment producer)                 wildfireguardian-evaluation
   ------------------------                  ---------------------------
   runs worlds, events, policies    ---->    generic records table
                                              |
                                              +-- validation
                                              +-- event-level aggregation
                                              +-- paired comparison
                                              +-- cluster bootstrap
                                              +-- equivalence / non-inferiority
                                              +-- tail metrics
                                              +-- stratification
                                              +-- failure analysis
                                              +-- provenance
                                              |
                                              v
                                            a conclusion, and the reasons it
                                            is allowed to be believed
```

The interface is a file. A producer writes Parquet or CSV conforming to the
schema in `wg-eval schema`; this library reads it and never asks how it was
made. Anything that would require the evaluator to understand the producer's
internals is out of scope by construction.

## Why the nesting matters so much

A wildfire experiment produces many observations per fire. Those observations
share the fire's weather, terrain, ignition point, time of day and every other
unmodelled condition. They are correlated, often strongly.

Statistically, the consequence is that the *effective* sample size is much
closer to the number of fires than to the number of observations. The library
measures this directly: the design effect and intraclass correlation appear in
every report, and the resampling unit is the world.

In the demonstration dataset, 1,200 observations per arm carry roughly the
information of 21 independent ones. An analysis that treats them as 1,200
produces a 95% interval that covers the truth 62% of the time.

## Current state

- The library is complete for its declared scope and exercised entirely on
  synthetic fixtures whose truth is known by construction.
- Six red-team scenarios each assert that a specific misleading analysis is
  fooled and the correct one is not.
- **No real Guardian output is integrated**, deliberately. See
  [`SCOPE.md`](SCOPE.md) and [`../tasks/ROADMAP.md`](../tasks/ROADMAP.md).

## Reading order

1. [`RESEARCH_QUESTION.md`](RESEARCH_QUESTION.md) — the question
2. [`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) — the binding answer
3. [`FAILURE_MODES.md`](FAILURE_MODES.md) — what goes wrong without it
4. [`ASSUMPTIONS.md`](ASSUMPTIONS.md) — when the answer stops being valid
5. [`DECISIONS.md`](DECISIONS.md) — why it is this answer and not another
