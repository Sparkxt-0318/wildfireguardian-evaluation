# Red-team scenarios

seed `20260919` | wg-eval 0.1.0 (git a5ae2b6)

Each scenario builds records with a known truth, runs the misleading analysis the records are designed to reward, and then runs the correct one.

### resident_bootstrap_false_precision: Resident-level bootstrap manufactures precision

TRAP     Resampling residents treats 1,200 nested observations as 1,200 experiments and returns a confidence interval several times too narrow.
TRUTH    The design supplies 20 independent worlds. The true mean loss difference is +0.6.
DEFENCE  Resample whole worlds; residents travel with the world they belong to.

records: 2400 observations in 20 worlds (60 observations per world per policy)
true mean loss difference (B - A): +0.600

  WRONG  resident-level bootstrap: +1.161 [+0.902, +1.421]  width 0.519
  RIGHT  world-level paired bootstrap: +1.161 [+0.192, +2.205]  width 2.013

  width ratio (right / wrong): 3.9x
  measured coverage over 60 replications -- resident: 62%, world: 90% (nominal 95%)
  design effect for policy_a: 1.9 (ICC 0.88, 40 observations worth 21 independent ones)

Trap reproduced: **True** · Defence held: **True**


### world_bootstrap_correct: World-level bootstrap is calibrated

TRAP     A single interval proves nothing; calibration has to be measured over repeated experiments.
TRUTH    The true mean loss difference is +0.6 in every replication.
DEFENCE  Measure coverage: a nominal 95% interval should contain the truth about 95% of the time.

true mean loss difference (B - A): +0.600
  world-level paired interval on this draw: [+0.192, +2.205] -> covers the truth
  measured coverage over 100 replications at 20 worlds: 90% (nominal 95%)
  measured coverage over 50 replications at 60 worlds: 98% (nominal 95%)
  for contrast, resident-level coverage: 61%
  mean interval width -- world: 1.723, resident: 0.659

Trap reproduced: **True** · Defence held: **True**


### tail_risk_disagreement: Mean favours A while tail risk favours B

TRAP     policy_a has the lower average loss, so a mean-only report recommends it.
TRUTH    policy_a carries a 4% per-observation chance of a +60 loss; its 90% CVaR is far worse than policy_b's while its mean is 0.8 better.
DEFENCE  Declare tail metrics up front and report the disagreement rather than resolving it silently.

  mean_loss   B - A = +0.846 [+0.365, +1.332] -> favours policy_a
  cvar90_loss B - A = -2.709 [-4.710, -1.216] -> favours policy_b

  TAIL DISAGREEMENT on `loss`: mean_loss favours policy_a while cvar90_loss favours policy_b. Average performance and tail risk point in opposite directions; reporting either alone would mislead.
  METRIC SPLIT for policy_b vs policy_a: different declared metrics resolve in favour of different policies (mean_loss->policy_a, cvar90_loss->policy_b, success_rate->policy_a). There is no single winner; the choice depends on which metric the decision actually cares about, and that must be declared in advance.

Trap reproduced: **True** · Defence held: **True**


### practical_equivalence: Practically equivalent, and provably so

TRAP     The interval includes zero, so the report says 'no significant difference' and readers hear 'the same'.
TRUTH    The true difference is +0.03 against a practical margin of 0.40 -- real, and negligible.
DEFENCE  TOST against the declared margin turns 'we failed to detect' into 'we established equivalence'.

  true difference: +0.030, declared practical margin: +/-0.4
  paired difference: -0.0055 [-0.1068, +0.0970]  (bootstrap p = 0.885)

  WRONG  'p > 0.05, therefore the policies are the same.'
  RIGHT  Equivalent within +/-0.4: the 90% interval for the difference (-0.09097, 0.08149) lies entirely inside the declared practical margin.
  verdict: equivalent -- mean_loss: policy_b and policy_a are practically equivalent within +/-0.4. The interval (-0.09097, 0.08149) fits inside the margin.

Trap reproduced: **True** · Defence held: **True**


### missing_worlds_reverse_ranking: Missing worlds reverse the ranking

TRAP     policy_b has no results on the 22 hardest worlds; comparing each policy over its own worlds makes the worse policy look better.
TRUTH    policy_b is worse by +1.2 mean loss on every world.
DEFENCE  Pair on the worlds both policies ran, and record every world excluded.

  true difference (B - A): +1.200 (positive = B is worse)
  policy_b is missing from 22 of 60 worlds -- the hardest ones

  WRONG  unpaired over available worlds: -1.862 [-3.147, -0.770] -> favours policy_b
  RIGHT  paired on the 38 shared worlds: +0.948 [+0.622, +1.266] -> favours policy_a

  The ranking reverses. Nothing about either policy changed; only which worlds were counted.

Trap reproduced: **True** · Defence held: **True**


### easier_worlds_confound: A policy wins only because it was tested on easier worlds

TRAP     policy_a was run mostly on flat worlds and policy_b mostly on steep ones; pooling the two makes policy_a look better.
TRUTH    policy_b is better by -1.5 mean loss on identical worlds.
DEFENCE  Pair on shared worlds, print the allocation ledger, and report per stratum.

  true difference (B - A): -1.500 (negative = B is better)
  allocation: policy_a ran on 30 flat worlds and 6 steep; policy_b on 30 steep and 6 flat. Only 12 worlds carry both.

  WRONG  pooled over each policy's own worlds: +3.223 [+2.137, +4.479] -> favours policy_a
  RIGHT  paired on the 12 shared worlds: -1.693 [-2.053, -1.371] -> favours policy_b

  allocation imbalance on `landscape`: 67% share gap
  stratum flat: favours policy_b
  stratum steep: favours policy_b

Trap reproduced: **True** · Defence held: **True**
