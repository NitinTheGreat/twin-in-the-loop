# Twin-in-the-Loop — System Architecture

Status: draft v1, pre-implementation
---

## 1. Purpose of this document

This document defines the system before any code is written. Every module, data contract, and experiment arm is specified here so that team members can build in parallel without colliding, and so that design questions get argued now rather than during week 10.

Nothing here is sacred, but changes should be made by editing this file (and noting the change in `docs/decisions/`), not by silently diverging in code.

---

## 2. What the system does, in one paragraph

We simulate a small edge/fog network of servers and IoT devices running services under a workload. A fault injector breaks things on a schedule. An agent observes telemetry, diagnoses the fault, and proposes a remediation action. Before that action is applied to the live network, it is executed on a *digital twin* — a separate, deliberately imperfect copy of the network — and the predicted outcome is evaluated against service-level objectives. Actions predicted to cause harm are rejected, with the reason fed back to the agent for a retry. We measure what this validation step buys us, across varying twin fidelity, against both a rule-based baseline and an unvalidated LLM agent.

---

## 3. Design principles

1. **The simulator is the ground truth, and it is deterministic.** Given a seed, an episode replays identically. Without this, counterfactual evaluation is impossible.
2. **The twin is never assumed perfect.** Fidelity is a first-class, tunable parameter, not an afterthought.
3. **The agent is swappable.** Rule-based, LLM, and null agents all implement the same interface, so experiment arms differ by one line of config.
4. **Actions are a closed, validated set.** The LLM chooses from a fixed action schema. It never emits free-form commands into the simulator.
5. **Everything is logged as structured records.** The analysis layer never re-runs the simulation; it reads logs.
6. **The LLM is isolated behind one interface.** Swapping a cloud model for a local one must not touch the simulator.

---

## 4. High-level component map

```
                    ┌────────────────────────────────────┐
                    │        ExperimentRunner            │
                    │  (episodes, arms, seeds, logging)  │
                    └───────────────┬────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐          ┌────────────────┐          ┌────────────────┐
│ FaultInjector │─────────▶│  NetworkSim    │◀─────────│    Executor    │
│  (schedules   │          │  ("real" env)  │  applies │ (applies action│
│   faults)     │          │                │  action  │  after approval│
└───────────────┘          └───────┬────────┘          └────────▲───────┘
                                   │                            │
                                   │ telemetry snapshot          │ approved
                                   ▼                            │ action
                          ┌────────────────┐                    │
                          │  Telemetry     │                    │
                          │  Collector +   │                    │
                          │  Summarizer    │                    │
                          └───────┬────────┘                    │
                                  │ observation                 │
                                  ▼                             │
                          ┌────────────────┐   proposed   ┌─────┴────────┐
                          │     Agent      │─────action──▶│ TwinValidator│
                          │ (LLM / rules)  │◀──rejection──│              │
                          └────────────────┘   + reason   └──────┬───────┘
                                                                 │ forks
                                                                 ▼
                                                        ┌────────────────┐
                                                        │  TwinSim       │
                                                        │ (degraded copy)│
                                                        └────────────────┘
```

A parallel, offline path (not shown) forks the *real* simulator to establish counterfactual ground truth for every proposed action, including rejected ones. This path never affects the live episode.

---

## 5. Module specifications

### 5.1 `sim/` — the network simulator

Discrete, tick-based. One tick represents a fixed wall-clock interval (default: 1 second of simulated time). An episode is a fixed number of ticks (default: 300).

**Entities**

| Entity | Key attributes |
|---|---|
| `Node` | id, role (edge server / device / gateway), cpu_capacity, mem_capacity, cpu_used, mem_used, status (healthy / degraded / down) |
| `Link` | id, endpoints, bandwidth, base_latency_ms, current_latency_ms, loss_rate, status |
| `Service` | id, host_node_id, cpu_demand_per_req, mem_footprint, replicas, queue, status |
| `Workload` | per-service request arrival process (Poisson or trace-driven) |

**Per-tick update order** (fixed, must not vary):
1. Apply scheduled fault events for this tick
2. Generate workload arrivals
3. Route requests over links, compute latency and loss
4. Process service queues, compute response times, drop on overflow
5. Update resource utilisation
6. Emit metrics record

**Core interface**

```python
class NetworkSim:
    def __init__(self, topology: Topology, config: SimConfig, seed: int): ...
    def step(self) -> TickMetrics: ...
    def snapshot(self) -> SimState: ...
    def restore(self, state: SimState) -> None: ...
    def fork(self) -> "NetworkSim": ...
    def apply_action(self, action: Action) -> ActionResult: ...
```

`fork()` is the backbone of both the twin and the counterfactual path. It must be a genuine deep copy with an independent RNG stream derived deterministically from the parent seed.

**Default topology (v1)**: 1 gateway, 4 edge servers, 12 IoT devices, 6 services. Small enough to run hundreds of episodes, large enough that placement decisions are non-trivial.

### 5.2 `faults/` — fault injection

Faults are declarative, scheduled, and seeded.

| Fault type | Effect | Realistic cause |
|---|---|---|
| `node_cpu_saturation` | node cpu_used pinned near capacity | runaway process |
| `node_crash` | node status → down, services unavailable | power/hardware failure |
| `link_degradation` | latency multiplied, loss increased | congestion, interference |
| `link_failure` | link status → down | cable/radio failure |
| `service_memory_leak` | mem_footprint grows per tick until OOM | software defect |
| `traffic_surge` | arrival rate multiplied for a subset of services | demand spike |

Each fault has: type, target, start_tick, duration, magnitude. A `FaultSchedule` is generated per episode from a seed, so arm A and arm C face **identical fault sequences**. This is essential for fair comparison.

### 5.3 `telemetry/` — observation layer

Two stages, deliberately separated:

- **Collector** produces a structured `Observation`: current metrics, short rolling history (last N ticks), topology summary, active SLO status.
- **Summarizer** renders that `Observation` into the compact text/JSON block handed to the LLM.

Keeping these separate means the rule-based agent consumes the structured form directly, and we can experiment with different LLM representations without touching the simulator.

The summarizer must be token-budgeted. Target under roughly 1,500 tokens per observation. Raw dumps of every metric will blow cost and degrade reasoning quality.

### 5.4 `actions/` — the action space

Closed set. The LLM selects one action and its parameters; anything outside the schema is rejected before it reaches the twin (and logged as a malformed-action event, which is itself a metric worth reporting).

| Action | Parameters | Intent |
|---|---|---|
| `migrate_service` | service_id, target_node_id | move load off a struggling node |
| `restart_service` | service_id | clear leaked memory or a stuck queue |
| `scale_service` | service_id, delta_replicas | add or remove capacity |
| `reroute_traffic` | service_id, path_hint | avoid a degraded link |
| `throttle_service` | service_id, rate_limit | shed load to protect SLOs |
| `no_op` | — | explicitly do nothing |

`no_op` matters: an agent that correctly does nothing during a transient blip is behaving well, and without this action it is forced to act harmfully.

Validation happens in three layers: schema validity → semantic validity (does the target node exist and have capacity?) → twin validation (does it actually help?).

### 5.5 `agent/` — the decision maker

```python
class Agent(Protocol):
    def decide(self, obs: Observation, feedback: Optional[TwinFeedback]) -> Action: ...
```

Three implementations:

- **`RuleAgent`** — the baseline. Threshold-driven: if node cpu > 90% for k ticks, migrate the largest service to the least loaded node; if link latency > threshold, reroute; and so on. Must be a *fair* baseline, not a strawman. Reviewers will assume you weakened it deliberately, so document its tuning.
- **`LLMAgent`** — prompt contains role, topology summary, current observation, action schema, and (on retry) the twin's rejection reason. Returns structured JSON. Temperature low, retries bounded.
- **`NullAgent`** — always `no_op`. Establishes the do-nothing floor, which is required to show that any agent helps at all.

Prompts live in `agent/prompts/` as versioned text files, never inline strings. Prompt version goes into every log record.

### 5.6 `twin/` — validation and fidelity

**Validator flow**:
1. Fork the twin's current view of the network
2. Apply the proposed action
3. Roll forward `H` ticks (default: 30) under a forecast workload
4. Evaluate predicted SLO outcome versus the predicted no-op trajectory
5. Return `TwinVerdict(approved: bool, reason: str, predicted_metrics: dict)`

Approval rule (v1): approve if predicted SLO violations over the horizon are not worse than the no-op baseline by more than a tolerance margin. Keep this simple and explicit — a complicated approval rule becomes an unexplainable confound.

**Fidelity model — the most important knob in the project.** The twin diverges from the real network along controllable axes:

| Fidelity axis | Parameter | Effect at low fidelity |
|---|---|---|
| Observation noise | `sigma_obs` | twin's starting state is a noisy read of reality |
| Staleness | `lag_ticks` | twin's state is from `lag_ticks` ago |
| Structural simplification | `simplify_queueing` | twin uses a coarser service-time model |
| Parameter drift | `drift_pct` | twin's node capacities/service profiles are off by a percentage |
| Workload forecast error | `forecast_err` | twin mispredicts future arrivals |

A single scalar `fidelity ∈ [0, 1]` maps onto these for headline plots, with the individual axes available for ablation. **`fidelity = 1.0` (perfect twin) is a sanity-check configuration, never the headline result.**

### 5.7 `experiment/` — arms, runner, logging

**Experiment arms**

| Arm | Agent | Twin validation | Purpose |
|---|---|---|---|
| A0 | Null | — | do-nothing floor |
| A1 | Rule | — | conventional baseline |
| A2 | LLM | off | unvalidated AI (the risky condition) |
| A3 | LLM | on, fidelity swept | **our system** |
| A4 | Rule | on | isolates twin benefit from LLM benefit |
| A5 | LLM | on, fidelity = 1.0 | upper bound / sanity check |

Arm A4 is easy to forget and very valuable: it answers "is the win from the LLM or from the twin?" Without it, a reviewer can claim your result is really about validation-in-general and has nothing to do with AI.

**Runner responsibilities**: iterate seeds × arms × fidelity levels, guarantee identical fault schedules across arms for a given seed, enforce timeouts and retry caps, write logs, checkpoint so a crashed run resumes.

**Logging schema** — one JSON line per decision event:

```
episode_id, seed, arm, fidelity, tick, fault_active,
observation_digest, agent_type, prompt_version,
proposed_action, action_valid, twin_verdict, twin_reason,
counterfactual_outcome, applied_action,
slo_violations_before, slo_violations_after,
llm_latency_ms, llm_tokens_in, llm_tokens_out
```

`counterfactual_outcome` is populated by the offline fork path for **every** proposed action, approved or rejected. This field is what makes the classifier analysis possible.

### 5.8 `analysis/` — metrics and figures

**Primary metrics**

| Metric | Definition | Why it matters |
|---|---|---|
| Recovery rate | fraction of faults where SLOs return to normal within the episode | headline effectiveness |
| MTTR | mean ticks from fault onset to SLO recovery | speed |
| SLO violation-ticks | total ticks in violation | cumulative user-facing harm |
| **Harmful action rate** | fraction of proposed actions that worsen SLOs (by counterfactual) | the risk being mitigated |
| **Twin precision / recall** | twin's accuracy at classifying harmful actions | the core contribution |
| Net harm prevented | harmful actions blocked minus beneficial actions wrongly blocked | honest bottom line |
| Overhead | added latency, tokens, and cost per decision | practicality |

**Headline figure**: twin precision/recall and net harm prevented, plotted against twin fidelity. The crossover point where validation stops paying for itself is the single most quotable result in the paper.

---

## 6. Repository layout

```
twin-in-the-loop/
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── .env.example
├── configs/
│   ├── base.yaml
│   ├── topology_small.yaml
│   ├── faults.yaml
│   └── experiments/
│       ├── arm_a1_rule.yaml
│       ├── arm_a2_llm_direct.yaml
│       └── arm_a3_llm_twin.yaml
├── src/twinloop/
│   ├── sim/          engine.py, node.py, link.py, service.py, workload.py, state.py
│   ├── faults/       injector.py, catalog.py, schedule.py
│   ├── telemetry/    collector.py, summarizer.py
│   ├── actions/      schema.py, validator.py, executor.py
│   ├── agent/        base.py, rule_agent.py, llm_agent.py, null_agent.py, prompts/
│   ├── twin/         validator.py, fidelity.py
│   ├── llm/          client.py, providers.py
│   ├── experiment/   runner.py, arms.py, logging.py
│   └── analysis/     metrics.py, plots.py
├── scripts/
│   ├── lab1_network_demo.py
│   ├── lab2_fault_demo.py
│   ├── lab3_diagnosis_demo.py
│   ├── lab4_proposal_demo.py
│   ├── lab5_twin_demo.py
│   ├── lab6_full_loop_demo.py
│   └── run_experiments.py
├── tests/
├── data/
├── results/
│   ├── logs/
│   └── figures/
├── paper/
└── docs/
    ├── architecture.md
    └── decisions/
```

The `scripts/lab*.py` files map one-to-one onto the six graded lab sessions. Each is a self-contained, runnable demonstration with printed output — so a lab demo is always one command, never a scramble.

---

## 7. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | team familiarity, ecosystem |
| Simulation | custom discrete-tick engine | full control over `fork()` and determinism; off-the-shelf simulators make forking and fidelity degradation painful |
| Config | YAML + Pydantic models | validated config, no magic strings |
| LLM access | one thin client wrapper | provider-swappable, enables local vs cloud comparison |
| Structured output | JSON schema enforced at parse time | prevents free-form commands reaching the executor |
| Data | pandas | log analysis |
| Plots | matplotlib | conference-standard figures |
| Tests | pytest | determinism and fork-correctness tests are non-negotiable |

Deliberately **not** using iFogSim or NS-3 in v1: both make deep-copy forking and controlled fidelity degradation awkward, and both add a large learning cost that buys us nothing for this particular research question. We note this as a limitation and a future-work item in the paper.

---

## 8. Decisions to lock before writing code

Each of these should be settled and recorded in `docs/decisions/`:

1. Tick duration and episode length
2. Topology size and shape for v1
3. The exact SLO definition (e.g. p95 response time under X ms, availability over Y%)
4. Fault catalogue and magnitude ranges
5. Twin approval rule and tolerance margin
6. Fidelity parameter ranges to sweep
7. LLM provider(s), model names, temperature, retry cap
8. Seed policy (how many seeds per arm — target at least 30 for meaningful error bars)

---

## 9. Threats to validity, and how the architecture handles them

| Threat | Anticipated question | Architectural answer |
|---|---|---|
| Perfect twin makes validation trivial | "Isn't your twin identical to reality?" | fidelity is a swept parameter; headline results use imperfect twins |
| Weak baseline | "Did you tune the rule agent fairly?" | rule agent tuning is documented and its config is in the repo |
| Benefit comes from validation, not AI | "Would any agent do this?" | arm A4 (rule + twin) isolates the effect |
| Cherry-picked faults | "Did you pick faults the LLM handles well?" | fault schedules are seed-generated and identical across arms |
| Unfair counterfactual | "How do you know an action was harmful?" | deterministic fork of the real sim gives exact ground truth |
| Cost hidden | "Is this practical?" | latency, tokens, and cost logged per decision |

---

## 10. Build order

Strictly sequential for the first three; parallel after that.

1. `sim/` with determinism tests and a working `fork()`
2. `faults/` with seeded schedules
3. `telemetry/` + `actions/` schema
4. Then in parallel: `agent/rule_agent.py` | `agent/llm_agent.py` + `llm/` | `twin/`
5. `experiment/` runner and logging
6. `analysis/` metrics and figures

Nothing downstream can be trusted until step 1's determinism tests pass. Resist the temptation to start with the LLM agent because it is the fun part.