# Full report: tail-risk dataset

## 1. What was analysed

- **Source**: `/home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet`
- **Checksum**: `sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e`
- **Rows**: 7200 (parquet)
- **Inference structure**: `world_id > event_id > resident_id   (resampling unit: world_id; 2 nested level(s))` (declared, and checked against the records)
- **Resampling**: 1500 bootstrap resamples of whole `world_id`s, seed `20260919`, method `percentile`, 95% intervals
- **Pairing**: paired on shared units
- **Missing data**: policy `drop_record`, assumed mechanism `unknown`
- **Analysis status**: `unspecified` -- not asserted to be preregistered
- **Code**: wg-eval 0.1.0 (git 3d729e2+dirty) | report generator report-1.0.0

### Design

- hierarchy: **world_id > event_id > resident_id**
- world_ids: **60**, observations: 7200
- observations per world_id: 120.0 (nested, not independent)
- policies: policy_a, policy_b
- world_ids carrying every policy: 60

### Filters and exclusions

- none applied

### Run status ledger

| policy   | status    | handling  | rows | units |
|----------|-----------|-----------|------|-------|
| policy_a | completed | completed | 3600 | 60    |
| policy_b | completed | completed | 3600 | 60    |

Declared missingness mechanism: `unknown`.

A run that did not complete is not automatically a missing value. Rows handled as `failure` stay in the denominator; rows handled as `excluded_documented` condition the estimand on feasibility.

## 2. Findings

### Primary outcome

**mean_loss** (lower_is_better, at `event_id` level)

- mean_loss: the 95% interval for the estimated difference (policy_b - policy_a) is (0.3655, 1.332) and excludes 0, favouring policy_a on this metric, paired on 60 shared world_id.
- estimand conditioning: all 60 observed world_ids carry every policy
- note: No practical margin declared for mean_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.

### Secondary outcomes

**cvar90_loss** (lower_is_better, at `event_id` level)

- cvar90_loss: the 95% interval for the estimated difference (policy_b - policy_a) is (-4.71, -1.216) and excludes 0, favouring policy_b on this metric, paired on 60 shared world_id.
- tail convention: summarises the **upper** tail, which is the harmful end for a `lower_is_better` metric
- estimand conditioning: all 60 observed world_ids carry every policy
- note: No practical margin declared for cvar90_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.

**success_rate** (higher_is_better, at `resident_id` level)

- success_rate: the 95% interval for the estimated difference (policy_b - policy_a) is (-0.0375, -0.003611) and excludes 0, favouring policy_a on this metric, paired on 60 shared world_id.
- estimand conditioning: all 60 observed world_ids carry every policy
- note: No practical margin declared for success_rate; equivalence cannot be concluded for this metric however wide or narrow the interval is.

### Exploratory outcomes

**p90_loss** (lower_is_better, at `event_id` level)

- p90_loss: the 95% interval for the estimated difference (policy_b - policy_a) is (-2.31, 0.2484) and includes 0. No practical margin was declared, so equivalence cannot be assessed. This is 'undetermined', not a finding that the policies match, paired on 60 shared world_id.
- tail convention: summarises the **upper** tail, which is the harmful end for a `lower_is_better` metric
- estimand conditioning: all 60 observed world_ids carry every policy
- note: No practical margin declared for p90_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.

### All contrasts

| role        | metric       | better | contrast             | baseline | candidate | difference | 95% CI               | world_ids | margin | adj. p   | finding      |
|-------------|--------------|--------|----------------------|----------|-----------|------------|----------------------|-----------|--------|----------|--------------|
| primary     | mean_loss    | lower  | policy_b vs policy_a | 8.375    | 9.221     | 0.8458     | [0.3655, 1.332]      | 60        | none   | 0.00133  | worse        |
| secondary   | cvar90_loss  | lower  | policy_b vs policy_a | 16.36    | 13.65     | -2.709     | [-4.71, -1.216]      | 60        | none   | 0.000666 | superior     |
| secondary   | success_rate | higher | policy_b vs policy_a | 0.8275   | 0.8072    | -0.02028   | [-0.0375, -0.003611] | 60        | none   | 0.0187   | worse        |
| exploratory | p90_loss     | lower  | policy_b vs policy_a | 13.47    | 12.38     | -1.089     | [-2.31, 0.2484]      | 60        | none   | 0.0933   | inconclusive |

`adj. p` is adjusted within the declared role family; intervals are marginal and are not adjusted for multiplicity.

## 3. Where the metrics disagree

- CENTRAL AND TAIL CONTRASTS DISAGREE on `loss` (lower_is_better; the upper tail is the harmful one). mean_loss: +0.8458 [+0.3655, +1.332] favours policy_a. cvar90_loss: -2.709 [-4.71, -1.216] favours policy_b. Both contrasts are reported with their intervals; this library does not rank the two and implies no overall preference. Which one governs the decision is a declared risk preference, not a statistical result.
- METRICS RESOLVE IN DIFFERENT DIRECTIONS for policy_b vs policy_a: mean_loss (primary) favours policy_a, cvar90_loss (secondary) favours policy_b, success_rate (secondary) favours policy_a. No single ranking follows; the primary metric declared in advance is the one the analysis was designed to answer.

## 4. Analysis families and multiplicity

| role        | test                              | method | family size | p (raw)  | p (adj)  | survives |
|-------------|-----------------------------------|--------|-------------|----------|----------|----------|
| primary     | mean_loss|policy_b_vs_policy_a    | none   | 1           | 0.00133  | 0.00133  | yes      |
| secondary   | cvar90_loss|policy_b_vs_policy_a  | none   | 2           | 0.000666 | 0.000666 | yes      |
| secondary   | success_rate|policy_b_vs_policy_a | none   | 2           | 0.0187   | 0.0187   | yes      |
| exploratory | p90_loss|policy_b_vs_policy_a     | none   | 1           | 0.0933   | 0.0933   | no       |

- The primary family of 1 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.
- The secondary family of 2 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.
- The exploratory family of 1 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.

## 5. Design diagnostics

| metric       | policy   | ICC     | mean unit size | design effect | effective n | observations |
|--------------|----------|---------|----------------|---------------|-------------|--------------|
| mean_loss    | policy_a | 0.449   | 4              | 2.35          | 102.3       | 240          |
| mean_loss    | policy_b | 0.893   | 4              | 3.68          | 65.24       | 240          |
| cvar90_loss  | policy_a | 0.449   | 4              | 2.35          | 102.3       | 240          |
| cvar90_loss  | policy_b | 0.893   | 4              | 3.68          | 65.24       | 240          |
| success_rate | policy_a | 0.00993 | 60             | 1.59          | 2270        | 3600         |
| success_rate | policy_b | 0.0211  | 60             | 2.25          | 1602        | 3600         |
| p90_loss     | policy_a | 0.449   | 4              | 2.35          | 102.3       | 240          |
| p90_loss     | policy_b | 0.893   | 4              | 3.68          | 65.24       | 240          |

A design effect of *d* means the nested observations carry the information of roughly `observations / d` independent ones. It is a diagnostic; the cluster bootstrap, not this number, produces the intervals.

## 6. Failure analysis

```
failure analysis on `failure_reason`
  policy_a: 621 failures / 3600 observations (17.2%)
  policy_b: 694 failures / 3600 observations (19.3%)

  policy        reason                         count    share     rate
  policy_a      reason_a                         341    54.9%    9.47%
  policy_a      reason_b                         140    22.5%    3.89%
  policy_a      reason_c                         140    22.5%    3.89%
  policy_b      reason_b                         390    56.2%   10.83%
  policy_b      reason_d                         201    29.0%    5.58%
  policy_b      reason_c                         103    14.8%    2.86%
  note: policy_b raises the per-world_id rate of 'reason_b' by +6.94% on average relative to policy_a; an improved overall rate can still hide a worsened failure mode.
```

Paired per-world_id shift in each failure mode (candidate - baseline):

| reason   | baseline rate | candidate rate | paired shift | world_ids |
|----------|---------------|----------------|--------------|-----------|
| reason_b | 0.0389        | 0.108          | 0.0694       | 60        |
| reason_d | 0             | 0.0558         | 0.0558       | 60        |
| reason_c | 0.0389        | 0.0286         | -0.0103      | 60        |
| reason_a | 0.0947        | 0              | -0.0947      | 60        |

## 7. Stratified results and allocation

### By `difficulty`

| difficulty | role        | metric       | contrast             | difference | CI                   | world_ids | finding      |
|------------|-------------|--------------|----------------------|------------|----------------------|-----------|--------------|
| __all__    | primary     | mean_loss    | policy_b vs policy_a | 0.8458     | [0.3655, 1.332]      | 60        | worse        |
| __all__    | secondary   | cvar90_loss  | policy_b vs policy_a | -2.709     | [-4.71, -1.216]      | 60        | superior     |
| __all__    | secondary   | success_rate | policy_b vs policy_a | -0.02028   | [-0.0375, -0.003611] | 60        | worse        |
| __all__    | exploratory | p90_loss     | policy_b vs policy_a | -1.089     | [-2.31, 0.2484]      | 60        | inconclusive |
| high       | primary     | mean_loss    | policy_b vs policy_a | 0.8134     | [0.1548, 1.439]      | 30        | worse        |
| high       | secondary   | cvar90_loss  | policy_b vs policy_a | -2.141     | [-3.695, -0.7773]    | 30        | superior     |
| high       | secondary   | success_rate | policy_b vs policy_a | -0.02111   | [-0.04751, 0.006667] | 30        | inconclusive |
| high       | exploratory | p90_loss     | policy_b vs policy_a | -0.6808    | [-1.888, 0.4245]     | 30        | inconclusive |
| low        | primary     | mean_loss    | policy_b vs policy_a | 0.8782     | [0.1267, 1.59]       | 30        | worse        |
| low        | secondary   | cvar90_loss  | policy_b vs policy_a | -3.389     | [-6.595, -0.8015]    | 30        | superior     |
| low        | secondary   | success_rate | policy_b vs policy_a | -0.01944   | [-0.04222, 0.001667] | 30        | inconclusive |
| low        | exploratory | p90_loss     | policy_b vs policy_a | -1.047     | [-2.95, 0.7266]      | 30        | inconclusive |

Allocation ledger (how many world_ids of each stratum each policy ran):

| policy   | difficulty | world_ids | share of that policy's units |
|----------|------------|-----------|------------------------------|
| policy_a | high       | 30        | 50%                          |
| policy_a | low        | 30        | 50%                          |
| policy_b | high       | 30        | 50%                          |
| policy_b | low        | 30        | 50%                          |

Composition shift, all observed units to shared units:

| difficulty | shift in share |
|------------|----------------|
| high       | +0.0%          |
| low        | +0.0%          |

A large shift means the paired analysis runs on a different mix of material than the full set of observed units.


### By `scale`

| scale   | role        | metric       | contrast             | difference | CI                    | world_ids | finding      |
|---------|-------------|--------------|----------------------|------------|-----------------------|-----------|--------------|
| __all__ | primary     | mean_loss    | policy_b vs policy_a | 0.8458     | [0.3655, 1.332]       | 60        | worse        |
| __all__ | secondary   | cvar90_loss  | policy_b vs policy_a | -2.709     | [-4.71, -1.216]       | 60        | superior     |
| __all__ | secondary   | success_rate | policy_b vs policy_a | -0.02028   | [-0.0375, -0.003611]  | 60        | worse        |
| __all__ | exploratory | p90_loss     | policy_b vs policy_a | -1.089     | [-2.31, 0.2484]       | 60        | inconclusive |
| large   | primary     | mean_loss    | policy_b vs policy_a | 0.5556     | [-0.2737, 1.314]      | 30        | inconclusive |
| large   | secondary   | cvar90_loss  | policy_b vs policy_a | -3.35      | [-6.93, -0.747]       | 30        | superior     |
| large   | secondary   | success_rate | policy_b vs policy_a | -0.01778   | [-0.04, 0.006667]     | 30        | inconclusive |
| large   | exploratory | p90_loss     | policy_b vs policy_a | -0.7566    | [-2.441, 0.5728]      | 30        | inconclusive |
| small   | primary     | mean_loss    | policy_b vs policy_a | 1.136      | [0.5635, 1.693]       | 30        | worse        |
| small   | secondary   | cvar90_loss  | policy_b vs policy_a | -2.044     | [-3.855, -0.6097]     | 30        | superior     |
| small   | secondary   | success_rate | policy_b vs policy_a | -0.02278   | [-0.04778, 0.0005556] | 30        | inconclusive |
| small   | exploratory | p90_loss     | policy_b vs policy_a | -1.685     | [-3.141, 0.8798]      | 30        | inconclusive |

Allocation ledger (how many world_ids of each stratum each policy ran):

| policy   | scale | world_ids | share of that policy's units |
|----------|-------|-----------|------------------------------|
| policy_a | large | 30        | 50%                          |
| policy_a | small | 30        | 50%                          |
| policy_b | large | 30        | 50%                          |
| policy_b | small | 30        | 50%                          |

Composition shift, all observed units to shared units:

| scale | shift in share |
|-------|----------------|
| large | +0.0%          |
| small | +0.0%          |

A large shift means the paired analysis runs on a different mix of material than the full set of observed units.


## 8. Notes carried from the analysis

- 7200 observations nested in 60 world_ids; the bootstrap resamples world_ids, not observations.
- RUN STATUS: all runs completed. Declared missingness mechanism: unknown.
- CENTRAL AND TAIL CONTRASTS DISAGREE on `loss` (lower_is_better; the upper tail is the harmful one). mean_loss: +0.8458 [+0.3655, +1.332] favours policy_a. cvar90_loss: -2.709 [-4.71, -1.216] favours policy_b. Both contrasts are reported with their intervals; this library does not rank the two and implies no overall preference. Which one governs the decision is a declared risk preference, not a statistical result.
- METRICS RESOLVE IN DIFFERENT DIRECTIONS for policy_b vs policy_a: mean_loss (primary) favours policy_a, cvar90_loss (secondary) favours policy_b, success_rate (secondary) favours policy_a. No single ranking follows; the primary metric declared in advance is the one the analysis was designed to answer.
- The primary family of 1 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.
- The secondary family of 2 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.
- The exploratory family of 1 test(s) is reported without multiplicity correction, as declared. Read it as that many separate comparisons.

## 9. Provenance and reproducibility

<details><summary><code>mean_loss</code> (primary) -- policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
created_at: 2026-09-20T03:38:34+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
config: None (sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd)
inference: primary_unit=world_id nested=['event_id', 'resident_id']
estimand: policy_b - policy_a at event_id level over world_id
conditioning: all 60 observed world_ids carry every policy
metric: mean_loss = arithmetic mean of event_id-level values of `loss`
bootstrap: 1500 resamples of world_ids, seed=20260919, method=percentile, hierarchical=False
confidence_level: 0.95
filters: none
exclusions: none
--- reproducibility manifest ---
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
analysis_config_hash: sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd
source_data_hash: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
protocol_hash: (none declared)
report_generator_version: report-1.0.0
scientific_fingerprint: sha256:a617ea2f4bfee0755048b50f3e8fbec0e2722a3ec217cb0edcda70728f500a73
full_fingerprint: sha256:9d3f78596b0c45da749d3a8376ff954eaed22679fc90730cb757d79d422b9484
```

</details>

<details><summary><code>cvar90_loss</code> (secondary) -- policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
created_at: 2026-09-20T03:38:34+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
config: None (sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd)
inference: primary_unit=world_id nested=['event_id', 'resident_id']
estimand: policy_b - policy_a at event_id level over world_id
conditioning: all 60 observed world_ids carry every policy
metric: cvar90_loss = CVaR at alpha=0.9 in the upper tail of event_id-level values of `loss`: the unweighted mean of the k = max(1, ceil((1-alpha)*n)) most extreme values, with (1-alpha)*n rounded to 9 decimals before the ceiling, and ties included by position in the sorted order
bootstrap: 1500 resamples of world_ids, seed=20260919, method=percentile, hierarchical=False
confidence_level: 0.95
filters: none
exclusions: none
--- reproducibility manifest ---
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
analysis_config_hash: sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd
source_data_hash: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
protocol_hash: (none declared)
report_generator_version: report-1.0.0
scientific_fingerprint: sha256:f1e521dd4ffda19c9e4a005a24277851d27ad513fadde6b7a9f9fb2f2c1d0443
full_fingerprint: sha256:a311fcfe412461f32f74d85afd5082b82e9c9d5d4292a1b02c6e54fc3ec2b762
```

</details>

<details><summary><code>success_rate</code> (secondary) -- policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
created_at: 2026-09-20T03:38:34+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
config: None (sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd)
inference: primary_unit=world_id nested=['event_id', 'resident_id']
estimand: policy_b - policy_a at resident_id level over world_id
conditioning: all 60 observed world_ids carry every policy
metric: success_rate = arithmetic mean of resident_id-level values of `mission_success`
bootstrap: 1500 resamples of world_ids, seed=20260919, method=percentile, hierarchical=False
confidence_level: 0.95
filters: none
exclusions: none
--- reproducibility manifest ---
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
analysis_config_hash: sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd
source_data_hash: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
protocol_hash: (none declared)
report_generator_version: report-1.0.0
scientific_fingerprint: sha256:e71875311689a14cbf1783a7653af4e69088ef88d3edae9abd6321b7dd68418e
full_fingerprint: sha256:6e7f9f1ac317a6c14079eadbb0532c26e5b1e51d90208ff5652af04c4b3c993b
```

</details>

<details><summary><code>p90_loss</code> (exploratory) -- policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
created_at: 2026-09-20T03:38:34+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
config: None (sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd)
inference: primary_unit=world_id nested=['event_id', 'resident_id']
estimand: policy_b - policy_a at event_id level over world_id
conditioning: all 60 observed world_ids carry every policy
metric: p90_loss = the q=0.9 quantile of event_id-level values of `loss`, by linear interpolation between order statistics (numpy 'linear' method: the quantile sits at position q*(n-1) in the sorted values, zero-indexed)
bootstrap: 1500 resamples of world_ids, seed=20260919, method=percentile, hierarchical=False
confidence_level: 0.95
filters: none
exclusions: none
--- reproducibility manifest ---
code_version: wg-eval 0.1.0 (git 3d729e2+dirty)
analysis_config_hash: sha256:b9cbaa3afdd6af4eaee33da42dee0b615740b0187345c33819d0aea590714edd
source_data_hash: sha256:133103f98a752425a2980b4ba43fac1c2ef6d7f3d635e363ad7648ffc12bec7e
protocol_hash: (none declared)
report_generator_version: report-1.0.0
scientific_fingerprint: sha256:0c88bf34e0f44efb7518e9c4ae29f4c1be0b17458da5404ebc28c42ce167ff71
full_fingerprint: sha256:9e6126d3ce0d622fd1d8986a6caf51743983f8433d7011da40d78e1c1a68c964
```

</details>

---

How to read this report:

1. An interval that includes zero means **undetermined**. Only a test against a declared margin can support equivalence, and equivalence within a margin is a statistical statement, not a statement that two options are interchangeable in use.
2. Every interval comes from resampling whole `world_id`s. Observation counts are context, not the sample size.
3. Findings are ordered primary, secondary, exploratory. A favourable exploratory metric is not a headline.
4. Where metrics disagree, the disagreement is the finding. This report names no winner across metrics.
