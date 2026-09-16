# Research memo: agent commitment, resource allocation, and remediation discipline

Research date: 2026-09-16. Scope: H1, H4, H6, production remediation practice, and adjacent code findings. This memo reports read-only inspection and in-memory probes; production code was not changed. External claims below use retrieved primary sources. Recommendations and mathematical deductions are explicitly distinguished from published findings.

## Executive findings

1. **H1 confirmed locally:** a model can spend all four allowed outputs on legitimate tools and never receive a turn to decide. This is a deterministic harness failure, independently reproduced without a live model. Published agent frameworks have several exhaustion semantics; explicit finalization is an established option, not a universal property.
2. **H4 partially confirmed:** several failures map to the same `NoOp` action and the prompt encourages holding. But no-op is already charged for continuing SLO violations during twin rollout. Zero intervention charge does not mean zero state cost. Adding an arbitrary inactivity penalty would change the question being measured.
3. **H6 partially confirmed:** noisy-neighbour harm is real, but this model implements host-local CPU reallocation, with unused allocations not reclaimed. It does not implement ordinary cluster-level horizontal capacity provisioning. Scale harm is therefore materially shaped by the model.
4. **An additional important flaw:** controller internal state changes at proposal time. A rejected RuleAgent proposal starts a cooldown despite no applied intervention, and retries append duplicate same-tick memory samples. The gate can alter the proposal generator through hidden state as well as through the simulated plant.
5. **Another structural omission:** interventions persist beyond the triggering fault, while the rule policy lacks a recovery/downscale rule. Holding preserves those changes. This is missing reconciliation with a desired operating state.

## H1: terminal decision starvation

### Exact code evidence

- `src/twinloop/agent/llm_agent.py:177` loops `range(react_max_steps)` over model outputs.
- Tool output at lines 202–211 always executes the tool and continues, including on the last iteration.
- The model receives neither total/remaining step budget nor a final-turn signal. Every decision starts with the same choice between a tool and an action (`:155`).
- If no action exists, lines 273–275 set `exhausted=True` and return `NoOp`.
- `src/twinloop/config.py:93` sets four steps; `:94` sets a 20-second soft elapsed-time guard.
- The prompt explicitly presents four tool types, six action schemas, and the instruction to prefer no-op when benefit is unclear.

**In-memory reproduction, 2026-09-16:** a deterministic fake client returns four valid topology reads followed by a valid restart. With the existing harness and otherwise identical observation/context:

| Budget | Actual client calls | Returned action | Exhausted |
|---|---:|---|---|
| 4 | 4 | `no_op` | true |
| 5 | 5 | `restart_service(svc0)` | false |

This isolates the mechanism without claiming that a fifth turn would make the real model choose a useful repair. The reported 12/12 all-tool trace is exactly the signature this mechanism predicts. A single shared budget is not intrinsically wrong: a four-turn budget can work if the fourth turn is reserved for a decision. The error is allowing exploratory transitions at a boundary where only a terminal transition can satisfy the interface contract.

### What frameworks and literature actually do

**Hugging Face smolagents:** `_run_stream` calls `_handle_max_steps_reached` after exhausting the loop; that helper calls `provide_final_answer`, which makes a separate model generation using accumulated memory. The returned run can still be marked `max_steps_error`. This is an explicit finalization attempt, not a guarantee of a semantically correct answer or safe control action. [Official source, `agents.py`, methods `_run_stream`, `_handle_max_steps_reached`, `provide_final_answer`](https://github.com/huggingface/smolagents/blob/main/src/smolagents/agents.py).

**LangGraph:** reaching the graph recursion bound normally raises `GraphRecursionError`. Official docs show a `RemainingSteps` managed value and conditional routing into a fallback/completion node before the bound. It is inaccurate to say all production frameworks automatically force an action on their last step. [Graph API: recursion limit and proactive handling](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit), [recursion error reference](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT).

**Liu et al., Budget-Aware Tool Use Enables Effective Agent Scaling**, arXiv:2511.17006, submitted 2025-11-21; current abstract records acceptance at COLM 2026. This work studies web-search agents. Its Budget Tracker exposes remaining tool budget; larger budgets alone are insufficient in its experiments. In the retrieved v1, §5.1 explicitly adds a final-answer request after the tool budget is exhausted, and §6.2 describes producing an answer from progress so far when the budget runs out. This is a closely relevant published design, not an experiment on network repairs. [Paper, v1 full text](https://arxiv.org/html/2511.17006v1), [current bibliographic record](https://arxiv.org/abs/2511.17006).

**Evidence boundary:** searches for the exact short-budget + closed remediation action set + no-op fallback combination did not locate a primary paper establishing it as a named failure mode. Budget blindness is documented; the exact combination's causal explanation here comes from source inspection and the probe. Six action types are not, by themselves, demonstrably a “large” action space. The parameterized target/path/replica choices are much larger than six discrete alternatives.

### Minimal design correction

Reserve a terminal decision call, or reserve the final call within the same total budget. Restrict the final schema to an action object, disable read-only tools for that call, state remaining budget, and validate the result. Keep distinct outcomes for deliberate hold, invalid output, tool exhaustion, timeout, and rejection exhaustion. A failure may safely execute no-op, but the scientific record must retain that it was a failure to decide. Do not force a non-no-op action.

Increasing four turns to five alone is not a durable fix: it shifts the same boundary. Compare at equal total model-call budgets when evaluating this change; a four-exploration-plus-one-finalization arm has more compute than the current four-call arm.

## H4: no-op bias versus state cost

**Confirmed:** the prompt favours no-op under ambiguity, no action after the inner loop becomes no-op, and the outer graph also defaults to no-op on rejected-action retry exhaustion (`agent/graph.py:179`). The actuator validator accepts no-op unconditionally. These create an operational bias toward holding. The investigation's separate denominator audit found that the supplied 1628/1682 aggregate pools multiple arms, including 900 no-agent A0 no-ops. Audited LLM-arm totals were A2 42/60 and A3 300/300, hence 342/360 overall. The pooled 96.8% number must not be assigned to a single model's preference; failure reasons and model/configuration groups need separate reporting.

**Refuted literally:** no-op is not free in the twin's objective. `twin/validator.py:124` rolls it forward for the full horizon, `:134` subtracts its violation-ticks from the candidate's, and `:136` approves only if the difference meets tolerance. A continuing outage therefore charges the no-op branch. `actions/executor.py:36` returns zero *intervention* charge; intervention charges are separately logged. The gate objective does not add the numeric `ActionResult.cost` field as an optimization term; it sees only consequences reflected in SLO rollout.

The appropriate decision-theoretic objective is state cost plus intervention cost, such as `J = E[sum_t loss(state_t) + action_charge_t]`; any cost for delayed recovery should come from the actual service objective, backlog, resource pressure, or recovery-time target. Inventing a penalty for the action label `no_op` encourages unsupported actions and biases the evaluation. This equation is a proposed formulation, not a claim about current implementation.

The gate asks whether an offered action is worse than continued operation. It does not choose the best available repair or ensure that the system returns to a target state. Kubernetes's documented controller pattern repeatedly compares current and desired state, makes changes, and reports actual state for other controllers. This supports a desired-state recovery controller around the proposal/filter interface. [Kubernetes controller pattern](https://kubernetes.io/docs/concepts/architecture/controller/).

**Important qualification:** staying still can be optimal when all presently available interventions have negative net value. Higher action frequency is not a valid success criterion; completion of an explicit decision and realized recovery value are separate measurements.

## H6: what “scale” actually means

### Exact mathematical meaning

Let `C` be available CPU on a host, `r_i` replicas of service i, `R=sum_j r_j`, and `d_i` CPU demand per request. `engine.py:214` allocates `C/R` per replica, and `service.py:46` multiplies this by the service's replica count. Thus:

`CPU_i = C r_i/R`, and `mu_i = C r_i/(R d_i)`.

After adding `delta` replicas to service i:

- Target CPU is `C (r_i+delta)/(R+delta)`.
- Every neighbour's CPU is multiplied by `R/(R+delta)`.
- Total host CPU capacity stays C.
- When i is the only service on its host, scale-up gives it **no additional CPU at all**.
- All colocated service replicas count, even when a service is down or has no runnable work. Unused allocations are not redistributed within `process_queue`.

The local schema has one `host_node_id` per service, not one placement per replica. `scale_service` changes a count immediately; it has no scheduling, new-node admission, startup, readiness, per-replica placement, or independent per-replica failure states. `process_queue` aggregates all replica capacity into one accelerated server (`in_service` is a single request). The action is therefore much closer to changing a service's CPU weight than adding independently scheduled replicas.

### Fresh in-memory resource probe

The default colocated pair `svc0`/`svc4` starts with 50/50 CPU. Scaling `svc0` from one to two replicas changes that to 66.67/33.33. Marking `svc4` down leaves that allocation unchanged. Removing the neighbour, then scaling `svc0` again, leaves its CPU at 100 before and after. These numerical results directly exercise `NetworkSim._allocated_cpu`, not just the algebra.

### Comparison to real Linux/Kubernetes

| Mechanism | Real documented behaviour | Difference relevant here |
|---|---|---|
| Kubernetes CPU requests | Scheduler uses requests for node fit; runtime CPU weighting matters under contention; spare CPU may be used above a request. | This action has no CPU request or fit check; its semantic validator checks replica cap and host memory only. |
| Linux cgroup weights | CPU is proportionally divided among active child groups; idle children do not consume an allocation. | The simulator counts down and idle services and cannot lend their unused capacity. |
| CPU limits / CFS bandwidth | Quota bounds CPU time per period and throttles a group after quota exhaustion. | The simulator has no independent quota, period, burst, or throttling state. |
| Horizontal scale | Creates more Pods; scheduling and available infrastructure determine where they run. | Every extra replica remains on the same host and total host CPU stays fixed. |

Kubernetes documents resource requests, scheduler fit, resource use above requests, and CPU limit enforcement through throttling. [Resource management for Pods and containers](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). Linux documents weighted distribution as work-conserving among active children, and `cpu.weight` versus `cpu.max`. [cgroup v2, resource distribution and CPU controller](https://docs.kernel.org/admin-guide/cgroup-v2.html). Its CFS documentation specifies quota/period enforcement and the exported throttling statistics. [CFS bandwidth control](https://docs.kernel.org/scheduler/sched-bwc.html).

**Conclusion:** redistribution and noisy-neighbour harm are credible under shared-host contention. The claim that every real scale-up proportionally harms every neighbour is false. The magnitude and dominance of scale harm here cannot be generalized without testing at least a work-conserving allocation model and a scheduler-aware capacity model. Alternatively keep the model but name the action and experiment honestly as host-local resource reallocation. Do not tune away contention just to manufacture a positive result.

## Production remediation discipline and H7

Kubernetes's HorizontalPodAutoscaler illustrates concrete mechanisms: desired/current metric ratio; tolerances around the setpoint; rate limits on replica changes; stabilization windows; and conservative treatment of missing metrics and not-yet-ready Pods. Startup/readiness timing is explicitly considered before using CPU measurements. These are stateful temporal decisions. Their exact production durations should not be copied into a one-second-tick lab; the mechanisms should be scaled to action delay and measurement timing. [HPA algorithm and scaling behaviour](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/).

Liveness and readiness answer different questions. A failed liveness probe can trigger restart; an unready Pod stops receiving service traffic. These supply reachability/ability-to-serve signals that high CPU and successful-request latency cannot replace. [Kubernetes liveness, readiness and startup probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/).

Google SRE describes overload feedback where a failed replica adds load to remaining replicas and can trigger a cascade. It recommends managing overload and recovery rather than assuming that a locally plausible intervention is globally beneficial. This supports evaluating cohost and downstream effects, and shows that protective actions can themselves sustain failure. [Addressing cascading failures](https://sre.google/sre-book/addressing-cascading-failures/). Its monitoring chapter treats latency, traffic, errors, and saturation as distinct signals and distinguishes successful from failed request latency; an absence of successful responses is not evidence of low end-user latency. [Monitoring distributed systems](https://sre.google/sre-book/monitoring-distributed-systems/).

**H7 should be partial, not wholesale confirmed:** RuleAgent already has sustained-condition checks, a 15-tick cooldown, and an in-flight-effect guard. The replica cap implements a basic actuator bound. There is no integral controller, so “integral windup” is only an analogy. No measured oscillation was supplied; absence of a stability proof does not prove instability. The concrete omissions are shared action lifecycle, actual-outcome feedback, reversible recovery/reconciliation policy, and budget-aware termination.

## Additional local findings to weigh heavily

### 1. A rejected proposal changes the baseline's future decisions

`RuleAgent.decide` writes `_last_action_tick[action.service_id] = ctx.tick` immediately when selecting a proposal (`rule_agent.py:53`). The graph validates afterward (`graph.py:166`) and executes afterward (`:190`). Thus a gate rejection starts the same cooldown as a successful action. The rule agent ignores its `feedback` argument, and all later same-target rules are suppressed by `_available` until the cooldown expires.

`_record_mem` is also called on every `decide`, including retries at the same simulator tick; these duplicates pollute a sequence tested for strictly increasing memory. Its log calls `mem_growth_window` “ticks,” but it is sampled once per decision/retry, unlike `_sustained_p95_violation`, which uses tick-level telemetry history. At a ten-tick decision interval, five nominal memory observations cover roughly forty ticks absent retries.

**Implication:** gate-on versus gate-off can alter hidden policy state in addition to plant state. The remedy is to record observations once per tick and update execution cooldown from accepted/applied/completed action acknowledgements. If a proposal suppression cooldown is wanted, model it as a separate event and document it. The reported stub retry-all-no-op result remains a separate stub behaviour; this code finding concerns the rule baseline.

### 2. No reconciliation of persistent interventions

Scaling permanently changes `service.replicas`; throttling permanently changes `service.rate_limit`; rerouting permanently changes a route. RuleAgent's scale delta is positive and there is no downscale/recovery rule. `NoOp` leaves these interventions intact. The schema allows negative scale deltas and a larger throttle rate, so undo is not wholly impossible, but the policy never seeks it and there is no explicit remove-throttle operation.

Transient injected faults can disappear while intervention side effects continue. A persistent bad allocation can therefore dominate the episode even if a short local lookahead once approved it. This is a concrete explanation to test before concluding that the workload merely lacks beneficial faults.

### 3. Agents have unequal information and temporal state

LLM tools expose actual `node.status` and `link.status` (`llm_agent.py:59, :73, :86, :123`), so crashes/failures are not globally unobservable through the complete tool interface. Memory footprint is omitted from every LLM tool and the summary, while the rule agent reads it directly. Availability/traffic symptoms can prompt tool diagnosis even if successful-request latency is zero. State observability must be assessed on the entire sensor/tool interface, not on just the initial summary.

The LLM rebuilds a fresh two-message conversation for each decision. It remembers the immediately rejected proposal during a retry, but does not receive a structured record of applied action, completion time, expected settling period, and observed post-action result. Its `_validate` uses current state but does not enforce the RuleAgent's pending-effect/cooldown discipline.

### 4. The elapsed-time budget is not a hard deadline

`react_timeout_seconds=20` is checked before each `client.complete`; the individual client timeout defaults to 30 seconds. A call starting just before the guard can overrun the remaining budget. This is a routine deadline-propagation issue; a reserved terminal phase also needs reserved time/token budget, not just a reserved counter slot. This was established by source inspection, not by spending time on a live timeout experiment.

## Proposed verification sequence

1. Separate policy-chosen hold from failed decision. Run live prompt/harness ablations at equal total call budgets; report completion rate first, useful proposal rate second.
2. Verify action semantics with isolated resource cases: single host/single service; two runnable services; idle neighbour; failed neighbour; spare-node placement. Report total CPU and every service's capacity.
3. Move controller memory/cooldown updates to their correct lifecycle events, and compare gate arms with the same observation and proposed/applied/completed event semantics.
4. Add explicit recovery scenarios where resource settings must be restored after fault clearance. Measure lingering post-fault loss separately.
5. Keep production-policy changes separate from fixed-proposal gate classification experiments. Otherwise improved gate metrics may reflect changed proposal distributions and state visitation.

These changes make the existing hypotheses testable. They do not establish that an LLM, a twin gate, or any particular fidelity level is superior before the experiments are run.
