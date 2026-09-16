# Literature and inference audit

**Date:** 16 September 2026  
**Scope:** Independent verification of the literature and reasoning in `docs/lab6_baseline_comparison_and_limitations.md`. This is an audit of the submitted report; its experimental numbers are treated as reported observations here. The separate implementation audit determines what those numbers actually measure.

## Summary

All nine cited identifiers resolve to authentic papers with matching or slightly abbreviated titles. The problem is interpretation, not fabricated references. Several central conclusions are false or substantially overstated: a negative average action value does not make reject-all optimal; a finite-horizon perfect-model gate is not a ceiling on episode performance; the cited process-plant paper contains quantitative results; and one of the report's own cited security papers explicitly evaluates a blanket blocker and the utility it destroys. The project's classifier and simulator-counterfactual framing must be positioned against established shielding and simulation literature before claiming novelty.

## 1. Citation verification

Primary pages and, where available, full HTML were retrieved during this audit. A working URL verifies existence; it does not endorse every conclusion in the source.

| Report reference | Verification | Appropriate use |
|---|---|---|
| Aether, `2604.18233` | [Title and identifier match](https://arxiv.org/abs/2604.18233). | Network change-validation architecture; corrections below. |
| Process plants, `2505.02076` | [Title and identifier match](https://arxiv.org/abs/2505.02076); the record links an IEEE ETFA DOI. | Closest propose–simulate–validate–reprompt architecture. |
| SLBench, `2607.09016` | [Title and identifier match](https://arxiv.org/abs/2607.09016). | Skill-following and mitigation evaluation, with substantial scope restrictions. |
| MicroRemed, `2511.01166` | [Title and identifier match](https://arxiv.org/abs/2511.01166). | Remediation benchmark. It generates Ansible playbooks from diagnosis reports and evaluates recovery, so diagnosis availability differs from this project. |
| NIKA, `2512.16381` | [Identifier matches](https://arxiv.org/abs/2512.16381). Formal title: *A Network Arena for Benchmarking AI Agents on Network Troubleshooting*; NIKA is the framework name. | Realistic diagnosis/localization testbed, not a measured action-safety gate. |
| NetInjectBench, `2607.10490` | [Identifier matches](https://arxiv.org/abs/2607.10490). Formal title includes “Benchmarking” and “Large Language Model.” | Authorization safety and utility; directly relevant conservative-blocking baseline. |
| NetAgentBench, `2604.09678` | [Identifier matches](https://arxiv.org/abs/2604.09678). Formal title includes “Evaluating.” | Stateful configuration and bounded multi-turn behavior. |
| Fidelity study, `10.1007/s10845-023-02144-x` | [Title and DOI match](https://link.springer.com/article/10.1007/s10845-023-02144-x). Published online June 2023; journal volume 35, pages 2263–2276 is dated 2024. | Fidelity-versus-failure-detection precedent. |
| Safe-RL survey, `2505.17342` | [Identifier matches](https://arxiv.org/abs/2505.17342). Full title adds *A Technical Survey on Single-Agent and Multi-Agent Safety*. | Literature map; cite the underlying research for technical guarantees. |

### Aether: accurate numbers, incorrect setting and count

The full text supports five agents; 25 routers; 277 addresses; 263 VRFs across 613 instances; 76 ACLs/274 rules; 30,000+ configuration lines; 94% synthetic detection, 0.64 precision, 223 seconds; and 100% incident detection, 57%/90% precision, 414/395 seconds. But it reports **8 synthetic scenarios, not 88**, with valid/problematic candidates, paraphrases and ten repeated runs. It evaluates an **ISP laboratory replica** using past incidents. Emulation support is planned; Cisco NSO is an ingestion source, not evidence of implemented emulation. Final deployment approval is human. “Works in production” overstates this evaluation.

No scalar-fidelity sweep or numerical counterfactual SLO-cost study is reported. Nevertheless, the system already forks snapshots and compares pre/post-change modeled behavior; its incident labels incorporate expert postmortems. “Cannot evaluate effects” is too strong. Absence of a shared public dataset prevents the cited head-to-head comparison, but expert ground truth is a reference baseline. [Full paper, §§IV–VIII, Tables VII–VIII](https://arxiv.org/html/2604.18233v1).

### Process plants: the qualitative-only characterization is false

The paper studies one clogging scenario, but Tables I–II report correct, incorrect and missed actions, reprompts and tokens across two models and three representations. For GPT-4o, text obtains 15/15 correct actions with one reprompt; Modelica and diagram inputs require six and five reprompts. Limited scope is a valid criticism; absence of quantitative data is not. Its algorithm explicitly uses state and fault type and, after exhausting retries, forces the current action into the plant. Neither its information boundary nor its fallback matches this project's symptom-only, no-op design. Do not copy that fallback as a safety recommendation. [Full paper, Algorithm 1 and §VI](https://arxiv.org/html/2505.02076v1).

### Guard comparators: important omitted qualifications

**SLBench:** 11 to 4 violations is supported, approximately 63.6% reduction. It is an intervention on **11 selected previously violating cases**, for one agent/backbone pair, not a population safety rate or a random sample. Some inconclusive outcomes with no violation signals are merged into the safe category. Three human annotators audit skill clarity on a related 12-case set; “three annotators established every gate outcome” would misstate their role. This cannot be compared numerically to SLO ticks or classifier recall. [Full paper, §§4.3–4.4 and Limitations](https://arxiv.org/html/2607.09016v1).

**NetInjectBench:** the 82.50% unsafe tool-action rate is supported for 240 attack instances. The report omits the result most relevant to its argument: a static allowlist blocks the high-impact tool globally, obtains 5% unsafe actions, but **0% approved-change usefulness and 100% approved-change overblocking**. The metadata gate reports 0/240 unsafe actions and 100% approved-change usefulness. Approved-change cases are explicitly included to avoid rewarding trivial blockers. These are mock-tool, single-step authorization outcomes, not simulated network performance. The report's universal claim that none of its cited guard papers evaluates the utility lost through indiscriminate blocking is therefore directly contradicted by a cited source. [Full paper, §§3.2–3.4 and §4](https://arxiv.org/html/2607.10490v1).

### Fidelity precedent: valid, but it points against a monotonicity assumption

The Su–Hicks–Nassehi article varies six image/model attributes for detecting extrusion errors and layer shifts. Its abstract explicitly reports that lower-fidelity twins can perform comparably to higher-fidelity ones while reducing costs. Therefore an outcome plateau does not inherently indicate a defective fidelity metric, and strictly increasing precision is not an established expectation. This is a detection precedent, not an action-gating experiment. Publisher abstract and metadata were accessible; the full article was subscription-restricted, so detailed numerical tables were not independently verified here. [Publisher page](https://link.springer.com/article/10.1007/s10845-023-02144-x).

## 2. Established prior work that limits the novelty claim

### Model-based action screening and a safe backup are established designs

Wabersich and Zeilinger's *A predictive safety filter for learning-based control of constrained nonlinear dynamical systems* receives a proposed control, predicts whether it can be safe, and minimally modifies it if required. It incorporates uncertainty and maintains a backup policy. Its guarantee depends on its assumptions and constraints; it is not equivalent to approving any action predicted no worse than no-op. This is a more appropriate engineering foundation than citing only a survey. [Automatica, 2021](https://doi.org/10.1016/j.automatica.2021.109597), [author preprint](https://arxiv.org/abs/1812.05506).

Bastani's model predictive shielding uses forward simulation to decide whether the learned policy can proceed while retaining recoverability under a backup policy. The later robust version addresses stochastic disturbances with sampled trajectories and tube-based control. These establish the connection to runtime assurance and explain why merely having a perfect short rollout is not an infinite-horizon safety or optimality proof. [Model predictive shielding](https://arxiv.org/abs/1905.10691), [robust model predictive shielding](https://arxiv.org/abs/1910.10885).

### A shield as a classifier with state–action ground truth is not novel

Shperberg et al., *A Rule-based Shield: Accumulating Safety Rules from Catastrophic Action Effects* (CoLLAs 2022), explicitly define a ground-truth safety-label function on state–action pairs and a binary shield, discuss false positives/negatives, and note that observation aliasing complicates shielding. Their positive class is “safe,” so their FP/FN terminology is the reverse of the submitted report's “harmful” positive class. Their experiment is not the identical paired no-op/fidelity sweep; it does establish that classification and action-specific safety labels predate this project. [Proceedings](https://proceedings.mlr.press/v199/shperberg22a.html), [§§3.1–3.2](https://proceedings.mlr.press/v199/shperberg22a/shperberg22a.pdf).

### Paired simulator alternatives are an established experimental instrument

Simulation experiments have long compared system designs using common random numbers to reduce variance in performance differences. That supplies the statistical lineage of using the same exogenous randomness in action/no-op branches. Deterministic cloning is a useful implementation of the instrument, not evidence by itself of a new causal-estimation method. [Yang and Nelson, *Operations Research*, 1991](https://doi.org/10.1287/opre.39.4.583).

A directly relevant modern example studies action alternatives using rollout simulations and common random numbers, including how the continuation policy affects relative-utility variance. [Yadav et al., *Using Common Random Numbers for Simulation-based Planning with Rollouts*, RLC 2026](https://rlj.cs.umass.edu/2026/papers/Paper52.html).

### Safety-versus-usefulness evaluation is established

AgentDojo publicly reports utility, utility under attack and attack success together. Its result page also warns that combinations of attacks and defenses do not support an uncomplicated leaderboard. This is a concrete counterexample to a purported general guardrail-literature blindness to useful task completion. [Official benchmark results](https://agentdojo.spylab.ai/results/).

**Novelty verdict:** the exact combination of this network simulator, relative SLO harm labels for every candidate, independent model-error axes and matched intervention baselines may be a useful contribution. This audit does not establish that no prior work has that exact combination. Do not make a first-ever claim from checking a small set of adjacent papers. State an implemented measurement capability and its limitations, then claim originality only after a targeted systematic review.

## 3. Mathematical and inferential corrections

The following are deductions from the reported design and numbers, not assertions copied from a paper.

### 3.1 Negative mean does not imply reject-all optimality

Let `D(s,a) = cost(action) - cost(no-op)` on fixed decision opportunities. Positive values mean harm. Suppose nine actions each have `D = +10`, and one has `D = -20`. The ungated mean is `+7`, so the action portfolio is harmful on average. Reject-all has mean incremental cost zero. A selective gate that rejects the nine bad actions and accepts the good one has mean `-2`: it beats both.

Formally, an ideal selector achieves `E[min(D, 0)]`, which may be strictly negative even when `E[D] > 0`. The gain depends on the conditional distribution and distinguishability of beneficial actions, not the sign of the overall mean.

The submitted report's larger logical leap is from **episode performance** to the average value of an action portfolio. Closed-loop policies visit different states, generate different later proposals and have different action interactions. Subtracting two episode means is not itself an estimate of the average isolated causal effect of a shared list of actions.

**Correction:** “On these tested seeds and controllers, rejecting all proposals performs about as well as the tested predictive gates. We have not yet established whether this reflects scarce useful proposals, a flawed label/decision horizon, gate errors, or interactions between later decisions.”

### 3.2 A finite-horizon exact rollout is not an episode oracle ceiling

An exact model can evaluate the wrong objective perfectly. An action can save one violation-tick within horizon `H` but add ten after `H`; a perfect `H`-tick selector accepts it, while reject-all has the better episode outcome. Likewise, an action that is useful when followed by a competent recovery policy may be harmful when followed by no-op, or vice versa.

There are at least three different objects:

1. **Exact local evaluator:** evaluates one specified candidate and continuation over one stated horizon.
2. **Candidate-set oracle:** chooses the best currently offered candidate under that local evaluation.
3. **Optimal episode policy:** chooses an action sequence to minimize the full episode objective while modeling future decisions and information.

Only the last, optimized over a policy class containing reject-all, must perform at least as well as reject-all in a deterministic episode. The reported 130.6 versus 130.5 is evidence against the label “theoretical ceiling,” not evidence that rejecting everything defeats perfect global planning.

**Correction:** rename the current arm “perfect-model, fault-schedule-aware `H`-tick gate.” Do not claim the twin saturates all attainable performance or that 0.3 ticks is the maximum available benefit. Equality of rounded means does not even establish identical decisions or trajectories.

### 3.3 The report contradicts its own table

“Reject-all matches or beats the twin at every fidelity” is false without restricting the statement to the stub agent: the rule-agent table gives twin/oracle **130.2**, reject-all **130.5**. “The gate does not beat inaction” is numerically false for that same rule-agent table, although the improvement is small and its generalizability remains uncertain. Use “no demonstrated material advantage over inaction” if supported by uncertainty intervals and an explicit practical-effect threshold.

### 3.4 Fidelity zero does not imply no predictive information

A degraded twin that retains topology, action dynamics, resource constraints and coarse state can still carry useful predictive information. A 70% harmful-action recall does not demonstrate that its predictions were destroyed. The fraction of aggregate benefit retained cannot be interpreted as the fraction of benefit independent of prediction. A scalar label of zero is a parameter convention, not proof of statistical independence from true effects.

Measure residual information and calibration on a fixed action set; compare against deterministic heuristics and randomized gates under an explicit matching rule. The random ablation already shows that arbitrary rejection is not sufficient in these runs.

### 3.5 Precision need not increase with fidelity

Precision depends on prevalence and the decision threshold. It need not improve monotonically even if numerical prediction error decreases. Here the gate can also change later states, candidate types and retry counts, so different fidelities may be scored on different datasets. Comparing such precision values does not isolate a model-fidelity effect.

Use a frozen corpus of identical state/candidate pairs with a fixed labeling horizon, evaluate numerical error and decisions on that corpus, and separately report closed-loop episode outcomes. Keep harm threshold and gate tolerance aligned or report the resulting gray zone explicitly.

### 3.6 Horizon comparisons need a fixed target

If the label horizon changes with the prediction horizon, “recall falls when lookahead grows” compares different definitions of a positive example. Even with a fixed target, longer prediction under an imperfect disturbance model need not improve classification. Neither observation is inherently a paradox or a new control-theoretic result.

A proper experiment fixes a ground-truth consequence horizon and continuation policy, varies the twin's prediction horizon, and reports prediction error, false-safe cost, missed-benefit cost and computation. Separately evaluate the policy-level tradeoff using full episodes.

### 3.7 Fault-boundary attribution is local evidence, not a universal mechanism proof

The reported stratification establishes exact agreement in 41 sampled non-no-op cases without an in-horizon fault boundary. It does not prove that all future error is caused only by boundaries or that boundary prediction is the unique remedy. Nor does it show the *direction* of bias in action-minus-baseline cost: both branches can be wrong, and their errors may reinforce or cancel.

Before prescribing “fault-arrival prediction,” distinguish fault arrival from recovery, specify what information is legally available to an operational predictor, test controlled removal of that uncertainty, and compare uncertainty-aware horizons or disturbance ensembles. Supplying the hidden schedule to the deployed twin would replace the experiment's information constraint with clairvoyance.

### 3.8 Random blocking does not identify the proposed persistence mechanism

Poor random-gate outcomes are an observed ablation result. The explanation that one leaked action causes persistent damage is plausible, but it requires action-duration and trajectory evidence. Repeated proposals can also bypass a random gate eventually; differences in retries and proposal opportunities can create the same pattern. A matched probability is not automatically a matched realized intervention count, especially when the gate changes subsequent behavior.

Report the matching denominator, whether retries are included, realized blocking counts and effects per action trajectory. The observed random result does refute the simple statement that any arbitrary rejection rule is sufficient in these runs.

### 3.9 Equal outputs do not prove the fidelity mapping saturates

Identical aggregate outputs at fidelity 0.8 and 1.0 can arise from thresholding, a coarse integer score, insensitive workloads or genuinely similar dynamics. Inspect parameter values and predictions before claiming the mapping itself stops changing. A plateau can also be a valid finding: the less expensive model is sufficient for the tested decisions.

### 3.10 “Exact harm” requires the estimand to be stated

The fork produces an exact result **inside this simulator**, under a particular exogenous realization, horizon, continuation policy and metric. It is not automatically the action's realized closed-loop episode effect, an expectation over uncertain futures, a proof of safety, or physical-network ground truth.

Even a mathematically exact label can be the wrong research target. State these four ingredients alongside every harm metric:

`initial state + intervention + continuation policy + evaluation horizon`.

The natural causal quantity for an action is an advantage relative to a specified baseline policy. One no-op now and no-op forever are different baselines. A candidate's effect can depend on later controller actions, fault restorations and the persistence of its own actuator changes.

### 3.11 Ten seeds and many proposals are not many independent trials

Actions within a seed share faults and states; retries are also dependent. More proposals do not create more independent episodes. The suggestion to run 50–100 seeds is a practical budget, not a power analysis, and the Wilcoxon signed-rank test is not universally justified by a small or asymmetric set of paired differences.

Report per-seed paired deltas, an appropriate interval, effect size and the number of nonzero differences. Prespecify a material improvement threshold and estimate the episode count needed for it. Do not reinterpret a small rounded difference as either a proved benefit or a proved absence of benefit.

### 3.12 A blanket blocker's reported FPR exposes the population problem

By definition, a gate that rejects every non-no-op proposal has **100% false-positive rate among safe non-no-op proposals**, whenever that set is nonempty. The stub table reports 22 false positives but only 15.49% FPR; the rule table reports five false positives but 4% FPR. Those percentages therefore cannot describe the gate's propensity to reject useful eligible actions.

The denominators implied by the printed numbers are 142 and 125 safe proposals. A plausible reconstruction includes explicit no-ops and rejection-generated no-op retries as true negatives. For example, the stub's 22 safe non-no-ops plus 46 initial no-ops plus 74 no-op retries gives 142. The missing experiment harness prevents confirming that precise reconstruction; the conditional 100% result follows from the policy definition regardless.

Counting no-ops is not an arithmetic error if that population is declared. It becomes a measurement error when used to suggest low overblocking of candidate repairs. Rejection can create its own easy negative examples, artificially reducing the reported FPR. Report separate first-attempt non-no-op confusion matrices, retry behavior, harmless-neutral versus beneficial candidates, and **benefit lost through rejection**. A safe action is not necessarily a beneficial action.

## 4. Corrected comparison and research positioning

Use three separate comparisons:

1. **Architectural base:** process-plant action validation, plus model predictive safety filtering for the engineering design.
2. **Domain context:** network change validation and reproducible network troubleshooting/configuration benchmarks.
3. **Evaluation method:** action-label accuracy, preserved remediation benefit, harm prevented, intervention costs and conservative baselines on a shared local dataset.

No external paper provides a directly compatible numerical leaderboard here. Precision on seeded faulty configurations, authorization violation rate, remediation success and violation-ticks have different units, label rules and populations. Even identical metric formulas are not enough. A millisecond toy-simulator comparison with seconds of LLM-driven verification cannot support a performance victory.

The scientifically defensible claim is an implemented, explicitly scoped **experimental testbed** for studying how model imperfections affect action-screening decisions and closed-loop outcomes. It becomes stronger when it exposes where classifier accuracy, short-horizon advantage and full-episode benefit disagree. It does not need an unsupported universal indictment of guardrail evaluation or a proof that reject-all is optimal.

## 5. Access and search limitations

- arXiv abstract records and full HTML for the eight arXiv references were retrieved; publisher metadata/abstract verified the fidelity DOI.
- Primary shielding papers and proceedings were checked. The exact paired-fork/fidelity combination was not proven absent from prior work.
- The OpenReview result *Shielding Regular Safety Properties* surfaced relevant model-accuracy/horizon ablations in search, but direct access returned a browser challenge. It is not used as substantiated evidence above.
- This audit did not rerun the papers' experiments or validate their code. It verifies what the primary sources actually claim and which deductions follow from the submitted report.
