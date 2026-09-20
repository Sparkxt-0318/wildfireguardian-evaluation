# Red-team scenarios

seed `20260919` | wg-eval 0.1.0 (git 3d729e2+dirty)

Each scenario builds records with a known truth, runs the misleading analysis the records are designed to reward, and then runs the correct one.

### observation_bootstrap_false_precision: Observation-level bootstrap manufactures precision

TRAP       Resampling observations treats 1,200 nested rows as 1,200 experiments and returns an interval several times too narrow.
TRUTH      The design supplies 20 independent units. The true mean loss difference is +0.6.
DEFENCE    Resample whole units; observations travel with the unit they belong to.
SUPPORTS   That the observation-level interval under-covers by a margin larger than Monte Carlo error at this number of replications.
STILL NOT  That the unit-level interval is calibrated at 20 units -- measured coverage there is below nominal too, just far less so.

records: 2400 observations in 20 units (60 observations per unit per policy)
true mean loss difference (B - A): +0.600

  WRONG  observation-level bootstrap: +1.161 [+0.902, +1.421]  width 0.519
  RIGHT  unit-level paired bootstrap: +1.161 [+0.192, +2.205]  width 2.013

  width ratio (right / wrong): 3.9x
  observation-level (pseudoreplicated) bootstrap: 55.0% coverage of a nominal 95% interval (R=200, MC SE 0.035, 95% Wilson CI [48.1%, 61.7%])
  world_id-level cluster bootstrap: 91.5% coverage of a nominal 95% interval (R=200, MC SE 0.020, 95% Wilson CI [86.8%, 94.6%])
  design effect for policy_a: 1.9 (ICC 0.88, 40 observations worth 21 independent ones)

Trap reproduced: **True** · Defence held: **True**


### unit_bootstrap_calibration: Unit-level bootstrap coverage, with its Monte Carlo error

TRAP       One interval establishes nothing on its own, and a bare coverage percentage from 100 replications is read as if it were exact.
TRUTH      The true mean loss difference is +0.6 in every replication.
DEFENCE    Report empirical coverage with its Monte Carlo SE and a Wilson interval, and compare coverage at two unit counts.
SUPPORTS   That coverage improves with more units and is consistent with nominal at 60 units.
STILL NOT  That the percentile interval is calibrated at 20 units; the Wilson interval there excludes the nominal level.

true mean loss difference (B - A): +0.600
  unit-level paired interval on this draw: [+0.192, +2.205] -> covers the truth

  20 units: world_id-level cluster bootstrap: 91.5% coverage of a nominal 95% interval (R=200, MC SE 0.020, 95% Wilson CI [86.8%, 94.6%])
  60 units: world_id-level cluster bootstrap: 95.0% coverage of a nominal 95% interval (R=200, MC SE 0.015, 95% Wilson CI [91.0%, 97.3%])
  contrast, 20 units: observation-level (pseudoreplicated) bootstrap: 55.0% coverage of a nominal 95% interval (R=200, MC SE 0.035, 95% Wilson CI [48.1%, 61.7%])

  consistent with nominal at 20 units: False
  consistent with nominal at 60 units: True

Trap reproduced: **True** · Defence held: **True**


### tail_risk_disagreement: Mean favours A while the harmful tail favours B

TRAP       policy_a has the lower average loss, so a mean-only report recommends it.
TRUTH      policy_a carries a 4% per-observation chance of a +60 loss; its 90% CVaR is far worse than policy_b's while its mean is 0.8 better.
DEFENCE    Declare tail metrics in advance, orient them by the metric's direction, and report the disagreement with both intervals.
SUPPORTS   That the two contrasts point in opposite directions, each with its own interval.
STILL NOT  Which policy is preferable; that needs a risk preference declared outside the analysis.

  orientation: lower_is_better; harmful tail is the upper one
  mean_loss   B - A = +0.846 [+0.365, +1.332] -> favours policy_a
  cvar90_loss B - A = -2.709 [-4.710, -1.216] -> favours policy_b

  CENTRAL AND TAIL CONTRASTS DISAGREE on `loss` (lower_is_better; the upper tail is the harmful one). mean_loss: +0.8458 [+0.3655, +1.332] favours policy_a. cvar90_loss: -2.709 [-4.71, -1.216] favours policy_b. Both contrasts are reported with their intervals; this library does not rank the two and implies no overall preference. Which one governs the decision is a declared risk preference, not a statistical result.
  METRICS RESOLVE IN DIFFERENT DIRECTIONS for policy_b vs policy_a: mean_loss (primary) favours policy_a, cvar90_loss (secondary) favours policy_b, success_rate (secondary) favours policy_a. No single ranking follows; the primary metric declared in advance is the one the analysis was designed to answer.

Trap reproduced: **True** · Defence held: **True**


### practical_equivalence: Equivalent within a declared margin

TRAP       The interval includes zero, so the report is read as a finding that the policies match.
TRUTH      The true difference is +0.03 against a declared margin of +/-0.40 -- real, and negligible at that margin.
DEFENCE    TOST against the declared margin, with the decision caveat attached.
SUPPORTS   That the contrast lies inside the predeclared margin.
STILL NOT  That the policies are interchangeable in use; the margin's adequacy is a domain judgement.

  true difference: +0.030, declared margin: +/-0.4
  paired difference: -0.0055 [-0.1068, +0.0970]  (bootstrap p = 0.885)

  WRONG  'the interval contains zero, so the policies match.'
  RIGHT  Equivalent within the declared margin +/-0.4: the 90% interval for the estimated difference (-0.09097, 0.08149) lies inside it.
  verdict: statistically_equivalent
  caveat: STATISTICALLY_EQUIVALENT is not OPERATIONALLY_INTERCHANGEABLE: this is a statement that the estimated contrast lies inside the predeclared margin, not that the alternatives are interchangeable in use. Whether the margin marks a decision-relevant difference is a domain judgement outside this library.

Trap reproduced: **True** · Defence held: **True**


### missing_units_reverse_ranking: Missing units reverse the ranking

TRAP       policy_b has no rows on the 22 hardest units; comparing each policy over its own units favours the worse one.
TRUTH      policy_b is worse by +1.2 mean loss on every unit.
DEFENCE    Pair on the units both policies ran, record every exclusion, and test whether the excluded units resemble the shared ones.
SUPPORTS   The paired contrast among units observed under both policies.
STILL NOT  A contrast for the full set of observed units, because overlap depends on unit difficulty.

  true difference (B - A): +1.200 (positive = B is worse)
  policy_b is missing from 22 of 60 units -- the hardest ones

  WRONG  unpaired over available units: -1.862 [-3.147, -0.770] -> favours policy_b
  RIGHT  paired on the 38 shared units: +0.948 [+0.622, +1.266] -> favours policy_a

  estimand: world_ids observed under every compared policy (38 of 60)
  COMPLETE-CASE REPRESENTATIVENESS: the world_ids excluded by the pairing differ from the shared ones on this metric by more than 0.2 pooled SD. The paired estimate remains correct for the 38 shared world_ids, but it should not be read as an estimate for the full set of 60 observed world_ids, still less for the target population.

Trap reproduced: **True** · Defence held: **True**


### easier_units_confound: A policy allocated easier units

TRAP       policy_a ran mostly on low-difficulty units and policy_b mostly on high ones; pooling favours policy_a.
TRUTH      policy_b has 1.5 lower mean loss on identical units.
DEFENCE    Pair on shared units, print the allocation ledger, and report per stratum.
SUPPORTS   That the aggregate and within-stratum contrasts disagree, and how the allocation differed.
STILL NOT  Why the allocation differed, or that it was the cause; the ledger describes, it does not diagnose.

  true difference (B - A): -1.500 (negative = B has lower loss)
  allocation: policy_a ran on 30 low-difficulty units and 6 high; policy_b on 30 high and 6 low. Only 12 units carry both.

  WRONG  pooled over each policy's own units: +3.223 [+2.137, +4.479] -> favours policy_a
  RIGHT  paired on the 12 shared units: -1.693 [-2.053, -1.371] -> favours policy_b

  allocation imbalance on `difficulty`: 67% share gap
  stratum high: favours policy_b
  stratum low: favours policy_b

Trap reproduced: **True** · Defence held: **True**


### dependence_above_declared_unit: Units that share a higher-level shock

TRAP       80 units look like 80 replicates, so a unit-level cluster bootstrap looks correct and is not.
TRUTH      The units come in 20 groups of 4 sharing an event-level shock that acts differently on the two policies, so there are 20 independent replicates.
DEFENCE    Declare the inference structure and check it against the records; an event that contains units is refused as a nested level.
SUPPORTS   That resampling at the wrong level under-covers even though it is a cluster bootstrap.
STILL NOT  That the library can detect an unrecorded dependence; it can only see groupings that appear in the records.

  design: 80 units, but each group of 4 shares one event. The event carries a shock that affects the two policies DIFFERENTLY, so it does not cancel in the paired difference. The event is a level ABOVE the unit: there are 20 independent replicates, not 80.
  true difference: +0.600

  WRONG  inference.primary_unit: world_id
         refused: the declared inference structure contradicts the records:
         if forced anyway: world_id-level cluster bootstrap: 78.7% coverage of a nominal 95% interval (R=150, MC SE 0.033, 95% Wilson CI [71.4%, 84.5%])

  RIGHT  inference.primary_unit: event_id
         +1.032 [+0.127, +1.915] over 20 events
         event_id-level cluster bootstrap: 93.3% coverage of a nominal 95% interval (R=150, MC SE 0.020, 95% Wilson CI [88.2%, 96.3%])

Trap reproduced: **True** · Defence held: **True**


### outcome_dependent_missingness: Runs that crash on the hardest units

TRAP       policy_b's crashed runs are treated as missing values and dropped, so it is scored only where it survived.
TRUTH      policy_b is worse by +1.0, and its runs crash on the 18 hardest units.
DEFENCE    Keep failed runs in the records with a status, declare the mechanism, and report a worst-case bound beside the complete-case estimate.
SUPPORTS   The complete-case contrast and a bound on how far the missing runs could move it.
STILL NOT  The target-population contrast; no method recovers outcomes that were never produced.

  true difference (B - A): +1.000
  18 of 50 units have policy_b runs marked `crashed`; mechanism declared `MNAR`

  WRONG  drop them: +0.974 [+0.680, +1.292] over 32 units
  BOUND  impute worst: +4.166 [+2.805, +5.599]

  ledger: 1080 rows did not complete; they stay in the accounting
  COMPLETE-CASE REPRESENTATIVENESS: the world_ids excluded by the pairing differ from the shared ones on this metric by more than 0.2 pooled SD. The paired estimate remains correct for the 32 shared world_ids, but it should not be read as an estimate for the full set of 50 observed world_ids, still less for the target population.

Trap reproduced: **True** · Defence held: **True**


### too_few_units: Many observations, almost no replicates

TRAP       A 90% CVaR computed from 5 units is reported like any other number.
TRUTH      Every design here has 160 observations per unit; only the unit count varies, from 5 to 50.
DEFENCE    Per-estimator credibility thresholds, so the warning matches the statistic instead of a universal minimum n.
SUPPORTS   Which statistics the available unit count can support.
STILL NOT  A universal minimum sample size; the threshold depends on the estimator.

  5 units, 160 observations each: plenty of rows, almost no replicates

     n  metric                    difference    width  credible
     5  mean_loss     +1.085 [+0.037, +2.223]    2.186  yes
     5  cvar90_loss   -0.789 [-1.681, +0.500]    2.181  NO
     5  p90_loss      -0.563 [-1.681, +0.979]    2.660  NO
    10  mean_loss     +0.661 [-0.116, +1.414]    1.530  yes
    10  cvar90_loss   +0.062 [-1.534, +3.044]    4.578  NO
    10  p90_loss      +0.308 [-1.398, +2.668]    4.067  NO
    20  mean_loss     +0.434 [-0.449, +1.347]    1.795  yes
    20  cvar90_loss   +1.974 [+0.799, +4.325]    3.525  yes
    20  p90_loss      +2.169 [+0.054, +4.696]    4.642  yes
    50  mean_loss     +0.787 [+0.216, +1.313]    1.097  yes
    50  cvar90_loss   +1.115 [-0.144, +2.406]    2.551  yes
    50  p90_loss      +0.918 [-0.578, +2.289]    2.867  yes

Trap reproduced: **True** · Defence held: **True**


### wrong_cvar_tail: CVaR summarising the beneficial tail

TRAP       A higher-is-better metric analysed with an upper-tail CVaR describes the best events and calls the result risk.
TRUTH      policy_a has the higher mean success rate and a much heavier low tail.
DEFENCE    `tail: harmful` resolves against the declared direction; a contradictory `params.tail` is rejected at config load.
SUPPORTS   The contrast in the harmful tail, with the tail side printed beside it.
STILL NOT  Anything about the harmful tail from a beneficial-tail statistic, however confident its interval looks.

  metric orientation: higher_is_better; harmful tail is the lower one

  WRONG  cvar(upper) on success: -0.0429 [-0.0643, -0.0304] -> favours policy_a
  RIGHT  cvar(lower) on success: +0.5357 [+0.4223, +0.5768] -> favours policy_b

  right-hand definition: CVaR at alpha=0.9 in the lower tail of event_id-level values of `mission_success`: the unweighted mean of the k = max(1, ceil((1-alpha)*n)) most extreme values, with (1-alpha)*n rounded to 9 decimals before the ceiling, and ties included by position in the sorted order

Trap reproduced: **True** · Defence held: **True**


### asymmetric_margin: A symmetric margin that hides an unacceptable loss

TRAP       A symmetric +/-0.50 margin declares a 0.25 increase in loss negligible.
TRUTH      policy_b is 0.25 worse, and only 0.15 of extra loss was acceptable.
DEFENCE    Support asymmetric margins, and record each margin's source.
SUPPORTS   Whether the contrast lies inside the margin that was actually declared.
STILL NOT  Which margin is correct; the library checks arithmetic against a declaration, not the declaration itself.

  true difference: +0.250 (B has +0.25 more loss)
  90% TOST interval: [+0.1515, +0.2944]

  SYMMETRIC  +/-0.5 -> statistically_equivalent
  ASYMMETRIC (-0.5, +0.15) -> not_equivalent

  Not equivalent: the 90% interval (0.1515, 0.2944) lies entirely outside the declared margin (-0.5, +0.15), so the estimated difference exceeds what was declared negligible.

Trap reproduced: **True** · Defence held: **True**


### bounded_outcome_interval: An interval that runs past the end of the scale

TRAP       A basic bootstrap interval on a rate near 1.0 includes values above 1.0 and is reported without comment.
TRUTH      The metric is a proportion with declared support [0, 1].
DEFENCE    Declare the support, check the endpoints, report the violation, and do not clamp.
SUPPORTS   That the interval construction does not suit this metric here.
STILL NOT  A corrected interval; the library flags the problem rather than silently substituting a different method.

  a success rate pushed close to its upper bound of 1.0, with only 8 units. The sampling distribution is squeezed against the boundary and is left-skewed, so reflecting it about the estimate sends the upper endpoint past 1.
  declared support: [0.0, 1.0]

  WRONG  basic:      0.9875 [0.9775, 1.0025]  impossible=True
  RIGHT  percentile: 0.9875 [0.9725, 0.9975]  impossible=False

  note: Interval endpoints lie outside the metric's declared support. They are reported unclamped: truncating them would understate the interval's width without making the construction appropriate. Consider an interval on a transformed scale.

Trap reproduced: **True** · Defence held: **True**


### uncorrected_family: Twenty null metrics, uncorrected

TRAP       Twenty exploratory contrasts on pure noise are reported individually and some of them resolve.
TRUTH      Every one of the twenty columns has a true difference of exactly zero.
DEFENCE    Assign each metric a role, declare the family, and apply Holm across it.
SUPPORTS   Which findings survive correction over the family that was declared.
STILL NOT  Anything about a family the configuration did not declare; the library will not infer one.

  20 outcome columns generated with no policy effect at all, analysed as one declared exploratory family alongside a null primary metric.
  true difference on every metric: +0.0

  WRONG  21 uncorrected contrasts -> 1 resolved: ['noise_12']
  RIGHT  Holm over the declared secondary family of [20] -> 0 survive: []

  The primary family of 1 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.
  Holm correction applied across the declared secondary family of 20 test(s) at alpha=0.05. Confidence intervals shown elsewhere are marginal and are NOT adjusted for multiplicity.

Trap reproduced: **True** · Defence held: **True**


### failed_runs_as_missing: A crashed run counted as a missing value

TRAP       policy_b crashes on 22% of runs; dropping those runs makes it look better.
TRUTH      policy_b raises the success rate of the runs that finish while failing to finish more than a fifth of them.
DEFENCE    Declare how each run status is handled; counting a crash as a failure keeps it in the denominator.
SUPPORTS   That the finding's sign depends on the declared handling, and what each handling gives.
STILL NOT  Which handling is right; that is a domain question the library forces into the open rather than answering.

  policy_b raises the success rate of the runs that complete, but 22% of its runs crash and produce nothing. Whether that is an improvement depends entirely on how a crash is counted.

  WRONG  crashes -> missing, then dropped: +0.0640 [+0.0489, +0.0793] -> favours policy_b
  RIGHT  crashes -> counted as failures:   -0.1589 [-0.2129, -0.1057] -> favours policy_a

  run status ledger:
    policy_a   completed      completed            rows=3600
    policy_b   completed      completed            rows=2680
    policy_b   crashed        failure              rows=920

Trap reproduced: **True** · Defence held: **True**


### paired_data_analysed_unpaired: Paired data analysed as two independent arms

TRAP       A fully paired design compared arm-to-arm loses a real effect in unit variance.
TRUTH      Every unit carries both policies; the unit SD is 4.0 and the effect is 0.9.
DEFENCE    Pair within unit by default, and label any unpaired comparison as such everywhere it appears.
SUPPORTS   That discarding the pairing widens the interval enough to change the finding.
STILL NOT  That pairing is always available; when units are not shared, the estimand changes rather than the precision.

  a fully paired design: every unit carries both policies, and the unit effect (SD 4.0) is far larger than the policy effect (0.9).
  true difference: +0.900

  WRONG  unpaired: +0.966 [-1.237, +3.146]  width 4.383  -> inconclusive
  RIGHT  paired:   +0.966 [+0.672, +1.265]  width 0.593  -> worse

  width ratio: 7.4x

Trap reproduced: **True** · Defence held: **True**


### metric_selected_after_the_fact: Choosing the headline metric after seeing the results

TRAP       Twelve exploratory metrics carry no effect, one of them looks favourable, and it becomes the headline.
TRUTH      policy_b is genuinely worse on the declared primary metric by +0.55.
DEFENCE    Exactly one primary metric, reported first; every other finding labelled with its role and corrected within its declared family.
SUPPORTS   The primary contrast, and that the favourable exploratory ones are consistent with noise.
STILL NOT  That the analysis was preregistered; `analysis_status` records the claim and the existence of a config file is not evidence for it.

  policy_b is genuinely worse on the declared primary metric. Forty exploratory columns carry no effect at all, and some of them will look favourable.

  PRIMARY (declared)  mean_loss: +0.421 [+0.083, +0.773] -> favours policy_a
  SHOPPED (exploratory) noise_03: -0.074 [-0.160, +0.001] -> favours None, raw p=0.052, adjusted p=1.0

  13 of 40 noise metrics point at policy_b; 0 of them resolve at the 95% level
  report order: primary section at char 1177, exploratory at 1646

Trap reproduced: **True** · Defence held: **True**
