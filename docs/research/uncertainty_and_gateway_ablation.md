# Uncertainty on the headline result, and the unfaultable-gateway ablation

Date: 2026-09-16. Repository: `F:\twin-in-the-loop`. `paper/main.tex` was not modified.

## Bottom line

1. **The headline claim is not established.** The rule-agent twin gate's 4.4 violation-tick
   advantage over reject-all comes from **one seed out of ten**. The per-seed differences
   are `[0, 0, 0, 0, 0, 0, -44, 0, 0, 0]`. Exact Wilcoxon signed-rank p = **1.000** and paired
   t-test p = 0.343. The 95% t-interval [−14.35, +5.55] includes zero. With one non-tied pair,
   no rank test can reach significance. **At 10 seeds this difference cannot be told apart
   from noise.**
2. **The gateway is not where the benefit comes from, but that is because no agent ever
   uses it.** In every ablation run (4 conditions, 2 agents, 8 gate variants, about 9,200
   proposals), **no proposal migrated a service to the gateway**. The rule agent only
   considers edge nodes as migration targets (`src/twinloop/agent/rule_agent.py:118`, `:136`).
   The scripted agent only ever scales or holds. The gateway's share of migration benefit is
   **0%** wherever migrations occurred. The one decision behind the headline seed is a
   `restart_service svc0` during a memory leak.
3. **The twin's advantage over reject-all is no clearer when the gateway can fail.** It is
   −4.4 with the gateway immune and −4.8 on seeds where the gateway actually fails. Both are
   carried by a single seed, and both have Wilcoxon p = 1.000. Nothing was lost, but nothing
   was established either.
4. **The gateway asymmetry still matters, just elsewhere.** It affects the privileged
   opportunity-gap enumerator (`src/twinloop/diagnostics/enumerator.py:34`), which does
   consider `gw0`. The earlier finding that "all 16 enumerator picks migrate to gw0" is where
   the safe-haven concern applies. That analysis is not re-run here.

## Protocol

This is the same harness as `docs/lab6_baseline_comparison_and_limitations.md` §3.3/§3A, rebuilt
as `scripts/gateway_uncertainty_ablation.py`. It uses 120-tick episodes, a decision every 10
ticks, retry cap 2, twin horizon 20, tolerance 0 and harm threshold 3. The fault config is the
`FaultConfig` default: 3 faults per episode, start ticks 20–260, so some faults start after the
episode ends. The random gate uses p = 0.3 for the rule agent and p = 0.747 for the scripted
agent. Gates run on identical seeds, faults and agent code.

The re-run on seeds 0–9 with the gateway immune reproduces every published mean exactly:
rule 136.3 / 138.4 / 136.3 / 139.3 / 138.1 / 136.0 / 131.9 / 131.9, and scripted 166.1 /
136.3 / 136.8 / 136.7.

Reproduce (about 7 minutes per condition):

```powershell
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py run --workdir <dir> --output default.json
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py run --gateway-faultable --workdir <dir> --output faultable.json
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py run --seeds 24,49,54,60,65,69,73,75,77,84 --workdir <dir> --output exposed_default.json
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py run --gateway-faultable --seeds 24,49,54,60,65,69,73,75,77,84 --workdir <dir> --output exposed_faultable.json
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py analyze --default default.json --faultable faultable.json --output stats.json
```

The complete statistics are in `uncertainty_gateway_stats_seeds0-9.json` and
`uncertainty_gateway_stats_exposed_seeds.json`: every per-seed value, every comparison,
schedules, exposure counts, and every non-no-op action applied under twin@1.0 and the
schedule-aware gate.

---

# Analysis 1: Uncertainty on the headline result

## Choice of test

- **Primary test: exact Wilcoxon signed-rank** (zero differences dropped per Wilcoxon, average
  ranks for ties, p-value from full enumeration of all 2^m sign assignments). The paired
  differences are **not remotely normal**. They are zero-inflated: most seeds tie exactly,
  because gates only differ when a non-no-op action is proposed. The non-zero values are
  isolated spikes. With n = 10 and this shape, a t-test's normality assumption does not hold.
- **Also reported:** the exact sign-flip permutation test on the mean, the paired t-test and
  t-interval (for completeness, not relied on), and a 10,000-resample percentile bootstrap CI.
  With one non-zero seed the bootstrap is degenerate: its upper bound of 0.00 only means that
  many resamples omit that seed.
- **Multiple comparisons:** 11 comparisons per agent per condition. Holm-adjusted Wilcoxon
  p-values are given.
- **A structural limit applies to every comparison below.** With m non-tied pairs, the
  smallest two-sided exact Wilcoxon p-value achievable is 2/2^m: 1.0 for m = 1, 0.5 for m = 2,
  0.25 for m = 3, 0.125 for m = 4, 0.0625 for m = 5 and 0.031 for m = 6. **A comparison that
  differs on five or fewer of the ten seeds cannot reach p < 0.05 at any effect size.**

## Per-seed episode violation-ticks (seeds 0–9, gateway immune; the published condition)

| Arm | s0 | s1 | s2 | s3 | s4 | s5 | s6 | s7 | s8 | s9 | Mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Rule** A0 do nothing | 133 | 93 | 125 | 194 | 153 | 101 | 195 | 143 | 120 | 106 | 136.3 |
| ungated | 133 | 93 | 125 | 194 | 181 | 101 | 170 | 157 | 120 | 110 | 138.4 |
| reject-all | 133 | 93 | 125 | 194 | 153 | 101 | 195 | 143 | 120 | 106 | 136.3 |
| random p=0.3 | 133 | 93 | 125 | 194 | 177 | 101 | 170 | 157 | 120 | 123 | 139.3 |
| twin@0.0 | 133 | 93 | 125 | 194 | 170 | 101 | 192 | 143 | 120 | 110 | 138.1 |
| twin@0.6 | 133 | 93 | 125 | 194 | 153 | 101 | 192 | 143 | 120 | 106 | 136.0 |
| twin@1.0 | 133 | 93 | 125 | 194 | 153 | 101 | **151** | 143 | 120 | 106 | 131.9 |
| schedule-aware | 133 | 93 | 125 | 194 | 153 | 101 | 151 | 143 | 120 | 106 | 131.9 |
| **Scripted** ungated | 152 | 114 | 154 | 202 | 222 | 127 | 248 | 191 | 129 | 122 | 166.1 |
| reject-all | 133 | 93 | 125 | 194 | 153 | 101 | 195 | 143 | 120 | 106 | 136.3 |
| random p=0.747 | 145 | 102 | 147 | 208 | 193 | 186 | 198 | 147 | 136 | 135 | 159.7 |
| twin@0.0 | 137 | 113 | 146 | 194 | 169 | 113 | 195 | 143 | 136 | 120 | 146.6 |
| twin@0.6 | 137 | 93 | 130 | 194 | 153 | 101 | 195 | 143 | 120 | 106 | 137.2 |
| twin@1.0 | 133 | 93 | 130 | 194 | 153 | 101 | 195 | 143 | 120 | 106 | 136.8 |
| schedule-aware | 133 | 93 | 142 | 194 | 140 | 101 | 195 | 143 | 120 | 106 | 136.7 |

**Reject-all equals A0 on every seed for both agents.** A rejected proposal is retried and ends as
a no-op, so reject-all *is* doing nothing. "Twin versus reject-all" and "twin versus inaction"
are the same comparison here.

## Paired comparisons, rule agent (seeds 0–9, gateway immune)

The difference is the first arm minus the second, in violation-ticks; negative means the first
arm is better. "B/T/W" counts seeds where the first arm was better, tied, or worse. "Seeds
needed" is for 80% power at α = 0.05 and is given as the normal formula / a bootstrap-resampled
Wilcoxon simulation.

| Comparison | Per-seed differences | Mean | SD | 95% t-CI | Bootstrap CI | B/T/W | Wilcoxon (W+, W−, m) | p exact | p Holm | Paired t (p) | Distinguishable at 10 seeds? | Seeds needed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **twin@1.0 − reject-all** | 0,0,0,0,0,0,−44,0,0,0 | **−4.40** | 13.91 | [−14.35, +5.55] | [−13.20, 0.00] | 1/9/0 | 0, 1, 1 | **1.000** | 1.000 | −1.00 (0.343) | **No** | 79 / 100 |
| twin@0.6 − reject-all | 0,0,0,0,0,0,−3,0,0,0 | −0.30 | 0.95 | [−0.98, +0.38] | [−0.90, 0.00] | 1/9/0 | 0, 1, 1 | 1.000 | 1.000 | −1.00 (0.343) | No | 79 / 100 |
| twin@0.0 − reject-all | 0,0,0,0,17,0,−3,0,0,4 | +1.80 | 5.59 | [−2.20, +5.80] | [−0.60, +5.50] | 1/7/2 | 5, 1, 3 | 0.500 | 1.000 | 1.02 (0.335) | No | 76 / 75 |
| twin@1.0 − ungated | 0,0,0,0,−28,0,−19,−14,0,−4 | −6.50 | 10.19 | [−13.79, +0.79] | [−13.00, −1.20] | 4/6/0 | 0, 10, 4 | 0.125 | 1.000 | −2.02 (0.075) | No (0.125 is the smallest p possible with 4 non-tied seeds) | 20 / 20 |
| twin@1.0 − A0 | identical to twin@1.0 − reject-all | −4.40 | 13.91 | [−14.35, +5.55] | | 1/9/0 | 0, 1, 1 | 1.000 | 1.000 | (0.343) | No | 79 / 100 |
| reject-all − ungated | 0,0,0,0,−28,0,25,−14,0,−4 | −2.10 | 13.24 | [−11.57, +7.37] | [−9.80, +5.70] | 3/6/1 | 3, 7, 4 | 0.625 | 1.000 | −0.50 (0.628) | No | 312 / 200 |
| reject-all − A0 | all 0 | 0.00 | 0 | [0, 0] | | 0/10/0 | — | 1.000 | 1.000 | — | Identical | no effect to detect |
| ungated − A0 | 0,0,0,0,28,0,−25,14,0,4 | +2.10 | 13.24 | [−7.37, +11.57] | [−5.70, +9.80] | 1/6/3 | 7, 3, 4 | 0.625 | 1.000 | 0.50 (0.628) | No | 312 / 200 |
| random − ungated | 0,0,0,0,−4,0,0,0,0,13 | +0.90 | 4.43 | [−2.27, +4.07] | [−1.20, +3.90] | 1/8/1 | 2, 1, 2 | 1.000 | 1.000 | 0.64 (0.537) | No | 191 / 300 |
| twin@1.0 − twin@0.0 | 0,0,0,0,−17,0,−41,0,0,−4 | −6.20 | 13.34 | [−15.74, +3.34] | [−14.80, 0.00] | 3/7/0 | 0, 6, 3 | 0.250 | 1.000 | −1.47 (0.176) | No | 37 / 30 |
| schedule-aware − twin@1.0 | all 0 | 0.00 | 0 | [0, 0] | | 0/10/0 | — | 1.000 | 1.000 | — | Identical | no effect to detect |

**Rule agent: none of the 11 comparisons can be told apart from noise at 10 seeds.** That
includes the headline. The twin versus the ungated agent is the closest (4 seeds better,
none worse), but with only 4 non-tied seeds the smallest achievable exact p is 0.125.

## Paired comparisons, scripted agent (seeds 0–9, gateway immune)

| Comparison | Per-seed differences | Mean | SD | 95% t-CI | Bootstrap CI | B/T/W | Wilcoxon (W+, W−, m) | p exact | p Holm | Paired t (p) | Distinguishable at 10 seeds? | Seeds needed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **twin@1.0 − reject-all** | 0,0,5,0,0,0,0,0,0,0 | **+0.50** | 1.58 | [−0.63, +1.63] | [0.00, +1.50] | 0/9/1 | 1, 0, 1 | **1.000** | 1.000 | 1.00 (0.343) | **No** (the direction is twin worse) | 79 / 100 |
| twin@0.6 − reject-all | 4,0,5,0,0,0,0,0,0,0 | +0.90 | 1.91 | [−0.47, +2.27] | [0.00, +2.20] | 0/8/2 | 3, 0, 2 | 0.500 | 1.000 | 1.49 (0.171) | No | 36 / 40 |
| twin@0.0 − reject-all | 4,20,21,0,16,12,0,0,16,14 | +10.30 | 8.49 | [+4.23, +16.37] | [+5.30, +15.10] | 0/3/7 | 28, 0, 7 | 0.0156 | 0.125 | 3.84 (0.004) | Unadjusted yes; **not after Holm** | — |
| **twin@1.0 − ungated** | −19,−21,−24,−8,−69,−26,−53,−48,−9,−16 | **−29.30** | 20.40 | [−43.89, −14.71] | [−41.80, −18.20] | 10/0/0 | 0, 55, 10 | **0.0020** | **0.0215** | −4.54 (0.0014) | **Yes** | — |
| twin@1.0 − A0 | identical to twin@1.0 − reject-all | +0.50 | 1.58 | [−0.63, +1.63] | | 0/9/1 | 1, 0, 1 | 1.000 | 1.000 | (0.343) | No | 79 / 100 |
| **reject-all − ungated** | −19,−21,−29,−8,−69,−26,−53,−48,−9,−16 | **−29.80** | 20.31 | [−44.33, −15.27] | [−42.20, −18.60] | 10/0/0 | 0, 55, 10 | **0.0020** | **0.0215** | −4.64 (0.0012) | **Yes** | — |
| reject-all − A0 | all 0 | 0.00 | 0 | [0, 0] | | 0/10/0 | — | 1.000 | 1.000 | — | Identical | no effect to detect |
| **ungated − A0** | 19,21,29,8,69,26,53,48,9,16 | **+29.80** | 20.31 | [+15.27, +44.33] | [+18.60, +42.20] | 0/0/10 | 55, 0, 10 | **0.0020** | **0.0215** | 4.64 (0.0012) | **Yes** (ungated is harmful) | — |
| random − ungated | −7,−12,−7,6,−29,59,−50,−44,7,13 | −6.40 | 31.35 | [−28.83, +16.03] | [−24.00, +13.00] | 6/0/4 | 20, 35, 10 | 0.490 | 1.000 | −0.65 (0.535) | No | 189 / 150 |
| twin@1.0 − twin@0.0 | −4,−20,−16,0,−16,−12,0,0,−16,−14 | −9.80 | 7.91 | [−15.46, −4.14] | [−14.20, −5.20] | 7/3/0 | 0, 28, 7 | 0.0156 | 0.125 | −3.92 (0.0035) | Unadjusted yes; **not after Holm** | — |
| schedule-aware − twin@1.0 | 0,0,12,0,−13,0,0,0,0,0 | −0.10 | 5.90 | [−4.32, +4.12] | [−3.90, +3.60] | 1/8/1 | 1, 2, 2 | 1.000 | 1.000 | −0.05 (0.958) | No (effectively zero) | 27,288 / 300 |

**Scripted agent: the only robust results are that the agent is harmful ungated and that
any blocking gate removes the harm.** Twin@1.0 and reject-all are each about 29–30 ticks
better than ungated on 10 of 10 seeds (Holm p = 0.0215). The fidelity effect (twin@1.0 better
than twin@0.0 by 9.8) is significant unadjusted but not after Holm. **The twin is not
distinguishable from reject-all; its point estimate is slightly worse.**

## What the headline comparison would need

- **Sample size.** At the observed paired variance, detecting the rule-agent effect needs about
  **79 seeds** (normal formula) or **100 seeds** (bootstrap Wilcoxon simulation, 94% power; 75
  seeds was not enough). This number is not informative about the effect's size. For any
  pattern where 1 seed in 10 shows an effect and 9 tie, sd/|mean| is fixed at √10, so the
  formula returns 79 whatever the magnitude. What it actually assumes is that **10% of
  episodes contain a decision where selective approval matters**. That 10% rests on one event:
  the 95% Clopper–Pearson interval for 1/10 is 0.25%–44.5%. The honest requirement is
  "somewhere between a few dozen and several hundred seeds".
- **Mechanism.** The entire −44 on seed 6 traces to a small set of decisions (see Analysis 2).
  At t60 the twin approved `restart_service svc0` during a memory leak (counterfactual
  harm delta −29 ticks), which reject-all blocked. The twin also approved two scale-ups
  (−6 and −9) and rejected three harmful proposals (+5, +4, +10). Episode differences are
  closed-loop and do not add up from per-decision counterfactuals.
- **A more efficient design than more seeds.** Most seeds contribute nothing, because the agent
  proposes no non-trivial action or the gates agree. A workload where every episode contains a
  decision point with a real repair would give each seed information.

---

# Analysis 2: Is the benefit an artifact of the unfaultable gateway?

## Exposure: how many migrations target the gateway

"Avoided ticks" for an applied proposal is max(0, −Δ), where Δ = V₂₀(action, then no-op) −
V₂₀(no-op) is its per-decision counterfactual harm delta. This is a per-proposal quantity and
does not add up to episode differences.

| Record set | Proposals | Migrations proposed | → gateway | → edge | Migrations applied (gateway/edge) | Ticks avoided by applied migrations | …of which gateway-targeted | Ticks avoided by all applied actions |
|---|---|---|---|---|---|---|---|---|
| Ablation, seeds 0–9, gateway immune (both agents, all gates) | 2,295 | **0** | 0 | 0 | 0 / 0 | 0 | — | 809 (restart 115, scale 694) |
| Ablation, seeds 0–9, gateway faultable | 2,291 | **0** | 0 | 0 | 0 / 0 | 0 | — | 734 |
| Ablation, gateway-exposed seeds, gateway immune | 2,327 | 46 (rule) | **0** | 46 | 0 / 12 | 49 | **0 (0%)** | 509 |
| Ablation, gateway-exposed seeds, gateway faultable | 2,310 | 22 (rule) | **0** | 22 | 0 / 9 | 50 | **0 (0%)** | 487 |
| Recorded runs on disk, `results/*/proposals.jsonl` (7 directories, older simulator) | 2,123 | 2 | **0** | 2 | 0 / 2 | 38 (11.5% of all avoided) | **0 (0%)** | 331 |

**No agent-proposed migration in any recorded or re-run experiment ever targeted the gateway,
so the gateway's share of migration benefit is 0%.** This follows from the code, not chance:

- `RuleAgent` excludes non-edge nodes as migration targets (`rule_agent.py:118`, `:136`).
- The scripted agent's repertoire is `scale_service` and `no_op` only.
- Only the privileged diagnostic enumerator lists gateway and device nodes as destinations
  (`diagnostics/enumerator.py:34`). The gateway safe-haven concern therefore applies to the
  opportunity-gap result (`lab6_baseline_comparison_and_limitations.md` §3A.2–3A.3), where
  all 16 enumerator picks migrated to `gw0`. It does not apply to the gate ablation.

In the published condition, the rule agent's headline seed is decided by a restart and two
scale-ups. Migrations play no part in it.

## The flag

- `FaultConfig.gateway_faultable: bool = False` (`src/twinloop/config.py`).
- `targets_from_topology(topology, include_gateway=False)` appends the gateway to the node-fault
  target pool when the flag is on (`src/twinloop/faults/schedule.py`). The gateway becomes a
  valid target for the two node fault types, `node_cpu_saturation` and `node_crash`. Link
  faults could already target gateway links (`l_gw_*`); service faults do not apply to nodes.
- Wired through the experiment runner, dashboard driver and live server.
- Four regression tests in `tests/test_faults.py`:
  - the default pool is unchanged and excludes `gw0`;
  - the flag adds `gw0`;
  - default schedules are identical before and after the plumbing change;
  - a gateway crash takes the gateway down and drops all service throughput to zero.
- The full suite passes: **267 passed** (263 before, plus these 4) with `-W error`.

**Default behaviour is unchanged.** Default schedules are byte-identical for seeds 0–19 in the
test, and the default re-run reproduces every published mean.

### A side-effect you need to know about

Enlarging the target pool changes how the schedule's random draws map to targets. So turning
the flag on **re-targets some edge-node faults even when the gateway is never chosen**. On
seeds 0–9:

- **No in-episode fault lands on the gateway.** Only 4 node faults start within the 120-tick
  episode (seed 3 at t76, seed 6 at t66, seed 7 at t96, seed 8 at t109), and each has a
  1-in-5 chance of drawing the gateway.
- Two in-episode faults move: seed 6 `node_cpu_saturation` edge1→edge2 at t66, and seed 7
  edge2→edge3 at t96. Seed 8's crash at t239 also moves, but it starts after the episode ends.

**The "same seeds, gateway faultable" run is therefore not a test of gateway failure.** It
tests a slightly different edge fault placement. To actually expose the gateway, I found the
seeds whose flag-on schedule has an in-episode gateway fault: **32 of 400 seeds (8%)**. I took
the first ten (24, 49, 54, 60, 65, 69, 73, 75, 77, 84) and ran both conditions on them. Seeds
were chosen from the fault schedule alone, before any outcome was seen. With the gateway
immune, the same seeds put those faults on edge nodes instead. Gateway faults are severe: A0
rises from 143.7 to 213.5 mean violation-ticks, and seed 77 reaches 360 with an overlapping
gateway crash and CPU saturation.

## Headline comparison under all four conditions

### Rule agent, twin@1.0 − reject-all

| Condition | twin@1.0 mean | reject-all mean | Per-seed differences | Mean | SD | 95% t-CI | Wilcoxon W+/W−/m, p | Paired t p | Distinguishable? | Seeds needed |
|---|---|---|---|---|---|---|---|---|---|---|
| Seeds 0–9, gateway immune (published) | 131.9 | 136.3 | 0,0,0,0,0,0,−44,0,0,0 | **−4.40** | 13.91 | [−14.35, +5.55] | 0/1/1, **1.000** | 0.343 | **No** | 79 / 100 |
| Seeds 0–9, gateway faultable (no gateway fault drawn) | 128.1 | 132.2 | 0,0,0,0,0,0,−41,0,0,0 | **−4.10** | 12.97 | [−13.37, +5.17] | 0/1/1, **1.000** | 0.343 | **No** | 79 / 100 |
| Gateway-exposed seeds, gateway immune | 141.4 | 143.7 | 0,0,0,0,−23,0,0,0,0,0 | **−2.30** | 7.27 | [−7.50, +2.90] | 0/1/1, **1.000** | 0.343 | **No** | 79 / 100 |
| Gateway-exposed seeds, **gateway faultable** | 208.7 | 213.5 | 0,0,0,0,−48,0,0,0,0,0 | **−4.80** | 15.18 | [−15.66, +6.06] | 0/1/1, **1.000** | 0.343 | **No** | 79 / 100 |

### Scripted agent, twin@1.0 − reject-all

| Condition | twin@1.0 mean | reject-all mean | Per-seed differences | Mean | 95% t-CI | Wilcoxon p | Distinguishable? |
|---|---|---|---|---|---|---|---|
| Seeds 0–9, gateway immune | 136.8 | 136.3 | 0,0,5,0,0,0,0,0,0,0 | +0.50 | [−0.63, +1.63] | 1.000 | No |
| Seeds 0–9, gateway faultable | 132.7 | 132.2 | 0,0,5,0,0,0,0,0,0,0 | +0.50 | [−0.63, +1.63] | 1.000 | No |
| Gateway-exposed seeds, gateway immune | 144.4 | 143.7 | 0,0,7,0,0,0,0,0,0,0 | +0.70 | [−0.88, +2.28] | 1.000 | No |
| Gateway-exposed seeds, gateway faultable | 216.8 | 213.5 | 0,0,20,0,0,0,13,0,0,0 | +3.30 | [−1.81, +8.41] | 0.500 | No |

## Full comparison tables, side by side

Each cell gives the mean difference, [95% t-CI], and exact Wilcoxon p, with Holm p where
significant unadjusted. **Bold** marks comparisons that stay significant after Holm.

### Seeds 0–9: gateway immune vs gateway faultable

| Comparison | Rule, immune | Rule, faultable | Scripted, immune | Scripted, faultable |
|---|---|---|---|---|
| twin@1.0 − reject-all | −4.40 [−14.35, +5.55] p=1.000 | −4.10 [−13.37, +5.17] p=1.000 | +0.50 [−0.63, +1.63] p=1.000 | +0.50 [−0.63, +1.63] p=1.000 |
| twin@0.6 − reject-all | −0.30 [−0.98, +0.38] p=1.000 | 0.00, all seeds tie | +0.90 [−0.47, +2.27] p=0.500 | +0.90 [−0.47, +2.27] p=0.500 |
| twin@0.0 − reject-all | +1.80 [−2.20, +5.80] p=0.500 | +2.10 [−1.75, +5.95] p=0.500 | +10.30 [+4.23, +16.37] p=0.016 (Holm 0.125) | +10.70 [+4.96, +16.44] p=0.008 (Holm 0.063) |
| twin@1.0 − ungated | −6.50 [−13.79, +0.79] p=0.125 | −6.50 [−13.79, +0.79] p=0.125 | **−29.30 [−43.89, −14.71] p=0.002 (Holm 0.022)** | **−29.10 [−43.62, −14.58] p=0.002 (Holm 0.022)** |
| reject-all − ungated | −2.10 [−11.57, +7.37] p=0.625 | −2.40 [−11.39, +6.59] p=0.625 | **−29.80 [−44.33, −15.27] p=0.002 (Holm 0.022)** | **−29.60 [−44.07, −15.13] p=0.002 (Holm 0.022)** |
| ungated − A0 | +2.10 [−7.37, +11.57] p=0.625 | +2.40 [−6.59, +11.39] p=0.625 | **+29.80 [+15.27, +44.33] p=0.002 (Holm 0.022)** | **+29.60 [+15.13, +44.07] p=0.002 (Holm 0.022)** |
| random − ungated | +0.90 [−2.27, +4.07] p=1.000 | +0.90 [−2.27, +4.07] p=1.000 | −6.40 [−28.83, +16.03] p=0.490 | −6.20 [−28.49, +16.09] p=0.490 |
| twin@1.0 − twin@0.0 | −6.20 [−15.74, +3.34] p=0.250 | −6.20 [−15.74, +3.34] p=0.250 | −9.80 [−15.46, −4.14] p=0.016 (Holm 0.125) | −10.20 [−15.53, −4.87] p=0.008 (Holm 0.063) |
| schedule-aware − twin@1.0 | 0.00, identical | 0.00, identical | −0.10 [−4.32, +4.12] p=1.000 | −0.10 [−4.32, +4.12] p=1.000 |
| reject-all − A0 | 0.00, identical | 0.00, identical | 0.00, identical | 0.00, identical |

Arm means with the gateway faultable on seeds 0–9: rule A0 132.2, ungated 134.6, reject-all 132.2,
random 135.5, twin@0.0 134.3, twin@0.6 132.2, twin@1.0 128.1, schedule-aware 128.1. Scripted:
ungated 161.8, random 155.6, twin@0.0 142.9, twin@0.6 133.1, twin@1.0 132.7, schedule-aware 132.6.

### Gateway-exposed seeds (24, 49, 54, 60, 65, 69, 73, 75, 77, 84): gateway immune vs gateway faultable

| Comparison | Rule, immune | Rule, faultable | Scripted, immune | Scripted, faultable |
|---|---|---|---|---|
| twin@1.0 − reject-all | −2.30 [−7.50, +2.90] p=1.000 | −4.80 [−15.66, +6.06] p=1.000 | +0.70 [−0.88, +2.28] p=1.000 | +3.30 [−1.81, +8.41] p=0.500 |
| twin@0.6 − reject-all | −4.80 [−15.66, +6.06] p=1.000 | −4.80 [−15.66, +6.06] p=1.000 | +2.40 [+0.46, +4.34] p=0.063 | +4.50 [−0.37, +9.37] p=0.063 |
| twin@0.0 − reject-all | −3.00 [−11.69, +5.69] p=1.000 | −2.80 [−11.05, +5.45] p=1.000 | +9.70 [+1.56, +17.84] p=0.016 (Holm 0.125) | +9.00 [+0.64, +17.36] p=0.008 (Holm 0.063) |
| twin@1.0 − ungated | −13.20 [−33.66, +7.26] p=0.250 | −5.70 [−16.42, +5.02] p=0.250 | **−27.50 [−50.36, −4.64] p=0.004 (Holm 0.043)** | **−23.90 [−47.18, −0.62] p=0.004 (Holm 0.035)** |
| reject-all − ungated | −10.90 [−30.21, +8.41] p=0.250 | −0.90 [−16.46, +14.66] p=0.750 | **−28.20 [−50.84, −5.56] p=0.004 (Holm 0.043)** | **−27.20 [−49.29, −5.11] p=0.002 (Holm 0.022)** |
| ungated − A0 | +10.90 [−8.41, +30.21] p=0.250 | +0.90 [−14.66, +16.46] p=0.750 | **+28.20 [+5.56, +50.84] p=0.004 (Holm 0.043)** | **+27.20 [+5.11, +49.29] p=0.002 (Holm 0.022)** |
| random − ungated | −9.70 [−27.20, +7.80] p=0.500 | −1.30 [−12.43, +9.83] p=1.000 | −9.70 [−31.98, +12.58] p=0.359 | −10.80 [−29.95, +8.35] p=0.289 |
| twin@1.0 − twin@0.0 | +0.70 [−2.99, +4.39] p=1.000 | −2.00 [−5.18, +1.18] p=0.500 | −9.00 [−16.98, −1.02] p=0.016 (Holm 0.125) | −5.70 [−13.67, +2.27] p=0.039 (Holm 0.273) |
| schedule-aware − twin@1.0 | 0.00, identical | 0.00, identical | −0.70 [−2.28, +0.88] p=1.000 | −0.10 [−0.33, +0.13] p=1.000 |
| reject-all − A0 | 0.00, identical | 0.00, identical | 0.00, identical | 0.00, identical |

Arm means on the gateway-exposed seeds:
- **Rule, gateway immune:** A0 143.7, ungated 154.6, reject-all 143.7, random 144.9,
  reject-migrate 150.3, twin@0.0 140.7, twin@0.6 138.9, twin@1.0 141.4, schedule-aware 141.4.
- **Rule, gateway faultable:** A0 213.5, ungated 214.4, reject-all 213.5, random 213.1,
  reject-migrate 220.1, twin@0.0 210.7, twin@0.6 208.7, twin@1.0 208.7, schedule-aware 208.7.
- **Scripted, gateway immune:** ungated 171.9, random 162.2, twin@0.0 153.4, twin@0.6 146.1,
  twin@1.0 144.4, schedule-aware 143.7.
- **Scripted, gateway faultable:** ungated 240.7, random 229.9, twin@0.0 222.5, twin@0.6 218.0,
  twin@1.0 216.8, schedule-aware 216.7.

On the gateway-exposed seeds with the gateway immune, **twin@0.6 (138.9) beats twin@1.0 (141.4)**.
The fidelity ordering is not stable once the rule agent starts migrating.

## Does the twin's advantage over reject-all survive when the gateway can fail?

**The question assumes an advantage exists, and at 10 seeds none has been demonstrated in any
condition.**

- **Magnitude.** The point estimate does not shrink when the gateway can fail. It is −4.4
  (published), −4.1 (flag on, no gateway fault drawn), −2.3 (exposed seeds, gateway immune)
  and **−4.8 (exposed seeds, gateway failing)**.
- **Significance.** In all four conditions, exactly one seed differs, Wilcoxon p = 1.000, and
  the 95% interval includes zero.
- **Gateway mechanism ruled out.** The deciding decisions are a restart during a memory leak
  (seed 6) and edge-node migrations and scale-ups (seed 65). None targets the gateway.

**So the benefit did not disappear, and the gateway is not an artifact of the gate ablation.
But there was never a statistically established benefit to lose.**

## Consequences for how results should be stated

1. The rule-agent claim "the twin beats reject-all / inaction by 4.4 violation-ticks" should be
   reported as a single-seed observation: one episode out of ten, where the twin approved a
   memory-leak restart. It is not an established effect.
2. The only claims these data support at 10 seeds (Holm-adjusted) are for the scripted agent:
   the ungated agent is harmful, and gating (twin or reject-all) removes about 28–30
   violation-ticks of that harm. **The data cannot tell the twin from reject-all.**
3. The gateway asymmetry is a real modelling issue for the privileged opportunity-gap
   enumerator, which picks `gw0` as its destination. It is not the source of any gate-ablation
   result, because neither controller can propose a gateway migration.
4. The flag's re-targeting side-effect means `gateway_faultable=True` is a different fault
   distribution, not a strict superset of the default. A cleaner design, which would change
   default schedules and is therefore not done here, draws the gateway as a separate
   Bernoulli event so edge targeting is untouched.
5. Next measurement: a workload where most episodes contain a decision with a real repair,
   rather than more seeds of the current one. Otherwise 80–100+ seeds are required, and even
   that estimate rests on a single observed event.

## Files added or changed in this session

- `src/twinloop/config.py`: `FaultConfig.gateway_faultable` (default `False`).
- `src/twinloop/faults/schedule.py`: `targets_from_topology(..., include_gateway=False)`.
- `src/twinloop/experiment/runner.py`, `src/twinloop/dashboard/driver.py`,
  `src/twinloop/dashboard/live.py`: pass the flag through.
- `tests/test_faults.py`: four gateway-flag tests.
- `scripts/gateway_uncertainty_ablation.py`: ablation runner and statistics, including exact
  Wilcoxon, sign-flip test, t-CI, bootstrap, Holm and power simulation.
- `docs/research/uncertainty_gateway_stats_seeds0-9.json` and
  `docs/research/uncertainty_gateway_stats_exposed_seeds.json`: full statistics.
- `paper/main.tex`: not touched.
