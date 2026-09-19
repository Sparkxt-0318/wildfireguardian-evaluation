# Statistical protocol

This protocol is binding. Where the library and this document disagree, the
document is right and the library has a bug. Changes require a decision record
in [`DECISIONS.md`](DECISIONS.md).

---

## 1. Unit of inference

**The unit of inference is the world.**

A world is one independent replicate of whatever generated the data: one
scenario instance, one simulated or observed setting, drawn from the population
of worlds the study means to generalise to. Events are nested in worlds.
Observations ("residents") are nested in events.

```
world_id          <- the unit of inference, the resampling unit
  event_id        <- nested; may be the unit of inference if worlds are absent
    resident_id   <- an observation. NEVER a replicate.
```

The unit may be changed to `event` only when events are themselves the
independent replicates — that is, when there is no meaningful world-level
grouping, or when each world contains exactly one event. Setting
`unit_of_inference: event` while worlds contain many correlated events
reintroduces the same error one level down.

`unit_of_inference: resident` does not exist. The configuration loader raises.

**Consequence for power.** Precision comes from *worlds*. Doubling the number
of residents per event narrows the interval only through the small
within-event variance term; doubling the number of worlds narrows it by
roughly √2. A study that needs more power needs more worlds.

## 2. Nesting and what may never be a replicate

Observations within one event share everything about that event. Events within
one world share everything about that world. Both correlations are real and
usually large: the demonstration data has an intraclass correlation around
0.45–0.89 at the event level.

The structural consequences the protocol enforces:

- **Resampling** draws whole worlds with replacement. Every observation inside
  a drawn world travels with it, including into a repeated draw.
- **Aggregation** proceeds one level at a time with a declared rule per column.
- **Validation** rejects an `event_id` that appears under more than one
  `world_id`, because then the nesting is undefined.
- **Counting** never reports observations as a sample size without also
  reporting the cluster count and the design effect.

## 3. Aggregation

Two declared steps:

```yaml
aggregation:
  resident_to_event: {loss: mean, resource_use: sum, mission_success: mean}
  event_to_world:    {loss: mean, mission_success: mean}
  default_rule: mean
```

Supported rules: `mean`, `sum`, `median`, `max`, `min`, `any`, `all`, `first`.

The default is `mean`, and the default is a *choice*, not an absence of one:
it weights every event equally regardless of size. For an extensive quantity
(total resource consumed, total responder-hours) `sum` is usually right at the
`resident_to_event` step. Getting this wrong changes the estimand, not just
the number.

A metric declares the level it pools over:

| level | The estimator sees | Typical use |
|---|---|---|
| `resident` | one value per observation | rates over the observation population |
| `event` | one value per (world, event, policy) | the default; "per fire" quantities |
| `world` | one value per (world, policy) | world-level summaries |

A metric's level may not be coarser than the resampling unit. `compare_policies`
raises with an explanation if it is, because a world-level value cannot be
assigned to one of several events.

## 4. Paired comparison

Policies are compared on the clusters they share.

1. Compute the metric panel at the metric's level.
2. Intersect the cluster sets of the policies being compared.
3. Record every cluster dropped, and from which policy.
4. Estimate on the shared set only.

`comparison.require_common_worlds: false` disables step 2. It remains available
because sometimes there is no alternative, but every result produced that way
carries an `UNPAIRED` note through the panel, the bootstrap result, the report
and the JSON. It is not a quiet option.

**Why exclusion is the conservative choice.** Dropping a world that only one
policy ran loses information. Keeping it imports the whole world effect into
the comparison. The world effect is typically several times the policy effect,
so keeping it is the larger error — and the direction of the resulting bias is
unknown, because it depends on which worlds went missing and why. Scenario
`missing_worlds_reverse_ranking` is the demonstration.

## 5. Bootstrap design

| Parameter | Default | Note |
|---|---|---|
| `cluster_level` | `world` | `resident` is rejected at load |
| `n_resamples` | 2000 | minimum 100 enforced; below ~1000 the endpoints are visibly noisy |
| `method` | `percentile` | `basic` and `bca` also available |
| `confidence_level` | 0.95 | |
| `seed` | required in practice | an absent seed makes the result irreproducible |

**Procedure for a paired difference.** For replicate *b*:

1. Draw *K* cluster indices with replacement, where *K* is the number of shared
   clusters.
2. Evaluate the estimator on the baseline arm restricted to those clusters.
3. Evaluate it on the candidate arm restricted to **the same** clusters.
4. Record the difference.

Applying one draw to both arms is what preserves the pairing. Drawing
independently for each arm would give a valid interval for the difference of
two independent studies, which is not the study that was run.

**Method choice.** `percentile` is the default because it is transformation
respecting and makes no symmetry assumption. `bca` corrects bias and skewness
using a leave-one-cluster-out jackknife and is worth using for strongly skewed
statistics such as CVaR; when the jackknife or the bias correction is
degenerate it falls back to percentile endpoints *and says so in the result's
notes*. `basic` is provided for comparison.

**Known limitation.** The percentile cluster bootstrap under-covers when
clusters are few. Measured on the demonstration design: about 90% actual
coverage for a nominal 95% interval at 20 worlds, about 98% at 60. The
validator warns below 20 clusters, and the cluster count appears next to every
interval. Do not quietly report a 95% interval from 8 worlds.

**Bootstrap p-values.** Reported for completeness as
`2 · min(P(θ* ≤ 0), P(θ* ≥ 0))`, floored at `1/(B+1)`. They are a coarse
inversion of the percentile interval, not an exact test, and no verdict in this
library is based on one.

## 6. Equivalence and non-inferiority

### The rule

**"p > 0.05" is never a conclusion of no difference.** A large p-value and a
wide interval mean the study could not resolve the question. To claim two
policies are practically the same, declare how large a difference would still
be practically the same, then show the interval fits inside it.

### Margins

A margin is a positive distance on the difference scale, in the metric's own
units, declared before the analysis:

```yaml
equivalence:
  alpha: 0.05
  margins:
    mean_loss: 0.40
  non_inferiority: [mean_loss]
```

There is no default margin and there never will be. The smallest difference
that still matters is a domain judgement; this library does not know the
domain. `tost` raises `MarginRequired` when asked to work without one, and
metrics without a margin get an explicit note saying equivalence cannot be
concluded for them however narrow the interval is.

**Setting a margin honestly.** Decide it from the decision the evaluation
feeds, before seeing the data, and write down the reasoning. A margin chosen
after seeing the interval is a margin chosen to produce the desired verdict.

### TOST

Equivalence at level `alpha` is concluded exactly when the central
`1 − 2·alpha` interval for the difference lies strictly inside `(−margin,
+margin)`. With `alpha = 0.05` that is the 90% interval — the standard
correspondence, here with bootstrap endpoints rather than normal-theory ones.

Three outcomes:

| Outcome | Condition | Means |
|---|---|---|
| `equivalent` | interval inside ±margin | the difference is smaller than what matters |
| `not_equivalent` | interval entirely outside ±margin | the difference is larger than what matters |
| `inconclusive` | neither | the study cannot tell |

### Non-inferiority

One-sided, and it needs a direction:

- `lower_is_better`: non-inferior when the one-sided `1 − alpha` **upper** bound
  is below `+margin`.
- `higher_is_better`: non-inferior when the one-sided lower bound is above
  `−margin`.

### Verdict vocabulary

`classify_difference` emits exactly one of:

| Label | Condition |
|---|---|
| `superior` | interval excludes 0 in the candidate's favour |
| `worse` | interval excludes 0 in the baseline's favour |
| `equivalent` | interval includes 0 *and* TOST concludes equivalence |
| `statistically_different_practically_equivalent` | interval excludes 0 but fits inside the margin |
| `inconclusive` | anything else |

"No difference" is not in the list. The `inconclusive` sentence says so
explicitly.

## 7. Missing data

```yaml
missing_data:
  policy: drop_record        # drop_record | fail | impute_worst | impute_best
  require_complete_worlds: true
  max_count_imbalance: 0.2
```

| Policy | Behaviour | When to use |
|---|---|---|
| `drop_record` | drop observations with a missing metric value | default; missingness unrelated to the outcome |
| `fail` | raise | when missingness must be explained before any analysis |
| `impute_worst` | fill with the observed extreme in the *bad* direction for that metric | sensitivity bound: cannot flatter the policy that lost data |
| `impute_best` | fill with the observed extreme in the good direction | the other end of the same bound |

Three separate things are recorded, because they fail differently:

1. **Missing values** within a present record — handled by `policy` above, with
   counts per policy in the provenance.
2. **Missing clusters** — a world one policy ran and another did not. Handled by
   the paired design (§4): excluded from the comparison and listed.
3. **Count imbalance** — a world where one policy contributed far more
   observations than the other. Flagged above `max_count_imbalance`, because
   the paired difference there is estimated with unequal precision on the two
   sides.

**Running `impute_worst` and `impute_best` and reporting both is the honest
treatment of substantial missingness.** If the verdict survives both, the
missing data did not decide it. If it does not, that is the finding.

## 8. Stratification

Strata are **world-level design variables** — `landscape`, `mobility`,
`fire_regime`, `resource_level`, or anything else declared. A world belongs to
exactly one level of each stratum; if its rows disagree the modal value is used
and the disagreement is warned about, because splitting a world across strata
would break the pairing.

Every stratified analysis produces two things:

1. **Per-stratum comparisons**, skipping strata with fewer than `min_clusters`
   worlds with a recorded reason.
2. **An allocation ledger** — how many worlds of each stratum each policy
   actually ran. This is the confounding check: if the policies were given
   different material, a pooled comparison measures the material.

Two conflicts are detected and surfaced as notes:

- `sign_flip_between_strata` — the effect reverses between strata, so the
  pooled number describes no stratum.
- `pooled_contradicts_strata` — a Simpson-style reversal where the pooled
  result disagrees with every resolved stratum.

`stratified_estimate` provides a cluster-weighted combination. It is a
correction, not a replacement for reporting the strata.

## 9. Multiplicity

This library does not apply a family-wise or false-discovery correction, and
that is a deliberate omission rather than an oversight.

Corrections require a declared family, and the family depends on the
decision — which metrics, which policies, which strata were candidates for
the claim. The library cannot know that, so instead it makes the size of the
family visible:

- every metric comparison is reported, including the ones that did not resolve;
- `detect_disagreements` flags when declared metrics resolve in opposite
  directions;
- per-stratum results are printed together, so a reader can see how many
  comparisons were run.

**Declare the primary metric in advance.** Secondary metrics are
hypothesis-generating. Picking the winner from twenty comparisons after the
fact is the error no correction applied afterwards can repair.

## 10. What a report must contain

Enforced by `render_markdown`:

1. Source path and checksum, row count, format.
2. Unit of inference, resampling design, seed, confidence level, method.
3. Whether the comparison was paired, and how many clusters were shared.
4. Every filter and exclusion, with rows *and worlds* removed by each.
5. Point estimates, intervals and cluster counts for every metric.
6. A verdict sentence per metric, in the §6 vocabulary.
7. Declared margins, or an explicit note that none was declared.
8. Metric disagreements.
9. Design effect and effective sample size.
10. Failure-mode composition and the paired per-world shift in each mode.
11. Per-stratum results and the allocation ledger.
12. A provenance block per metric, with a fingerprint.

## 11. Checklist before reporting a conclusion

- [ ] `wg-eval validate-results` passes, and the warnings have been read.
- [ ] At least 20 clusters, or the cluster count is stated next to every interval.
- [ ] The comparison is paired, or the reason it is not is written down.
- [ ] The primary metric was declared before the analysis.
- [ ] Margins were declared before the analysis, for any equivalence claim.
- [ ] Every excluded world is accounted for.
- [ ] Tail metrics were examined, not only means.
- [ ] Metric disagreements are reported, not resolved after the fact.
- [ ] Failure modes are broken down, not just the overall success rate.
- [ ] The provenance fingerprint is recorded with the conclusion.
