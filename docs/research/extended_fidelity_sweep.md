# Extended fidelity sweep: 150 seeds, six fidelity levels, rule and scripted agents

Date: 2026-09-17. No language-model calls were made. `paper/main.tex` was not modified.
Machine-readable results: `docs/research/extended_fidelity_sweep.json`.

## Bottom line

1. **Rule agent: the twin's episode-level advantage over reject-all is now significant.** At
   fidelity 1.0 it is −2.32 violation-ticks per episode, 95% CI [−3.76, −0.88]. 22 of 150
   seeds differ (18 better, 4 worse). Exact Wilcoxon p = 0.00097, Holm-adjusted p = 0.0078,
   observed power 0.96. Fidelity 0.8 is also significant (−1.71, Holm p = 0.025); 0.6 and
   below are not. The 10-seed estimate (−4.4) overstated the effect by about 2×. The
   prediction that about 125 seeds would be needed was right in order of magnitude: at the
   observed SD, 117 are needed.
2. **Scripted agent: the twin never beats reject-all at the episode level, at any fidelity.**
   At 1.0 the difference is +0.23 [−0.16, +0.62], p = 0.29, not significant. At 0.8 and below
   the twin is *significantly worse* than reject-all. Even the schedule-aware gate is not
   significantly better (−0.21, p = 0.087).
3. **The primary local curve, net Σ D over approved candidates, is monotone for both agents.**
   - **Scripted agent:** it crosses zero at **fidelity 0.555**, bootstrap 95% interval
     [0.471, 0.677], with a single crossing in 99.9% of resamples.
   - **Rule agent:** it is at or below zero at every level (−0.15 at 0.0 down to −2.12 at 1.0).
     The observed curve never crosses zero. The confidence interval at 0.0 includes zero, so
     any crossing would lie below about 0.28.
4. **The locally predicted crossing does not carry over to episodes.**
   - **Scripted agent:** net Σ D says the twin should beat reject-all above fidelity 0.555, but
     at the episode level the twin is worse or tied everywhere; that curve has no zero
     crossing in 89% of resamples.
   - **Rule agent:** the episode curve crosses at about 0.32 [0.12, 0.55]. Local net Σ D is
     already negative there, yet the twin is worse than reject-all at fidelities 0.0 and 0.2.
5. **The three-point non-monotonicity in rule-agent benefit_preserved was noise.** Across six
   levels it is flat at about 0.4 from fidelity 0.0 to 0.6 (0.409, 0.431, 0.373, 0.424), then
   rises (0.609, then 0.970). No adjacent change below 0.8 has a CI excluding zero. The only
   significant step is 0.8 → 1.0 (+0.361 [+0.103, +0.586]).
6. **Few seeds contain any opportunity.** Seeds with at least one beneficial candidate:
   - **Rule agent:** 59/150 in any arm, **23/150 on the reject-all trajectory**, 22/150 at
     twin@1.0.
   - **Scripted agent:** 149/150 in any arm, but **12/150 on the reject-all trajectory** and
     21/150 at twin@1.0.

   Available benefit on the reject-all trajectory is 439 ticks for the rule agent
   (2.9 per seed) and **52 ticks for the scripted agent (0.35 per seed)**.

## Configuration

| Item | Value |
|---|---|
| Seeds | **150**, contiguous 0–149 (not scaled down) |
| Fidelity levels | 0.0, 0.2, 0.4, 0.6, 0.8, 1.0 |
| Agents | Rule agent; scripted agent (the deterministic scale-or-hold stub, runner kind `llm`, no model calls) |
| Arms per agent | ungated, reject-all, random (p = 0.30 rule, 0.747 scripted), schedule-aware H-tick gate, twin at each of the 6 fidelities |
| Reference | A0 (null agent), 150 seeds |
| Protocol | Published: 120-tick episodes, decision every 10 ticks, retry cap 2, horizon 20, tolerance 0, harm threshold 3, default `FaultConfig` (3 faults, start ticks 20–260), gateway immune |
| Episodes | 3,150 (150 × (2 × 10 + 1)) |
| Uncertainty | Seed-level bootstrap, 5,000 resamples, with the same resampled seeds across arms so comparisons stay paired; t-intervals; exact Wilcoxon signed-rank for n = 150 (dynamic programming, conditional on the observed ties, checked equal to brute-force enumeration on 300 random tie-heavy cases); Holm adjustment within each agent's 12 episode comparisons |

**Runtime.** 11,885 CPU-seconds of episode time over about 28 minutes of wall time on 6 worker
processes. Mean cost per episode (under 6-way contention):

| Arm | Rule | Scripted |
|---|---|---|
| ungated | 1.77 s | 2.88 s |
| reject-all | 1.86 s | 4.42 s |
| random | 1.78 s | 4.23 s |
| twin@0.0 … twin@1.0 | 2.98–3.25 s | 5.07–5.75 s |
| schedule-aware | 3.17 s | 6.24 s |
| A0 null | 1.72 s | |

**Reproduction check.** Seeds 0–9 of this sweep reproduce the published 10-seed per-seed values
exactly for every arm recorded in both runs (ungated, reject-all, random, twin@0.0, twin@0.6,
twin@1.0, schedule-aware; both agents): 0 mismatches. Reject-all equals A0 on all 150 seeds for
both agents.

**Reproduce.**

```powershell
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py sweep --shards <dir>\shards --workdir <dir>\work --worker <k> --workers 6 --seed-count 150
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py sweep-analyze --shards <dir>\shards --output docs\research\extended_fidelity_sweep.json
```

Run the first command once for each k = 0…5. Workers write one file per episode and skip
episodes already on disk, so an interrupted sweep resumes.

---

## 1–2. benefit_preserved, harm_prevented and net Σ D, per fidelity

Definitions follow `candidate_composition.md` Analysis B. D = V₂₀(action, then no-op) −
V₂₀(no-op). Each arm is scored on its own candidates, with no-ops excluded. Ratio CIs are
seed-bootstrap percentile intervals. Net Σ D is the per-seed mean of Σ D over approved
candidates; negative means the approved set is locally better than rejecting everything.
**No denominator is zero anywhere in this sweep**, so every ratio is estimable.

### Rule agent

| Gate | benefit_preserved [95% CI] | (approved / available) | harm_prevented [95% CI] | (rejected / available) | **Net Σ D per seed** | t-CI | Bootstrap CI | Seeds with non-zero net | Net Σ D total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (reference) | **0.000** [0.000, 0.000] | 0 / 439 | **1.000** [1.000, 1.000] | 759 / 759 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.409 [0.274, 0.568] | 209 / 511 | 0.747 [0.643, 0.833] | 551 / 738 | **−0.147** | [−0.635, +0.342] | [−0.627, +0.313] | 27 | −22 |
| twin@0.2 | 0.431 [0.285, 0.615] | 198 / 459 | 0.818 [0.716, 0.894] | 625 / 764 | **−0.393** | [−0.783, −0.004] | [−0.800, −0.027] | 22 | −59 |
| twin@0.4 | 0.373 [0.216, 0.566] | 144 / 386 | 0.926 [0.857, 0.980] | 684 / 739 | **−0.593** | [−1.103, −0.084] | [−1.127, −0.113] | 16 | −89 |
| twin@0.6 | 0.424 [0.250, 0.665] | 143 / 337 | 0.965 [0.925, 0.995] | 725 / 751 | **−0.780** | [−1.242, −0.318] | [−1.273, −0.360] | 16 | −117 |
| twin@0.8 | 0.609 [0.388, 0.862] | 206 / 338 | 0.989 [0.962, 1.000] | 744 / 752 | **−1.320** | [−2.061, −0.579] | [−2.107, −0.653] | 18 | −198 |
| twin@1.0 | **0.970** [0.902, 1.000] | 325 / 335 | **0.991** [0.975, 1.000] | 755 / 762 | **−2.120** | [−3.219, −1.021] | [−3.240, −1.093] | 23 | −318 |
| schedule-aware | 1.000 [1.000, 1.000] | 326 / 326 | 1.000 [1.000, 1.000] | 767 / 767 | −2.173 | [−3.261, −1.086] | [−3.287, −1.180] | 22 | −326 |
| random p=0.3 | 0.694 [0.559, 0.830] | 410 / 591 | 0.294 [0.183, 0.397] | 207 / 705 | +0.587 | [−0.355, +1.528] | [−0.367, +1.487] | 62 | +88 |
| ungated (reference) | 1.000 | 622 / 622 | 0.000 | 0 / 630 | +0.053 | [−1.079, +1.186] | [−1.067, +1.147] | 69 | +8 |

### Scripted agent

| Gate | benefit_preserved [95% CI] | (approved / available) | harm_prevented [95% CI] | (rejected / available) | **Net Σ D per seed** | t-CI | Bootstrap CI | Seeds with non-zero net | Net Σ D total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (reference) | **0.000** [0.000, 0.000] | 0 / 52 | **1.000** [1.000, 1.000] | 7,712 / 7,712 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.727 [0.671, 0.785] | 1,286 / 1,770 | 0.681 [0.641, 0.719] | 3,712 / 5,453 | **+3.033** | [+2.130, +3.937] | [+2.173, +3.893] | 124 | +455 |
| twin@0.2 | 0.803 [0.738, 0.866] | 1,102 / 1,373 | 0.755 [0.720, 0.788] | 4,507 / 5,971 | **+2.413** | [+1.523, +3.303] | [+1.567, +3.267] | 116 | +362 |
| twin@0.4 | 0.819 [0.751, 0.888] | 709 / 866 | 0.870 [0.842, 0.896] | 5,722 / 6,579 | **+0.987** | [+0.296, +1.677] | [+0.347, +1.653] | 82 | +148 |
| twin@0.6 | 0.839 [0.738, 0.925] | 317 / 378 | 0.962 [0.948, 0.974] | 6,947 / 7,221 | **−0.287** | [−0.800, +0.226] | [−0.807, +0.213] | 41 | −43 |
| twin@0.8 | 0.901 [0.781, 0.973] | 163 / 181 | 0.990 [0.984, 0.995] | 7,438 / 7,513 | **−0.587** | [−0.993, −0.180] | [−1.027, −0.193] | 22 | −88 |
| twin@1.0 | 0.877 [0.746, 0.969] | 135 / 154 | 0.994 [0.990, 0.998] | 7,531 / 7,573 | **−0.620** | [−1.011, −0.229] | [−1.027, −0.253] | 21 | −93 |
| schedule-aware | 1.000 [1.000, 1.000] | 107 / 107 | 1.000 [1.000, 1.000] | 7,556 / 7,556 | −0.713 | [−1.071, −0.356] | [−1.093, −0.387] | 19 | −107 |
| random p=0.747 | 0.257 [0.213, 0.305] | 704 / 2,735 | 0.737 [0.702, 0.768] | 3,880 / 5,268 | +4.560 | [+3.642, +5.478] | [+3.647, +5.493] | 120 | +684 |
| ungated (reference) | 1.000 | 2,661 / 2,661 | 0.000 | 0 / 3,339 | +4.520 | [+3.388, +5.652] | [+3.400, +5.647] | 144 | +678 |

The three-point values quoted from the 10-seed data (scripted net Σ D +38 / −4 / −16 at fidelity
0.0 / 0.6 / 1.0) become +455 / −43 / −93 over 150 seeds: +3.03 / −0.29 / −0.62 per seed against
+3.8 / −0.4 / −1.6 per seed at 10 seeds. The 10-seed figures had the right shape and roughly
the right size at 0.0 and 0.6, but overstated the effect at 1.0 by about 2.5×.

### Changes between adjacent fidelity levels (paired seed bootstrap)

"Excl. 0" marks steps whose 95% CI excludes zero.

| Step | Rule: Δ net Σ D | Rule: Δ benefit_preserved | Rule: Δ harm_prevented | Scripted: Δ net Σ D | Scripted: Δ benefit_preserved | Scripted: Δ harm_prevented |
|---|---|---|---|---|---|---|
| 0.0 → 0.2 | −0.247 [−0.553, +0.060] | +0.022 [−0.044, +0.104] | **+0.071 [+0.008, +0.137] excl. 0** | −0.620 [−1.347, +0.073] | **+0.076 [+0.024, +0.134] excl. 0** | **+0.074 [+0.043, +0.106] excl. 0** |
| 0.2 → 0.4 | −0.200 [−0.620, +0.153] | −0.058 [−0.241, +0.098] | **+0.108 [+0.040, +0.200] excl. 0** | **−1.427 [−2.187, −0.673] excl. 0** | +0.016 [−0.057, +0.090] | **+0.115 [+0.086, +0.145] excl. 0** |
| 0.4 → 0.6 | **−0.187 [−0.447, −0.013] excl. 0** | +0.051 [−0.045, +0.180] | +0.040 [−0.019, +0.108] | **−1.273 [−1.900, −0.687] excl. 0** | +0.020 [−0.062, +0.094] | **+0.092 [+0.068, +0.119] excl. 0** |
| 0.6 → 0.8 | **−0.540 [−1.140, −0.067] excl. 0** | +0.185 [−0.003, +0.395] | +0.024 [−0.017, +0.069] | −0.300 [−0.773, +0.133] | +0.062 [−0.056, +0.171] | **+0.028 [+0.016, +0.041] excl. 0** |
| 0.8 → 1.0 | **−0.800 [−1.593, −0.140] excl. 0** | **+0.361 [+0.103, +0.586] excl. 0** | +0.001 [−0.021, +0.031] | −0.033 [−0.273, +0.213] | −0.024 [−0.103, +0.049] | **+0.004 [+0.001, +0.009] excl. 0** |

- **harm_prevented** rises with fidelity for both agents at every step; no step decreases.
- **Rule-agent benefit_preserved** is flat from 0.0 to 0.6: two small decreases and two small
  increases, none distinguishable from zero. Improvement comes only above 0.6, and only the
  0.8 → 1.0 step is individually significant.
- **Scripted-agent benefit_preserved** rises at 0.0 → 0.2 and is statistically flat
  thereafter. The 0.8 → 1.0 point estimate is a drop (0.901 → 0.877) that is not
  distinguishable from zero.
- **Net Σ D** falls at every step for both agents: monotone in point estimates, and
  significantly so for rule 0.4 → 1.0 and scripted 0.2 → 0.6. For the scripted agent it is
  flat above 0.6, so fidelity above 0.6 buys harm prevention but no further net local gain.

## 3. Crossover fidelity for net Σ D

The crossover is the zero crossing found by linear interpolation between adjacent levels. For the
interval, crossings were recomputed in each of 5,000 seed-bootstrap resamples; the percentile
interval uses the resamples with exactly one crossing.

| Agent | Net Σ D per seed at 0.0 / 0.2 / 0.4 / 0.6 / 0.8 / 1.0 | Monotone? | Observed crossing | Resamples with 0 / 1 / ≥2 crossings | Crossing 95% interval (single-crossing resamples) |
|---|---|---|---|---|---|
| Rule | −0.147 / −0.393 / −0.593 / −0.780 / −1.320 / −2.120 | Yes, strictly decreasing | **None in [0, 1]**: negative at every level | 3,639 / 1,343 / 18 | [0.006, 0.28], median 0.071 |
| **Scripted** | **+3.033 / +2.413 / +0.987 / −0.287 / −0.587 / −0.620** | Yes, strictly decreasing | **0.555** | 0 / 4,997 / 3 | **[0.471, 0.677]**, median 0.554 |

- **Rule agent: no crossover to report.** Net Σ D is at or below zero at every level. The
  interval at fidelity 0.0 includes zero ([−0.63, +0.31]), so 27% of resamples place a crossing
  just above 0.0, below 0.28. Even the most degraded twin approves a locally non-harmful set
  for this agent, because rule-agent candidates are nearly balanced between benefit and harm
  (ungated net Σ D is +0.05 per seed).
- **Scripted agent: one clean crossing at 0.555 [0.471, 0.677].** Below it, the twin approves
  more harm than benefit; above it, less. The curve is monotone, so no fitting through a
  non-monotone region was needed.

## 4. Episode-level comparisons (paired per seed, n = 150)

Difference = first arm − second arm, in episode violation-ticks. Negative means the first arm is
better. "Non-zero" counts seeds where the arms differ; "B/W" counts better/worse seeds. Power is
the bootstrap-estimated power of the Wilcoxon test at n = 150 using the observed differences.
"Seeds for 80%" uses the normal approximation. Holm adjustment is over the 12 comparisons per
agent.

### Rule agent

| Comparison | Mean | 95% t-CI | Bootstrap CI | SD | Non-zero (B/W) | W+ / W− | Exact Wilcoxon p | Holm p | **Significant at 150?** | Observed power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +1.073 | [−0.048, +2.194] | [−0.020, +2.233] | 6.95 | 29 (6/23) | 333 / 102 | 0.0110 | 0.066 | **No** (unadjusted yes; twin worse) | 0.73 | 329 |
| twin@0.2 − reject-all | +0.760 | [−0.356, +1.876] | [−0.320, +1.873] | 6.92 | 25 (8/17) | 222 / 103 | 0.111 | 0.557 | **No** | 0.36 | 651 |
| twin@0.4 − reject-all | −0.500 | [−1.518, +0.518] | [−1.513, +0.447] | 6.31 | 16 (10/6) | 51.5 / 84.5 | 0.411 | 0.822 | **No** | 0.13 | 1,251 |
| twin@0.6 − reject-all | −1.067 | [−2.167, +0.034] | [−2.260, −0.140] | 6.82 | 17 (11/6) | 43 / 110 | 0.117 | 0.557 | **No** | 0.32 | 322 |
| twin@0.8 − reject-all | **−1.707** | [−2.987, −0.426] | [−3.047, −0.567] | 7.94 | 18 (15/3) | 21.5 / 149.5 | **0.0036** | **0.025** | **Yes** | 0.87 | 170 |
| **twin@1.0 − reject-all** | **−2.320** | **[−3.762, −0.878]** | [−3.880, −1.020] | 8.94 | **22 (18/4)** | 30.5 / 222.5 | **0.00097** | **0.0078** | **Yes** | 0.96 | 117 |
| schedule-aware − reject-all | −2.587 | [−4.027, −1.146] | [−4.160, −1.287] | 8.93 | 22 (20/2) | 16.5 / 236.5 | 8.3 × 10⁻⁵ | 0.00083 | Yes | 0.99 | 94 |
| ungated − reject-all | +3.393 | [+1.338, +5.449] | [+1.473, +5.520] | 12.74 | 71 (14/57) | 1933 / 623 | 1.2 × 10⁻⁴ | 0.0011 | Yes (ungated worse) | 0.98 | 111 |
| random − reject-all | +3.327 | [+1.366, +5.287] | [+1.367, +5.333] | 12.15 | 63 (13/50) | 1571.5 / 444.5 | 6.8 × 10⁻⁵ | 0.00075 | Yes (random worse) | 0.98 | 105 |
| twin@1.0 − twin@0.0 | −3.393 | [−5.018, −1.768] | [−5.073, −1.940] | 10.07 | 38 (33/5) | 63 / 678 | 9.2 × 10⁻⁷ | 1.1 × 10⁻⁵ | Yes | 1.00 | 70 |
| schedule-aware − twin@1.0 | −0.267 | [−0.601, +0.068] | [−0.633, 0.000] | 2.07 | 3 (3/0) | 0 / 6 | 0.25 | 0.75 | **No** (3 seeds; smallest possible p is 0.25) | 0.10 | 474 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | — | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

**The rule agent's advantage over reject-all reaches significance at 150 seeds** at fidelity 1.0
(Holm p = 0.0078) and 0.8 (Holm p = 0.025), but not at 0.6 or below. The effect is carried by
22 of 150 seeds. The twin@1.0 gate is indistinguishable from the schedule-aware gate: they differ
on 3 seeds, all in the schedule-aware gate's favour, totalling 40 ticks.

The episode-level curve (twin − reject-all) is +1.07, +0.76, −0.50, −1.07, −1.71, −2.32 at
fidelities 0.0 → 1.0. It crosses zero at about **0.32** (bootstrap single-crossing interval
[0.12, 0.55]; no crossing in 2.7% of resamples). Below about 0.3, a degraded twin is worse than
rejecting everything, even though its local net Σ D is slightly negative there.

### Scripted agent

| Comparison | Mean | 95% t-CI | Bootstrap CI | SD | Non-zero (B/W) | W+ / W− | Exact Wilcoxon p | Holm p | **Significant at 150?** | Observed power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +15.013 | [+12.847, +17.179] | [+12.873, +17.147] | 13.42 | 128 (2/126) | 8247 / 9 | 2.1 × 10⁻³⁷ | 1.9 × 10⁻³⁶ | Yes (twin worse) | 1.00 | 7 |
| twin@0.2 − reject-all | +11.280 | [+9.449, +13.111] | [+9.527, +13.153] | 11.35 | 122 (2/120) | 7497 / 6 | 6.0 × 10⁻³⁶ | 4.8 × 10⁻³⁵ | Yes (twin worse) | 1.00 | 8 |
| twin@0.4 − reject-all | +6.313 | [+4.722, +7.905] | [+4.760, +7.907] | 9.86 | 84 (4/80) | 3530.5 / 39.5 | 7.7 × 10⁻²² | 5.4 × 10⁻²¹ | Yes (twin worse) | 1.00 | 20 |
| twin@0.6 − reject-all | +2.367 | [+1.231, +3.502] | [+1.333, +3.580] | 7.04 | 42 (7/35) | 867 / 36 | 2.2 × 10⁻⁹ | 1.3 × 10⁻⁸ | Yes (twin worse) | 1.00 | 70 |
| twin@0.8 − reject-all | +0.560 | [+0.090, +1.030] | [+0.100, +1.047] | 2.91 | 23 (6/17) | 220.5 / 55.5 | 0.0097 | 0.049 | Yes (twin worse) | 0.72 | 213 |
| **twin@1.0 − reject-all** | **+0.233** | **[−0.156, +0.622]** | [−0.127, +0.620] | 2.41 | **17 (7/10)** | 99.5 / 53.5 | **0.287** | 0.574 | **No** | **0.21** | 839 |
| schedule-aware − reject-all | −0.213 | [−0.607, +0.180] | [−0.627, +0.173] | 2.44 | 18 (12/6) | 46 / 125 | 0.087 | 0.262 | **No** | 0.40 | 1,025 |
| ungated − reject-all | +25.060 | [+22.320, +27.800] | [+22.420, +27.820] | 16.98 | 149 (0/149) | 11175 / 0 | 2.8 × 10⁻⁴⁵ | 3.4 × 10⁻⁴⁴ | Yes (ungated worse) | 1.00 | 4 |
| random − reject-all | +20.180 | [+17.441, +22.919] | [+17.627, +22.954] | 16.97 | 132 (3/129) | 8765 / 13 | 2.9 × 10⁻³⁸ | 3.2 × 10⁻³⁷ | Yes (random worse) | 1.00 | 6 |
| twin@1.0 − twin@0.0 | −14.780 | [−16.988, −12.572] | [−17.000, −12.587] | 13.69 | 127 (125/2) | 6 / 8122 | 1.9 × 10⁻³⁷ | 1.9 × 10⁻³⁶ | Yes | 1.00 | 7 |
| schedule-aware − twin@1.0 | −0.447 | [−0.888, −0.005] | [−0.927, −0.033] | 2.74 | 13 (10/3) | 17 / 74 | 0.046 | 0.185 | No after Holm | 0.48 | 295 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | — | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

**The scripted agent's twin gate never beats reject-all.** It is significantly worse at every
fidelity up to 0.8, and at 1.0 it is not distinguishable from reject-all (+0.23, p = 0.29,
power 0.21; about 839 seeds would be needed to detect a difference that small). Not even the
schedule-aware gate reaches significance against reject-all. The episode-level curve (+15.0,
+11.3, +6.3, +2.4, +0.56, +0.23) approaches zero from above and never crosses in the observed
data; 89% of resamples have no crossing.

## 5. Candidate composition per fidelity level

Each arm's own candidates, no-ops excluded. "Seeds with benefit" counts the seeds (of 150)
containing at least one candidate with D < 0 on that arm's trajectory.

### Rule agent

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Available benefit | Available harm | Beneficial \|D\| median / p90 / max | Harmful D median / p90 / max | **Seeds with benefit** | Benefit by action type |
|---|---|---|---|---|---|---|---|---|---|---|
| ungated | 204 | 79 | 37 | 88 (78) | 622 | 630 | 7 / 12 / 28 | 7 / 11 / 25 | 57 | scale 357, restart 178, migrate 87 |
| **reject-all** | 168 | 48 | 21 | 99 (91) | **439** | 759 | 7 / 20 / 30 | 8 / 11 / 14 | **23** | migrate 213, restart 201, scale 25 |
| twin@0.0 | 177 | 55 | 32 | 90 (87) | 511 | 738 | 8 / 18 / 29 | 8 / 12 / 20 | 35 | restart 200, scale 180, migrate 131 |
| twin@0.2 | 173 | 48 | 33 | 92 (89) | 459 | 764 | 8 / 18 / 29 | 8 / 13 / 20 | 33 | restart 189, scale 150, migrate 120 |
| twin@0.4 | 159 | 35 | 31 | 93 (90) | 386 | 739 | 8 / 28 / 30 | 8 / 12 / 14 | 25 | restart 201, migrate 115, scale 70 |
| twin@0.6 | 159 | 30 | 34 | 95 (89) | 337 | 751 | 8 / 28 / 30 | 8 / 12 / 14 | 26 | restart 201, migrate 85, scale 51 |
| twin@0.8 | 157 | 29 | 33 | 95 (91) | 338 | 752 | 8 / 28 / 30 | 8 / 12 / 14 | 23 | restart 212, migrate 85, scale 41 |
| **twin@1.0** | 159 | 30 | 33 | 96 (92) | **335** | 762 | 8 / 28 / 30 | 8 / 12 / 14 | **22** | restart 212, migrate 77, scale 46 |
| schedule-aware | 159 | 29 | 33 | 97 (93) | 326 | 767 | 8 / 28 / 30 | 8 / 12 / 14 | 22 | restart 212, migrate 77, scale 37 |

Seeds with at least one beneficial candidate in *any* rule-agent arm: **59 of 150**.

Unlike the 10-seed data, which had no migrations, the 150-seed sweep contains rule-agent
migrations: 24 in the ungated arm and 46 on the reject-all trajectory, all to edge nodes.
Together with restarts they are the high-value repairs. On the reject-all trajectory, 213 of
the 439 available ticks come from migrations and 201 from restarts. Scale-ups there are
overwhelmingly harmful: 6 beneficial against 86 harmful.

### Scripted agent

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Available benefit | Available harm | Beneficial \|D\| median / p90 / max | Harmful D median / p90 / max | **Seeds with benefit** |
|---|---|---|---|---|---|---|---|---|---|
| ungated | 1,260 | 459 | 212 | 589 (413) | 2,661 | 3,339 | 6 / 10 / 14 | 5 / 10 / 19 | 148 |
| **reject-all** | 1,175 | **15** | 178 | 982 (922) | **52** | 7,712 | 3 / 7 / 8 | 8 / 11 / 27 | **12** |
| twin@0.0 | 1,241 | 273 | 191 | 777 (676) | 1,770 | 5,453 | 7 / 11 / 14 | 7 / 11 / 27 | 116 |
| twin@0.2 | 1,224 | 215 | 182 | 827 (727) | 1,373 | 5,971 | 6 / 11 / 14 | 7 / 11 / 27 | 107 |
| twin@0.4 | 1,203 | 140 | 181 | 882 (788) | 866 | 6,579 | 6 / 11 / 14 | 8 / 11 / 27 | 76 |
| twin@0.6 | 1,174 | 68 | 169 | 937 (866) | 378 | 7,221 | 5 / 11 / 14 | 8 / 11 / 27 | 41 |
| twin@0.8 | 1,168 | 33 | 169 | 966 (897) | 181 | 7,513 | 5 / 10 / 14 | 8 / 11 / 27 | 22 |
| **twin@1.0** | 1,167 | **29** | 167 | 971 (905) | **154** | 7,573 | 5 / 9 / 14 | 8 / 11 / 27 | **21** |
| schedule-aware | 1,166 | 25 | 164 | 977 (911) | 107 | 7,556 | 4 / 7 / 9 | 8 / 11 / 27 | 19 |

All scripted-agent candidates are `scale_service`. Seeds with at least one beneficial candidate
in *any* scripted-agent arm: **149 of 150**. Opportunity collapses as the gate gets more
accurate: available benefit falls from 1,770 ticks (116 seeds) at fidelity 0.0 to 154 ticks
(21 seeds) at 1.0. A high-fidelity gate keeps the trajectory close to reject-all's, where
scale-ups are almost never useful (15 beneficial against 982 harmful).

---

## Specific checks requested

**Is rule-agent benefit_preserved non-monotonic?** In the 10-seed data it read 0.420 → 0.341 →
1.000. With six levels and 150 seeds it reads 0.409, 0.431, 0.373, 0.424, 0.609, 0.970. The
point estimates still wobble below 0.6, but none of the four adjacent changes there has a CI
excluding zero; the largest is −0.058 [−0.241, +0.098]. **The dip was noise.** The defensible
description is a flat plateau of about 0.4 up to fidelity 0.6, a rise at 0.8 that is not
individually significant, and a significant jump to 0.97 at 1.0. That is not smooth monotone
improvement, and it should not be drawn as a line through six points.

**Does the rule agent's episode-level advantage over reject-all reach significance at 150
seeds?** **Yes.** At fidelity 1.0: −2.32 violation-ticks per episode [−3.76, −0.88], exact
Wilcoxon p = 0.00097, Holm p = 0.0078, 18 better and 4 worse seeds out of 22 that differ,
observed power 0.96. Analysis C estimated about 79–125 seeds at the 10-seed SD of 13.9. The
150-seed SD is lower (8.94), and so is the effect (−2.32 versus the 10-seed −4.4); at those
values about 117 seeds give 80% power.

**How many seeds contain at least one beneficial candidate?**

| Agent | Any arm | Reject-all trajectory | Twin@1.0 trajectory | Ungated trajectory |
|---|---|---|---|---|
| Rule | 59 / 150 (39%) | **23 / 150 (15%)** | 22 / 150 (15%) | 57 / 150 (38%) |
| Scripted | 149 / 150 (99%) | **12 / 150 (8%)** | 21 / 150 (14%) | 148 / 150 (99%) |

The reject-all column bounds the episode-level result, because it is the benefit reject-all
actually throws away. For the rule agent it is 23 seeds and 439 ticks, which a good gate can
recover, and the twin does. For the scripted agent it is 12 seeds and 52 ticks, 0.35 per seed,
too little for any gate to show against reject-all. The schedule-aware gate itself does not
reach significance.

## Interpretation

1. **The twin's predictive value is now established for the rule agent,** at fidelity ≥ 0.8
   and at the episode level, against the no-prediction control. The effect is small (about
   2 violation-ticks per 120-tick episode, about 2% of the 122-tick baseline) and concentrated
   in about 15% of episodes, those containing a restart or migration opportunity.
2. **For the scripted agent, the null is structural, not a power problem.** With 0.35 ticks
   per seed of discarded benefit, detecting a gain would need hundreds to thousands of seeds,
   and the observed direction is the wrong way. A scale-or-hold proposer offers a gate nothing
   to preserve.
3. **Local metrics and episode outcomes disagree in a consistent direction.** Net Σ D over
   approved candidates is more optimistic than the episode result for both agents: the
   scripted crossing at 0.555 has no episode-level counterpart, and the rule agent's
   low-fidelity twins look locally neutral-to-good but are worse over whole episodes. 20-tick
   counterfactuals with a no-op continuation do not capture how approved scale-ups change later
   states. The local curve describes the filter; only episode outcomes support performance
   claims.
4. **Fidelity is not uniformly valuable.** Harm prevention improves steadily with fidelity.
   Benefit preservation for the rule agent does not improve until above 0.6. For the scripted
   agent, local net gain stops improving above 0.6. The useful operating region for the rule
   agent is 0.8–1.0; below about 0.3 the gate is worse than rejecting everything.

## Files

- `scripts/gateway_uncertainty_ablation.py`: extended with `sweep` (a resumable, parallel
  worker writing one file per episode) and `sweep-analyze` (seed-bootstrap CIs, exact
  dynamic-programming Wilcoxon, Holm, crossings, adjacent-step changes, per-arm composition).
  Existing `run`/`analyze` subcommands are unchanged.
- `docs/research/extended_fidelity_sweep.json`: every per-seed episode total, every metric
  above with its CI, bootstrap crossing counts, adjacent-step changes, composition by action
  type, and the list of seeds with beneficial candidates.
- The raw per-episode proposal files (3,150) are in the session scratchpad; they can be
  regenerated deterministically with the commands above.
