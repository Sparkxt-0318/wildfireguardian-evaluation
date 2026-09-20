# Estimands

> Name the estimand before the estimator.

"Mean loss" is not an estimand. An estimand says *what quantity, over what
population of units, after what aggregation, under what contrast*. Two analyses
of the same table that differ on any of those four are estimating different
things, and reconciling their numbers is a category error rather than a
debugging problem.

Every comparison this library produces records its estimand in the provenance
block, under `estimand`.

---

## The six parts

A comparison here is specified by six things. The first five determine what the
number means; the sixth determines how it is computed.

| # | Part | Declared in | Recorded as |
|---|---|---|---|
| 1 | Target population of units | prose, by the analyst | `estimand.unit` plus the conditioning note |
| 2 | Observed / shared population | derived from the data | `estimand.conditioning` |
| 3 | Per-unit aggregation | `aggregation`, `metrics[].level` | `aggregation`, `estimand.aggregation_level` |
| 4 | Policy contrast | `comparison` | `estimand.contrast` |
| 5 | Estimand | the four above, together | this document |
| 6 | Estimator | `metrics[].estimator`, `bootstrap` | `metric.definition`, `bootstrap` |

### 1. Target population of units

The population of experimental units the conclusion is meant to generalise to:
"worlds drawn from the generator with these settings", "incidents of this class
in this region and season", "the 600 scenarios in the benchmark". The library
cannot know this. It knows which units are in the file.

**This is the part most often left unstated, and the part that most often makes
a correct calculation useless.** A perfectly estimated contrast over the units
that happened to run is not an estimate for the population somebody has in mind
unless those units stand in for it.

### 2. Observed and shared population

Two subsets matter and they are usually different:

* **Observed**: units that appear in the file at all.
* **Shared**: units observed under *every* policy in the contrast.

The paired analysis runs on the shared set. `panel.coverage_fraction` reports
the ratio, and `panel.estimand_conditioning` states it in words.

### 3. Per-unit aggregation

The function `g` that turns a unit's raw observations into one value. It is the
declared roll-up chain, one step per level:

```
observations --(resident_id->event_id rule)--> events --(event_id->world_id rule)--> unit value
```

`g` is not unique and the choice changes the estimand. A per-observation mean
weights events by size; a two-step mean weights events equally. On the worked
example in `tests/test_reference_calculations.py` the same five numbers give
12.5 one way and 12.0 the other.

### 4. Policy contrast

`candidate - baseline`, on the metric's scale, at the metric's level.

---

## The paired estimand, formally

Let `i` index experimental units and let `Y_{i,P}` be the observations recorded
for unit `i` under policy `P`. Let `g` be the declared aggregation. Define the
per-unit contrast

```
    Delta_i  =  g(Y_{i,A})  -  g(Y_{i,B})
```

and the estimand

```
    theta  =  E[ Delta_i ]
```

**The expectation is over the population of units named in part 1**, not over
observations, not over events, and not over the particular units in the file.
That is the whole content of "the unit of inference is the world": it names the
index the expectation runs over.

The estimator is the sample mean of `Delta_i` over the units used, and its
uncertainty comes from resampling those units.

### What the library actually estimates

When pairing restricts to shared units — the default — the estimand is **not**
`theta`. It is

```
    theta_shared  =  E[ Delta_i  |  unit i observed under every compared policy ]
```

The library says so rather than letting `theta_shared` be read as `theta`. In
the provenance:

```
estimand:
  contrast: policy_b - policy_a
  unit: world_id
  aggregation_level: event_id
  conditioning: world_ids observed under every compared policy (38 of 60)
```

and in the report, next to the finding.

`theta_shared = theta` when overlap is independent of the outcome. That is an
assumption, not a fact, and it is checkable in one direction: if the excluded
units differ from the shared ones on the metric, the assumption is false. The
library performs that check (`diagnostics.overlap`) and flags a standardized
shift above 0.2. Passing the check is not proof; failing it is disproof.

See `docs/FAILURE_MODES.md` F2 and scenario
`missing_units_reverse_ranking` for what happens when this is ignored.

---

## Pooled estimands

Not every metric decomposes per unit. A 90% CVaR over events is a functional of
the pooled distribution of event values; there is no `Delta_i` for it.

For these, the estimand is

```
    theta  =  T( F_A )  -  T( F_B )
```

where `T` is the estimator functional (CVaR, quantile, exceedance rate) and
`F_P` is the distribution of level-`L` values under policy `P`, over the target
population of units.

Pairing still applies, and it applies in the resampling rather than in the
statistic: each bootstrap replicate draws one set of units and evaluates **both**
`T(F_A)` and `T(F_B)` on it, so unit-level noise common to the two arms cancels
in the difference. The point estimate is a difference of two pooled statistics;
the interval is paired.

---

## Estimands by metric level

The level changes the population the estimator's `F` is taken over.

| `level` | `F` is the distribution of | Typical estimand |
|---|---|---|
| `resident_id` | raw observations | a rate over the observation population |
| `event_id` | event values after the first roll-up | a per-event quantity |
| `world_id` | unit values after the full roll-up | a per-unit quantity |

A metric may not sit at a level coarser than the resampling unit: a unit-level
value cannot be assigned to one of several sub-units. The config loader refuses
it by name.

---

## Estimands under run failure

When runs do not complete, the estimand acquires a further condition, and which
one depends on the declared `status_handling`:

| Handling | Estimand becomes |
|---|---|
| `completed` | unchanged |
| `failure` | over all attempted runs, with a failed run scored as a failed observation |
| `excluded_documented` | conditional on the run being feasible |
| `missing` | conditional on the run having produced a value (a complete-case estimand) |

These are different quantities. Scenario `failed_runs_as_missing` shows the
same records giving opposite findings under two of them. The library does not
choose; it records the choice and reports the ledger either way.

---

## Weighted units

**Not supported.** A `weights` key in a configuration raises, and a
weight-looking column in the records is reported and ignored.

The reason is that two different quantities share the word:

* **A sampling weight across units.** Unit `i` stands for `w_i` units of the
  target population, because units were sampled with unequal probability. This
  changes the estimand to `theta_w = sum(w_i * Delta_i) / sum(w_i)`, and it
  requires knowing the sampling design.
* **A scenario probability inside one unit.** An observation within a unit is
  more likely than another. This changes `g`, the per-unit aggregation, and
  belongs in the roll-up rules.

Applying the first where the second was meant silently re-weights the
population; applying the second where the first was meant silently re-weights
within units. Neither error announces itself, and a library cannot tell them
apart from a column of numbers. So it refuses both, and the analyst either
encodes an intra-unit probability in the aggregation rules or states the
sampling design and computes the weighted estimand outside this library.

---

## Writing the estimand down

Before running an analysis, complete this sentence:

> We estimate the **[mean / 90% CVaR / …]** of the **[per-event / per-unit]**
> difference in **[metric]** between **[candidate]** and **[baseline]**, over
> the population of **[target units]**, using the **[N]** units observed under
> both policies, having aggregated observations to events by **[rule]** and
> events to units by **[rule]**.

If any bracket cannot be filled, the analysis is not specified yet. Everything
after the word "using" is what the library fills in and checks; everything
before it is the analyst's to state.
