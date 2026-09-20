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

It is **not** part of WildfireGuardian. It has no model of the process that
produced its input, and it cannot produce experiment records; it can only
evaluate them. If a reader of `src/wg_eval/` can tell what domain the data came
from, that is a defect — and it is a *tested* defect:
`tests/test_domain_independence.py` scans every module for domain vocabulary.

This independence is the point. An evaluator that shares assumptions with the
system it evaluates will reproduce those assumptions in its conclusions.

## The relationship to the systems it evaluates

```
   (any experiment producer)                 wildfireguardian-evaluation
   ------------------------                  ---------------------------
   runs worlds, events, policies    ---->    generic records table
                                              |
                                              +-- declared structure, checked
                                              +-- validation
                                              +-- declared aggregation
                                              +-- paired comparison
                                              +-- cluster bootstrap
                                              +-- equivalence / non-inferiority
                                              +-- oriented tail metrics
                                              +-- multiplicity over declared families
                                              +-- run status and missing data
                                              +-- stratification and allocation
                                              +-- failure analysis
                                              +-- provenance and manifest
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

An experiment of the kind this library evaluates produces many observations per
unit. Those observations share everything about the unit that nobody modelled.
They are correlated, often strongly.

The consequence is that the *effective* sample size is much closer to the number
of units than to the number of observations. The library measures this directly:
the design effect and intraclass correlation appear in every report, and the
resampling unit is the one the configuration declared and the records confirmed.

The magnitude is bounded, and the bound is worth knowing because the folk
version of the rule is too strong. Writing `sigma_I` for the unit-by-policy
interaction, `V_E` for the paired sub-unit variance and `E` for sub-units per
unit, the variance ratio between an observation-level and a unit-level paired
bootstrap is `(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)`. It tends to `1/E`
when units differ strongly in how they respond to the policies, and to **1**
when they do not. Pseudoreplication is not always costly; it is costly exactly
when the units are heterogeneous in their response, which is when the comparison
is interesting.

## Why the structure is declared rather than assumed

An earlier version of this library hardwired `world_id` as the outermost level.
That cannot express a design where one grouping spans many units — a shared
shock above the unit — and on such data a unit-level cluster bootstrap covers
78.7% of the time against a nominal 95%, while the correct event-level one
covers 93.3%. "Cluster bootstrap" is not a synonym for "correct".

The structure is now declared and then falsified against the records. Identifiers
that differ are not evidence of independence.

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
