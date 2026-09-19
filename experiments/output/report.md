# Full report: tail-risk dataset

## 1. What was analysed

- **Source**: `/home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet`
- **Checksum**: `sha256:f23f0cb04377cc16f9d18cdf4eb05e905e550a8d23cc7f6d8e8146d536006160`
- **Rows**: 7200 (parquet)
- **Unit of inference**: `world`
- **Resampling**: 1500 bootstrap resamples of whole `world`s, seed `20260919`, method `percentile`, 95% intervals
- **Pairing**: paired on shared clusters
- **Missing data**: `drop_record`
- **Code**: wg-eval 0.1.0 (git de836e8+dirty)

### Design

- worlds: **60**, events: 240, observations: 7200
- observations per world: 120.0 (these are nested, not independent)
- policies: policy_a, policy_b
- worlds carrying every policy: 60

### Filters and exclusions

- none applied

## 2. Results

| metric       | level    | contrast             | baseline | candidate | difference | 95% CI               | clusters | margin | verdict      |
|--------------|----------|----------------------|----------|-----------|------------|----------------------|----------|--------|--------------|
| mean_loss    | event    | policy_b vs policy_a | 8.375    | 9.221     | 0.8458     | [0.3655, 1.332]      | 60       | none   | worse        |
| cvar90_loss  | event    | policy_b vs policy_a | 16.36    | 13.65     | -2.709     | [-4.71, -1.216]      | 60       | none   | superior     |
| p90_loss     | event    | policy_b vs policy_a | 13.47    | 12.38     | -1.089     | [-2.31, 0.2484]      | 60       | none   | inconclusive |
| success_rate | resident | policy_b vs policy_a | 0.8275   | 0.8072    | -0.02028   | [-0.0375, -0.003611] | 60       | none   | worse        |

### Verdicts

- **mean_loss** — mean_loss: policy_a beats policy_b. The 95% paired interval for policy_b - policy_a is (0.3655, 1.332) and excludes 0.
  - note: No practical margin declared for mean_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.
- **cvar90_loss** — cvar90_loss: policy_b beats policy_a. The 95% paired interval for policy_b - policy_a is (-4.71, -1.216) and excludes 0.
  - note: No practical margin declared for cvar90_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.
- **p90_loss** — p90_loss: the 95% interval for policy_b - policy_a is (-2.31, 0.2484) and includes 0. No practical margin was declared, so equivalence cannot be assessed. This is 'undetermined', NOT 'no difference'.
  - note: No practical margin declared for p90_loss; equivalence cannot be concluded for this metric however wide or narrow the interval is.
- **success_rate** — success_rate: policy_a beats policy_b. The 95% paired interval for policy_b - policy_a is (-0.0375, -0.003611) and excludes 0.
  - note: No practical margin declared for success_rate; equivalence cannot be concluded for this metric however wide or narrow the interval is.

## 3. Disagreements between metrics

- TAIL DISAGREEMENT on `loss`: mean_loss favours policy_a while cvar90_loss favours policy_b. Average performance and tail risk point in opposite directions; reporting either alone would mislead.
- METRIC SPLIT for policy_b vs policy_a: different declared metrics resolve in favour of different policies (mean_loss->policy_a, cvar90_loss->policy_b, success_rate->policy_a). There is no single winner; the choice depends on which metric the decision actually cares about, and that must be declared in advance.

## 4. Design diagnostics

| metric       | policy   | ICC     | mean cluster size | design effect | effective n | observations |
|--------------|----------|---------|-------------------|---------------|-------------|--------------|
| mean_loss    | policy_a | 0.449   | 4                 | 2.35          | 102.3       | 240          |
| mean_loss    | policy_b | 0.893   | 4                 | 3.68          | 65.24       | 240          |
| cvar90_loss  | policy_a | 0.449   | 4                 | 2.35          | 102.3       | 240          |
| cvar90_loss  | policy_b | 0.893   | 4                 | 3.68          | 65.24       | 240          |
| p90_loss     | policy_a | 0.449   | 4                 | 2.35          | 102.3       | 240          |
| p90_loss     | policy_b | 0.893   | 4                 | 3.68          | 65.24       | 240          |
| success_rate | policy_a | 0.00993 | 60                | 1.59          | 2270        | 3600         |
| success_rate | policy_b | 0.0211  | 60                | 2.25          | 1602        | 3600         |

A design effect of *d* means the nested observations carry the information of roughly `observations / d` independent ones. It is a diagnostic; the cluster bootstrap, not this number, produces the intervals.

## 5. Failure analysis

```
failure analysis on `failure_reason`
  policy_a: 621 failures / 3600 observations (17.2%)
  policy_b: 694 failures / 3600 observations (19.3%)

  policy        reason                         count    share     rate
  policy_a      unreachable                      341    54.9%    9.47%
  policy_a      late_arrival                     140    22.5%    3.89%
  policy_a      resource_exhausted               140    22.5%    3.89%
  policy_b      late_arrival                     390    56.2%   10.83%
  policy_b      route_blocked                    201    29.0%    5.58%
  policy_b      resource_exhausted               103    14.8%    2.86%
  note: policy_b raises the per-world_id rate of 'late_arrival' by +6.94% on average relative to policy_a; an improved overall rate can still hide a worsened failure mode.
```

Paired per-world shift in each failure mode (candidate − baseline):

| reason             | baseline rate | candidate rate | paired shift | worlds |
|--------------------|---------------|----------------|--------------|--------|
| late_arrival       | 0.0389        | 0.108          | 0.0694       | 60     |
| route_blocked      | 0             | 0.0558         | 0.0558       | 60     |
| resource_exhausted | 0.0389        | 0.0286         | -0.0103      | 60     |
| unreachable        | 0.0947        | 0              | -0.0947      | 60     |

## 6. Stratified results

### By `landscape`

| landscape | metric       | contrast             | difference | CI                   | clusters | verdict      |
|-----------|--------------|----------------------|------------|----------------------|----------|--------------|
| __all__   | mean_loss    | policy_b vs policy_a | 0.8458     | [0.3655, 1.332]      | 60       | worse        |
| __all__   | cvar90_loss  | policy_b vs policy_a | -2.709     | [-4.71, -1.216]      | 60       | superior     |
| __all__   | p90_loss     | policy_b vs policy_a | -1.089     | [-2.31, 0.2484]      | 60       | inconclusive |
| __all__   | success_rate | policy_b vs policy_a | -0.02028   | [-0.0375, -0.003611] | 60       | worse        |
| flat      | mean_loss    | policy_b vs policy_a | 0.8782     | [0.1267, 1.59]       | 30       | worse        |
| flat      | cvar90_loss  | policy_b vs policy_a | -3.389     | [-6.595, -0.8015]    | 30       | superior     |
| flat      | p90_loss     | policy_b vs policy_a | -1.047     | [-2.95, 0.7266]      | 30       | inconclusive |
| flat      | success_rate | policy_b vs policy_a | -0.01944   | [-0.04222, 0.001667] | 30       | inconclusive |
| steep     | mean_loss    | policy_b vs policy_a | 0.8134     | [0.1548, 1.439]      | 30       | worse        |
| steep     | cvar90_loss  | policy_b vs policy_a | -2.141     | [-3.695, -0.7773]    | 30       | superior     |
| steep     | p90_loss     | policy_b vs policy_a | -0.6808    | [-1.888, 0.4245]     | 30       | inconclusive |
| steep     | success_rate | policy_b vs policy_a | -0.02111   | [-0.04751, 0.006667] | 30       | inconclusive |

Allocation ledger (how many worlds of each stratum each policy actually ran):

| policy   | landscape | worlds | share of that policy's worlds |
|----------|-----------|--------|-------------------------------|
| policy_a | flat      | 30     | 50%                           |
| policy_a | steep     | 30     | 50%                           |
| policy_b | flat      | 30     | 50%                           |
| policy_b | steep     | 30     | 50%                           |


### By `mobility`

| mobility | metric       | contrast             | difference | CI                    | clusters | verdict      |
|----------|--------------|----------------------|------------|-----------------------|----------|--------------|
| __all__  | mean_loss    | policy_b vs policy_a | 0.8458     | [0.3655, 1.332]       | 60       | worse        |
| __all__  | cvar90_loss  | policy_b vs policy_a | -2.709     | [-4.71, -1.216]       | 60       | superior     |
| __all__  | p90_loss     | policy_b vs policy_a | -1.089     | [-2.31, 0.2484]       | 60       | inconclusive |
| __all__  | success_rate | policy_b vs policy_a | -0.02028   | [-0.0375, -0.003611]  | 60       | worse        |
| high     | mean_loss    | policy_b vs policy_a | 1.136      | [0.5635, 1.693]       | 30       | worse        |
| high     | cvar90_loss  | policy_b vs policy_a | -2.044     | [-3.855, -0.6097]     | 30       | superior     |
| high     | p90_loss     | policy_b vs policy_a | -1.685     | [-3.141, 0.8798]      | 30       | inconclusive |
| high     | success_rate | policy_b vs policy_a | -0.02278   | [-0.04778, 0.0005556] | 30       | inconclusive |
| low      | mean_loss    | policy_b vs policy_a | 0.5556     | [-0.2737, 1.314]      | 30       | inconclusive |
| low      | cvar90_loss  | policy_b vs policy_a | -3.35      | [-6.93, -0.747]       | 30       | superior     |
| low      | p90_loss     | policy_b vs policy_a | -0.7566    | [-2.441, 0.5728]      | 30       | inconclusive |
| low      | success_rate | policy_b vs policy_a | -0.01778   | [-0.04, 0.006667]     | 30       | inconclusive |

Allocation ledger (how many worlds of each stratum each policy actually ran):

| policy   | mobility | worlds | share of that policy's worlds |
|----------|----------|--------|-------------------------------|
| policy_a | high     | 30     | 50%                           |
| policy_a | low      | 30     | 50%                           |
| policy_b | high     | 30     | 50%                           |
| policy_b | low      | 30     | 50%                           |


## 7. Notes carried from the analysis

- 7200 observations nested in 60 worlds; the bootstrap resamples worlds, not observations.
- TAIL DISAGREEMENT on `loss`: mean_loss favours policy_a while cvar90_loss favours policy_b. Average performance and tail risk point in opposite directions; reporting either alone would mislead.
- METRIC SPLIT for policy_b vs policy_a: different declared metrics resolve in favour of different policies (mean_loss->policy_a, cvar90_loss->policy_b, success_rate->policy_a). There is no single winner; the choice depends on which metric the decision actually cares about, and that must be declared in advance.

## 8. Provenance

<details><summary><code>mean_loss</code> — policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git de836e8+dirty)
created_at: 2026-09-19T18:08:09+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:f23f0cb04377cc16f9d18cdf4eb05e905e550a8d23cc7f6d8e8146d536006160
config: None (sha256:ceb047140c553b48fff28472fd13845194afc64b62d20125523e69c0016259a7)
metric: mean_loss = arithmetic mean of event-level values of `loss`
unit_of_inference: world
bootstrap: 1500 resamples at world level, seed=20260919, method=percentile
confidence_level: 0.95
filters: none
exclusions: none
fingerprint: sha256:e3375edd17b57b911a9e4a9248bd063c9d12e4d83b6ceb38541efffbd296f124
```

</details>

<details><summary><code>cvar90_loss</code> — policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git de836e8+dirty)
created_at: 2026-09-19T18:08:09+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:f23f0cb04377cc16f9d18cdf4eb05e905e550a8d23cc7f6d8e8146d536006160
config: None (sha256:ceb047140c553b48fff28472fd13845194afc64b62d20125523e69c0016259a7)
metric: cvar90_loss = CVaR at alpha=0.9 (upper tail) of event-level values of `loss`: the mean of the k = max(1, ceil((1-alpha) * n)) most extreme values
unit_of_inference: world
bootstrap: 1500 resamples at world level, seed=20260919, method=percentile
confidence_level: 0.95
filters: none
exclusions: none
fingerprint: sha256:bd63bf48fca7eb3063e8f95fa6246442d6677f868175c7f649cb07bb24780cd3
```

</details>

<details><summary><code>p90_loss</code> — policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git de836e8+dirty)
created_at: 2026-09-19T18:08:09+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:f23f0cb04377cc16f9d18cdf4eb05e905e550a8d23cc7f6d8e8146d536006160
config: None (sha256:ceb047140c553b48fff28472fd13845194afc64b62d20125523e69c0016259a7)
metric: p90_loss = the q=0.9 quantile (linear interpolation) of event-level values of `loss`
unit_of_inference: world
bootstrap: 1500 resamples at world level, seed=20260919, method=percentile
confidence_level: 0.95
filters: none
exclusions: none
fingerprint: sha256:f44e2c7e4b80e785a18e11b7efab75174d74fabfaca91bf3ab9211e838b7891d
```

</details>

<details><summary><code>success_rate</code> — policy_b vs policy_a</summary>

```
code_version: wg-eval 0.1.0 (git de836e8+dirty)
created_at: 2026-09-19T18:08:09+00:00
source: /home/user/wildfireguardian-evaluation/experiments/output/data/tail_risk_disagreement.parquet
source_checksum: sha256:f23f0cb04377cc16f9d18cdf4eb05e905e550a8d23cc7f6d8e8146d536006160
config: None (sha256:ceb047140c553b48fff28472fd13845194afc64b62d20125523e69c0016259a7)
metric: success_rate = arithmetic mean of resident-level values of `mission_success`
unit_of_inference: world
bootstrap: 1500 resamples at world level, seed=20260919, method=percentile
confidence_level: 0.95
filters: none
exclusions: none
fingerprint: sha256:d496c6895f515c1ffbaaa03cc63330ad31842adacf62c2a3f546276c8f3ff1d7
```

</details>

---

Reading rules for this report:

1. An interval that includes zero means **undetermined**, never "no difference". Only a TOST against a declared margin can support equivalence.
2. Every interval here comes from resampling whole `world`s. Observation counts are reported for context and are not the sample size.
3. Where metrics disagree, the disagreement is the finding. Do not pick the flattering one after the fact.
