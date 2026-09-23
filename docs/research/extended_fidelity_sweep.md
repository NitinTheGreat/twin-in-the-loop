# Extended fidelity sweep: 150 seeds, six fidelity levels, two queueing conditions

Date: 2026-09-20. No language-model calls were made; the "scripted" controller is the
deterministic scale-or-hold stub. `paper/main.tex` was not modified.
Machine-readable results: `docs/research/extended_fidelity_sweep.json`.

This supersedes the 2026-09-17 version of this file, which covered condition (i) only. Every
condition (i) number below was re-run from scratch and reproduces that version **exactly**
(same curves, same p-values, same composition counts), so the earlier results stand.

## Bottom line

1. **The net-Σ-D sign change is not an artifact of the discrete queueing switch.** Holding
   `simplify_queueing` off at every fidelity level, the scripted controller's net Σ D still
   crosses zero, at **0.549 [0.434, 0.677]** against **0.555 [0.471, 0.677]** as configured.
   The curve shifts down by 0.16–0.81 ticks per seed at the three levels the switch touches,
   and not at all at the three it does not. **The crossover survives; report it as a fidelity
   effect.**
2. **The concern was nevertheless well founded.** The crossing sits between f = 0.4 and f = 0.6,
   exactly where the switch flips. Only the paired two-condition run rules it out. At f = 0.4
   the switch is worth **−0.160 ticks per seed [−0.813, +0.480], Wilcoxon p = 0.61**, against a
   0.4 → 0.6 step of −1.27; it cannot account for the sign change.
3. **The rule controller has no net-Σ-D crossover under either condition.** Net Σ D is at or
   below zero at all six levels in both. Under the held-constant condition the curve is
   **non-monotone** (−0.127, −0.447, −0.787, −0.780, −1.320, −2.120): the 0.4 → 0.6 step is
   +0.007 [−0.133, +0.193]. That is reported as a flat region, not fitted through.
4. **The rule controller's episode advantage over reject-all does reach significance at 150
   seeds.** At fidelity 1.0: **−2.320 violation-ticks [−3.762, −0.878]**, exact Wilcoxon
   p = 0.00097, Holm p = 0.0078, 22 of 150 seeds differ (18 better, 4 worse), observed power
   0.96. The normal formula puts 80% power at **117 seeds**; Analysis C predicted ~125.
5. **The scripted controller's twin never beats reject-all at the episode level.** At fidelity
   1.0 the difference is +0.233 [−0.156, +0.622], p = 0.287, power 0.21 (~839 seeds needed).
   Below 0.8 it is significantly worse. Unchanged by the queueing condition.
6. **Rule-agent `benefit_preserved` is still non-monotone at 150 seeds, and the wobble moves
   when the queueing model changes** — which is itself evidence that it is noise. As
   configured: 0.409, 0.431, 0.373, 0.424, 0.609, 0.970. Held constant: 0.430, 0.349, 0.482,
   0.424, 0.609, 0.970. Only the 0.8 → 1.0 step has a CI excluding zero, in both conditions.
   Not smoothed.
7. **Most of the reported false positive count is definitional, not predictive error.** At the
   published τ = 0, θ = 3 setting, **83.6%** of the scripted controller's false positives at
   fidelity 1.0 (56 of 67) are actions whose true harm lies in 0 < D ≤ 3. On the τ = θ
   diagonal that band is empty by construction, so every false positive there is genuine
   predictive error: FPR falls from 0.256 to **0.065** at τ = θ = 3 with recall unchanged at
   0.996.
8. **Opportunity is the binding constraint, as before.** Seeds containing at least one
   beneficial candidate: rule **23/150** on the reject-all trajectory (59/150 in any arm);
   scripted **12/150** (149/150 in any arm).

---

## Configuration

| Item | Value |
|---|---|
| Seeds | **150**, contiguous 0–149 |
| Fidelity levels | 0.0, 0.2, 0.4, 0.6, 0.8, 1.0 |
| Controllers | Rule agent; scripted agent (deterministic scale-or-hold stub, runner kind `llm`, **no model calls**) |
| Arms per controller | ungated, reject-all, random (p = 0.30 rule, 0.747 scripted), schedule-aware (`oracle`), twin at each of the 6 fidelities |
| Reference | A0 (null agent), 150 seeds |
| Protocol | Published condition: 120-tick episodes, decision every 10 ticks, retry cap 2, twin horizon 20, tolerance τ = 0, harm threshold θ = 3, default `FaultConfig` (3 faults, start ticks 20–260), **gateway immune** |
| Queueing conditions | **(i) discrete switch active** (as configured); **(ii) `simplify_queueing` forced off at every level** |
| Episodes | 3,150 per condition (150 × (2 × 10 + 1)); **6,300 total** |
| Uncertainty | Seed-level bootstrap, 5,000 resamples (2,000 for the τ/θ grid), resampled seeds shared across arms so comparisons stay paired; t-intervals; exact Wilcoxon signed-rank at n = 150 by dynamic programming; Holm within each controller's 12 episode comparisons |

`simplify_queueing` is the only discrete threshold on the fidelity axis
(`src/twinloop/twin/fidelity.py:32`, `SIMPLIFY_BELOW = 0.5`). The other four knobs are
continuous in `1 − fidelity`, except `lag_ticks`, which is an integer staircase but monotone:

| Fidelity | sigma_obs | lag_ticks | drift_pct | forecast_err | simplify_queueing (as configured) |
|---|---|---|---|---|---|
| 0.0 | 0.300 | 12 | 0.300 | 0.400 | **on** |
| 0.2 | 0.240 | 10 | 0.240 | 0.320 | **on** |
| 0.4 | 0.180 | 7 | 0.180 | 0.240 | **on** |
| 0.6 | 0.120 | 5 | 0.120 | 0.160 | off |
| 0.8 | 0.060 | 2 | 0.060 | 0.080 | off |
| 1.0 | 0.000 | 0 | 0.000 | 0.000 | off |

Condition (ii) forces the last column off everywhere and changes nothing else. It is applied by
patching `fidelity_to_config` in the sweep worker, so the twin's construction is otherwise
byte-identical.

### Actual cost

**2,338 s of wall clock (39 minutes)** for both conditions on 10 worker processes: 1,157 s for
condition (i) and 1,181 s for condition (ii). That is **7.8 s of wall time per seed per
condition**, or 15.6 s per seed across both — inside the 9–17 s estimate, so 150 seeds were run
as specified and no reduction was needed. Measured episode time totalled 23,208 CPU-seconds
(11,484 + 11,724) over 6,300 episodes; single-threaded the cost is about 1.55 s per episode,
32.5 s per seed per condition.

Mean seconds per episode under 10-way contention:

| Arm | Rule (i) | Rule (ii) | Scripted (i) | Scripted (ii) |
|---|---|---|---|---|
| ungated | 1.83 | 1.83 | 2.46 | 2.46 |
| reject-all | 1.91 | 1.92 | 3.79 | 3.80 |
| random | 1.86 | 1.87 | 3.57 | 3.58 |
| twin@0.0 | 3.13 | 3.31 | 4.71 | 5.01 |
| twin@1.0 | 3.31 | 3.30 | 5.57 | 5.57 |
| schedule-aware | 3.25 | 3.26 | 5.52 | 5.52 |
| A0 null | 1.83 | 1.84 | — | — |

### Reproduce

```powershell
# condition (i), run once per k = 0..9
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py sweep --shards <dir>\discrete --workdir <dir>\w1 --worker <k> --workers 10 --seed-count 150
# condition (ii), run once per k = 0..9
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py sweep --shards <dir>\continuous --workdir <dir>\w2 --worker <k> --workers 10 --seed-count 150 --no-simplify-queueing
.\.venv\Scripts\python.exe scripts\gateway_uncertainty_ablation.py extended-analyze --discrete <dir>\discrete --continuous <dir>\continuous --output docs\research\extended_fidelity_sweep.json
```

Workers write one file per episode and skip episodes already on disk, so an interrupted sweep
resumes. `sweep` and `sweep-analyze` behave exactly as before when the new flag is omitted.

### Validity checks passed

- **Condition (i) reproduces the 2026-09-17 sweep exactly**, including every curve, every
  Holm-adjusted p-value and every composition count.
- **Seeds 0–9 reproduce the published 10-seed per-seed values** for every arm recorded in both.
- **Reject-all equals A0 on all 150 seeds** for both controllers, in both conditions.
- **The queueing flag touches only what it should.** Across all 150 seeds, the two conditions
  produce **bit-identical episodes** for every non-twin arm and for twin@0.6, twin@0.8 and
  twin@1.0 (0 differing seeds). Only twin@0.0, twin@0.2 and twin@0.4 differ. This is exactly
  the footprint of a switch that is off at f ≥ 0.5.
- **The recorded verdict is reconstructed exactly from the predicted deltas at τ = 0**: 0
  mismatches out of every twin-arm candidate in the corpus, in both conditions.
- **θ = 3 reproduces the experiment's own `ground_truth_harmful` label**: 0 mismatches out of
  1,326 twin@1.0 candidates.
- **No denominator is zero anywhere**, in any arm or any τ/θ cell, and every bootstrap resample
  is estimable. The "not estimable" convention is still enforced in code; it simply never
  fires here.

---

# CRITICAL: disambiguating the discrete queueing switch

## The question

The queueing-simplification axis flips on at f < 0.5 rather than degrading continuously. The
observed net-Σ-D sign change straddles that boundary, so the "crossover" might be the queueing
model flipping rather than a fidelity effect. Both curves are below, side by side.

## 1. Net Σ D per seed, both conditions

| Controller | Queueing condition | f=0.0 | f=0.2 | f=0.4 | f=0.6 | f=0.8 | f=1.0 | Sign changes | Monotone |
|---|---|---|---|---|---|---|---|---|---|
| Rule | (i) discrete switch active | −0.147 | −0.393 | −0.593 | −0.780 | −1.320 | −2.120 | 0 | yes |
| Rule | (ii) queueing held constant | −0.127 | −0.447 | −0.787 | −0.780 | −1.320 | −2.120 | 0 | **no** |
| Scripted | (i) discrete switch active | **+3.033** | **+2.413** | **+0.987** | **−0.287** | **−0.587** | **−0.620** | **1** | yes |
| Scripted | (ii) queueing held constant | **+2.227** | **+1.787** | **+0.827** | **−0.287** | **−0.587** | **−0.620** | **1** | yes |

With bootstrap 95% CIs:

| Controller | Queueing | f=0.0 | f=0.2 | f=0.4 | f=0.6 | f=0.8 | f=1.0 |
|---|---|---|---|---|---|---|---|
| Rule | (i) | −0.147 [−0.627, +0.313] | −0.393 [−0.800, −0.027] | −0.593 [−1.127, −0.113] | −0.780 [−1.273, −0.360] | −1.320 [−2.107, −0.653] | −2.120 [−3.240, −1.093] |
| Rule | (ii) | −0.127 [−0.633, +0.387] | −0.447 [−0.913, 0.000] | −0.787 [−1.307, −0.347] | −0.780 [−1.273, −0.360] | −1.320 [−2.107, −0.653] | −2.120 [−3.240, −1.093] |
| Scripted | (i) | +3.033 [+2.173, +3.893] | +2.413 [+1.567, +3.267] | +0.987 [+0.347, +1.653] | −0.287 [−0.807, +0.213] | −0.587 [−1.027, −0.193] | −0.620 [−1.027, −0.253] |
| Scripted | (ii) | +2.227 [+1.367, +3.100] | +1.787 [+0.920, +2.613] | +0.827 [+0.133, +1.560] | −0.287 [−0.807, +0.213] | −0.587 [−1.027, −0.193] | −0.620 [−1.027, −0.253] |

The f = 0.6, 0.8 and 1.0 columns are identical by construction and identical in fact.

## 2. What the switch is worth, measured directly

Same seeds, same trajectories up to the first disagreement. Difference is condition (ii) minus
condition (i); negative means holding the queueing model constant made that arm better.

| Controller | Arm | Δ net Σ D / seed | 95% CI | seeds ≠ 0 | Wilcoxon p | Δ episode ticks / seed | 95% CI | seeds ≠ 0 | Wilcoxon p |
|---|---|---|---|---|---|---|---|---|---|
| Rule | twin@0.0 | +0.020 | [−0.240, +0.287] | 12 | 0.925 | −0.100 | [−0.740, +0.407] | 13 | 0.986 |
| Rule | twin@0.2 | −0.053 | [−0.320, +0.207] | 9 | 0.684 | **−0.900** | [−1.760, −0.227] | 13 | **0.010** |
| Rule | twin@0.4 | −0.193 | [−0.513, +0.040] | 8 | 0.266 | −0.267 | [−1.047, +0.427] | 9 | 0.570 |
| Rule | twin@0.6 / 0.8 / 1.0 | 0.000 | [0, 0] | **0** | 1.0 | 0.000 | [0, 0] | **0** | 1.0 |
| Rule | ungated / reject-all / random / schedule-aware | 0.000 | [0, 0] | **0** | 1.0 | 0.000 | [0, 0] | **0** | 1.0 |
| Scripted | twin@0.0 | −0.807 | [−1.487, −0.127] | 84 | 0.064 | **−2.580** | [−4.193, −1.020] | 87 | **0.0089** |
| Scripted | twin@0.2 | −0.627 | [−1.393, +0.113] | 85 | 0.206 | −0.700 | [−2.193, +0.787] | 87 | 0.259 |
| Scripted | twin@0.4 | −0.160 | [−0.813, +0.480] | 70 | 0.607 | −0.147 | [−1.367, +1.153] | 74 | 0.659 |
| Scripted | twin@0.6 / 0.8 / 1.0 | 0.000 | [0, 0] | **0** | 1.0 | 0.000 | [0, 0] | **0** | 1.0 |
| Scripted | ungated / reject-all / random / schedule-aware | 0.000 | [0, 0] | **0** | 1.0 | 0.000 | [0, 0] | **0** | 1.0 |

The switch has a real effect, and it is in the direction that would *flatter* the low-fidelity
twin: turning queueing simplification off makes the degraded twin slightly better, not worse.
It is significant at the episode level in two of six affected arms and nowhere on net Σ D.

## 3. Crossover fidelity, both conditions

Crossings are found by linear interpolation between adjacent levels, recomputed in each of 5,000
seed-bootstrap resamples; the interval uses the resamples with exactly one crossing.

### Primary curve: net Σ D over approved candidates

| Controller | Queueing | Curve | Monotone | Observed crossing | Resamples 0 / 1 / ≥2 | 95% interval | median |
|---|---|---|---|---|---|---|---|
| Rule | (i) | −0.147 / −0.393 / −0.593 / −0.780 / −1.320 / −2.120 | yes | **none in [0,1]** | 3,639 / 1,343 / 18 | [0.006, 0.280] | 0.071 |
| Rule | (ii) | −0.127 / −0.447 / −0.787 / −0.780 / −1.320 / −2.120 | **no** | **none in [0,1]** | 3,422 / 1,575 / 3 | [0.006, 0.235] | 0.073 |
| **Scripted** | **(i)** | +3.033 / +2.413 / +0.987 / −0.287 / −0.587 / −0.620 | yes | **0.555** | 0 / 4,997 / 3 | **[0.471, 0.677]** | 0.554 |
| **Scripted** | **(ii)** | +2.227 / +1.787 / +0.827 / −0.287 / −0.587 / −0.620 | yes | **0.549** | 0 / 4,997 / 3 | **[0.434, 0.677]** | 0.548 |

### Episode-level curve: twin − reject-all

| Controller | Queueing | Curve | Observed crossing | Resamples 0 / 1 / ≥2 | 95% interval |
|---|---|---|---|---|---|
| Rule | (i) | +1.073 / +0.760 / −0.500 / −1.067 / −1.707 / −2.320 | 0.321 | 136 / 4,861 / 3 | [0.121, 0.553] |
| Rule | (ii) | +0.973 / −0.140 / −0.767 / −1.067 / −1.707 / −2.320 | **0.175** | 146 / 4,748 / **106** | [0.032, 0.534] |
| Scripted | (i) | +15.013 / +11.280 / +6.313 / +2.367 / +0.560 / +0.233 | none in [0,1] | 4,445 / 555 / 0 | [0.797, 1.000] |
| Scripted | (ii) | +12.433 / +10.580 / +6.167 / +2.367 / +0.560 / +0.233 | none in [0,1] | 4,445 / 555 / 0 | [0.797, 1.000] |

## 4. Verdict, stated plainly

**The sign change survives when the queueing model is held constant. It is not an artifact of
the discrete axis.**

The evidence, in order of weight:

1. **The crossing barely moves.** Scripted net Σ D crosses at 0.555 as configured and 0.549 held
   constant. Each point estimate sits comfortably inside the other's interval.
2. **The sign change happens at the same step in both conditions**, 0.4 → 0.6, and the curve is
   strictly monotone decreasing in both. Held constant, net Σ D at f = 0.4 is still **+0.827
   [+0.133, +1.560]** — positive with a CI excluding zero — and at f = 0.6 still −0.287.
3. **The switch is too small to explain the step.** At f = 0.4 it is worth −0.160 ticks per seed
   [−0.813, +0.480], p = 0.61, against a 0.4 → 0.6 step of −1.113 [−1.753, −0.507] in condition
   (ii). Even at its largest, at f = 0.0, it is worth −0.807 against a curve that spans 2.8
   ticks per seed.
4. **The levels the switch does not touch carry the result.** f = 0.6, 0.8 and 1.0 are
   bit-identical across conditions, and the negative half of the curve lives entirely there.

Two things should be reported alongside that verdict, because they are also true:

- **The rule controller's *episode-level* crossover is not robust to the queueing condition.**
  It moves from 0.321 [0.121, 0.553] to 0.175 [0.032, 0.534], and the fraction of resamples with
  two or more crossings rises from 3/5,000 to 106/5,000. The intervals overlap heavily, but the
  point estimate nearly halves. That crossover should be quoted as "somewhere below about 0.4",
  not as a number.
- **The rule controller's net-Σ-D curve becomes non-monotone once queueing is held constant.**
  The 0.4 → 0.6 step is +0.007 [−0.133, +0.193]. Reported as a flat region between 0.4 and 0.6,
  not fitted through.

---

# 1–2. benefit_preserved, harm_prevented and net Σ D, per fidelity

Definitions follow `candidate_composition.md` Analysis B. D = V₂₀(action, then no-op) − V₂₀(no-op);
D < 0 is beneficial. Each arm is scored on **its own** candidates, no-ops excluded. Ratio CIs are
seed-bootstrap percentile intervals. Net Σ D is the per-seed mean of Σ D over approved
candidates; negative means the approved set is locally better than rejecting everything.

- benefit_preserved = Σ max(−D, 0) over approved ÷ Σ max(−D, 0) over all candidates.
- harm_prevented = Σ max(D, 0) over rejected ÷ Σ max(D, 0) over all candidates.
- **No denominator is zero in this sweep**, so nothing is "not estimable". Where one would be,
  the code reports n/e rather than 1.0 or 0.0.

## Rule controller — condition (i), discrete switch active

| Gate | benefit_preserved [95% CI] | (appr/avail) | harm_prevented [95% CI] | (rej/avail) | **Net Σ D per seed** | bootstrap CI | t-CI | seeds ≠ 0 | Net total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (ref) | **0.000** [0.000, 0.000] | 0 / 439 | **1.000** [1.000, 1.000] | 759 / 759 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.409 [0.274, 0.568] | 209 / 511 | 0.747 [0.643, 0.833] | 551 / 738 | **−0.147** | [−0.627, +0.313] | [−0.635, +0.342] | 27 | −22 |
| twin@0.2 | 0.431 [0.285, 0.615] | 198 / 459 | 0.818 [0.716, 0.894] | 625 / 764 | **−0.393** | [−0.800, −0.027] | [−0.783, −0.004] | 22 | −59 |
| twin@0.4 | 0.373 [0.216, 0.566] | 144 / 386 | 0.926 [0.857, 0.980] | 684 / 739 | **−0.593** | [−1.127, −0.113] | [−1.103, −0.084] | 16 | −89 |
| twin@0.6 | 0.424 [0.250, 0.665] | 143 / 337 | 0.965 [0.925, 0.995] | 725 / 751 | **−0.780** | [−1.273, −0.360] | [−1.242, −0.318] | 16 | −117 |
| twin@0.8 | 0.609 [0.388, 0.862] | 206 / 338 | 0.989 [0.962, 1.000] | 744 / 752 | **−1.320** | [−2.107, −0.653] | [−2.061, −0.579] | 18 | −198 |
| **twin@1.0** | **0.970** [0.902, 1.000] | 325 / 335 | **0.991** [0.975, 1.000] | 755 / 762 | **−2.120** | [−3.240, −1.093] | [−3.219, −1.021] | 23 | −318 |
| schedule-aware | 1.000 [1.000, 1.000] | 326 / 326 | 1.000 [1.000, 1.000] | 767 / 767 | −2.173 | [−3.287, −1.180] | [−3.261, −1.086] | 22 | −326 |
| random p=0.3 | 0.694 [0.559, 0.830] | 410 / 591 | 0.294 [0.183, 0.397] | 207 / 705 | +0.587 | [−0.367, +1.487] | [−0.355, +1.528] | 62 | +88 |
| ungated (ref) | 1.000 [1.000, 1.000] | 622 / 622 | 0.000 [0.000, 0.000] | 0 / 630 | +0.053 | [−1.067, +1.147] | [−1.079, +1.186] | 69 | +8 |

## Rule controller — condition (ii), queueing held constant

| Gate | benefit_preserved [95% CI] | (appr/avail) | harm_prevented [95% CI] | (rej/avail) | **Net Σ D per seed** | bootstrap CI | t-CI | seeds ≠ 0 | Net total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (ref) | **0.000** [0.000, 0.000] | 0 / 439 | **1.000** [1.000, 1.000] | 759 / 759 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.430 [0.292, 0.599] | 215 / 500 | 0.737 [0.627, 0.834] | 549 / 745 | **−0.127** | [−0.633, +0.387] | [−0.649, +0.396] | 25 | −19 |
| twin@0.2 | 0.349 [0.217, 0.521] | 153 / 439 | 0.886 [0.805, 0.951] | 666 / 752 | **−0.447** | [−0.913, 0.000] | [−0.910, +0.016] | 21 | −67 |
| twin@0.4 | 0.482 [0.304, 0.690] | 172 / 357 | 0.928 [0.866, 0.976] | 695 / 749 | **−0.787** | [−1.307, −0.347] | [−1.269, −0.305] | 19 | −118 |
| twin@0.6 | 0.424 [0.250, 0.665] | 143 / 337 | 0.965 [0.925, 0.995] | 725 / 751 | **−0.780** | [−1.273, −0.360] | [−1.242, −0.318] | 16 | −117 |
| twin@0.8 | 0.609 [0.388, 0.862] | 206 / 338 | 0.989 [0.962, 1.000] | 744 / 752 | **−1.320** | [−2.107, −0.653] | [−2.061, −0.579] | 18 | −198 |
| **twin@1.0** | **0.970** [0.902, 1.000] | 325 / 335 | **0.991** [0.975, 1.000] | 755 / 762 | **−2.120** | [−3.240, −1.093] | [−3.219, −1.021] | 23 | −318 |
| schedule-aware | 1.000 [1.000, 1.000] | 326 / 326 | 1.000 [1.000, 1.000] | 767 / 767 | −2.173 | [−3.287, −1.180] | [−3.261, −1.086] | 22 | −326 |
| random p=0.3 | 0.694 [0.559, 0.830] | 410 / 591 | 0.294 [0.183, 0.397] | 207 / 705 | +0.587 | [−0.367, +1.487] | [−0.355, +1.528] | 62 | +88 |
| ungated (ref) | 1.000 [1.000, 1.000] | 622 / 622 | 0.000 [0.000, 0.000] | 0 / 630 | +0.053 | [−1.067, +1.147] | [−1.079, +1.186] | 69 | +8 |

## Scripted controller — condition (i), discrete switch active

| Gate | benefit_preserved [95% CI] | (appr/avail) | harm_prevented [95% CI] | (rej/avail) | **Net Σ D per seed** | bootstrap CI | t-CI | seeds ≠ 0 | Net total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (ref) | **0.000** [0.000, 0.000] | 0 / 52 | **1.000** [1.000, 1.000] | 7,712 / 7,712 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.727 [0.671, 0.785] | 1,286 / 1,770 | 0.681 [0.641, 0.719] | 3,712 / 5,453 | **+3.033** | [+2.173, +3.893] | [+2.130, +3.937] | 124 | +455 |
| twin@0.2 | 0.803 [0.738, 0.866] | 1,102 / 1,373 | 0.755 [0.720, 0.788] | 4,507 / 5,971 | **+2.413** | [+1.567, +3.267] | [+1.523, +3.303] | 116 | +362 |
| twin@0.4 | 0.819 [0.751, 0.888] | 709 / 866 | 0.870 [0.842, 0.896] | 5,722 / 6,579 | **+0.987** | [+0.347, +1.653] | [+0.296, +1.677] | 82 | +148 |
| twin@0.6 | 0.839 [0.738, 0.925] | 317 / 378 | 0.962 [0.948, 0.974] | 6,947 / 7,221 | **−0.287** | [−0.807, +0.213] | [−0.800, +0.226] | 41 | −43 |
| twin@0.8 | 0.901 [0.781, 0.973] | 163 / 181 | 0.990 [0.984, 0.995] | 7,438 / 7,513 | **−0.587** | [−1.027, −0.193] | [−0.993, −0.180] | 22 | −88 |
| **twin@1.0** | **0.877** [0.746, 0.969] | 135 / 154 | **0.994** [0.990, 0.998] | 7,531 / 7,573 | **−0.620** | [−1.027, −0.253] | [−1.011, −0.229] | 21 | −93 |
| schedule-aware | 1.000 [1.000, 1.000] | 107 / 107 | 1.000 [1.000, 1.000] | 7,556 / 7,556 | −0.713 | [−1.093, −0.387] | [−1.071, −0.356] | 19 | −107 |
| random p=0.747 | 0.257 [0.213, 0.305] | 704 / 2,735 | 0.737 [0.702, 0.768] | 3,880 / 5,268 | +4.560 | [+3.647, +5.493] | [+3.642, +5.478] | 120 | +684 |
| ungated (ref) | 1.000 [1.000, 1.000] | 2,661 / 2,661 | 0.000 [0.000, 0.000] | 0 / 3,339 | +4.520 | [+3.400, +5.647] | [+3.388, +5.652] | 144 | +678 |

## Scripted controller — condition (ii), queueing held constant

| Gate | benefit_preserved [95% CI] | (appr/avail) | harm_prevented [95% CI] | (rej/avail) | **Net Σ D per seed** | bootstrap CI | t-CI | seeds ≠ 0 | Net total |
|---|---|---|---|---|---|---|---|---|---|
| reject-all (ref) | **0.000** [0.000, 0.000] | 0 / 52 | **1.000** [1.000, 1.000] | 7,712 / 7,712 | **0.000** | [0, 0] | [0, 0] | 0 | 0 |
| twin@0.0 | 0.751 [0.695, 0.812] | 1,281 / 1,706 | 0.710 [0.673, 0.745] | 3,951 / 5,566 | **+2.227** | [+1.367, +3.100] | [+1.351, +3.103] | 127 | +334 |
| twin@0.2 | 0.772 [0.710, 0.836] | 1,057 / 1,370 | 0.783 [0.749, 0.814] | 4,776 / 6,101 | **+1.787** | [+0.920, +2.613] | [+0.930, +2.644] | 112 | +268 |
| twin@0.4 | 0.802 [0.738, 0.865] | 708 / 883 | 0.874 [0.846, 0.899] | 5,760 / 6,592 | **+0.827** | [+0.133, +1.560] | [+0.116, +1.537] | 82 | +124 |
| twin@0.6 | 0.839 [0.738, 0.925] | 317 / 378 | 0.962 [0.948, 0.974] | 6,947 / 7,221 | **−0.287** | [−0.807, +0.213] | [−0.800, +0.226] | 41 | −43 |
| twin@0.8 | 0.901 [0.781, 0.973] | 163 / 181 | 0.990 [0.984, 0.995] | 7,438 / 7,513 | **−0.587** | [−1.027, −0.193] | [−0.993, −0.180] | 22 | −88 |
| **twin@1.0** | **0.877** [0.746, 0.969] | 135 / 154 | **0.994** [0.990, 0.998] | 7,531 / 7,573 | **−0.620** | [−1.027, −0.253] | [−1.011, −0.229] | 21 | −93 |
| schedule-aware | 1.000 [1.000, 1.000] | 107 / 107 | 1.000 [1.000, 1.000] | 7,556 / 7,556 | −0.713 | [−1.093, −0.387] | [−1.071, −0.356] | 19 | −107 |
| random p=0.747 | 0.257 [0.213, 0.305] | 704 / 2,735 | 0.737 [0.702, 0.768] | 3,880 / 5,268 | +4.560 | [+3.647, +5.493] | [+3.642, +5.478] | 120 | +684 |
| ungated (ref) | 1.000 [1.000, 1.000] | 2,661 / 2,661 | 0.000 [0.000, 0.000] | 0 / 3,339 | +4.520 | [+3.400, +5.647] | [+3.388, +5.652] | 144 | +678 |

The 10-seed three-point figures quoted in the brief (scripted net Σ D +38 / −4 / −16 at
fidelity 0.0 / 0.6 / 1.0) become **+455 / −43 / −93** over 150 seeds as configured, and
**+334 / −43 / −93** with queueing held constant. Per seed: +3.03 / −0.29 / −0.62 and
+2.23 / −0.29 / −0.62, against +3.8 / −0.4 / −1.6 at 10 seeds. The 10-seed numbers had the
right shape and roughly the right size at 0.0 and 0.6, and overstated the effect at 1.0 by
about 2.5×.

## Changes between adjacent fidelity levels (paired seed bootstrap)

"excl. 0" marks steps whose 95% CI excludes zero.

### Rule — (i) discrete switch active

| Step | Δ net Σ D | Δ benefit_preserved | Δ harm_prevented |
|---|---|---|---|
| 0.0 → 0.2 | −0.247 [−0.553, +0.060] | +0.022 [−0.044, +0.104] | **+0.071 [+0.008, +0.137] excl. 0** |
| 0.2 → 0.4 | −0.200 [−0.620, +0.153] | −0.058 [−0.241, +0.098] | **+0.108 [+0.040, +0.200] excl. 0** |
| 0.4 → 0.6 | **−0.187 [−0.447, −0.013] excl. 0** | +0.051 [−0.045, +0.180] | +0.040 [−0.019, +0.108] |
| 0.6 → 0.8 | **−0.540 [−1.140, −0.067] excl. 0** | +0.185 [−0.003, +0.395] | +0.024 [−0.017, +0.069] |
| 0.8 → 1.0 | **−0.800 [−1.593, −0.140] excl. 0** | **+0.361 [+0.103, +0.586] excl. 0** | +0.001 [−0.021, +0.031] |

### Rule — (ii) queueing held constant

| Step | Δ net Σ D | Δ benefit_preserved | Δ harm_prevented |
|---|---|---|---|
| 0.0 → 0.2 | **−0.320 [−0.653, −0.007] excl. 0** | −0.081 [−0.222, +0.029] | **+0.149 [+0.073, +0.243] excl. 0** |
| 0.2 → 0.4 | −0.340 [−0.800, +0.033] | +0.133 [−0.039, +0.322] | +0.042 [−0.026, +0.115] |
| **0.4 → 0.6** | **+0.007 [−0.133, +0.193]** | −0.057 [−0.232, +0.073] | +0.037 [−0.008, +0.096] |
| 0.6 → 0.8 | **−0.540 [−1.140, −0.067] excl. 0** | +0.185 [−0.003, +0.395] | +0.024 [−0.017, +0.069] |
| 0.8 → 1.0 | **−0.800 [−1.593, −0.140] excl. 0** | **+0.361 [+0.103, +0.586] excl. 0** | +0.001 [−0.021, +0.031] |

### Scripted — (i) discrete switch active

| Step | Δ net Σ D | Δ benefit_preserved | Δ harm_prevented |
|---|---|---|---|
| 0.0 → 0.2 | −0.620 [−1.347, +0.073] | **+0.076 [+0.024, +0.134] excl. 0** | **+0.074 [+0.043, +0.106] excl. 0** |
| 0.2 → 0.4 | **−1.427 [−2.187, −0.673] excl. 0** | +0.016 [−0.057, +0.090] | **+0.115 [+0.086, +0.145] excl. 0** |
| 0.4 → 0.6 | **−1.273 [−1.900, −0.687] excl. 0** | +0.020 [−0.062, +0.094] | **+0.092 [+0.068, +0.119] excl. 0** |
| 0.6 → 0.8 | −0.300 [−0.773, +0.133] | +0.062 [−0.056, +0.171] | **+0.028 [+0.016, +0.041] excl. 0** |
| 0.8 → 1.0 | −0.033 [−0.273, +0.213] | −0.024 [−0.103, +0.049] | **+0.004 [+0.001, +0.009] excl. 0** |

### Scripted — (ii) queueing held constant

| Step | Δ net Σ D | Δ benefit_preserved | Δ harm_prevented |
|---|---|---|---|
| 0.0 → 0.2 | −0.440 [−1.227, +0.373] | +0.021 [−0.046, +0.088] | **+0.073 [+0.041, +0.107] excl. 0** |
| 0.2 → 0.4 | **−0.960 [−1.793, −0.107] excl. 0** | +0.030 [−0.029, +0.090] | **+0.091 [+0.060, +0.122] excl. 0** |
| **0.4 → 0.6** | **−1.113 [−1.753, −0.507] excl. 0** | +0.037 [−0.064, +0.127] | **+0.088 [+0.064, +0.113] excl. 0** |
| 0.6 → 0.8 | −0.300 [−0.773, +0.133] | +0.062 [−0.056, +0.171] | **+0.028 [+0.016, +0.041] excl. 0** |
| 0.8 → 1.0 | −0.033 [−0.273, +0.213] | −0.024 [−0.103, +0.049] | **+0.004 [+0.001, +0.009] excl. 0** |

The decisive row is **Scripted, condition (ii), 0.4 → 0.6: −1.113 [−1.753, −0.507]**. That step
carries the sign change, it excludes zero, and in condition (ii) the queueing model is identical
on both sides of it.

---

# 3. Crossover fidelity

Reported in full in the disambiguation section above. Summary:

- **Scripted, net Σ D:** one clean crossing in both conditions. **0.555 [0.471, 0.677]** as
  configured, **0.549 [0.434, 0.677]** held constant. Monotone in both; 4,997 of 5,000
  resamples have exactly one crossing in both. No fitting through a non-monotone region was
  needed.
- **Rule, net Σ D:** **no crossing to report** in either condition. The curve is at or below
  zero at every level. The interval at f = 0.0 includes zero in both conditions, so 27% (i) and
  31% (ii) of resamples place a crossing just above 0.0, below about 0.28. In condition (ii) the
  curve is non-monotone between 0.4 and 0.6; that is reported as a flat region rather than
  fitted.
- **Episode level:** the scripted curve never crosses in either condition, approaching zero from
  above (89% of resamples have no crossing). The rule curve crosses at 0.321 [0.121, 0.553] as
  configured and 0.175 [0.032, 0.534] held constant — **not robust to the queueing condition**;
  quote it as "below about 0.4".

---

# 4. Episode-level paired differences, n = 150

Difference = first arm − second arm, in episode violation-ticks. Negative means the first arm is
better. "Non-zero" counts seeds where the arms differ; B/W counts better/worse. Power is the
bootstrap-estimated power of the Wilcoxon test at n = 150 on the observed differences. "Seeds
for 80%" uses the normal approximation. Holm is over the 12 comparisons per controller per
condition.

## Rule controller — condition (i), discrete switch active

| Comparison | Mean | 95% t-CI | bootstrap CI | SD | non-zero (B/W) | exact Wilcoxon p | Holm p | **Significant at 150?** | Power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +1.073 | [−0.048, +2.194] | [−0.020, +2.233] | 6.95 | 29 (6/23) | 0.0110 | 0.0658 | **No** (unadjusted yes; twin worse) | 0.73 | 329 |
| twin@0.2 − reject-all | +0.760 | [−0.356, +1.876] | [−0.320, +1.873] | 6.92 | 25 (8/17) | 0.111 | 0.557 | No | 0.36 | 651 |
| twin@0.4 − reject-all | −0.500 | [−1.518, +0.518] | [−1.513, +0.447] | 6.31 | 16 (10/6) | 0.411 | 0.822 | No | 0.13 | 1,251 |
| twin@0.6 − reject-all | −1.067 | [−2.167, +0.034] | [−2.260, −0.140] | 6.82 | 17 (11/6) | 0.117 | 0.557 | No | 0.32 | 322 |
| twin@0.8 − reject-all | **−1.707** | [−2.987, −0.426] | [−3.047, −0.567] | 7.94 | 18 (15/3) | **0.0036** | **0.025** | **Yes** | 0.87 | 170 |
| **twin@1.0 − reject-all** | **−2.320** | **[−3.762, −0.878]** | [−3.880, −1.020] | 8.94 | **22 (18/4)** | **0.00097** | **0.0078** | **Yes** | **0.96** | 117 |
| schedule-aware − reject-all | −2.587 | [−4.027, −1.146] | [−4.160, −1.287] | 8.93 | 22 (20/2) | 8.3 × 10⁻⁵ | 0.00083 | Yes | 0.99 | 94 |
| ungated − reject-all | +3.393 | [+1.338, +5.449] | [+1.473, +5.520] | 12.74 | 71 (14/57) | 1.2 × 10⁻⁴ | 0.0011 | Yes (ungated worse) | 0.98 | 111 |
| random − reject-all | +3.327 | [+1.366, +5.287] | [+1.367, +5.333] | 12.15 | 63 (13/50) | 6.8 × 10⁻⁵ | 0.00075 | Yes (random worse) | 0.98 | 105 |
| twin@1.0 − twin@0.0 | −3.393 | [−5.018, −1.768] | [−5.073, −1.940] | 10.07 | 38 (33/5) | 9.2 × 10⁻⁷ | 1.1 × 10⁻⁵ | Yes | 1.00 | 70 |
| schedule-aware − twin@1.0 | −0.267 | [−0.601, +0.068] | [−0.633, 0.000] | 2.07 | 3 (3/0) | 0.25 | 0.75 | **No** (3 seeds; 0.25 is the smallest achievable p) | 0.10 | 474 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

## Rule controller — condition (ii), queueing held constant

| Comparison | Mean | 95% t-CI | bootstrap CI | SD | non-zero (B/W) | exact Wilcoxon p | Holm p | **Significant at 150?** | Power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +0.973 | [−0.053, +2.000] | [−0.033, +1.993] | 6.36 | 28 (6/22) | 0.0147 | 0.0885 | **No** (twin worse) | 0.68 | 336 |
| twin@0.2 − reject-all | −0.140 | [−1.152, +0.872] | [−1.147, +0.813] | 6.27 | 19 (10/9) | 0.977 | 1.0 | No | 0.04 | 15,761 |
| twin@0.4 − reject-all | −0.767 | [−1.968, +0.434] | [−2.007, +0.287] | 7.44 | 18 (10/8) | 0.474 | 1.0 | No | 0.10 | 740 |
| twin@0.6 − reject-all | −1.067 | [−2.167, +0.034] | [−2.260, −0.140] | 6.82 | 17 (11/6) | 0.117 | 0.583 | No | 0.32 | 322 |
| twin@0.8 − reject-all | **−1.707** | [−2.987, −0.426] | [−3.047, −0.567] | 7.94 | 18 (15/3) | **0.0036** | **0.025** | **Yes** | 0.87 | 170 |
| **twin@1.0 − reject-all** | **−2.320** | **[−3.762, −0.878]** | [−3.880, −1.020] | 8.94 | **22 (18/4)** | **0.00097** | **0.0078** | **Yes** | **0.96** | 117 |
| schedule-aware − reject-all | −2.587 | [−4.027, −1.146] | [−4.160, −1.287] | 8.93 | 22 (20/2) | 8.3 × 10⁻⁵ | 0.00083 | Yes | 0.99 | 94 |
| ungated − reject-all | +3.393 | [+1.338, +5.449] | [+1.473, +5.520] | 12.74 | 71 (14/57) | 1.2 × 10⁻⁴ | 0.0011 | Yes (ungated worse) | 0.98 | 111 |
| random − reject-all | +3.327 | [+1.366, +5.287] | [+1.367, +5.333] | 12.15 | 63 (13/50) | 6.8 × 10⁻⁵ | 0.00075 | Yes (random worse) | 0.98 | 105 |
| twin@1.0 − twin@0.0 | −3.293 | [−4.910, −1.677] | [−4.920, −1.833] | 10.02 | 37 (32/5) | 9.1 × 10⁻⁷ | 1.1 × 10⁻⁵ | Yes | 1.00 | 73 |
| schedule-aware − twin@1.0 | −0.267 | [−0.601, +0.068] | [−0.633, 0.000] | 2.07 | 3 (3/0) | 0.25 | 1.0 | **No** | 0.10 | 474 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

## Scripted controller — condition (i), discrete switch active

| Comparison | Mean | 95% t-CI | bootstrap CI | SD | non-zero (B/W) | exact Wilcoxon p | Holm p | **Significant at 150?** | Power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +15.013 | [+12.847, +17.179] | [+12.873, +17.147] | 13.42 | 128 (2/126) | 2.1 × 10⁻³⁷ | 1.9 × 10⁻³⁶ | Yes (twin worse) | 1.00 | 7 |
| twin@0.2 − reject-all | +11.280 | [+9.449, +13.111] | [+9.527, +13.153] | 11.35 | 122 (2/120) | 6.0 × 10⁻³⁶ | 4.8 × 10⁻³⁵ | Yes (twin worse) | 1.00 | 8 |
| twin@0.4 − reject-all | +6.313 | [+4.722, +7.905] | [+4.760, +7.907] | 9.86 | 84 (4/80) | 7.7 × 10⁻²² | 5.4 × 10⁻²¹ | Yes (twin worse) | 1.00 | 20 |
| twin@0.6 − reject-all | +2.367 | [+1.231, +3.502] | [+1.333, +3.580] | 7.04 | 42 (7/35) | 2.2 × 10⁻⁹ | 1.3 × 10⁻⁸ | Yes (twin worse) | 1.00 | 70 |
| twin@0.8 − reject-all | +0.560 | [+0.090, +1.030] | [+0.100, +1.047] | 2.91 | 23 (6/17) | 0.0097 | 0.049 | Yes (twin worse) | 0.72 | 213 |
| **twin@1.0 − reject-all** | **+0.233** | **[−0.156, +0.622]** | [−0.127, +0.620] | 2.41 | **17 (7/10)** | **0.287** | 0.574 | **No** | **0.21** | 839 |
| schedule-aware − reject-all | −0.213 | [−0.607, +0.180] | [−0.627, +0.173] | 2.44 | 18 (12/6) | 0.087 | 0.262 | **No** | 0.40 | 1,025 |
| ungated − reject-all | +25.060 | [+22.320, +27.800] | [+22.420, +27.820] | 16.98 | 149 (0/149) | 2.8 × 10⁻⁴⁵ | 3.4 × 10⁻⁴⁴ | Yes (ungated worse) | 1.00 | 4 |
| random − reject-all | +20.180 | [+17.441, +22.919] | [+17.627, +22.954] | 16.97 | 132 (3/129) | 2.9 × 10⁻³⁸ | 3.2 × 10⁻³⁷ | Yes (random worse) | 1.00 | 6 |
| twin@1.0 − twin@0.0 | −14.780 | [−16.988, −12.572] | [−17.000, −12.587] | 13.69 | 127 (125/2) | 1.9 × 10⁻³⁷ | 1.9 × 10⁻³⁶ | Yes | 1.00 | 7 |
| schedule-aware − twin@1.0 | −0.447 | [−0.888, −0.005] | [−0.927, −0.033] | 2.74 | 13 (10/3) | 0.046 | 0.185 | No after Holm | 0.48 | 295 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

## Scripted controller — condition (ii), queueing held constant

| Comparison | Mean | 95% t-CI | bootstrap CI | SD | non-zero (B/W) | exact Wilcoxon p | Holm p | **Significant at 150?** | Power | Seeds for 80% |
|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 − reject-all | +12.433 | [+10.550, +14.316] | [+10.593, +14.353] | 11.67 | 130 (4/126) | 1.5 × 10⁻³⁴ | 1.5 × 10⁻³³ | Yes (twin worse) | 1.00 | 7 |
| twin@0.2 − reject-all | +10.580 | [+8.623, +12.537] | [+8.707, +12.633] | 12.13 | 115 (3/112) | 2.2 × 10⁻³² | 1.8 × 10⁻³¹ | Yes (twin worse) | 1.00 | 11 |
| twin@0.4 − reject-all | +6.167 | [+4.592, +7.741] | [+4.667, +7.787] | 9.76 | 86 (7/79) | 4.1 × 10⁻²² | 2.9 × 10⁻²¹ | Yes (twin worse) | 1.00 | 20 |
| twin@0.6 − reject-all | +2.367 | [+1.231, +3.502] | [+1.333, +3.580] | 7.04 | 42 (7/35) | 2.2 × 10⁻⁹ | 1.3 × 10⁻⁸ | Yes (twin worse) | 1.00 | 70 |
| twin@0.8 − reject-all | +0.560 | [+0.090, +1.030] | [+0.100, +1.047] | 2.91 | 23 (6/17) | 0.0097 | 0.049 | Yes (twin worse) | 0.72 | 213 |
| **twin@1.0 − reject-all** | **+0.233** | **[−0.156, +0.622]** | [−0.127, +0.620] | 2.41 | **17 (7/10)** | **0.287** | 0.574 | **No** | **0.21** | 839 |
| schedule-aware − reject-all | −0.213 | [−0.607, +0.180] | [−0.627, +0.173] | 2.44 | 18 (12/6) | 0.087 | 0.262 | **No** | 0.40 | 1,025 |
| ungated − reject-all | +25.060 | [+22.320, +27.800] | [+22.420, +27.820] | 16.98 | 149 (0/149) | 2.8 × 10⁻⁴⁵ | 3.4 × 10⁻⁴⁴ | Yes (ungated worse) | 1.00 | 4 |
| random − reject-all | +20.180 | [+17.441, +22.919] | [+17.627, +22.954] | 16.97 | 132 (3/129) | 2.9 × 10⁻³⁸ | 3.2 × 10⁻³⁷ | Yes (random worse) | 1.00 | 6 |
| twin@1.0 − twin@0.0 | −12.200 | [−14.095, −10.305] | [−14.154, −10.387] | 11.74 | 128 (123/5) | 1.4 × 10⁻³³ | 1.3 × 10⁻³² | Yes | 1.00 | 8 |
| schedule-aware − twin@1.0 | −0.447 | [−0.888, −0.005] | [−0.927, −0.033] | 2.74 | 13 (10/3) | 0.046 | 0.185 | No after Holm | 0.48 | 295 |
| reject-all − A0 | 0.000 | [0, 0] | [0, 0] | 0 | 0 | 1.0 | 1.0 | Identical on all 150 seeds | — | — |

**What reaches significance at 150 seeds.** For the rule controller, twin@1.0 and twin@0.8
against reject-all, in both queueing conditions, plus the ungated/random/schedule-aware
comparisons. Nothing at 0.6 or below. For the scripted controller, every twin level up to 0.8 is
significantly *worse* than reject-all; twin@1.0 is not distinguishable from it and neither is
the schedule-aware gate.

**Where power is the limit.** Scripted twin@1.0 − reject-all has observed power 0.21 at 150
seeds and would need about 839 seeds at the observed SD. Rule twin@0.6 − reject-all has power
0.32 (322 seeds). Rule twin@0.4 − reject-all has power 0.13 (1,251 seeds). Condition (ii) makes
rule twin@0.2 a near-perfect null: mean −0.140, p = 0.977, power 0.04, 15,761 seeds.

---

# 5. Candidate composition per fidelity level

Each arm's own candidates, no-ops excluded. "Seeds with benefit" counts seeds (of 150)
containing at least one candidate with D < 0 on that arm's trajectory. Magnitudes are
median / p90 / max.

## Rule controller — condition (i), discrete switch active

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Avail. benefit | Avail. harm | Beneficial \|D\| | Harmful D | **Seeds w/ benefit** | Benefit by action |
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
| random | 205 | 76 | 34 | 95 (84) | 591 | 705 | 7 / 13 / 28 | 7 / 11 / 14 | 48 | scale 318, restart 153, migrate 120 |

Seeds with at least one beneficial candidate in *any* rule arm: **59 of 150**.

## Rule controller — condition (ii), queueing held constant

Only the three low-fidelity twin arms differ; every other row is identical to the table above.

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Avail. benefit | Avail. harm | Beneficial \|D\| | Harmful D | **Seeds w/ benefit** | Benefit by action |
|---|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 | 175 | 53 | 31 | 91 (88) | 500 | 745 | 8 / 18 / 29 | 8 / 12 / 20 | 35 | restart 189, scale 180, migrate 131 |
| twin@0.2 | 165 | 44 | 30 | 91 (88) | 439 | 752 | 8 / 25 / 30 | 8 / 13 / 19 | 29 | restart 201, migrate 135, scale 103 |
| twin@0.4 | 160 | 33 | 33 | 94 (90) | 357 | 749 | 8 / 25 / 29 | 8 / 12 / 14 | 26 | restart 189, migrate 99, scale 69 |

Seeds with at least one beneficial candidate in *any* rule arm: **59 of 150** (unchanged).

## Scripted controller — condition (i), discrete switch active

All scripted candidates are `scale_service`.

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Avail. benefit | Avail. harm | Beneficial \|D\| | Harmful D | **Seeds w/ benefit** |
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
| random | 1,296 | 386 | 176 | 734 (637) | 2,735 | 5,268 | 7 / 11 / 14 | 7 / 11 / 27 | 119 |

Seeds with at least one beneficial candidate in *any* scripted arm: **149 of 150**.

## Scripted controller — condition (ii), queueing held constant

Only the three low-fidelity twin arms differ.

| Arm | Candidates | Beneficial | Neutral | Harmful (D>3) | Avail. benefit | Avail. harm | Beneficial \|D\| | Harmful D | **Seeds w/ benefit** |
|---|---|---|---|---|---|---|---|---|---|
| twin@0.0 | 1,237 | 265 | 182 | 790 (688) | 1,706 | 5,566 | 7 / 11 / 14 | 7 / 11 / 27 | 119 |
| twin@0.2 | 1,231 | 210 | 180 | 841 (740) | 1,370 | 6,101 | 7 / 10 / 14 | 7 / 11 / 27 | 101 |
| twin@0.4 | 1,202 | 148 | 176 | 878 (795) | 883 | 6,592 | 6 / 10 / 14 | 8 / 11 / 27 | 77 |

Seeds with at least one beneficial candidate in *any* scripted arm: **149 of 150** (unchanged).

**Gating changes the candidate population, and it does so in both conditions.** For the scripted
controller, available benefit collapses from 1,770 ticks (116 seeds) at fidelity 0.0 to 154 ticks
(21 seeds) at 1.0. An accurate gate keeps the trajectory close to reject-all's, where scale-ups
are almost never useful (15 beneficial against 982 harmful). For the rule controller, migrations
and restarts are the high-value repairs: on the reject-all trajectory, 213 of the 439 available
ticks come from migrations and 201 from restarts, while scale-ups there are 6 beneficial against
86 harmful.

---

# τ / θ sensitivity (re-analysis only, no new runs)

## Method and its limits

τ is the twin's approval tolerance: an action is rejected when its **predicted** delta
(`twin_predicted_violation_ticks_action − ..._noop`) exceeds τ. θ is the ground-truth harm
threshold: an action is truly harmful when its **counterfactual** D exceeds θ. The corpus is
every non-no-op candidate recorded by each twin arm across the 150 seeds, with both quantities
attached.

**This is a relabelling of decisions that were actually taken at τ = 0, not a simulation of a
system running at τ > 0.** A larger τ would approve more actions, which would change the
trajectory and therefore the candidate set. The τ rows below answer "how would these recorded
decisions have been scored under a different tolerance", not "what would a τ = 2 gate achieve".
Trajectories are held fixed. The τ = 0 row is the only one that is both a relabelling and the
system as run: it reconstructs the recorded verdict on every candidate, 0 mismatches.

Two structural facts follow from the definitions and should be read before the tables:

- **benefit_preserved, harm_prevented and net Σ D depend on τ only.** θ does not appear in them.
  They are therefore constant across each τ row, and vary only down the τ column. This is not a
  bug in the table; it is what the metrics are.
- **recall, FPR and precision depend on both.** θ moves the ground-truth labels; τ moves the
  predictions.

**Definitional false positives.** A false positive is an action the twin rejected (predicted
delta > τ) that θ does not call harmful (D ≤ θ). Of those, the ones with **τ < D ≤ θ** are
actions that do cause real harm, above the twin's own tolerance, but fall below where θ happens
to sit. They are counted as errors by definition, not by prediction. On the **τ = θ diagonal
that band is empty**, so every false positive there is genuine predictive error — which is why
the diagonal is the least confounded place to read the classifier claim.

θ = 3 reproduces the experiment's own `ground_truth_harmful` label exactly (0 mismatches on
1,326 twin@1.0 candidates), so the θ = 3 column is the published labelling.

## Rule controller, fidelity 1.0 (identical in both queueing conditions)

Corpus: 159 candidates — 30 beneficial, 33 neutral, 96 with D > 0 (95 with D > 1, 92 with D > 3,
72 with D > 5). Predicted delta: min −30, median +6, p90 +11, max +18.

| τ | θ | TP/FN/FP/TN | recall [95% CI] | FPR [95% CI] | precision | benefit_preserved | harm_prevented | net Σ D/seed | FP definitional | FP predictive | def. share | FP with D<0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 93/2/3/61 | 0.979 [0.946, 1.000] | 0.047 [0.000, 0.106] | 0.969 | 0.970 | 0.991 | −2.120 | 1 | 2 | 0.333 | 2 |
| **0** | **3** | 91/1/5/62 | **0.989** [0.962, 1.000] | **0.075** [0.017, 0.148] | 0.948 | 0.970 | 0.991 | −2.120 | 3 | 2 | 0.600 | 2 |
| 0 | 5 | 72/0/24/63 | 1.000 [1.000, 1.000] | 0.276 [0.182, 0.382] | 0.750 | 0.970 | 0.991 | −2.120 | 22 | 2 | 0.917 | 2 |
| **1 =** | **1** | 93/2/3/61 | **0.979** [0.946, 1.000] | **0.047** [0.000, 0.106] | 0.969 | 0.970 | 0.991 | −2.120 | **0** | 3 | **0.000** | 2 |
| 1 | 3 | 91/1/5/62 | 0.989 [0.962, 1.000] | 0.075 [0.017, 0.148] | 0.948 | 0.970 | 0.991 | −2.120 | 2 | 3 | 0.400 | 2 |
| 1 | 5 | 72/0/24/63 | 1.000 [1.000, 1.000] | 0.276 [0.182, 0.382] | 0.750 | 0.970 | 0.991 | −2.120 | 21 | 3 | 0.875 | 2 |
| 2 | 1 | 93/2/3/61 | 0.979 [0.946, 1.000] | 0.047 [0.000, 0.106] | 0.969 | 0.970 | 0.991 | −2.120 | 0 | 3 | 0.000 | 2 |
| 2 | 3 | 91/1/5/62 | 0.989 [0.962, 1.000] | 0.075 [0.017, 0.148] | 0.948 | 0.970 | 0.991 | −2.120 | 1 | 4 | 0.200 | 2 |
| 2 | 5 | 72/0/24/63 | 1.000 [1.000, 1.000] | 0.276 [0.182, 0.382] | 0.750 | 0.970 | 0.991 | −2.120 | 20 | 4 | 0.833 | 2 |
| 3 | 1 | 92/3/3/61 | 0.968 [0.929, 1.000] | 0.047 [0.000, 0.106] | 0.968 | 0.970 | 0.987 | −2.100 | 0 | 3 | 0.000 | 2 |
| **3 =** | **3** | 91/1/4/63 | **0.989** [0.962, 1.000] | **0.060** [0.013, 0.125] | 0.958 | 0.970 | 0.987 | −2.100 | **0** | 4 | **0.000** | 2 |
| 3 | 5 | 72/0/23/64 | 1.000 [1.000, 1.000] | 0.264 [0.178, 0.357] | 0.758 | 0.970 | 0.987 | −2.100 | 19 | 4 | 0.826 | 2 |

The rule controller at fidelity 1.0 is insensitive to τ over {0,1,2,3}: its predicted deltas are
mostly far from the boundary, so raising the tolerance moves at most one decision. **On both
diagonal cells, recall is 0.979–0.989 and FPR is 0.047–0.060 with zero definitional false
positives.** The 3–4 remaining false positives are real predictive errors, and 2 of them are
rejections of genuinely beneficial actions (D < 0).

## Scripted controller, fidelity 1.0 (identical in both queueing conditions)

Corpus: 1,167 candidates — 29 beneficial, 167 neutral, 971 with D > 0 (958 with D > 1, 905 with
D > 3, 760 with D > 5). Predicted delta: min −14, median +7, p90 +11, max +15.

| τ | θ | TP/FN/FP/TN | recall [95% CI] | FPR [95% CI] | precision | benefit_preserved | harm_prevented | net Σ D/seed | FP definitional | FP predictive | def. share | FP with D<0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 948/10/20/189 | 0.990 [0.981, 0.997] | 0.096 [0.056, 0.144] | 0.979 | 0.877 | 0.994 | −0.620 | 9 | 11 | 0.450 | 6 |
| **0** | **3** | 901/4/67/195 | **0.996** [0.990, 0.999] | **0.256** [0.196, 0.323] | 0.931 | 0.877 | 0.994 | −0.620 | **56** | 11 | **0.836** | 6 |
| 0 | 5 | 758/2/210/197 | 0.997 [0.993, 1.000] | 0.516 [0.449, 0.592] | 0.783 | 0.877 | 0.994 | −0.620 | 199 | 11 | 0.948 | 6 |
| **1 =** | **1** | 948/10/13/196 | **0.990** [0.981, 0.997] | **0.062** [0.027, 0.107] | 0.986 | 0.877 | 0.994 | −0.580 | **0** | 13 | **0.000** | 6 |
| 1 | 3 | 901/4/60/202 | 0.996 [0.990, 0.999] | 0.229 [0.172, 0.296] | 0.938 | 0.877 | 0.994 | −0.580 | 47 | 13 | 0.783 | 6 |
| 1 | 5 | 758/2/203/204 | 0.997 [0.993, 1.000] | 0.499 [0.432, 0.574] | 0.789 | 0.877 | 0.994 | −0.580 | 190 | 13 | 0.936 | 6 |
| 2 | 1 | 938/20/13/196 | 0.979 [0.968, 0.989] | 0.062 [0.027, 0.107] | 0.986 | 0.877 | 0.991 | −0.447 | 0 | 13 | 0.000 | 6 |
| 2 | 3 | 901/4/50/212 | 0.996 [0.990, 0.999] | 0.191 [0.138, 0.252] | 0.947 | 0.877 | 0.991 | −0.447 | 34 | 16 | 0.680 | 6 |
| 2 | 5 | 758/2/193/214 | 0.997 [0.993, 1.000] | 0.474 [0.408, 0.547] | 0.797 | 0.877 | 0.991 | −0.447 | 177 | 16 | 0.917 | 6 |
| 3 | 1 | 906/52/12/197 | 0.946 [0.932, 0.960] | 0.057 [0.026, 0.097] | 0.987 | 0.896 | 0.978 | +0.173 | 0 | 12 | 0.000 | 5 |
| **3 =** | **3** | 901/4/17/245 | **0.996** [0.990, 0.999] | **0.065** [0.032, 0.105] | 0.981 | 0.896 | 0.978 | +0.173 | **0** | 17 | **0.000** | 5 |
| 3 | 5 | 758/2/160/247 | 0.997 [0.993, 1.000] | 0.393 [0.335, 0.457] | 0.826 | 0.896 | 0.978 | +0.173 | 143 | 17 | 0.894 | 5 |

**This is the clearest result in the τ/θ analysis.** At the published τ = 0, θ = 3 setting the
scripted twin's FPR reads 0.256. Of the 67 false positives, **56 (83.6%) are definitional** —
actions with 0 < D ≤ 3, which really do cause harm, just less than θ. Move to the τ = θ = 3
diagonal and recall is unchanged at 0.996 while FPR falls to **0.065**, with **zero**
definitional false positives. At τ = 0, θ = 5 the definitional share reaches 94.8% (199 of 210).

The apparent trade-off between recall and FPR across the θ columns is therefore mostly an
artifact of where θ sits relative to τ, not a property of the predictor.

One caveat on reading the diagonal as a free improvement: at τ = 3 net Σ D turns **positive**
(+0.173 [−0.340, +0.600]) because a tolerance of 3 admits harmful scale-ups. The diagonal is the
right place to read the *classifier* claim; it is not the right operating point for the *gate*.

## Lower fidelity: the same decomposition, larger predictive error

Scripted at fidelity 0.0, condition (ii) (1,237 candidates, 265 beneficial):

| τ | θ | recall | FPR | benefit_preserved | harm_prevented | net Σ D/seed | FP total | definitional | predictive | def. share |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 0.695 | 0.253 | 0.751 | 0.710 | +2.227 | 122 | 24 | 98 | 0.197 |
| 0 | 3 | 0.705 | 0.293 | 0.751 | 0.710 | +2.227 | 161 | 63 | 98 | 0.391 |
| 0 | 5 | 0.718 | 0.369 | 0.751 | 0.710 | +2.227 | 256 | 158 | 98 | 0.617 |
| **1 =** | **1** | 0.614 | 0.180 | 0.806 | 0.629 | +4.600 | 87 | **0** | 87 | **0.000** |
| **3 =** | **3** | 0.496 | 0.128 | 0.876 | 0.506 | +8.387 | 70 | **0** | 70 | **0.000** |

Rule at fidelity 0.4, condition (ii) (160 candidates, 33 beneficial):

| τ | θ | recall | FPR | benefit_preserved | harm_prevented | net Σ D/seed | FP total | definitional | predictive | def. share |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 0.925 | 0.224 | 0.482 | 0.928 | −0.787 | 15 | 1 | 14 | 0.067 |
| 0 | 3 | 0.933 | 0.243 | 0.482 | 0.928 | −0.787 | 17 | 3 | 14 | 0.176 |
| 0 | 5 | 0.930 | 0.393 | 0.482 | 0.928 | −0.787 | 35 | 21 | 14 | 0.600 |
| **1 =** | **1** | 0.871 | 0.194 | 0.599 | 0.874 | −0.800 | 13 | **0** | 13 | 0.000 |
| **3 =** | **3** | 0.756 | 0.157 | 0.711 | 0.777 | −0.580 | 11 | **0** | 11 | 0.000 |

The pattern is the same everywhere: the definitional share rises with θ − τ and vanishes on the
diagonal, but the *predictive* error count is roughly flat in θ and is what actually degrades
with fidelity. For the scripted controller it goes from 11–17 predictive false positives at
fidelity 1.0 to 50–98 at fidelity 0.0 (the five cells shown here span 70–98; the full grid reaches 50 at the least sensitive corner, tau = 3, theta = 1). Complete grids for all four condition × controller
combinations at all six fidelities are in the JSON under `tau_theta`.

---

# Specific checks requested

**1. Is rule-agent `benefit_preserved` non-monotonic?** The 3-point 10-seed data read
0.420 → 0.341 → 1.000 on 13–15 candidates. At 150 seeds, over six levels:

| Condition | f=0.0 | f=0.2 | f=0.4 | f=0.6 | f=0.8 | f=1.0 |
|---|---|---|---|---|---|---|
| (i) discrete switch | 0.409 | 0.431 | 0.373 | 0.424 | 0.609 | 0.970 |
| (ii) queueing held constant | 0.430 | 0.349 | 0.482 | 0.424 | 0.609 | 0.970 |

**Still non-monotone in point estimates, under both conditions, and reported unsmoothed.** But
the wobble is not stable: the dip sits at f = 0.4 as configured and at f = 0.2 with queueing held
constant, on the same seeds. Below f = 0.6 no adjacent change has a CI excluding zero in either
condition — the largest are −0.058 [−0.241, +0.098] in (i) and +0.133 [−0.039, +0.322] in (ii).
The only significant step in either condition is 0.8 → 1.0 (+0.361 [+0.103, +0.586]). The
defensible description is **a flat plateau around 0.35–0.48 up to fidelity 0.6, a rise at 0.8
that is not individually significant, and a significant jump to 0.97 at 1.0.** It should not be
drawn as a line through six points.

**2. Does the rule controller's episode advantage over reject-all reach significance at 150
seeds?** **Yes**, at fidelity 1.0 and 0.8, in both queueing conditions:

| Level | Mean | 95% t-CI | exact Wilcoxon p | Holm p | non-zero (B/W) | Power | Seeds for 80% |
|---|---|---|---|---|---|---|---|
| twin@1.0 | **−2.320** | [−3.762, −0.878] | **0.00097** | **0.0078** | 22 (18/4) | 0.96 | **117** |
| twin@0.8 | −1.707 | [−2.987, −0.426] | 0.0036 | 0.025 | 18 (15/3) | 0.87 | 170 |
| twin@0.6 | −1.067 | [−2.167, +0.034] | 0.117 | 0.557 | 17 (11/6) | 0.32 | 322 |

Analysis C predicted ~125 seeds would be needed; the normal formula at the observed values gives
**117**. That prediction was accurate. Note the effect is smaller than the 10-seed estimate
suggested: −2.32 against −4.4, with SD 8.94 against 13.9, and it is carried by 22 of 150 seeds.

**3. How many seeds contain at least one beneficial candidate?**

| Controller | Any arm | Reject-all trajectory | Twin@1.0 trajectory | Ungated trajectory |
|---|---|---|---|---|
| Rule | 59 / 150 (39%) | **23 / 150 (15%)** | 22 / 150 (15%) | 57 / 150 (38%) |
| Scripted | 149 / 150 (99%) | **12 / 150 (8%)** | 21 / 150 (14%) | 148 / 150 (99%) |

Identical in both queueing conditions. The reject-all column is the binding one, because it is
the benefit reject-all actually throws away and therefore the ceiling on what any gate can
recover: 23 seeds and 439 ticks for the rule controller, which the twin does recover; 12 seeds
and 52 ticks (0.35 per seed) for the scripted controller, too little for any gate to show
against reject-all at any practical seed count.

---

# Interpretation

1. **The crossover is a fidelity effect.** Holding the queueing model constant moves the
   scripted controller's net-Σ-D crossing from 0.555 to 0.549 and leaves the sign change intact
   at the same step. The discrete switch is worth at most 0.81 ticks per seed at f = 0.0 and
   0.16 at f = 0.4, against a curve spanning 2.8. The crossover can be reported as such, with
   the caveat that it was only established by running both conditions.
2. **Two curves should not be quoted as numbers.** The rule controller's net Σ D is flat between
   0.4 and 0.6 once queueing is held constant (+0.007 [−0.133, +0.193]), and its episode-level
   crossover moves from 0.32 to 0.17 between conditions. Both should be given as ranges.
3. **The twin's episode-level value is established for the rule controller** at fidelity ≥ 0.8
   against the no-prediction control, in both queueing conditions. The effect is small — about
   2 violation-ticks per 120-tick episode, roughly 2% of the 122-tick baseline — and lives in
   about 15% of episodes, the ones containing a restart or migration opportunity.
4. **For the scripted controller the null is structural.** With 0.35 ticks per seed of discarded
   benefit on the reject-all trajectory, detecting a gain would need hundreds to thousands of
   seeds, and the observed direction is the wrong way. A scale-or-hold proposer offers a gate
   nothing to preserve. This is unchanged by the queueing condition.
5. **Most of the reported false positive rate is a definitional artifact.** At τ = 0, θ = 3 —
   the published setting — 83.6% of the scripted controller's fidelity-1.0 false positives are
   actions with 0 < D ≤ 3. The classifier claim should be quoted on the τ = θ diagonal, where
   FPR is 0.065 (scripted) and 0.047–0.060 (rule) with recall 0.979–0.996 and no definitional
   contamination. The diagonal is not, however, a good gate operating point: at τ = 3 net Σ D
   turns positive.
6. **Local metrics remain more optimistic than episode outcomes.** The scripted crossing at
   ~0.55 has no episode-level counterpart in either condition — the episode curve approaches
   zero from above and never crosses. 20-tick counterfactuals with a no-op continuation do not
   capture how approved scale-ups change later states. Use net Σ D to describe the filter, and
   episode outcomes for performance claims.
7. **Fidelity is not uniformly valuable.** Harm prevention rises with fidelity at every step for
   both controllers in both conditions. Benefit preservation for the rule controller does not
   improve until above 0.6. For the scripted controller, local net gain stops improving above
   0.6. The useful operating region for the rule controller is 0.8–1.0.

---

# Files

- `scripts/gateway_uncertainty_ablation.py`: extended, not rewritten. Added
  `sweep --no-simplify-queueing`; two predicted-tick fields to the retained record set; a
  reusable `analyze_shards` split out of `sweep-analyze`; and an `extended-analyze` subcommand
  producing the side-by-side conditions, the paired condition deltas and the τ/θ grid. The
  `run`, `analyze`, `sweep` and `sweep-analyze` behaviours are unchanged when the new flag and
  subcommand are not used.
- `docs/research/extended_fidelity_sweep.json`: both conditions in full — every per-seed episode
  total, every metric with its CI, bootstrap crossing counts, adjacent-step changes, per-arm
  composition, per-arm paired condition deltas, and the complete 4 × 3 τ/θ grid for every
  controller, fidelity and condition.
- `paper/main.tex`: not touched.
- The 6,300 raw per-episode shard files are in the session scratchpad; they regenerate
  deterministically from the commands above.
