# Candidate composition: why a good classifier produces a null episode result

Date: 2026-09-16. No new sweeps were run. `paper/main.tex` was not modified.

## Question

At fidelity 1.0, the twin gate has 96.1% recall and 4.07% FPR as a local classifier for the
scripted agent, and 100% / 0.83% for the rule agent. Yet it cannot be distinguished from
reject-all at the episode level (`uncertainty_and_gateway_ablation.md`). The hypothesis tested
here is that beneficial candidate actions are rare, so preserving them yields little
episode-level benefit.

## Answer

**Yes, with one refinement.** What is rare is the *amount of benefit on the gated
trajectories*. The *fraction* of candidates that are beneficial is not especially low.

- **Rule agent.** 30% of its non-no-op candidates are beneficial, but it proposes almost
  nothing: 88% of its proposals are no-ops, about 1.5 candidates per episode. On the twin@1.0
  trajectory, the available benefit is **44 violation-ticks over 10 episodes, all in seed 6
  (3 candidates)**. The twin preserves **100%** of it and prevents **100%** of the harm. The
  gate is perfect; there is simply almost nothing for it to preserve. On the reject-all
  trajectory, reject-all threw away just **35 ticks, again all in seed 6**.
- **Scripted agent.** Beneficial candidates are rare *and* small. On the twin@1.0 trajectory,
  4 of 73 candidates are beneficial, worth **28 ticks** against **420 ticks** of harm (15:1).
  On the reject-all trajectory, only **2 ticks** of benefit existed in 10 episodes. The twin's
  93% benefit-preserved figure applies to a quantity too small to matter.
- **Arithmetic.** For the rule agent, it reproduces the observed result *exactly*: predicted
  per-seed advantage `[0,0,0,0,0,0,−44,0,0,0]`, identical to the observed values. Even a
  perfect gate on these trajectories has an expected advantage of **3.5–4.4 ticks per seed**
  against a paired SD of 13.9. That gives **about 17% power at 10 seeds**, needs **79–125
  seeds** by normal approximation, and **cannot reach significance under an exact rank test**
  because the whole effect sits in one seed.
- **Conclusion.** The null was guaranteed by the workload, not caused by the gate. For the
  scripted agent, the available benefit (0.2 ticks per seed on the reject-all trajectory) is
  below any detectable size at any practical seed count.
- **Caveat.** The per-decision arithmetic is not reliable in general. It gets the *sign* wrong
  for the scripted agent (predicted −13 on seed 2, observed +5), and underestimates the rule
  agent's effect 3–6× on the gateway-exposed seeds. Twenty-tick counterfactuals with a no-op
  continuation do not add up to closed-loop episode effects.

## Data

- **Source:** proposal records saved by the four ablation runs from the previous analysis
  (`scripts/gateway_uncertainty_ablation.py run`). Every proposal carries
  `counterfactual_harm_delta`, `twin_verdict`, `was_applied` and `retry_index`.
- **Scope:** 2 agents × 8 gate variants × 10 seeds × 4 conditions, 9,223 proposals, all with a
  delta D. The **published condition** (seeds 0–9, gateway immune) is the primary one; the
  other three are summarised at the end. The older proposal files in `results/*/proposals.jsonl`
  come from the pre-correction simulator and are reported separately, not pooled.
- **Definition of D:** V₂₀(action, then no-op) − V₂₀(no-op), the counterfactual difference in
  service-violation-ticks over a 20-tick horizon, both branches forked from the pre-decision
  state with the fault schedule visible. D < 0 is beneficial, D = 0 neutral, D > 0 harmful.
  The experiment's harm *label* uses D > 3; here the sign is used, as requested, and the
  D > 3 count is shown alongside.
- **Approved / rejected:** approved means `twin_verdict == True`, rejected means `False`. In
  every gated arm, approval and application coincide exactly. The ungated arm has no verdict,
  so all its candidates are treated as approved.
- **Script:** `scripts/candidate_composition.py`. Full output:
  `docs/research/candidate_composition_stats.json`.

### An important caveat about pooling

Arms share a trajectory until their gates first disagree, so the same decision is recorded once
per arm. Seed 6's `restart_service svc0` at t60 appears in all 8 rule-agent arms. **Pooled
counts overstate the number of distinct candidates.** Tables therefore show both pooled counts
and counts de-duplicated on (seed, tick, retry, action, D). Analyses B and C use each arm's own
records, which contain no duplication.

---

# Analysis A: Candidate composition

## No-op proposals, excluded from everything below

| Condition | Agent | Proposals | No-op | Non-no-op candidates | No-op share |
|---|---|---|---|---|---|
| Published (seeds 0–9, gateway immune) | Rule | 1,014 | 895 | 119 | 88.3% |
| | Scripted | 1,281 | 671 | 610 | 52.4% |
| | **Both** | **2,295** | **1,566** | **729** | 68.2% |

The rule agent proposes a non-no-op action about 1.5 times per episode per arm (119 over 8 arms
× 10 seeds); the scripted agent about 7.6 times.

## Distribution of D, by agent and action type (published condition, pooled over all arms)

| Agent | Action | Candidates | Beneficial (D<0) | Neutral (D=0) | Harmful (D>0) | …of which D>3 | Available benefit Σ\|D\|, D<0 | Available harm Σ D, D>0 | Harm : benefit |
|---|---|---|---|---|---|---|---|---|---|
| Rule | restart_service | 8 | 8 (100%) | 0 | 0 | 0 | 202 | 0 | 0 |
| Rule | scale_service | 111 | 28 (25%) | 16 | 67 (60%) | 57 | 171 | 467 | 2.7 |
| **Rule** | **all** | **119** | **36 (30%)** | 16 (13%) | **67 (56%)** | 57 | **373** | **467** | **1.3** |
| Scripted | scale_service (only action) | 610 | 106 (17%) | 119 (20%) | 385 (63%) | 333 | 721 | 2,676 | 3.7 |
| **Both** | **all** | **729** | **142 (19%)** | 135 (19%) | **452 (62%)** | 390 | **1,094** | **3,143** | **2.9** |

No migrations, reroutes or throttles occur in the published condition.

**De-duplicated** on (seed, tick, retry, action, D):

| Agent | Distinct candidates | Beneficial | Neutral | Harmful | Available benefit | Available harm | Seeds containing any beneficial candidate |
|---|---|---|---|---|---|---|---|
| Rule | 26 | 9 | 2 | 15 | **90** | 101 | 4, 6, 7, 9 |
| Scripted | 174 | 61 | 19 | 94 | **431** | 565 | all 10 |

The 8 pooled restarts are **one decision** (seed 6, t60, D = −29, or −19 on trajectories where
earlier decisions differed), recorded in 8 arms. That single restart is 54% of the rule
agent's pooled available benefit.

## Magnitude distribution of D (published condition, pooled)

| Agent | ≤ −20 | −19..−10 | −9..−4 | −3..−1 | 0 | 1..3 | 4..9 | 10..19 | ≥ 20 |
|---|---|---|---|---|---|---|---|---|---|
| Rule | 5 | 3 | 22 | 6 | 16 | 10 | 37 | 20 | 0 |
| Scripted | 0 | 20 | 63 | 23 | 119 | 52 | 252 | 81 | 0 |

| Agent | Beneficial \|D\|: min / median / p90 / max | Harmful D: min / median / p90 / max |
|---|---|---|
| Rule, restart | 19 / 29 / 29 / 29 | — |
| Rule, scale | 1 / 6 / 9 / 9 | 1 / 8 / 10 / 10 |
| Scripted, scale | 1 / 8 / 10 / 14 | 1 / 7 / 11 / 12 |

Scale-ups are symmetric in size: beneficial and harmful ones have similar magnitudes (median
6–8 ticks), but harmful ones are 2.4–3.6× more numerous. The only large single benefits in the
published data are the memory-leak restarts.

## What each gated trajectory actually offered (published condition, per arm, no duplication)

| Agent | Arm | Proposals | No-op | Candidates | Beneficial / neutral / harmful | Available benefit | Available harm | Seeds with any benefit |
|---|---|---|---|---|---|---|---|---|
| Rule | ungated | 120 | 104 | 16 | 7 / 2 / 7 | 52 | 40 | 4, 6, 7, 9 |
| Rule | reject-all | 133 | 120 | 13 | 2 / 2 / 9 | **35** | 66 | **6** |
| Rule | twin@0.0 | 127 | 113 | 14 | 4 / 2 / 8 | 50 | 63 | 6, 9 |
| Rule | twin@0.6 | 130 | 116 | 14 | 3 / 2 / 9 | 44 | 66 | 6 |
| Rule | **twin@1.0** | 130 | 115 | 15 | **3** / 2 / 10 | **44** | 76 | **6** |
| Rule | schedule-aware | 130 | 115 | 15 | 3 / 2 / 10 | 44 | 76 | 6 |
| Scripted | ungated | 120 | 38 | 82 | 26 / 15 / 41 | 177 | 218 | all 10 |
| Scripted | reject-all | 192 | 120 | 72 | 1 / 15 / 56 | **2** | 430 | **4** |
| Scripted | twin@0.0 | 157 | 81 | 76 | 17 / 15 / 44 | 100 | 316 | 0, 1, 2, 4, 5, 8, 9 |
| Scripted | twin@0.6 | 172 | 100 | 72 | 4 / 14 / 54 | 22 | 412 | 0, 2, 4 |
| Scripted | **twin@1.0** | 174 | 101 | 73 | **4** / 14 / 55 | **28** | 420 | **2, 4, 6** |
| Scripted | schedule-aware | 172 | 100 | 72 | 4 / 16 / 52 | 18 | 387 | 2, 4 |

For the scripted agent, gating also *changes the candidate population*. Ungated, it faces 26
beneficial candidates. Behind a gate, only 1–4 remain. Its retries after a rejection are
always no-ops: none of its 610 candidates is a retry. The shift instead comes from later
decision points. Once scale-ups are blocked, the same services stay in violation and the agent
proposes scale-ups again from those persistently degraded states, where scaling is rarely
beneficial. The extra proposals in gated arms (up to 192 versus 120) are those no-op retries.

---

# Analysis B: Benefit preserved versus harm prevented

- benefit_preserved = Σ max(−D, 0) over approved candidates ÷ Σ max(−D, 0) over all candidates.
- harm_prevented = Σ max(D, 0) over rejected candidates ÷ Σ max(D, 0) over all candidates.
- Each arm is scored on its own candidates; no-ops are excluded.
- **"n/e" means not estimable (zero denominator), not a perfect score.**

## Published condition (seeds 0–9, gateway immune)

The ablation recorded fidelity levels 0.0, 0.6 and 1.0 only.

| Agent | Gate | Candidates | Approved / rejected | Benefit approved / available | **benefit_preserved** | Harm rejected / available | **harm_prevented** | Beneficial approved | Harmful rejected | Net Σ D over approved |
|---|---|---|---|---|---|---|---|---|---|---|
| Rule | **reject-all (reference)** | 13 | 0 / 13 | 0 / 35 | **0.000** | 66 / 66 | **1.000** | 0 of 2 | 9 of 9 | 0 |
| Rule | twin@0.0 | 14 | 7 / 7 | 21 / 50 | 0.420 | 45 / 63 | 0.714 | 3 of 4 | 6 of 8 | −3 |
| Rule | twin@0.6 | 14 | 4 / 10 | 15 / 44 | 0.341 | 66 / 66 | 1.000 | 2 of 3 | 9 of 9 | −15 |
| Rule | **twin@1.0** | 15 | 5 / 10 | 44 / 44 | **1.000** | 76 / 76 | **1.000** | 3 of 3 | 10 of 10 | **−44** |
| Rule | schedule-aware | 15 | 5 / 10 | 44 / 44 | 1.000 | 76 / 76 | 1.000 | 3 of 3 | 10 of 10 | −44 |
| Rule | random p=0.3 | 16 | 12 / 4 | 46 / 52 | 0.885 | 2 / 40 | 0.050 | 6 of 7 | 1 of 7 | −8 |
| Rule | ungated (reference) | 16 | 16 / 0 | 52 / 52 | 1.000 | 0 / 40 | 0.000 | 7 of 7 | 0 of 7 | −12 |
| Scripted | **reject-all (reference)** | 72 | 0 / 72 | 0 / 2 | **0.000** | 430 / 430 | **1.000** | 0 of 1 | 56 of 56 | 0 |
| Scripted | twin@0.0 | 76 | 39 / 37 | 60 / 100 | 0.600 | 218 / 316 | 0.690 | 11 of 17 | 29 of 44 | +38 |
| Scripted | twin@0.6 | 72 | 20 / 52 | 20 / 22 | 0.909 | 396 / 412 | 0.961 | 3 of 4 | 50 of 54 | −4 |
| Scripted | **twin@1.0** | 73 | 19 / 54 | 26 / 28 | **0.929** | 410 / 420 | **0.976** | 3 of 4 | 52 of 55 | **−16** |
| Scripted | schedule-aware | 72 | 20 / 52 | 18 / 18 | 1.000 | 387 / 387 | 1.000 | 4 of 4 | 52 of 52 | −18 |
| Scripted | random p=0.747 | 81 | 27 / 54 | 57 / 197 | 0.289 | 175 / 275 | 0.636 | 7 of 24 | 27 of 42 | +43 |
| Scripted | ungated (reference) | 82 | 82 / 0 | 177 / 177 | 1.000 | 0 / 218 | 0.000 | 26 of 26 | 0 of 41 | +41 |

Restricting to first attempts (no retries) leaves every twin and reject-all ratio unchanged,
except that denominators drop by the harmful retries (for example, rule twin@1.0 harm becomes
66/66 and reject-all 56/56).

**How to read it against the reference.** Reject-all sets the floor: harm_prevented 1.0,
benefit_preserved 0.0.
- **Rule agent at fidelity 1.0:** the twin matches reject-all on harm (1.000) *and* keeps all
  of the benefit (1.000). As a gate it is strictly better than reject-all, and identical to the
  schedule-aware gate. **The advantage is worth exactly the benefit available on its trajectory:
  44 ticks over 10 episodes.**
- **Scripted agent at fidelity 1.0:** the twin gives up 2.4% of harm prevention (10 ticks
  leaked) to keep 92.9% of the benefit (26 ticks). Net expected gain over reject-all is
  26 − 10 = **16 ticks over 10 episodes**.
- **Lower fidelity:** at twin@0.0 the scripted agent's leaked harm (98 ticks) exceeds its
  preserved benefit (60), so net Σ D is +38 and the gate is expected to do worse than
  reject-all. Rule twin@0.6 keeps only 34% of the benefit (it blocked seed 6's 29-tick restart)
  while matching reject-all on harm.

## The same metrics in the other three conditions (fidelity 1.0 and reject-all)

| Condition | Agent | Gate | benefit_preserved (approved / available) | harm_prevented (rejected / available) |
|---|---|---|---|---|
| Seeds 0–9, gateway-faultable flag on | Rule | reject-all | 0.000 (0/29) | 1.000 (62/62) |
| | Rule | twin@1.0 | 1.000 (29/29) | 1.000 (72/72) |
| | Scripted | reject-all | 0.000 (0/2) | 1.000 (444/444) |
| | Scripted | twin@1.0 | 0.905 (19/21) | 0.986 (428/434) |
| Gateway-exposed seeds, gateway immune | Rule | reject-all | 0.000 (0/48) | 1.000 (72/72) |
| | Rule | twin@1.0 | 1.000 (7/7) | 1.000 (76/76) |
| | Scripted | reject-all | **n/e (0/0)**: no beneficial candidate existed | 1.000 (416/416) |
| | Scripted | twin@1.0 | 1.000 (22/22) | 0.970 (385/397) |
| | Scripted | schedule-aware | **n/e (0/0)** | 1.000 (412/412) |
| Gateway-exposed seeds, gateway faultable | Rule | reject-all | 0.000 (0/32) | 1.000 (66/66) |
| | Rule | twin@1.0 | 1.000 (8/8) | 1.000 (53/53) |
| | Scripted | reject-all | 0.000 (0/1) | 1.000 (421/421) |
| | Scripted | twin@1.0 | 1.000 (11/11) | 0.997 (373/374) |

The gateway-exposed conditions also used fidelity 0.0 and 0.6; those values are in the JSON.
The pattern holds everywhere: **at fidelity 1.0 the twin is a near-perfect gate on its own
candidates, and the benefit it preserves is 7–44 ticks per 10-episode condition.**

## Older proposal files (`results/*/proposals.jsonl`, pre-correction simulator; reported separately)

- 2,123 proposals, of which 1,957 are no-ops and 166 are candidates.
- **Composition:**
  - scripted/LLM arms: 130 candidates, 30 beneficial / 13 neutral / 87 harmful, 231 ticks of
    benefit against 679 of harm;
  - rule arms: 36 candidates, 13 / 5 / 18, 118 against 101.
- **Gates:**
  - A3 twin@1.0: benefit_preserved 1.000 (19/19), harm_prevented 0.980 (287/293);
  - A3 twin@0.4: 0.743 (52/70) and 0.749 (131/175);
  - A4 rule twin@0.6: benefit_preserved **n/e (0/0)**, harm_prevented 0.625 (10/16);
  - A3 at fidelities 0.0, 0.25, 0.5 and 0.75: **n/e on both metrics**, because those runs
    recorded no non-no-op candidates at all.

The same picture holds on the older simulator: very little benefit reaches a gate.

---

# Analysis C: Does the arithmetic explain the null?

## Method

Reject-all is equivalent to applying a no-op at every decision (it matches A0 on every seed).
The twin's expected per-seed advantage over reject-all is therefore the sum of D over the
candidates the twin approved on its own trajectory: preserved benefit minus leaked harm.
Negative means the twin is better.

Two ceilings bound what *any* gate could gain:
- **(a) twin-trajectory ceiling:** all benefit on the twin@1.0 trajectory preserved;
- **(b) reject-trajectory ceiling:** the benefit reject-all actually discarded.

Detectability uses a paired normal approximation (α = 0.05 two-sided, 80% power). Because every
per-seed vector here has at most three non-zero seeds, **no comparison can reach p < 0.05 under
the exact Wilcoxon test at 10 seeds, whatever the magnitude.**

## Published condition

| | Rule agent | Scripted agent |
|---|---|---|
| Benefit available on twin@1.0 trajectory | 44 (seed 6 only) | 28 (seeds 2, 4, 6) |
| benefit_preserved × available | 1.000 × 44 = 44 | 0.929 × 28 = 26 |
| Harm leaked (approved D > 0) | 0 | 10 |
| **Expected advantage, per seed** | `[0,0,0,0,0,0,−44,0,0,0]` | `[0,0,−13,0,0,0,−3,0,0,0]` |
| Expected mean / SD | **−4.4** / 13.9 | **−1.6** / 4.1 |
| **Observed twin@1.0 − reject-all, per seed** | `[0,0,0,0,0,0,−44,0,0,0]` | `[0,0,+5,0,0,0,0,0,0,0]` |
| Observed mean / SD | **−4.4** / 13.9 | **+0.5** / 1.6 |
| Agreement | **Exact** (r = 1.00) | **Wrong sign** (r = −0.97) |
| Ceiling (a), twin trajectory | −4.4 per seed | −2.8 per seed |
| Ceiling (b), benefit reject-all discarded | **−3.5 per seed** (35 ticks, seed 6) | **−0.2 per seed** (2 ticks, seed 4) |
| Power at 10 seeds, expected effect, observed SD | 0.17 | 0.89 on the tiny observed SD; 0.23 on the expected SD |
| Seeds for 80% power, expected effect | 79 | 52 (expected SD) |
| Seeds for 80% power, ceiling (b), observed SD | 125 | 491 |
| Seeds for 80% power, ceiling (b), its own SD | 79 | 79 |

The scripted agent's 0.89 is not meaningful: the expected effect points the opposite way to the
observed one.

## Verdict per agent

**Rule agent.** The arithmetic explains the null completely.
- The twin preserved all available benefit and prevented all available harm, and the predicted
  per-seed advantage equals the observed one.
- The problem is magnitude and concentration: 44 ticks in one episode out of ten, about 4.4
  ticks per seed against an SD of 13.9.
- Even a perfect gate captures at most what reject-all discarded, 3.5 ticks per seed.
- **At 10 seeds that is undetectable:** about 17% power under a normal approximation, and
  impossible under an exact rank test because only one seed differs. Roughly 80–125 seeds would
  be needed *if* 1 episode in 10 contains such a decision, and that rate rests on a single
  observed event.

**Scripted agent.** The arithmetic explains why no advantage could appear, but does not predict
the observed values.
- Reject-all discarded 2 ticks of benefit in 10 episodes, so **no gate can gain more than
  0.2 ticks per seed** over reject-all on that trajectory. That is below any practical
  detection threshold (491 seeds at the observed SD).
- The expected advantage on the twin's own trajectory (−1.6) has the **wrong sign**
  (observed +0.5).
- On seed 2, the twin approved four scale-ups with D = +2, +4, −13 and −6 (net −13), but the
  episode ended 5 ticks *worse* than reject-all. Twenty-tick counterfactuals with a no-op
  continuation missed later consequences.

## The other conditions: where the arithmetic breaks down

| Condition | Agent | Expected per seed (Σ D approved) | Observed per seed | Ratio | Ceiling (b) per seed |
|---|---|---|---|---|---|
| Seeds 0–9, flag on | Rule | −29 on seed 6 → −2.9 mean | −41 on seed 6 → −4.1 mean | 1.4× | −2.9 |
| | Scripted | −13 on seed 2 → −1.3 mean | +5 on seed 2 → +0.5 mean | wrong sign | −0.2 |
| Gateway-exposed, gateway immune | Rule | −7 on seed 65 → −0.7 mean | −23 on seed 65 → −2.3 mean | 3.3× | −4.8 |
| | Scripted | −9 and −1 → −1.0 mean | +7 on seed 54 → +0.7 mean | wrong sign | 0.0 (nothing available) |
| Gateway-exposed, gateway faultable | Rule | −8 on seed 65 → −0.8 mean | −48 on seed 65 → −4.8 mean | 6.0× | −3.2 |
| | Scripted | −6, −1, −3 → −1.0 mean | +20 and +13 → +3.3 mean | wrong sign | −0.1 |

- **Rule agent:** the sign and the deciding seed are always predicted correctly. But when
  migrations are involved (gateway-exposed seeds), the episode effect is **3–6× larger** than
  the sum of 20-tick counterfactuals. A migration's downtime is paid inside the horizon, while
  its benefit keeps accruing for the rest of the fault.
- **Scripted agent:** the arithmetic predicts a small twin advantage in every condition, and the
  observed direction is a small twin *disadvantage* in every condition. Approved scale-ups that
  look beneficial locally do not pay off over the episode.

In every case, the effect is carried by 1–3 seeds and the expected magnitude, even at its
ceiling, is 0–4.8 ticks per seed.

## Overall conclusion

1. **The hypothesis is confirmed, stated precisely.** Within these experiments, the twin gate
   is close to a perfect filter on the candidates it sees. For the rule agent at fidelity 1.0,
   benefit_preserved and harm_prevented are both 1.000; for the scripted agent, 0.929 and 0.976.
   But the benefit available on the gated trajectories is tiny and concentrated:
   - rule: 44 ticks in one episode of ten, and reject-all discarded only 35;
   - scripted: 28 ticks against 420 of harm, and reject-all discarded only 2.

   A gate cannot beat reject-all by more than reject-all throws away.
2. **The local classifier result and the episode null are consistent.** High recall is earned
   on harmful candidates, which reject-all also blocks. Low FPR protects beneficial candidates,
   which barely exist. The classifier metrics measure the quality of the filter. The episode
   metric measures filter quality × available benefit, and available benefit is close to zero.
3. **At 10 seeds, even a perfect gate was not detectable.** For the rule agent: 3.5–4.4 ticks
   per seed, SD about 14, power about 0.17, one non-zero seed. For the scripted agent: at most
   0.2 ticks per seed. The null is a property of the workload and agents, not evidence that the
   twin's predictions are worthless.
4. **Per-decision counterfactuals are a poor substitute for closed-loop measurement.** They
   match the rule agent's published result exactly, but get the scripted agent's sign wrong in
   all four conditions, and underestimate migration benefits 3–6×. Use benefit_preserved and
   harm_prevented to describe the filter; do not convert them into episode claims.
5. **Implication for design.** A test that can separate a predictive gate from reject-all needs
   trajectories where reject-all discards substantial benefit in most episodes: tens of ticks
   per episode, not tens per ten episodes. That requires a proposer that regularly offers real
   repairs (restarts for leaks, migrations for sustained node faults), not more seeds of the
   current agents.
