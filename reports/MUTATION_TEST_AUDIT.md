# Mutation test audit

wg-eval 0.1.0 (git 4a6094e+dirty)

A test suite that passes proves nothing until you know it can fail. This audit
introduces deliberate statistical errors -- the exact errors the library exists
to prevent -- and records whether anything catches each one.

Random operator mutations are not used. They mostly produce crashes, and a
crash is not the failure mode that matters here. The interesting mutants are
the ones that produce *plausible numbers that are wrong*, so each is written by
hand, together with the check a reader would expect to notice it.

A mutant that nothing detects is reported as an undetected mutant, not omitted.


**11 of 11 mutants detected.**

No undetected mutants.

## Mutants

| mutant                             | what it breaks                                         | wrong behaviour                                                         | audit item | detected |
|------------------------------------|--------------------------------------------------------|-------------------------------------------------------------------------|------------|----------|
| `observation_bootstrap`            | resample observations instead of whole units           | confidence intervals several times too narrow                           | 4, 27      | yes      |
| `unpaired_analysis_of_paired_data` | draw each arm independently while the design is paired | the unit effect stops cancelling and a real effect is lost in noise     | 2, 27      | yes      |
| `equivalence_without_a_margin`     | invent a margin when the config declared none          | an equivalence verdict nobody declared the basis for                    | 11, 27     | yes      |
| `p_value_read_as_equivalence`      | relabel an inconclusive result as equivalent           | 'no significant difference' presented as a finding of sameness          | 12, 30     | yes      |
| `reversed_metric_orientation`      | treat lower-is-better metrics as higher-is-better      | tail statistics summarise the beneficial tail and verdicts invert       | 8, 27      | yes      |
| `cvar_wrong_tail`                  | CVaR averages the opposite tail from the one requested | a risk statistic that describes the good case                           | 9, 27      | yes      |
| `failed_runs_dropped_silently`     | discard runs that did not complete, with no ledger     | a policy improves its score by failing to produce results               | 14, 27     | yes      |
| `uneven_unit_reuse_unreported`     | stop reporting unequal observation counts between arms | a paired difference estimated with wildly unequal precision looks clean | 16, 27     | yes      |
| `multiplicity_family_ignored`      | leave every p-value in a declared family uncorrected   | a family of twenty null tests produces a headline                       | 19, 27     | yes      |
| `strata_incorrectly_merged`        | collapse every unit into a single stratum              | heterogeneity and allocation imbalance become invisible                 | 18, 27     | yes      |
| `inference_structure_unchecked`    | accept a declared structure the records contradict     | resampling at a level where independence does not hold                  | 3, 27      | yes      |

## Detection detail

- **`observation_bootstrap`** — detected.
- **`unpaired_analysis_of_paired_data`** — detected.
- **`equivalence_without_a_margin`** — detected.
- **`p_value_read_as_equivalence`** — detected.
- **`reversed_metric_orientation`** — detected.
- **`cvar_wrong_tail`** — detected.
- **`failed_runs_dropped_silently`** — detected.
- **`uneven_unit_reuse_unreported`** — detected.
- **`multiplicity_family_ignored`** — detected.
- **`strata_incorrectly_merged`** — detected.
- **`inference_structure_unchecked`** — detected.

## What this audit does not establish

Detecting a mutant shows that *some* check distinguishes the broken library
from the correct one. It does not show that the check would fire on a
different dataset, that the message would be understood, or that a reader would
act on it. Those are properties of the report and of the person reading it.

Two mutants needed their fixtures designed with care, and the reasons are worth
recording because they are facts about the statistics rather than about the code:

* **`observation_bootstrap`.** The narrowing that pseudoreplication buys is
  bounded. With `sigma_I` the unit-by-policy interaction, `V_E` the variance of
  a paired sub-unit difference and `E` sub-units per unit, the variance ratio is
  `(2*sigma_I^2 + V_E) / (2*E*sigma_I^2 + V_E)`. It tends to `1/E` when the
  interaction dominates and to **1** when there is none: on a design with no
  unit-by-policy interaction the two bootstraps estimate the same quantity, and
  no fixture can detect this mutant. "Resampling observations always understates
  uncertainty" is therefore false as stated; what is true is that it understates
  it whenever the units differ in how they respond to the policies.

* **A naive bootstrap that also drops the pairing makes two errors at once.**
  Ignoring clustering narrows the interval; ignoring pairing widens it. On a
  strongly paired design the second dominates, and the doubly-wrong interval
  looks conservative. The two errors are therefore separate mutants here, and
  the pseudoreplication mutant holds the pairing fixed.

