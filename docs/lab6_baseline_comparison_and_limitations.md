# Base-Paper Comparison and Limitations Analysis

**Project:** Twin-in-the-Loop — an agent proposes network repairs; a deliberately imperfect
digital twin vets them before they are applied.
**Purpose of this report:** to answer two specific criticisms —
1. *"Compare the project against a base paper."*
2. *"How can all test cases pass? Then there is no room for improvement — show the limitations."*

**Companion document:** [`docs/lab6_report.md`](lab6_report.md) — the functional test report.
**Its test cases are unchanged and remain valid.** This report does not revise them; it adds a
second suite designed on the opposite principle, and a literature comparison.
**Date of runs:** 2026-09-09.

> **⚠ NUMBERS SUPERSEDED (2026-09-16).** The Priority-1 simulator corrections (`docs/research/audit_fix1.md`)
> changed SLO semantics (zero-completion ticks are now non-compliant), fault composition, restart/migration
> downtime, and memory-pressure effects. The A0 do-nothing baseline moved from **130.5 → 136.3** mean
> violation-ticks (seed 6: 139 → 195). **Every violation-tick figure in this document predates those fixes
> and must be re-measured before publication.** Structural conclusions (fault-boundary blindness, the
> reject-all ablation, horizon trade-offs) predate those fixes. **The ablation HAS been re-run — see §3A,
> where the rule-agent result flips in the project's favour and the stub-agent result does not.**

---

## 0. Status of the previously selected base paper

The PDF selected earlier is **not available to me** — it is not attached to the current
request, and no copy exists in the repository (`paper/` is empty, and no file, citation, or
note anywhere in the repo names a base paper). I therefore could not compare against it
directly.

Rather than block, I searched the literature independently and identified candidates that
**do** have data we can compare against — which was the actual problem with the original
choice. Every paper cited below has been **verified to exist** by retrieving it; none is
recalled from memory. If you send the original PDF, §2 can be re-pointed at it in about an
hour, since the comparison axes are already defined.

> **Why the original choice failed.** The complaint that "there is no data to compare against"
> is the normal symptom of picking a *framework/architecture* paper as a base paper. Those
> papers report a case study, not a benchmark. A usable base paper must report metrics on the
> same axes you can measure. §2 selects on exactly that criterion.

---

## 1. Candidate base papers found

| # | Work | Venue / ID | Domain | Has comparable data? | Role for us |
|---|---|---|---|---|---|
| **1** | **Aether: Network Validation Using Agentic AI and Digital Twin** | [arXiv:2604.18233](https://arxiv.org/abs/2604.18233) | Network ops | **Yes — detection, precision, coverage, time** | **Recommended base paper** |
| 2 | Leveraging LLM Agents and Digital Twins for Fault Handling in Process Plants | [arXiv:2505.02076](https://arxiv.org/abs/2505.02076) (IEEE) | Process plants | No — single qualitative case study | Closest architecture; cite, don't benchmark |
| 3 | SLBench / SLGuard — how LLM agents follow logical relations in skills | [arXiv:2607.09016](https://arxiv.org/html/2607.09016) | Agent safety | **Yes — unsafe rates, guard reduction** | Guard-effectiveness comparator |
| 4 | MicroRemed: Benchmarking LLMs in Microservices Remediation | [arXiv:2511.01166](https://arxiv.org/abs/2511.01166) | Microservices | Yes (benchmark) | Establishes task difficulty |
| 5 | NIKA: A Network Arena for Benchmarking AI Agents on Network Troubleshooting | [arXiv:2512.16381](https://arxiv.org/pdf/2512.16381) | Network ops | Yes (benchmark) | Testbed-realism comparator |
| 6 | NetInjectBench — indirect prompt injection in network-ops agents | [arXiv:2607.10490](https://arxiv.org/html/2607.10490v1) | Network ops safety | Yes — 82.50 % unsafe tool-action rate | Threat-model comparator |
| 7 | Investigating the influence of fidelity on the capability of a digital twin to detect material extrusion failures | [J. Intell. Manuf. (2023)](https://link.springer.com/article/10.1007/s10845-023-02144-x) | Additive mfg. | Yes — fidelity vs detection | **Direct precedent for our fidelity sweep** |
| 8 | Model-predictive shielding / safe-RL action filtering | survey [arXiv:2505.17342](https://arxiv.org/pdf/2505.17342) | Safe RL | Yes | Classical precedent for the gate |

### Recommendation

**Use Aether (arXiv:2604.18233) as the base paper.** It is the only candidate that is
simultaneously (a) in our domain, (b) built on the same core mechanism — a digital twin that
validates proposed network changes before they are applied — and (c) reports quantitative
results on axes we already measure. Critically, it also has **two explicit gaps that our work
fills**, which is what makes a base paper useful rather than merely adjacent.

---

## 2. Head-to-head: Aether vs this project

### 2.1 What Aether does

Five specialised network-operations agents plus a unified Network Digital Twin (modelling,
simulation, emulation) automate network *change validation*. Evaluated on 88 synthetic change
scenarios across 8 change classes, plus two real incidents from a major ISP on a 25-router
network (CORE/Aggregation/Metro, 277 IPv4/IPv6 addresses, 263 VRFs over 613 instances, 76 ACLs
with 274 rules — 30 000+ lines of production-equivalent Cisco IOS-XR configuration).

### 2.2 Comparison table

| Axis | Aether (base paper) | This project | Assessment |
|---|---|---|---|
| **Domain** | Production ISP network | Simulated 17-node edge network | **They win decisively.** Real vendor configs vs our synthetic simulator |
| **Testbed realism** | Real configs + emulation (Batfish, Cisco NSO) | Deterministic discrete-tick simulator | **They win** |
| **Scale** | 25 routers, 30 000+ config lines | 1 gateway, 4 edge, 12 devices, 6 services, 16 links | **They win** |
| **Scenarios** | 88 synthetic + 2 real ISP incidents, 10 runs each | 10 seeds × 120 ticks × 8 arms, 15 faults/episode | Comparable volume, lower realism |
| **What is validated** | Human-authored *network changes* for correctness | *Agent-proposed repairs* for net SLO harm | Different question — ours is the harder autonomy setting |
| **Error detection** | **94 %** synthetic; **100 %** on both ISP incidents | **98.0 %** recall on harmful actions (stub agent, fidelity ≥ 0.8); **100 %** (rule agent) | Comparable |
| **Precision** | **0.64** synthetic; **0.57 / 0.90** on ISP cases | **89.3 %** (stub, fidelity 1.0); **93.5 %** (fidelity 0.4) | **We win** — but see §4.2, our labels are synthetic |
| **Validation time** | **223 s** synthetic; **395–414 s** real | **81 ms** per gated decision | **We win by ~3 orders of magnitude** — but they validate a whole change against 30 000 config lines; not like-for-like |
| **Twin fidelity studied?** | **No** — fidelity never varied | **Yes** — swept 0.0 → 1.0, 6 levels | **We win — genuine contribution** |
| **Are rejected changes' true effects measured?** | **No** — TPs are "faulty changes the system blocks"; no validation of what they'd have done in production | **Yes** — every proposal, including rejected ones, is replayed by forking the simulator to obtain an exact counterfactual harm label | **We win — the core contribution** |
| **Baseline comparison** | **None** — "lack of publicly available datasets makes comparison with NetCov/RELA not possible"; human expertise used as reference | A0 null / A1 rule / A2 ungated agent / A3–A4 gated, paired per seed | **We win** |
| **Cost of being wrong** | Not quantified | Quantified in SLO violation-ticks | **We win** |
| **Statistical treatment** | 10 runs per scenario | 10 seeds, paired, per-seed deltas reported | Comparable, both weak |

### 2.3 The honest summary of this comparison

**We lose on everything that makes a system credible, and win on everything that makes a
measurement credible.**

Aether is evaluated on a real ISP network with real vendor configurations and real production
incidents. We are evaluated on a synthetic simulator. No amount of methodological elegance
compensates for that, and a reviewer will say so.

What we have that Aether does not is a **ground-truth labelling method**. Aether counts a true
positive when its system blocks a change that was seeded as faulty — but it never establishes
what that change would actually have done to the production network, because it cannot: you
cannot deploy a known-bad change to an ISP to see how bad it was. We can, because our network
is synthetic and forkable. That is the one place where being simulated is an *advantage*
rather than an apology, and it is the axis the paper should be written on.

The same gap appears in every guard/gate paper checked. SLBench's SLGuard reduces violations
from 11 cases to 4 (**63 % reduction**), but grades whether agents *completed* unsafe actions —
it does not measure what a blocked action would have done. This is a **systematic blind spot in
the guardrail literature**, not an oversight specific to Aether.

### 2.4 Quantitative comparison of guard effectiveness

| Work | Ungated harm rate | Gated harm rate | Reduction | Label source |
|---|---|---|---|---|
| SLBench / SLGuard | 11 violation cases | 4 cases | **63 %** | Execution artefacts + human audit (3 annotators) |
| NetInjectBench (naive execution) | **82.50 %** unsafe tool-action rate | — | — | Attack-scenario labels |
| Aether | — | error detection 94–100 % | — | Seeded faulty changes (effect not verified) |
| **This project (A2 → A3, stub agent)** | **154.9** violation-ticks | **130.7** | **15.6 %** of violation-ticks; **98.0 %** of harmful actions blocked | **Counterfactual simulator fork (exact)** |
| **This project (A1 → A4, rule agent)** | **138.1** | **130.2** | **100 %** of harmful actions blocked (8/8) | **Counterfactual simulator fork (exact)** |

---

## 3. Limitations suite — tests designed to fail

The functional suite in `lab6_report.md` passes 8/8 because it tests **specified behaviour**:
does the schema reject malformed input, does the queue stay bounded, does a crashed node get
detected. Those are the right tests for that question, and they were correctly criticised for
being the *only* tests present.

This suite asks the opposite question: **where does the system stop working?** It is designed
so that passing is not the expected outcome. Harness:
`scratchpad/lab6_limits.py`, 10 seeds, stub-LLM agent (chosen because it proposes aggressively
and therefore gives the gate work to do).

### 3.1 Results

**9 of 10 fail or partially fail**, including the one that matters most (LT-10).

| ID | Property that *should* hold | Result | Verdict |
|---|---|---|---|
| LT-1 | The gate should beat no-gate at every fidelity | Holds at all six levels (−14.4 to −24.2 ticks) | **PASS** (but see LT-10 — this is bad news) |
| **LT-10** | **The twin's *predictions* should be what produces the benefit** | **A no-prediction "reject everything" gate matches or beats the twin at every fidelity, and beats a perfect-foresight oracle** | **FAIL — decisive** |
| LT-2 | A fidelity-1.00 twin should catch every harmful action | **98.0 %** — 1 harmful action never caught at *any* fidelity | **FAIL** |
| LT-3 | A fidelity-1.00 twin should predict harm exactly | **67.6 %** exact when a fault boundary is in-horizon | **FAIL** |
| LT-4 | Higher fidelity should improve precision | Precision **peaks at fidelity 0.40 (93.5 %)** and *declines* to 89.3 % | **FAIL** |
| LT-5 | The configured horizon (20) should be justified | Horizon 10 gets **100 %** recall at the same SLO outcome, at 55 % of the cost | **FAIL** |
| LT-6 | A longer horizon should improve recall | Recall **falls** 100 % → 98.0 % → 96.3 % as horizon grows 10 → 20 → 40 | **FAIL** |
| LT-7 | Retry-on-rejection should produce a better repair | **56 of 56 retries produced `no_op`** — never an alternative repair | **FAIL** |
| LT-8 | The fidelity scale should have resolution across its range | Fidelity 0.80 and 1.00 produce **identical** results | **FAIL** |
| LT-9 | The rule baseline should beat doing nothing | **138.1 vs 130.5** — it is worse | **FAIL** |

### 3.2 LT-1 — Fidelity crossover: *the crossover does not exist*

The experiment looked for the fidelity at which gating stops paying for itself. **There isn't
one in the measurable range.**

```
ungated baseline (A2, no twin): mean violation-ticks = 154.9

 fidelity  viol-ticks  vs ungated   recall      FPR  precision   TP   FN   FP
     0.00       140.5       -14.4    70.0%    8.47%      73.7%   28   12   10
     0.20       139.6       -15.3    72.7%    4.42%      86.5%   32   12    5
     0.40       134.4       -20.5    87.8%    2.56%      93.5%   43    6    3
     0.60       131.1       -23.8    94.1%    4.10%      90.6%   48    3    5
     0.80       130.7       -24.2    98.0%    4.80%      89.3%   50    1    6
     1.00       130.7       -24.2    98.0%    4.80%      89.3%   50    1    6
```

**Why this is a problem, not a success.** A *completely degraded* twin (fidelity 0.00,
recall 70 %, FPR 8.5 %) still improves outcomes by 14.4 violation-ticks — **59 % of the benefit
that a perfect twin delivers**. If most of the value survives destroying the twin's accuracy,
then most of the value is not coming from the twin's *predictions*. It is coming from the fact
that a gate blocks *some* actions at all, in a setting where the agent's actions are net
harmful on average (LT-9).

This weakens the paper's central story. "Twin fidelity matters" is only weakly supported: it
buys the last 41 % of the improvement, and the marginal return between fidelity 0.6 and 1.0 is
**0.4 violation-ticks**. A reviewer will ask whether a trivial gate captures the same benefit.

**That ablation has now been run — see §3.3. The answer is yes, and it is worse than
suspected:** a no-prediction reject-all gate matches or beats the twin at every fidelity, and
beats a perfect-foresight oracle.

### 3.3 LT-10 — The constant-reject ablation: the decisive negative result

§3.2 raised the suspicion that the twin's *predictions* might not be doing the work. This
experiment settles it. Every gate below runs under identical seeds, faults and agent; only the
gate logic changes. `always_reject` blocks every non-`no_op` proposal and performs **no
simulation at all**. `oracle` rolls the **real** simulator forward with the fault schedule
visible — perfect foresight, the theoretical ceiling for any gate.

**Stub-LLM agent (10 seeds):**

```
gate                                viol-ticks  vs ungated  blocked   TP   FN   FP   recall      FPR
A0  do nothing (no agent)                130.5
ungated  agent acts freely               154.9        +0.0        0    0   27    0     0.0%    0.00%
always_reject  (no prediction)           130.5       -24.4       74   52    0   22   100.0%   15.49%
random p=0.747   (no prediction)         154.5        -0.4       57   25   10   32    71.4%   22.54%
reject_migrate (heuristic)               154.9        +0.0        0    0   27    0     0.0%    0.00%
twin @ fidelity 0.00                     140.5       -14.4       38   28   12   10    70.0%    8.47%
twin @ fidelity 0.60                     131.1       -23.8       53   48    3    5    94.1%    4.10%
twin @ fidelity 1.00                     130.7       -24.2       56   50    1    6    98.0%    4.80%
oracle gate (upper bound)                130.6       -24.3       53   51    0    2   100.0%    1.64%
```

**Rule agent (10 seeds):**

```
gate                                viol-ticks  vs ungated  blocked   TP   FN   FP   recall      FPR
A0  do nothing (no agent)                130.5
ungated  agent acts freely               138.1        +0.0        0    0    5    0     0.0%    0.00%
always_reject  (no prediction)           130.5        -7.6       13    8    0    5   100.0%    4.00%
random p=0.3   (no prediction)           139.0        +0.9        4    0    5    4     0.0%    3.36%
reject_migrate (heuristic)               138.1        +0.0        0    0    5    0     0.0%    0.00%
twin @ fidelity 0.00                     132.3        -5.8        7    6    2    1    75.0%    0.84%
twin @ fidelity 0.60                     130.2        -7.9        9    8    0    1   100.0%    0.83%
twin @ fidelity 1.00                     130.2        -7.9        9    8    0    1   100.0%    0.83%
oracle gate (upper bound)                130.2        -7.9        9    8    0    1   100.0%    0.83%
```

#### What this shows

**1. For the stub agent, blanket rejection beats the twin — and beats perfect foresight.**
`always_reject` scores **130.5**, better than the twin at fidelity 1.00 (**130.7**) and better
than the **oracle** (**130.6**). A gate that runs no simulation, has no model, and simply
refuses everything is the best-performing gate in the entire experiment. The twin's predictions
are not merely unnecessary here; acting on them is *slightly worse* than ignoring them, because
the twin occasionally approves an action that turns out harmful.

**2. For the rule agent, the twin exactly attains the oracle — and the margin is 0.3 ticks.**
Twin @ 0.60, twin @ 1.00 and the oracle all score **130.2**, against `always_reject`'s
**130.5**. So the twin *does* beat blanket rejection, by identifying the small number of
genuinely beneficial actions that blanket rejection throws away. That is the result the project
wants — but the margin is **0.3 violation-ticks, about 0.2 %**, and the twin has already
saturated the ceiling, so no improvement to the twin can ever widen it.

**3. Random gating does not work, so "any gate helps" is false.** At a matched blocking rate
the random gate scores **154.5** (stub) and **139.0** (rule) — no better than ungated, and
worse than ungated for the rule agent. Blocking three-quarters of actions at random captures
essentially none of the benefit that blocking *all* of them captures. The harm is concentrated
and persistent: a single unblocked bad action inflicts damage that lasts, so a gate that leaks
25 % of actions leaks most of the harm. This is a genuinely interesting dynamic and it rules
out the lazy interpretation of §3.2.

**4. `reject_migrate` blocked nothing** (0 of 120 in both agents), so on these seeds neither
agent ever proposed a migration at a decision point. The migration path exercised in TC-2 of the
functional report is reachable only under the hand-built saturation fault, not under the random
15-fault schedule. The domain-heuristic comparison is therefore untested, not refuted.

#### Why this happens, and what it means

The cause is visible in the first row of both tables: **the agents' actions are net harmful on
this workload.** Ungated, the stub agent scores 154.9 and the rule agent 138.1, against 130.5
for doing nothing. When an action portfolio has negative expected value, the optimal gate is
"reject everything", and *no* predictive gate can beat it by more than the value of the few
beneficial actions in the portfolio — here, 0.3 violation-ticks.

**This is a defect in the benchmark, not in the twin.** The evaluation cannot demonstrate the
value of prediction because the setting gives prediction almost nothing to earn. The twin is
doing its job well — it hits the oracle exactly for the rule agent — but the experiment has no
headroom in which that competence can show up as a number.

**Consequence for the paper.** The claim "the twin gate improves outcomes" must be retired in
its current form. What the data supports is narrower and must be stated with the ablation
beside it:

> Against an agent whose actions are net harmful, a twin gate recovers the damage to parity
> with inaction and attains the perfect-foresight optimum — but so does a trivial reject-all
> gate, to within 0.3 violation-ticks. Distinguishing them requires a workload on which the
> agent has genuinely beneficial actions to contribute.

**The fix is a workload change, and it is now the project's top priority** — see §4.3.

### 3.4 LT-2 / LT-3 — Fault-boundary blindness, mechanism isolated

This is the most useful result in the report. Partitioning every proposal at fidelity 1.00 by
whether a fault **starts or ends inside the twin's 20-tick horizon**:

```
twin fidelity 1.00, horizon 20
  fault boundary INSIDE horizon : n= 63  non-no_op=34
      twin delta == oracle exactly : 23/34 (67.6%)
      mean |twin - oracle| error   : 1.62 ticks
      recall=93.3%  FPR=12.50%  FN=1

  no boundary inside horizon    : n=113  non-no_op=41
      twin delta == oracle exactly : 41/41 (100.0%)
      mean |twin - oracle| error   : 0.00 ticks
      recall=100.0%  FPR=0.00%  FN=0
```

**Every single error the twin makes at fidelity 1.00 is attributable to a fault boundary
falling inside its horizon.** With no boundary in the horizon it is *exactly* right — 41/41,
zero mean error, perfect recall, zero false positives. With a boundary, it degrades to 67.6 %
exact, 93.3 % recall, and **12.50 %** false-positive rate.

This converts a vague limitation ("the twin is imperfect") into a precise, mechanistic one
("the twin is exact except when the fault landscape changes inside its prediction window"),
and it names the fix: **fault-arrival prediction**, not higher simulation fidelity. Raising
fidelity cannot help here — the twin at fidelity 1.00 is already a perfect simulator; it is
blind to the *schedule*, not inaccurate about the *dynamics*.

### 3.5 LT-5 / LT-6 — The horizon is an untuned parameter with a perverse gradient

```
 horizon  viol-ticks   recall      FPR   FN   twin ms
       5       136.4   100.0%   27.27%    0      29.0
      10       130.7   100.0%   15.49%    0      42.0
      20       130.7    98.0%    4.80%    1      75.8
      40       130.7    96.3%    3.28%    2     142.6
```

Three findings, none of them comfortable:

1. **Recall gets *worse* as the horizon grows** (100 % → 96.3 %). This follows directly from
   §3.4: a longer horizon is more likely to contain a fault boundary, so a longer lookahead
   makes the fault-blind twin *less* reliable, not more. This is counter-intuitive and is a
   genuine, publishable observation about fault-blind twins.
2. **The configured default (20) was never justified.** Horizon 10 achieves 100 % recall at the
   same SLO outcome (130.7) for 55 % of the compute. It pays for that with a 3× higher
   false-positive rate (15.49 % vs 4.80 %) — so it is a real trade-off, not a strict
   improvement, but the default was selected without this sweep having been run.
3. **Over-blocking has a real cost.** Horizon 5 achieves perfect recall yet produces the
   *worst* outcome (136.4). Blocking 27 % of safe actions costs more than the harmful actions
   it catches. Recall alone is a misleading objective.

### 3.6 LT-7 — The retry loop is churn

```
 retry     n  rejected  harmful  exhausted
     0   120        56       51          0
     1    56         0        0          0

retry 0 action types : {'scale_service': 75, 'no_op': 45}
retry 1 action types : {'no_op': 56}
```

All 56 retries were approved and none was harmful — which looks like a 100 % success rate for
the retry mechanism, and is **not**. **Every one of the 56 retries proposed `no_op`.** The loop
never produced an alternative repair; it converted a rejected action into inaction.

The retry-on-rejection loop is presented as a key mechanism in the closest related work
(arXiv:2505.02076; the sovereignty/orchestration line). **In this project it is untested.** The
scripted stub deterministically falls back to `no_op` when re-prompted with a rejection, so
these runs measure the stub's fallback, not repair-by-reprompting. No claim about the retry
loop can be made from this data.

### 3.7 LT-8 — The top of the fidelity scale has no resolution

Fidelity 0.80 and 1.00 produce byte-identical aggregate results (130.7 ticks, 98.0 % recall,
4.80 % FPR, TP 50 / FN 1 / FP 6). The scalar mapping saturates: above ~0.8 the five underlying
axes are already at or near their perfect settings, so the last fifth of the x-axis carries no
information. Any fidelity curve plotted in the paper will have a flat, uninformative right-hand
segment, and should either be re-parameterised or plotted on a scale that reflects where the
mapping actually varies.

### 3.8 LT-9 and other limitations carried forward

Confirmed in the functional report and unchanged here:

- **The rule baseline is worse than doing nothing** — 138.1 vs 130.5 violation-ticks. The twin
  gate's achievement is recovering that loss to parity (130.2), *not* beating inaction.
- **The real-model arm is degenerate** — the live Gemini arm emits `no_op` in ~97 % of
  decisions, leaving an essentially empty confusion matrix. All A2/A3 numbers describe a
  **scripted stub**, not LLM behaviour.
- **Half the false positives are definitional** — 3 of 6 at fidelity 1.00 fall in the 1–3 tick
  band created by `tolerance_margin = 0` against `harm_threshold_ticks = 3`.
- **Twin and oracle share code**, differing only in fault mode — so the twin's errors are
  correlated with the oracle's blind spots by construction.
- **Effect carried by 4 of 10 seeds** (mean −7.9, sd 12.1). Directional, not significant.

### 3.9 What did *not* break

Reporting these so the suite is not mistaken for a list of everything that could go wrong:

- **Client disconnect is handled cleanly.** Aborting an SSE stream mid-episode logs
  `client disconnected after 18 events` and the server continues serving (verified live).
- **Determinism holds.** Identical parameters produce byte-identical event streams
  (`test_identical_options_produce_identical_streams`).
- **No retry budget was ever exhausted** (0 of 176 proposals), so the retry cap is not binding.
- **The three validation layers never let a malformed action through** in any run.

---

## 3A. ADDENDUM (2026-09-16) — re-run under the corrected model

Everything in §3 above was measured **before** the Priority-1 simulator corrections
(`docs/research/audit_fix1.md`). Those corrections created genuine recovery opportunities where
previously there were almost none, so the ablation in §3.3 was re-run. **One headline result
flipped; one did not.**

### 3A.1 The ablation, re-measured

**Rule agent — the result flips in the project's favour:**

| Gate | Before fixes | **After fixes** |
|---|---|---|
| A0 do nothing | 130.5 | 136.3 |
| ungated | 138.1 | 138.4 |
| always_reject (no prediction) | 130.5 | 136.3 |
| random p=0.3 | 139.0 | 139.3 |
| twin @ 0.00 | 132.3 | 138.1 |
| twin @ 0.60 | 130.2 | 136.0 |
| **twin @ 1.00** | **130.2** | **131.9** |
| oracle (upper bound) | 130.2 | 131.9 |

- **The twin now beats doing nothing** (131.9 vs 136.3). Before the fixes it only reached
  parity. The claim retired in §4.2 can be reinstated for this arm.
- **The twin's margin over blanket rejection grew from 0.3 to 4.4 violation-ticks — about 15×.**
  LT-10 no longer holds for the rule agent.
- **The twin still attains the oracle exactly** (131.9 = 131.9), now with real headroom beneath
  it rather than none.
- **The fidelity axis regained resolution at the top**: 138.1 → 136.0 → **131.9** across
  0.0 / 0.6 / 1.0, where before it saturated at 0.6. This partially repairs LT-8.

**Stub-LLM agent — the degenerate result persists:**
`always_reject` **136.3** still ties doing-nothing and still beats twin @ 1.00 (**136.8**) and
the oracle (**136.7**); ungated is 166.1. Nothing changed structurally.

**The difference between the two arms is the whole lesson.** The stub only ever proposes
`scale_service`, and scaling is worth ~1 violation-tick on these faults (§3A.3). An agent whose
repertoire contains no beneficial action cannot be helped by any gate, and for such an agent
reject-all is optimal. LT-10 is therefore **not a fact about twins — it is a diagnostic for
agents with no upside in their action repertoire.** That is the sharper, more defensible
version of the finding and it survives the correction.

### 3A.2 The opportunity gap: repairs exist, the agent never proposes them

Rule agent vs the privileged enumerator, scored on the same 40-tick horizon (seeds 0–1,
16 decision points, `scratchpad/opportunity_gap.py`):

```
decision points where a beneficial action EXISTED : 16/16 (100%)
  total available benefit (violation-ticks)       : 130
  total benefit the agent actually captured       : 0
  CAPTURE RATE                                    : 0.0%
  agent proposed exactly the best action          : 0/16
  agent held while a beneficial action existed    : 16/16

agent proposals      : {'no_op': 16}
enumerator best picks: {'migrate svc5->gw0': 8, 'migrate svc1->gw0': 4,
                        'migrate svc0->gw0': 2, 'migrate svc4->gw0': 2}
```

A beneficial action exists at **every** decision point, worth 6–22 violation-ticks each, and the
rule agent captures **none** of it — it holds every time. This satisfies the falsification
criterion in the investigation report's §9: repairs are reachable, so **Priority 2 (agent
decisiveness) is justified**.

### 3A.3 Two cautions before Priority 2

**(a) The upside is concentrated in one move, and half of it is a modelling asymmetry.**
Node faults only target `role == "edge"`, so **`gw0` can never be faulted**, and every device
client attaches directly to it — making the gateway a universally reachable, fault-immune safe
harbour. All 16 enumerator picks above are migrations to it. Excluding `gw0` roughly halves the
benefit, and every *non-migration* action is worth ~1 violation-tick:

| Fault | hold | best (all) | best **without gw0** | best **non-migration** |
|---|---|---|---|---|
| cpu_saturation | 145 | 95 | 117 | **144** |
| node_crash | 145 | 95 | 115 | **144** |
| link_degradation | 145 | 95 | 115 | **144** |
| link_failure | 145 | 95 | 115 | **144** |
| memory_leak | 107 | 55 | 55 | 55 |
| traffic_surge | 94 | 79 | 85 | 85 |

So "every fault type has a beneficial candidate" is true, but for four of six it is the *same*
candidate, and only memory-leak (restart) and traffic-surge (scale) are genuine non-migration
wins.

**(b) The advice is myopic and does not compose — which is good news.**

```
 seed   hold  1 svc->gw0  ALL svc->gw0
    0     44          28           360
    1     48          34           360
    2     77          64           360
```

Migrating **one** service to `gw0` helps; migrating **all six** is catastrophic — 360 is the
maximum possible (6 services × 60 ticks of total outage), because `gw0` carries the same CPU
capacity as an edge node and saturates. A greedy agent that followed the depth-1 advice at every
decision point would destroy the network.

This is the most encouraging finding in the addendum: **the benchmark is no longer degenerate in
either direction.** Before the fixes, "always hold" was optimal. After them, "always migrate to
`gw0`" is *not* a free win either. There is now a real capacity trade-off and a non-trivial
optimal policy — which is exactly the property the evaluation needed in order to measure
anything.

**Recommended before Priority 2:** remove the gateway asymmetry — make `gw0` faultable, or
restrict migration targets to edge nodes, or give it a distinct capacity — so a newly decisive
agent learns a real repair policy rather than a modelling exploit.

---

## 4. What this means for the paper

### 4.1 The claim that survives

> We contribute a **counterfactual labelling method** that assigns every agent proposal —
> including the ones a gate rejected — an exact harm label by forking a deterministic
> simulator, and we use it to score a validation gate as a classifier across a swept
> twin-fidelity axis.

This survives because no checked work does it: Aether does not verify what blocked changes
would have done; SLBench grades completed unsafe actions; the fidelity precedent
(J. Intell. Manuf. 2023) varies fidelity but has no agent and no gate.

### 4.2 The claims that do not survive

- ~~"A digital twin as a validation gate between an agent and a network is novel"~~ — Aether
  and arXiv:2505.02076 both do this; it is now common enough to have a survey.
- ~~"Higher twin fidelity produces better gating"~~ — true for recall, **false for precision**
  (LT-4) and **false for recall as a function of horizon** (LT-6).
- ~~"The twin gate improves network outcomes"~~ — it recovers a bad controller's damage to
  parity with inaction. It does not beat inaction.
- ~~"Our LLM agent…"~~ — there is no working LLM arm (LT-8, §3.8).
- ~~"The twin's predictions are what make gating work"~~ — **retired by §3.3.** A reject-all
  gate with no model matches or beats the twin at every fidelity and beats a perfect-foresight
  oracle. The twin's predictive advantage over blanket rejection is **0.3 violation-ticks** and
  exists only for the rule agent.

### 4.3 Priority of work, in order

1. **Build a workload with genuine upside.** This replaces the ablation as priority 1, because
   the ablation has now been run and §3.3 identifies the root cause: both agents' action
   portfolios have *negative expected value*, so the optimal gate is "reject everything" and
   prediction has almost nothing to earn. Until some agent actions are reliably beneficial —
   faults that genuinely require intervention and do not self-resolve, cheaper migrations, or
   an agent good enough to find real repairs — the experiment cannot measure what it was built
   to measure. Everything below is secondary to this.
2. **Fix the LLM arm.** No language-model claim is currently supported.
3. **Implement fault-arrival prediction** and re-run §3.4. The mechanism is isolated; the fix
   is identified; this converts a limitation into a contribution.
4. **Re-tune or justify the horizon** using §3.5's sweep rather than the inherited default.
5. **Reconcile `tolerance_margin` with `harm_threshold_ticks`**, and report a precision/recall
   curve instead of one arbitrary operating point.
6. **Raise seed count** to 50–100 with a Wilcoxon signed-rank test.
7. **Test the retry loop with a model that can actually propose alternatives** (LT-7).

### 4.4 How to frame this against the base paper

Do not claim to beat Aether — on realism, scale and deployment credibility we do not, and the
comparison table in §2.2 says so plainly. Frame it as: *Aether establishes that agentic
validation with a network digital twin works in production; it cannot measure what its gate's
rejections were worth, because production networks cannot be counterfactually replayed. We
supply that measurement in a setting where replay is possible, and report what it reveals —
including the uncomfortable finding that on a workload where the agent is net harmful, a
trivial reject-all gate matches a perfect-foresight oracle, so gate benefit on such workloads
says almost nothing about gate quality.*

That framing is stronger than the original one, not weaker. It converts §3.3 from an
embarrassment into the paper's most useful result: **a warning that the standard way of
evaluating guardrails — measuring outcome improvement over an ungated agent — is
uninformative whenever the agent's actions are net harmful, which is precisely when guardrails
are deployed.** Every guard paper in §1 reports exactly that uninformative number, and none of
them runs the reject-all ablation that would expose it. That is a methodological contribution
with teeth, and it is available to this project today, on the data already collected.

§3 is the evidence that the limits were looked for rather than avoided.

---

## Sources

- [Aether: Network Validation Using Agentic AI and Digital Twin — arXiv:2604.18233](https://arxiv.org/abs/2604.18233)
- [Leveraging LLM Agents and Digital Twins for Fault Handling in Process Plants — arXiv:2505.02076](https://arxiv.org/abs/2505.02076)
- [SLBench: Evaluating How LLM Agents Follow Logical Relations in Skills — arXiv:2607.09016](https://arxiv.org/html/2607.09016)
- [MicroRemed: Benchmarking LLMs in Microservices Remediation — arXiv:2511.01166](https://arxiv.org/abs/2511.01166)
- [NIKA: A Network Arena for Benchmarking AI Agents on Network Troubleshooting — arXiv:2512.16381](https://arxiv.org/pdf/2512.16381)
- [NetInjectBench: Indirect Prompt Injection in Tool-Using LLM Agents for Network Operations — arXiv:2607.10490](https://arxiv.org/html/2607.10490v1)
- [NetAgentBench: A State-Centric Benchmark for Agentic Network Configuration — arXiv:2604.09678](https://arxiv.org/pdf/2604.09678)
- [Investigating the influence of fidelity on the capability of a digital twin to detect material extrusion failures — J. Intelligent Manufacturing (2023)](https://link.springer.com/article/10.1007/s10845-023-02144-x)
- [A Survey of Safe Reinforcement Learning and Constrained MDPs — arXiv:2505.17342](https://arxiv.org/pdf/2505.17342)
