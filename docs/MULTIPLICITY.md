# Multiplicity

A correction is only meaningful relative to a **family**, and the family is a
property of the decision, not of the data. Which metrics were candidates for
the headline? Which policies? Which strata? The library cannot know, so it never
corrects automatically — and it never silently leaves the question open either.

---

## Families are declared by analysis role

Every metric carries a role:

```yaml
metrics:
  - name: mean_loss
    role: primary        # exactly one metric may be primary
  - name: cvar90_loss
    role: secondary
  - name: p90_loss
    role: exploratory

multiplicity:
  secondary_correction: holm     # none | holm | bonferroni
  exploratory_correction: none
  justification: "secondary family fixed in the protocol; exploratory reported raw"
```

Equivalently, with an `analysis_role` block:

```yaml
analysis_role:
  primary: mean_loss
  secondary: [cvar90_loss, success_rate]
  exploratory: [p90_loss, mean_travel]
```

Three families follow, and each is corrected independently:

| Role | Correction | Why |
|---|---|---|
| `primary` | never corrected | one prespecified hypothesis, so there is nothing to correct over |
| `secondary` | as declared | a prespecified family; correcting it is meaningful because its size was fixed in advance |
| `exploratory` | as declared, usually none | hypothesis-generating; correcting an open-ended family is theatre |

Declaring a second primary metric raises at config load. A primary outcome that
is plural is not a primary outcome.

---

## What is corrected, and what is not

**p-values are adjusted.** Holm is a step-down procedure controlling the
family-wise error rate under arbitrary dependence; Bonferroni is its single-step
version and is uniformly less powerful.

**Confidence intervals are not adjusted.** A Holm-adjusted interval is not a
rescaling of an unadjusted one — Holm's rejection region depends on the whole
family's ordering, and inverting it gives a region that is not generally an
interval. Silently widening the displayed intervals would misrepresent what was
computed. Reports therefore show marginal intervals next to adjusted p-values
and say so, in the table footnote and in the family notes.

This is a real limitation and it is stated rather than papered over: a reader
comparing an adjusted p-value against an unadjusted interval is comparing two
different procedures.

---

## Holm, precisely

For raw p-values sorted `p_(1) <= ... <= p_(m)`:

```
    p_adj_(i)  =  max_{j <= i}  min( 1, (m - j + 1) * p_(j) )
```

The running maximum enforces monotonicity, so an adjusted p-value never falls
below one ranked ahead of it. Worked example, `m = 5`:

| rank | raw | multiplier | product | running max |
|---|---|---|---|---|
| 1 | 0.005 | 5 | 0.025 | 0.025 |
| 2 | 0.011 | 4 | 0.044 | 0.044 |
| 3 | 0.020 | 3 | 0.060 | 0.060 |
| 4 | 0.040 | 2 | 0.080 | 0.080 |
| 5 | 0.130 | 1 | 0.130 | 0.130 |

And a case where the running maximum bites: `p = (0.040, 0.041)`, `m = 2` gives
products `0.080` and `0.041`; monotonicity raises the second to `0.080`.

Both are checked in `tests/test_reference_calculations.py`, against values
written out by hand and against a transcription of the definition rather than
against this library's own implementation.

---

## Bonferroni

```
    p_adj_(i)  =  min( 1, m * p_(i) )
```

Simpler, uniformly at least as conservative as Holm, and included mainly
because some protocols specify it. Prefer Holm when the choice is free.

---

## No correction, declared

`correction: none` is a legitimate declaration, and it is reported as one:

> The secondary family of 6 test(s) is reported without multiplicity
> correction, as declared. Read it as that many separate comparisons.

That is different from not thinking about it. The family size appears in the
report either way, so a reader can see how many comparisons produced the
finding they are looking at.

---

## What correction cannot fix

Correcting after choosing the metric does not repair the choosing. If twenty
exploratory metrics are computed and the most favourable becomes the headline,
the selection has already happened; a correction applied to the family
afterwards does not undo it, and a correction applied to only the reported
metric is worse than none.

Scenario `metric_selected_after_the_fact` is the demonstration: forty pure-noise
metrics, thirteen of which point the desired way, and the most convincing at raw
`p = 0.05`. The defences are structural rather than statistical:

* exactly one primary metric, declared, and reported first in every report;
* every other finding labelled with its role wherever it appears;
* `analysis_status` recording whether any of this was fixed in advance, with a
  protocol hash that makes the claim checkable;
* the config in the provenance, so a later reader can see what was declared.

None of these stop a determined analyst. They stop an accidental one, and they
leave a record for everyone else.

---

## Multiplicity beyond metrics

Three other dimensions multiply comparisons, and the library handles each by
making the count visible rather than by correcting:

| Dimension | What the library does |
|---|---|
| Policies | every candidate contrast is reported, each with its own family membership |
| Strata | per-stratum results are printed together, with unit counts, so a reader sees how many were run |
| Handlings (missing data, status) | alternative handlings are separate analyses with separate fingerprints, not a family |

Selecting the favourable stratum, or the favourable missing-data handling, is
the same error as selecting the favourable metric. The allocation ledger and the
run-status ledger exist so that selection is visible in the output.

---

## Reading the multiplicity section of a report

```
| role        | test                       | method | family size | p (raw) | p (adj) | survives |
|-------------|----------------------------|--------|-------------|---------|---------|----------|
| primary     | mean_loss|policy_b_vs_...  | none   | 1           | 0.002   | 0.002   | yes      |
| secondary   | cvar90_loss|policy_b_vs... | holm   | 2           | 0.031   | 0.062   | no       |
| secondary   | success_rate|policy_b_vs.. | holm   | 2           | 0.180   | 0.180   | no       |
```

* `family size` is how many tests the correction assumed. If it looks smaller
  than the number of comparisons actually run, the family was declared too
  narrowly.
* `survives` compares the adjusted p-value against `equivalence.alpha`.
* A `no` in `survives` next to a resolved interval is not a contradiction: the
  interval is marginal and the p-value is adjusted. The result note says so.
