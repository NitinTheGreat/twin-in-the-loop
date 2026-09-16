# Control, diagnosis, and model-based validation research

Research date: 2026-09-16. Scope: H2, H3, H5, H7, plus relevant corrections to the supplied limitations report. This memo separates literature results from inferences about this repository. Direct links below were retrieved through web search/open; access limitations are stated.

## Conclusions for the main report

1. **H2: partially confirmed.** The leak lacks a passive diagnostic signal in the LLM's effective observations. That is an observation/diagnosis interface defect. However, “you cannot control a state you cannot observe” is too broad, and the system has not received a formal global observability proof. More seriously, memory growth does not itself damage the scored SLO, and the scheduled leak continues after restart. Observability repair alone cannot create a beneficial SLO repair.
2. **H3: partially confirmed, with two refutations.** Short-horizon evaluation can miss repair payback; a persistence-only forecast creates forecast error at fault boundaries. Neither implies a universal Nyquist/dead-time law, a universally pessimistic action ranking, or an impossibility of using one fixed horizon.
3. **H5: confirmed as a capability limitation, not a universal architectural error.** A gate may legitimately be isolated from an independent proposer when gate quality is the research object. That gate does not become a competent remediation controller simply because it rejects harm. Planning through the model and predictive safety filters are established alternatives; each changes what the experiment measures.
4. **H7: partially confirmed; not the primary explanation for no-op collapse.** Explicit diagnosis, an achievable restoration objective, candidate search, action completion tracking, and uncertainty treatment are missing or incomplete. The supplied rule policy already has persistence/cooldown. No evidence establishes oscillation or integral windup as the current failure mechanism.

## 1. H2: use the correct control concepts

**Source result.** Observability asks whether the initial state can be reconstructed from input/output history. It does not require directly measuring every state. Detectability is weaker: asymptotically stable unobservable modes can permit a convergent state estimator. See Richard Murray's *Feedback Systems* supplement, “State Estimation,” §5.1–5.2, especially printed p. 62; the author-hosted PDF is indexed with the relevant passage, although direct web retrieval timed out. [Murray, State Estimation](https://www.cds.caltech.edu/~murray/books/AM05/pdf/lst-estimation_30Oct2020.pdf). The chapter's public summary also gives the finite-history definition and linear observability rank condition. [Åström and Murray, Output Feedback](https://www.cds.caltech.edu/~murray/FBS/Output_Feedback.html).

**Source result.** Fault diagnosability is a different question: can observed behavior establish that a fault occurred? Sampath et al. explicitly distinguish diagnosis, observability, and invertibility, and establish diagnosability conditions for discrete-event systems. Bibliography and abstract verified; full IEEE text was not exposed by the browser. [Sampath et al., “Diagnosability of discrete-event systems,” IEEE TAC 40(9), 1555–1575, 1995, DOI 10.1109/9.412626](https://ieeexplore.ieee.org/document/412626/). An accessible later primary paper states the distinction particularly clearly in its introduction: diagnosis is online assignment of normal/faulty/uncertain, whereas diagnosability is an offline determination of whether finite observations suffice. [Cabasino, Giua, and Seatzu, “Diagnosability of Discrete-Event Systems Using Labeled Petri Nets,” introduction, 2014](https://doi.org/10.1109/TASE.2013.2289360).

**Application/inference.** Three separate obligations are needed here:

- **Detection:** distinguish “something relevant changed” from ordinary workload variation.
- **Isolation / action selection:** distinguish cases needing incompatible actions. A controller can sometimes act correctly without uniquely naming the fault, if one action works for all plausible states.
- **Remediability under the measured objective:** there must exist an allowed action or sequence that improves the SLO outcome, within available resource and timing constraints.

An action list does not prove controllability or remediability. A `restart_service` symbol is not proof that restart stops the fault process, or that the fault carries SLO cost. Conversely, all hidden state need not be reconstructable to maintain output performance. The strongest actionable claim is **insufficient information for choosing certain repairs**, followed by a distinct **fault–actuator–objective mismatch**.

### A defensible local indistinguishability argument

Consider two otherwise identical simulator states, with the same requests, CPU, queues, routes, random streams, and future exogenous events except for service memory growth. Under no-op, if memory influences neither service rate nor the emitted metrics, both produce the same LLM-visible history. No passive classifier of that history can distinguish them. This is a proof about that observation channel and passive continuation, not a blanket proof for every possible active experiment. Migration downtime and placement checks depend on memory, so carefully chosen actions may reveal memory indirectly. Such costly probing is a poor substitute for emitting a memory metric, but prevents overstating formal global unobservability.

The root audit additionally finds that memory growth has no direct SLO effect and restart does not cancel the injector's continued `on_tick` growth. Both should be addressed before presenting leak remediation as an achievable benchmark objective.

### Fault-to-signal map: what the current description can and cannot justify

This is an engineering diagnosis map, not a proven classifier. Use full tool responses and their status fields when available; the initial summary is a lossy projection of them.

| Fault | Potential visible evidence | Ambiguity / missing evidence | Remedy condition |
|---|---|---|---|
| CPU saturation | High CPU, growing queue, high delay, reduced throughput | Can resemble offered-load surge without offered-arrival rate and demand/resource information | Migration needs a healthy host with spare resources; local replicas do not add host CPU |
| Traffic surge | Increased demand, queue growth, CPU saturation | Completed throughput is not offered traffic; successful throughput can flatten during overload | Extra effective capacity, feasible relocation, or explicit policy tradeoff for shedding |
| Node crash | Zero completed throughput, drops, affected cohosted services, node-down status if available | Low utilization is compatible with inactivity; zero successful-request latency is not health | A failover path/host must exist; a utilization threshold is the wrong predicate |
| Link degradation | Route-related delay and loss, elevated link metrics | Aggregate service delay can resemble queueing; topology plus per-link information helps | A genuinely healthier feasible route must exist |
| Link failure | Complete route loss / throughput collapse, link-down status if available | Summary may collapse failure into no completed samples; can resemble host crash at service level | Alternative connectivity must exist in the actual graph |
| Memory leak | None in the current passive LLM channel for otherwise identical states | Healthy versus leaking states can have identical SLO histories | Emit service memory trend and model a relevant failure consequence; ensure the repair interrupts the fault mechanism |

Important: the claim that node/link status is structurally unavailable must be checked against the read-only tools. A high-utilization-only rule being unable to fire is a rule coverage defect, not proof that the fault is unobservable through all tools.

## 2. H3: timing, payback, and forecast uncertainty

**Source result.** MathWorks' MPC engineering guidance recommends choosing a control interval first, then a prediction horizon, normally holding the horizon fixed while tuning other parameters. The horizon should anticipate relevant constraints and desired response; delays impose a lower bound on possible response. The rough interval guideline is 10–25% of desired closed-loop response time, followed by simulation checks. These are engineering guidelines, not a theorem relating the sampling interval directly to actuator dead time. [MathWorks, “Choose Sample Time and Horizons,” Sample Time / Prediction Horizon / Control Horizon](https://www.mathworks.com/help/mpc/ug/choosing-sample-time-and-horizons.html).

**Application/inference.** This system has three different clocks: simulator integration at one tick, action selection every ten ticks, and prediction of H simulator ticks. Do not silently treat H as H control decisions. The evaluator rolls one committed action forward; it does not optimize a sequence of future decisions. Therefore it resembles a one-action finite-horizon counterfactual evaluator more closely than full MPC.

A simple payback calculation clarifies what a short horizon misses. Suppose an action causes an incremental cost of `c` service-violation-ticks during downtime `d`, then saves `b` service-violation-ticks per tick while the fault would otherwise remain. Let `R` be the remaining actionable fault duration. An illustrative approximation is:

`delta(H, R) = c - b * max(0, min(H, R) - d)`.

Ignoring other effects, useful remediation requires `min(H, R) > d + c/b`. “H exceeds the downtime” is necessary for observing post-repair benefit in this example, but insufficient for observing **net payback**. In this repository, migration is not just dead time: it immediately causes a harmful transient and only later possible benefit. Calling it pure dead time obscures that cost.

### Refute the fixed-sign persistence claim

If the model assumes a fault persists and migration escapes it, the no-op branch accumulates continuing damage while the action branch recovers. If the real fault would expire quickly, the model **overvalues** migration: optimistic about intervention. If an action moves work toward a region assumed to stay degraded but which actually recovers, the model can undervalue that action. Fault starts, recovery, queue transients, resource interactions, and thresholded costs allow either direction. Absolute pessimism about future network conditions does not establish pessimism about the difference `cost(action) - cost(no_op)`.

Likewise, a higher chance of fault boundaries at larger H does not prove monotonic worsening of recall. Different H changes which benefits and harms fall inside the evaluation window; the root audit also finds the true-label horizon changes with the twin horizon. The existing sweep combines prediction changes with a changing classification target and changing visited states. It does not isolate a causal law of horizon versus accuracy.

### A fixed horizon can work

A fixed H can cover all action transients/payback periods in a specified operating regime, or work with an appropriate terminal value and backup continuation. Heterogeneous actuator delays do not mathematically require one horizon per action. Action-specific horizons are an option, but raw cumulative costs over different durations are not comparable unless a common terminal/evaluation convention is retained.

**Source result.** Rawlings, Mayne, and Diehl give finite-horizon MPC, stability conditions, terminal costs/sets, discrete actuators, and robust/stochastic MPC in one treatment. Relevant locations in the verified sixth printing are §§2.2–2.4 (pp. 91–130), §2.6 (p. 144 onward, conditions for omitting a terminal constraint), §2.9 (discrete actuators), and Chapter 3 (uncertainty). Terminal conditions are a design route with assumptions, not a universal requirement for every controller. [Rawlings, Mayne, Diehl, *Model Predictive Control: Theory, Computation, and Design*, 2nd ed., sixth printing, 2026](https://sites.engineering.ucsb.edu/~jbraw/mpc/MPC-book-2nd-edition-6th-printing.pdf).

**Minimum useful experiment.** Freeze an independently selected label horizon and continuation policy. Sweep only predictor H on the same state–action panel. Include long-horizon or episode outcomes for delayed and persistent effects. Log delta error and decision-margin errors, split by action and actual fault-boundary type. Model uncertain residual fault life from permitted history or run scenarios over plausible durations; exposing the test schedule is privileged information, not fault prediction.

## 3. H5: a rejection mechanism is not a recovery policy

**Source result.** Simplex separates an advanced controller from a simple reliable safety controller, with takeover before failure. Its 1996 architectural report explicitly allows omitting the safety controller only when the plant is inherently fail-safe. The original architecture therefore does not assume “reject the advanced action and do nothing” is generally safe. See printed pp. 3–4, especially the analytic-redundancy discussion. [Rivera et al., *An Architectural Description of the Simplex Architecture*, CMU/SEI-96-TR-006](https://www.sei.cmu.edu/documents/1146/1996_005_001_16463.pdf).

**Source result.** Wabersich and Zeilinger's predictive safety filter either passes a proposed input or modifies it; it constructs trajectories toward a terminal safe set, includes model uncertainty, and keeps a backup continuation if a new optimization cannot be completed feasibly. See §4.1, Eq. (5), Assumption 4.2, and Algorithm 1; the later robust formulation adds uncertainty assumptions. [Wabersich and Zeilinger, “A predictive safety filter for learning-based control of constrained nonlinear dynamical systems,” Automatica, 2021; preprint 1812.05506](https://arxiv.org/html/1812.05506).

**Application/inference.** This project's approval predicate is relative predicted non-worsening. It is weaker than invariant constraint satisfaction: both action and no-op can violate SLO throughout, and equality still passes. No-op is not a verified recovery policy. The gate has no obligation to produce a repair that exists, recover before a deadline, or keep the state within a recoverable region. Correctly rejecting harm and restoring service are separate properties. Calling every fallback “safe” assumes precisely the property the controller needs to establish.

A capable gate also cannot approve an action never proposed. The observed empty action stream therefore starves gate evaluation before fidelity can matter. Broad control additions do not repair that interface.

### Two legitimate research architectures

**Gate experiment:** freeze independent state–candidate examples with both beneficial and harmful actions; compare twin, heuristics, and constants on those identical candidates. Separately test outcomes of complete policies. This retains a clean assessment of validation predictions.

**Remediation-controller experiment:** let a candidate generator enumerate a bounded set; use the model to rank actions or short sequences; execute one action and replan from new observations; provide a documented recovery fallback. With this small discrete action family, exhaustive parameter-bounded candidate evaluation is a valuable non-LLM planning baseline.

**Source precedent.** POMCP uses a black-box simulator during online tree search under partial observability. It samples possible underlying states from a belief state, explores action/observation histories, and selects an action. This is direct evidence that planner access to a simulator is standard, while also showing that a clone of inaccessible true state is not equivalent to belief-state planning. See §2.2 and §3, and the algorithm. [Silver and Veness, “Monte-Carlo Planning in Large POMDPs,” NeurIPS 2010](https://papers.neurips.cc/paper_files/paper/2010/file/edfbe1afcf9246bb0d40eb4d8027d90f-Paper.pdf).

**Methodology tradeoff.** Exposing the same imperfect twin to the planner is valid if declared. It measures the combined planner/model/filter policy and permits the proposal distribution to depend on fidelity. It cannot then isolate the gate by comparing aggregate confusion matrices across different trajectories. Preserve a fixed-candidate experiment, equalize model-query/compute budgets for controller comparisons, prohibit oracle/test-schedule access, and evaluate against independent held-out dynamics or faults. Optimizing against the same model that judges approval can exploit its errors; approval is then not independent evidence of real benefit.

## 4. Missing continuation: why the “oracle” is not an upper bound

**Application/inference from the root code audit.** If the so-called oracle tests one proposal against no-op over H, then follows no future policy in those branches, it answers a local question with perfect simulator information. It does not find the minimum episode cost over allowed action sequences. Nor does it know the eventual consequence of the actual controller's subsequent decisions.

Thus all of the following may happen without contradicting perfect local prediction:

- An approved action is locally neutral but leaves a persistently harmful replica configuration after H.
- An action changes the next proposal, enabling a later mistake.
- A useful two-stage repair has an individually costly first stage and is rejected.
- An action benefits H but hurts the episode remainder.

Consequently, a reject-all policy beating the local oracle gate by 0.1 service-ticks is not evidence of beating an optimal policy, and is not enough to prove prediction intrinsically cannot help. Also, an action portfolio having negative **average** value does not make reject-all optimal: a selective policy can accept its positive subset. The relevant upper bound requires optimal continuation under stated information/action restrictions, or an explicitly limited finite-horizon candidate oracle with a correspondingly limited claim. MPC and dynamic programming distinguish precisely these local-value and continuation-value objects; see Rawlings et al. §§1.3.2–1.3.3 and 2.3 in the book linked above.

## 5. H7: discriminate missing mechanisms from irrelevant analogies

| Mechanism | Assessment here | Practical implication |
|---|---|---|
| Persistence and cooldown | Already present in the supplied rule baseline | Do not list as globally absent; assess LLM/wrapper coverage separately |
| Hysteresis and switching penalties | Potentially useful around thresholds; not established cause of a near-all-no-op trace | Add only if logs show toggling/churn or repeated actions before effects settle |
| Action completion state | Essential when delayed actions can overlap new decisions | Track requested, accepted, in progress, completed, and failed states; assess actual post-action outcomes |
| Resource feasibility / actuator bounds | Relevant to scaling on a saturated host and placement constraints | Reject infeasible resource assumptions before simulation; reason about every affected service |
| Anti-windup | A specific mechanism for controllers with integrating state and actuator saturation | No integrator has been identified; do not diagnose literal integral windup merely from retries or scale requests |
| Restoration objective / recovery controller | Absent from a pure relative-cost veto | State what progress is required and under which faults/resources it is achievable |
| Stability proof | Classical linear proof does not directly apply to this stochastic hybrid simulator and threshold SLO count | Specify a useful stability/recovery property first; use bounded scenario analysis and appropriate formal abstractions where justified |

The architecture should guarantee a completed **decision**, not force a non-no-op action. If every available action is worse than waiting, no-op is the correct decision. Inventing an action bonus or no-op penalty can make the action histogram look healthy while corrupting the operational objective.

## 6. Model fidelity: evaluate decision-relevant error

**Source result.** Su, Hicks, and Nassehi vary six image/rendering attributes and find that lower-fidelity digital twins can match higher-fidelity failure detection for the two printing-failure classes studied. This directly refutes a universal monotonic equation between overall fidelity and task utility. Abstract and publication metadata verified; full article was subscription-only. Published online June 2023, issue June 2024. [“Investigating the influence of fidelity on the capability of a digital twin to detect material extrusion failures,” J. Intelligent Manufacturing 35, 2263–2276](https://link.springer.com/article/10.1007/s10845-023-02144-x).

**Source result.** MBPO analyzes the tradeoff between useful model rollout and model-induced bias, then empirically uses short model rollouts branched from real states. Its learned-model setting differs from deliberately degraded simulation; it supports testing model error against rollout length, not automatically transferring a numeric H. See §§4–5 and experimental analysis. [Janner et al., “When to Trust Your Model: Model-Based Policy Optimization,” NeurIPS 2019](https://papers.neurips.cc/paper/9416-when-to-trust-your-model-model-based-policy-optimization.pdf).

**Source result.** Value-aware model learning explicitly incorporates the decision problem when learning model transitions, rather than treating all prediction error as equally important. [Farahmand, “Iterative Value-Aware Model Learning,” NeurIPS 2018](https://papers.neurips.cc/paper_files/paper/2018/hash/7a2347d96752880e3d58d72e9813cc14-Abstract.html).

**Application/inference.** A fidelity scalar here perturbs several axes at once; it cannot identify which is necessary for sound action ranking. Measure both branch-cost error and delta error. With true delta `D` and gate threshold `tau`, only model error crossing the margin `D - tau` changes a decision. Large common-mode errors may cancel; tiny errors near the threshold may flip approval. Therefore a flat metric interval can result from unchanged decision signs or sparse relevant candidates even when continuous fidelity parameters differ. It does not prove the parameter mapping has saturated.

The strongest defensible contribution is a controlled analysis of counterfactual **decision errors** and downstream outcomes under specified simulator assumptions. Novelty of rejected-action labels, superiority to other papers, and applicability to production all require separate evidence.
