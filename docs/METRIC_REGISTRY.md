# Metric registry

A metric here is never one thing. It is a column, an estimator, the level it
pools over, an orientation, a support, and a role in the analysis:

```yaml
- name: cvar90_loss
  column: loss
  estimator: cvar
  level: event_id
  params: {alpha: 0.9}
  direction: lower_is_better    # which end is bad
  tail: harmful                 # resolves against `direction`
  bounds: {lower: 0.0}          # declared support
  role: secondary               # primary | secondary | exploratory
  margin: {lower: 1.5, upper: 1.5, source: "..."}
```

Changing `level` changes the estimand; changing `direction` changes which tail a
tail statistic summarises. Both travel with the metric into the report and the
provenance. `wg-eval metrics` prints this registry from the code.

---

## Levels

| Level | The estimator sees | Use for |
|---|---|---|
| `resident_id` | one value per observation | rates over the observation population |
| `event_id` | one value per sub-unit, after the first roll-up | per-sub-unit quantities; the usual default |
| `world_id` | one value per unit, after the full roll-up | per-unit quantities |

Levels are the ones declared in `inference`, by column name. A metric's level
may not be coarser than the resampling unit.

---

## Orientation

`direction` is `lower_is_better` (default) or `higher_is_better`, and it is
machine-readable because five things depend on it:

1. which policy a resolved interval favours;
2. which side non-inferiority tests;
3. which extreme `impute_worst` / `impute_best` substitute;
4. which side of an asymmetric margin governs non-inferiority;
5. **which tail a tail statistic summarises.**

`harmful_side` is `upper` for `lower_is_better` and `lower` for
`higher_is_better`. `tail: harmful` (the default) resolves to it; `tail:
beneficial` to the other; `tail: upper` / `tail: lower` override explicitly and
appear in the report.

A `params.tail` that contradicts `tail` is **rejected at config load**, so the
harmful tail cannot be chosen by accident. Scenario `wrong_cvar_tail` shows the
two tails giving opposite verdicts on the same records.

Legal-but-suspect orientations are flagged as `metric_orientation` warnings: a
harmful-tail quantile with `q < 0.5` on a lower-is-better metric, or a `max`
where the harmful extreme is the `min`.

---

## Estimators

Each entry gives the definition, whether it is a tail statistic, and the unit
count below which it is reported as not credible. Thresholds are per estimator,
not a universal minimum n: a mean from 8 units is coarse but usable, while a 90%
CVaR from 8 units is the average of one value.

### `mean` — central, ≥5 units
> arithmetic mean of *level*-level values

Unbiased at any n; its interval is what degrades. Sensitive to the tail by
construction, so pair it with a tail metric.

### `rate` — central, ≥5 units
> proportion of *level*-level values equal to 1

Identical arithmetic to `mean`; a distinct name so a 0/1 outcome reads as a rate.

### `sum` — central, ≥5 units
> sum of *level*-level values

For extensive quantities. A sum over sub-units depends on how many there are, so
it usually belongs at the first roll-up step rather than at unit level.

### `median` — central, ≥8 units
> median; the mean of the two central order statistics when the count is even

Robust to the tail, which means it will not show you a tail problem.

### `std` — central, ≥10 units, ≥2 values
> sample standard deviation, `ddof = 1`

### `quantile` — tail, ≥20 units, ≥2 values
> the `q` quantile by linear interpolation: position `q*(n-1)` in the
> zero-indexed sorted values, interpolating between neighbours (numpy `linear`)

**Hand-checkable.** For `x = 1..10` and `q = 0.9`, the position is
`0.9*9 = 8.1`, between `x[8] = 9` and `x[9] = 10`, giving **9.1**.

One point of the distribution, so noisier than `cvar` at the same level, and it
says nothing about how bad the values beyond it are.

### `cvar` — tail, ≥20 units, ≥2 values
> the unweighted mean of the `k = max(1, ceil((1-alpha)*n))` most extreme
> values in the declared tail, with `(1-alpha)*n` rounded to 9 decimals before
> the ceiling, ties included by position in the sorted order

**Hand-checkable.** For `x = 1..10` and `alpha = 0.7`: `k = ceil(0.3*10) = 3`.
Upper tail is `mean(8, 9, 10) = 9`; lower tail is `mean(1, 2, 3) = 2`.

Why the rounding: `(1 - 0.7) * 10` evaluates to `3.0000000000000004` in binary
floating point, whose ceiling is 4. Without the rounding the implementation
averages one value more than its own definition states, for many ordinary
`(alpha, n)` pairs. This was a live defect found during the v0.1.0 audit by the
hand-computed reference tests.

Why not "mean of values beyond the quantile": undefined when no value exceeds
the quantile, which happens at high `alpha` and small `n` — exactly where tail
statistics matter.

The recommended tail metric: defined for every `alpha` and `n`, and sensitive to
*how bad* the tail is rather than only where it starts.

### `trimmed_mean` — central, ≥10 units
> mean after removing `floor(proportion * n)` values from each end

**Hand-checkable.** `x = 1..10`, `proportion = 0.2` removes 2 from each end,
leaving `mean(3..8) = 5.5`.

### `p_exceed` — tail, ≥20 units
> proportion of values **strictly** greater than `threshold`

A value exactly equal to the threshold does not count. **Hand-checkable.**
`x = [1,2,3,4,5]`, threshold 3 → 2/5 = 0.4.

### `max` / `min` — tail, ≥30 units
> the extreme value

The bootstrap is **not consistent** for an extreme order statistic: it has no
limiting normal distribution and the interval is not trustworthy at any n.
Report as descriptive; prefer `cvar` for inference.

### `count` — central, ≥1 unit
> number of finite values

A diagnostic, for checking that filters and handlings did what was intended.

---

## Tail metrics and disagreement

`TAIL_ESTIMATORS = {quantile, cvar, max, min, p_exceed}`.

When a central and a tail metric **on the same column** resolve in opposite
directions, the report says so, with the orientation, both contrasts, both
intervals and both unit counts, and names no winner:

```
CENTRAL AND TAIL CONTRASTS DISAGREE on `loss` (lower_is_better; the upper tail
is the harmful one). mean_loss: +0.846 [+0.365, +1.332] favours policy_a.
cvar90_loss: -2.709 [-4.710, -1.216] favours policy_b. Both contrasts are
reported with their intervals; this library does not rank the two and implies no
overall preference. Which one governs the decision is a declared risk
preference, not a statistical result.
```

That is not a warning to clear. It is the result.

---

## Bounds

```yaml
    bounds: {lower: 0.0, upper: 1.0}
```

* Values outside the support are a **validation error**: either the bound or the
  data is wrong, and both cannot be right.
* Interval endpoints outside the support are **reported, never clamped**. For a
  difference, the support is `(-(upper-lower), +(upper-lower))`.

See [`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) §10.

---

## Margins

A positive distance on the difference scale, in the metric's own units,
possibly asymmetric, with a recorded source. See
[`EQUIVALENCE_AND_NONINFERIORITY.md`](EQUIVALENCE_AND_NONINFERIORITY.md).

No margin means no equivalence conclusion for that metric, and the report says
so rather than leaving it ambiguous.

---

## Roles

`primary` (at most one), `secondary`, `exploratory`. The role determines report
order and family membership for multiplicity. See
[`MULTIPLICITY.md`](MULTIPLICITY.md).

---

## A recommended default set

```yaml
metrics:
  - {name: mean_loss, column: loss, estimator: mean, level: event_id,
     direction: lower_is_better, role: primary, bounds: {lower: 0.0}}
  - {name: cvar90_loss, column: loss, estimator: cvar, level: event_id,
     params: {alpha: 0.9}, tail: harmful, direction: lower_is_better, role: secondary}
  - {name: success_rate, column: mission_success, estimator: mean, level: resident_id,
     direction: higher_is_better, role: secondary, bounds: {lower: 0.0, upper: 1.0}}
  - {name: p90_loss, column: loss, estimator: quantile, level: event_id,
     params: {q: 0.9}, direction: lower_is_better, role: exploratory}
```

A central metric and a harmful-tail metric on the primary outcome, always.

---

## Adding an estimator

```python
from wg_eval.metrics import register_estimator

@register_estimator(
    "winsorized_mean",
    "mean of {level}-level values of `{column}` after winsorising at {proportion:.0%}",
    is_tail=False,
    min_units=10,
    estimand_params=("proportion",),
)
def _winsorized_mean(proportion: float = 0.05):
    def estimator(values):
        ...
    return estimator
```

The definition template is rendered with the estimator's parameters and becomes
the metric definition recorded in provenance, so a new estimator documents
itself from the moment it exists. Set `is_tail=True` for a tail statistic so
disagreement detection covers it, and give `min_units` honestly: it is the
number below which you would not want to see the statistic reported.

Add a hand-computed case to `tests/test_reference_calculations.py`. Production
code must not be its own oracle.
