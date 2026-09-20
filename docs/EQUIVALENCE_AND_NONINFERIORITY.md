# Equivalence and non-inferiority

Three questions are routinely confused. They have different null hypotheses,
different alternatives, and different failure modes, and only one of them can
be answered without a margin.

---

## 1. Difference testing

```
    H0 : Delta = 0          H1 : Delta != 0
```

Asks whether the data can rule out a zero difference. The library answers it
with the paired bootstrap interval: if the interval excludes 0, the contrast is
resolved in the direction of the estimate.

**What it cannot do.** Failing to reject `H0` is not evidence for it. A wide
interval containing 0 is equally consistent with a difference of zero and with
every other value it contains, including differences that would change the
decision. The library therefore has no verdict meaning "no difference"; that
case is `inconclusive`, and the sentence says "undetermined".

---

## 2. Equivalence

```
    H0 : Delta <= -delta_L   or   Delta >= +delta_U
    H1 : -delta_L < Delta < +delta_U
```

The null is that the difference is **at least as large as the margin**, on one
side or the other. Rejecting both one-sided nulls concludes equivalence. This
is the reverse of difference testing: here the interesting outcome is
rejection, and the burden is on showing smallness.

`delta_L` and `delta_U` are positive distances declared in advance. They need
not be equal — see **Asymmetric margins** below.

### Implementation: TOST

Two one-sided tests, implemented by the interval-inclusion rule that is
equivalent to them:

> Equivalence at level `alpha` is concluded exactly when the central
> `1 - 2*alpha` interval for the difference lies strictly inside
> `(-delta_L, +delta_U)`.

With `alpha = 0.05` that is the **90%** interval, not the 95% one. This
surprises people and is not a mistake: each one-sided test is run at 5%, and
their joint acceptance region is the 90% two-sided interval.

The endpoints are bootstrap percentiles rather than normal-theory ones, so no
symmetry of the sampling distribution is assumed.

Three outcomes:

| Outcome | Condition | Means |
|---|---|---|
| `statistically_equivalent` | interval inside the margin | the difference is smaller than what was declared to matter |
| `not_equivalent` | interval entirely outside the margin | the difference exceeds it |
| `inconclusive` | neither | the study cannot tell |

`inconclusive` here is **not** the same as `not_equivalent`. The first says the
data are silent; the second says they speak against equivalence.

### Bootstrap p-values

`p_lower` is the bootstrap achieved significance level against
`H0 : Delta <= -delta_L`, and `p_upper` against `H0 : Delta >= +delta_U`. The
TOST p-value is their maximum. These are reported for completeness; the verdict
comes from the interval.

---

## 3. Non-inferiority

```
    lower_is_better :   H0 : Delta >= +delta_U      H1 : Delta < +delta_U
    higher_is_better:   H0 : Delta <= -delta_L      H1 : Delta > -delta_L
```

One-sided: "not meaningfully worse", with no claim about being better. The side
tested is the **harmful** side, and which side that is depends on the metric's
declared orientation. This is why `direction` is a required, machine-readable
property of every metric rather than a convention in a column name.

For an asymmetric margin, non-inferiority uses only the harmful-side component:
`margin.upper` for a lower-is-better metric, `margin.lower` for a
higher-is-better one. `tests/test_equivalence.py` checks both directions
against a fixture where the two give opposite verdicts.

---

## Asymmetric margins

```yaml
equivalence:
  margins:
    mean_loss:
      lower: 0.50     # tolerated improvement
      upper: 0.15     # tolerated worsening
      scale: absolute
      source: "operational tolerance agreed 2026-09-01; see decision log entry 14"
```

The equivalence region is `(-0.50, +0.15)`: a candidate may be up to 0.50
better and still count as equivalent, but only 0.15 worse.

Asymmetry is supported because tolerance usually is asymmetric. A replacement
that is slightly better is rarely a problem; one that is slightly worse may be.
Forcing a symmetric margin either overstates the tolerance for harm or
understates the tolerance for benefit, and both distort the verdict.

Scenario `asymmetric_margin` shows the same interval concluding equivalence
under a symmetric margin and failing to under an asymmetric one. Neither
verdict is wrong: they answer different declared questions, which is exactly
why the margin has to be declared before the data are seen.

---

## Rules about margins

### There is no default, and there never will be

`tost` and `non_inferiority` raise `MarginRequired` when asked to work without
one. The smallest difference that still matters is a domain judgement; this
library does not know the domain. A default margin would be a claim about every
domain at once, adopted silently and defended by nobody.

### A margin must be in the metric's own units

`margins.mean_loss.upper: 0.15` means 0.15 loss units. If the metric is a rate,
the margin is in rate points.

`scale: standardized` is accepted but carries a warning in every result that
uses it, because the scaling constant is estimated from the same data: a
noisier experiment then gets a wider margin and finds equivalence more easily,
which is backwards. Prefer absolute margins.

### Never choose a margin from the observed data

A margin justified after the interval is known is a margin chosen to produce a
verdict. The library cannot prevent this; it makes it visible:

* `margin.source` records where the number came from, and its absence is
  flagged in the result, in the report and in validation;
* `analysis_status` records whether the analysis claims preregistration, and
  `protocol_hash` / `protocol_commit` / `protocol_timestamp` make the claim
  checkable by someone else;
* the margin is part of the scientific fingerprint, so changing it changes the
  identity of the result.

### Record the margin's provenance

```yaml
      source: "5% of the baseline incident cost; threshold from the 2026 operating review"
```

Write what a sceptical reader needs to decide whether the margin was chosen
honestly. "Agreed in advance" is weaker than a reference to where.

---

## Statistical equivalence is not operational interchangeability

The library attaches this caveat to every equivalence finding:

> STATISTICALLY_EQUIVALENT is not OPERATIONALLY_INTERCHANGEABLE: this is a
> statement that the estimated contrast lies inside the predeclared margin, not
> that the alternatives are interchangeable in use. Whether the margin marks a
> decision-relevant difference is a domain judgement outside this library.

The distinction matters because the two claims fail differently:

| Claim | What would falsify it |
|---|---|
| Statistical equivalence | a wider interval, a different margin, more units |
| Operational interchangeability | anything the metric does not measure — cost, failure modes, reversibility, who bears the risk |

A contrast can be equivalent on every declared metric and still describe two
options nobody would swap, because the declared metrics are not the whole
decision. The library reports the first and refuses to imply the second.

---

## The verdict vocabulary

`classify_difference` emits exactly one of five labels:

| Label | Interval excludes 0 | Equivalence |
|---|---|---|
| `superior` | yes, favouring the candidate | not equivalent or not tested |
| `worse` | yes, favouring the baseline | not equivalent or not tested |
| `statistically_different_within_margin` | yes | equivalent |
| `statistically_equivalent` | no | equivalent |
| `inconclusive` | no | not equivalent, or no margin |

`statistically_different_within_margin` is the case people find strange and is
common with large samples: the contrast is resolved in direction and smaller
than what was declared to matter. Both halves are true and the report says both.

"No difference" is not in the list, and no generated sentence may contain it —
`wg_eval.report.audit_wording` enforces that over every report the package can
produce, and `wg-eval report` exits non-zero if a report ever violates it.

---

## Worked example

```yaml
metrics:
  - name: mean_loss
    column: loss
    estimator: mean
    level: event_id
    direction: lower_is_better
    role: primary

equivalence:
  alpha: 0.05
  margins:
    mean_loss:
      lower: 0.40
      upper: 0.40
      source: "declared before data collection; see protocol hash below"
  non_inferiority: [mean_loss]

analysis_status: preregistered
protocol:
  hash: sha256:...
  commit: 4f2a91c
  timestamp: 2026-09-01T09:00:00Z
```

On the `practical_equivalence` scenario this yields:

```
paired difference: -0.0055 [-0.1068, +0.0970]   (95% interval)
TOST 90% interval: (-0.0910, +0.0815) inside +/-0.4
verdict: statistically_equivalent
caveat: STATISTICALLY_EQUIVALENT is not OPERATIONALLY_INTERCHANGEABLE ...
```

The true difference in that scenario is +0.03: real, and negligible at the
declared margin. Reporting it as "no significant difference" would have thrown
away the positive finding and asserted a stronger one.
