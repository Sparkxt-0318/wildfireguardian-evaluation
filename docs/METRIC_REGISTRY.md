# Metric registry

A metric here is three things, never one:

```yaml
- name: cvar90_loss        # what it is called
  column: loss             # what it measures
  estimator: cvar          # how it is summarised
  level: event             # over what population of units
  params: {alpha: 0.9, tail: upper}
  direction: lower_is_better
  margin: 1.5              # optional; required for any equivalence claim
```

Changing `level` changes the estimand, not just the number, so the level
travels with the metric into the report and the provenance. `wg-eval metrics`
prints this registry from the code.

---

## Levels

| Level | Estimator sees | Units | Use for |
|---|---|---|---|
| `resident` | one value per observation | many per event | rates over the observation population |
| `event` | one value per (world, event, policy) after `resident_to_event` | a few per world | "per fire" quantities — the usual default |
| `world` | one value per (world, policy) after both roll-up steps | one per world | world summaries |

A metric's level may not be coarser than the resampling unit.

---

## Estimators

### `mean`
> arithmetic mean of *level*-level values of `column`

The default. Sensitive to the tail by construction; pair it with a tail metric.

### `rate`
> proportion of *level*-level values equal to 1

Identical arithmetic to `mean`, distinct name so a 0/1 outcome reads as a rate
in reports.

### `sum`
> sum of *level*-level values

For extensive quantities. Note that a sum over events depends on how many
events a world has, so it is usually wanted at `event` level after a
`resident_to_event: sum` roll-up, not at `world` level.

### `median`
> median of *level*-level values

Robust to the tail — which means it will not show you a tail problem. Never a
substitute for a tail metric.

### `std`
> sample standard deviation (ddof = 1)

A dispersion summary. `nan` for fewer than two values.

### `quantile`
> the `q` quantile (linear interpolation) of *level*-level values

`params: {q: 0.9}`. One point of the distribution, so noisier than `cvar` at
the same level, and it says nothing about how bad the values beyond it are.

### `cvar`
> CVaR at `alpha` (`tail`) of *level*-level values: the mean of the
> `k = max(1, ceil((1 − alpha) · n))` most extreme values

`params: {alpha: 0.9, tail: upper}`. The recommended tail metric.

Two properties matter. It is defined for every `alpha` and every `n` — unlike
"mean of values beyond the quantile", which is empty when nothing exceeds the
quantile. And it is sensitive to *how bad* the tail is, not only where it
starts: two policies with identical 90th percentiles but different worst cases
have different CVaR.

`tail: lower` averages the `k` smallest values, for metrics where the bad end
is the low end.

See [`DECISIONS.md`](DECISIONS.md) D7 for why this estimator and not another.

### `trimmed_mean`
> mean after trimming `proportion` from each tail

`params: {proportion: 0.1}`. A compromise between mean and median. It discards
the tail, so like the median it cannot reveal a tail problem.

### `p_exceed`
> proportion of *level*-level values strictly greater than `threshold`

`params: {threshold: 25.0}`. For an absolute limit that matters in the domain:
"how often does loss exceed 25?"

### `max` / `min`
> the extreme *level*-level value

The worst case observed. Highly variable and its bootstrap interval is poorly
calibrated (see [`ASSUMPTIONS.md`](ASSUMPTIONS.md) A9) — report it as
descriptive, prefer `cvar` for inference.

### `count`
> number of finite *level*-level values

A diagnostic, for checking that filters and missing-data policies did what was
intended.

---

## Tail metrics

`TAIL_ESTIMATORS = {quantile, cvar, max, min, p_exceed}`.

Metrics built on these are marked `is_tail_metric`, and `detect_disagreements`
compares them against central metrics **on the same column**. When a mean and a
CVaR on `loss` resolve in opposite directions, the report says so:

```
TAIL DISAGREEMENT on `loss`: mean_loss favours policy_a while cvar90_loss
favours policy_b. Average performance and tail risk point in opposite
directions; reporting either alone would mislead.
```

That is not a warning to be cleared. It is the result. See scenario
`tail_risk_disagreement`.

---

## Direction

`lower_is_better` (default) or `higher_is_better`. It affects three things:

1. which policy a resolved interval favours;
2. which side the non-inferiority bound is taken on;
3. which extreme `impute_worst` and `impute_best` substitute.

A direction that is wrong will produce a confidently backwards verdict, so
check it for every metric.

---

## Margins

A margin is a **positive distance on the difference scale, in the metric's own
units**. `mean_loss: 0.40` means "a difference of less than 0.40 loss units
does not matter for the decision this evaluation feeds".

Set from `equivalence.margins.<metric>`, falling back to the metric's own
`margin`. No margin means no equivalence conclusion for that metric, and the
report says so rather than leaving it ambiguous.

---

## A recommended default set

```yaml
metrics:
  - {name: mean_loss,    column: loss,            estimator: mean, level: event}
  - {name: cvar90_loss,  column: loss,            estimator: cvar, level: event,
     params: {alpha: 0.9, tail: upper}}
  - {name: p90_loss,     column: loss,            estimator: quantile, level: event,
     params: {q: 0.9}}
  - {name: success_rate, column: mission_success, estimator: mean, level: resident,
     direction: higher_is_better}
  - {name: mean_travel,  column: travel_time,     estimator: mean, level: event}
```

A central metric and a tail metric on the primary outcome, always. Everything
else is secondary, and secondary metrics are hypothesis-generating — see
[`STATISTICAL_PROTOCOL.md`](STATISTICAL_PROTOCOL.md) §9.

---

## Adding an estimator

```python
from wg_eval.metrics import register_estimator

@register_estimator(
    "winsorized_mean",
    "mean of {level}-level values of `{column}` after winsorising at {proportion:.0%}",
)
def _winsorized_mean(proportion: float = 0.05):
    def estimator(values):
        ...
    return estimator
```

The definition template is rendered with the estimator's parameters and becomes
the metric definition recorded in provenance, so a new estimator is
self-documenting from the moment it exists. Add it to `TAIL_ESTIMATORS` if it
is a tail statistic, so disagreement detection covers it.
