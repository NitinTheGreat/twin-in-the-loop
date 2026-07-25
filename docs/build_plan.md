# Build Plan — Twelve Levels

Each level is a single Claude Code session. Each has a hard exit criterion: if the exit test does not pass, the level is not done and the next level does not start.

Rules that apply to every level:

- Read `docs/architecture.md` first. It is the contract.
- Code contains no comments. Names and structure carry the meaning.
- Do not build anything belonging to a later level, even if it seems convenient.
- Every level ends with passing tests and a commit.

---

## The twelve levels at a glance

| # | Level | Builds | Exit criterion | Lab demo |
|---|---|---|---|---|
| 1 | Scaffold | repo structure, config system, seed management, test harness | `pytest` green on a trivial determinism test | — |
| 2 | Core simulator | nodes, links, services, workload, tick loop, metrics | 300-tick run produces stable metrics; queueing sanity test passes | Lab 1 |
| 3 | Fork and snapshot | `snapshot`, `restore`, `fork` with independent RNG streams | fork-independence and parent-immutability tests pass | Lab 1 |
| 4 | Fault injection | fault catalogue, seeded schedules, injector | identical seed produces identical fault sequence; faults visibly move metrics | Lab 2 |
| 5 | Telemetry and SLO | collector, summarizer, SLO evaluation | observation renders under token budget; SLO violations detected correctly | Lab 3 |
| 6 | Actions | schema, semantic validator, executor with migration cost | every action type applies correctly; invalid actions rejected and logged | Lab 4 |
| 7 | Baseline agents | rule agent, null agent | rule agent recovers from at least one fault type end to end | Lab 4 |
| 8 | LLM layer and agent | provider abstraction, structured output, tracing, ReAct agent | agent emits schema-valid actions on real telemetry; traces captured | Lab 3, 4 |
| 9 | Twin and fidelity | validator, approval rule, five fidelity axes | twin approves helpful actions and rejects harmful ones at high fidelity | Lab 5 |
| 10 | LangGraph orchestration | state graph, conditional edges, retry cap, checkpointing | full loop runs end to end; graph diagram exports | Lab 6 |
| 11 | Experiment runner | arms, seed sweep, counterfactual fork path, structured logging | all six arms run across seeds; logs contain counterfactual outcomes | Lab 6 |
| 12 | Analysis | metrics, confusion matrix, fidelity sweep, figures | every paper figure regenerates from logs with one command | — |

Optional level 13 is the live dashboard. Build it only after level 12 produces real numbers.

---

## Level specifications

### Level 1 — Scaffold
Repository structure, dependency pinning, a Pydantic-based config system reading YAML, a seed manager that derives independent child streams from one master seed, structured logging setup, and pytest configuration. No simulation logic at all.

**Exit:** a test that constructs two seed managers from the same master seed and asserts their derived streams produce identical sequences, while different masters produce different ones.

### Level 2 — Core simulator
Node, Link, Service, and Workload entities. The tick loop with its fixed six-stage update order. Queue processing, utilisation tracking, latency computation, and per-tick metric emission. No faults, no forking.

**Exit:** a 300-tick run under steady Poisson load produces stable utilisation and latency. A test compares measured mean queue length against the M/M/1 analytical prediction within tolerance.

### Level 3 — Fork and snapshot
`snapshot()` capturing complete state including RNG, `restore()`, and `fork()` producing a fully independent copy with a deterministically derived child seed.

**Exit:** three tests. Fork then run both 100 ticks with no intervention, assert identical metrics. Fork, perturb the child, assert the parent is bitwise unchanged. Fork twice from the same parent, assert the two children are identical to each other.

### Level 4 — Fault injection
The six-fault catalogue, a `FaultSchedule` generated deterministically from a seed, and the injector that applies scheduled events at the correct tick.

**Exit:** identical seeds produce identical schedules. A demo script shows each fault type visibly degrading the relevant metric, then recovering when the fault expires.

### Level 5 — Telemetry and SLO
The collector producing a structured `Observation` with rolling history, the SLO evaluator, and the summarizer rendering observations into a token-budgeted text block.

**Exit:** summarizer output stays under the configured token budget on the largest realistic observation. SLO violation detection matches hand-computed expectations on a fixture.

### Level 6 — Actions
Action schema for the six action types, three-layer validation, and the executor including modelled migration downtime and transfer cost.

**Exit:** each action type applies with the correct effect. Semantically invalid actions, such as migrating to a node without capacity, are rejected before execution and counted.

### Level 7 — Baseline agents
The rule agent with documented, tuned thresholds, and the null agent. Both implement the shared `Agent` protocol.

**Exit:** the rule agent recovers from a CPU saturation fault end to end. Its tuning parameters live in config, not in code.

### Level 8 — LLM layer and agent
Provider abstraction over cloud and local backends, structured output enforcement with bounded retries, per-call tracing capturing latency and tokens, and the ReAct agent with telemetry tools.

**Exit:** the agent produces schema-valid actions from real observations across several fault types, with every call traced.

### Level 9 — Twin and fidelity
The twin validator implementing sync, fork, apply, roll forward, and compare against the no-op trajectory. The five fidelity axes and the scalar that maps onto them.

**Exit:** at fidelity 1.0 the twin's prediction matches the real outcome exactly. At degraded fidelity, predictions diverge in the expected direction and magnitude.

### Level 10 — LangGraph orchestration
The state graph wiring observe, diagnose, propose, validate, and execute, with the conditional rejection edge, retry cap, fallback to no-op, and SQLite checkpointing.

**Exit:** a full episode runs end to end with faults, rejections, and recoveries. The graph diagram exports to `docs/`.

### Level 11 — Experiment runner
Arm definitions, the seed sweep, the offline counterfactual fork path evaluating every proposed action including rejected ones, structured JSONL logging, and resumable checkpointing.

**Exit:** all six arms run across at least five seeds. Every decision record contains a populated counterfactual outcome.

### Level 12 — Analysis
Metric computation, the twin confusion matrix, the fidelity sweep, significance testing, and figure generation.

**Exit:** one command regenerates every figure from logged runs without re-querying any model.

---

## Ready-to-send prompts

### Prompt for Level 1

```
Read docs/architecture.md in full before writing anything.

Build Level 1 of the build plan: the scaffold. Nothing else.

Create:
- The full directory structure described in section 6 of the architecture doc, with
  empty __init__.py files in every package. Do not create placeholder implementations.
- requirements.txt with pinned versions for: pydantic, pyyaml, numpy, pandas,
  matplotlib, pytest. Nothing else yet.
- src/twinloop/config.py: Pydantic models for SimConfig, TopologyConfig, FaultConfig,
  TwinConfig, AgentConfig, ExperimentConfig, and a load_config(path) that reads YAML
  and validates. Every field needs an explicit type and a default where sensible.
- src/twinloop/seeding.py: a SeedManager class taking a master seed. It exposes
  stream(name) returning a numpy Generator derived deterministically from the master
  seed and the name, and child(label) returning a new SeedManager for forked
  components. Derivation must be pure and reproducible, not dependent on call order.
- src/twinloop/logging_setup.py: structured logging configuration writing JSONL.
- configs/base.yaml and configs/topology_small.yaml matching the architecture doc's
  default topology of 1 gateway, 4 edge servers, 12 devices, 6 services.
- .gitignore covering Python, .env, results/, and __pycache__.
- .env.example containing only LLM_API_KEY=
- pytest.ini or pyproject pytest config.

Tests in tests/test_seeding.py:
- Two SeedManagers with the same master produce identical sequences from the same
  stream name.
- Different stream names from the same master produce different sequences.
- Different masters produce different sequences.
- stream(name) called twice returns generators at the same starting state,
  independent of any other stream having been drawn from in between.

Constraints:
- No comments anywhere in the code.
- No simulation logic. No entities. No tick loop.
- Do not install or import anything not in requirements.txt.

Run pytest and confirm green before finishing.
```

### Prompt for Level 2

```
Read docs/architecture.md, then read src/twinloop/config.py and src/twinloop/seeding.py
to see the existing interfaces. Follow them exactly.

Build Level 2: the core simulator. No faults, no forking, no agents.

Create in src/twinloop/sim/:
- node.py: Node with id, role, cpu_capacity, mem_capacity, cpu_used, mem_used, status.
- link.py: Link with id, endpoints, bandwidth, base_latency_ms, current_latency_ms,
  loss_rate, status.
- service.py: Service with id, host_node_id, cpu_demand_per_req, mem_footprint,
  replicas, queue, status. Queue processing with a service rate derived from allocated
  CPU, and overflow dropping at a configured queue cap.
- workload.py: request arrival generation. Implement Poisson arrivals now, behind an
  interface that a trace-driven generator can implement later.
- metrics.py: TickMetrics carrying per-service p50 and p95 response time, throughput,
  drop rate, per-node utilisation, and per-link latency.
- state.py: SimState as a plain dataclass holding everything mutable.
- engine.py: NetworkSim with __init__(topology, config, seed) and step() -> TickMetrics.

The step() method must execute exactly this order and nothing else:
1. workload arrivals generated
2. requests routed over links, applying latency and loss
3. service queues processed, response times computed, overflow dropped
4. node resource utilisation updated
5. link state updated
6. TickMetrics emitted

Randomness must come only from SeedManager streams, never from numpy global state or
the random module.

Tests in tests/test_sim.py:
- A 300-tick run under steady load produces utilisation and p95 latency that stabilise,
  asserted as bounded variance over the final 100 ticks.
- Same seed run twice produces identical metric sequences.
- Queueing sanity: under Poisson arrivals to a single service with known arrival and
  service rates and no contention, measured mean queue length is within 15 percent of
  the M/M/1 analytical prediction L = rho^2 / (1 - rho). Use a long run to reduce noise.

Also create scripts/lab1_network_demo.py: builds the small topology, runs 120 ticks,
prints a readable per-tick table of node utilisation and service p95 latency, and
prints a summary at the end. This is a graded lab demo, so the output must be legible
to someone who has not seen the code.

Constraints:
- No comments anywhere.
- No fork, snapshot, or restore. That is Level 3.
- No fault logic. That is Level 4.

Run pytest and scripts/lab1_network_demo.py, confirm both work, before finishing.
```

### Prompt for Level 3

```
Read docs/architecture.md section 5.1, then read src/twinloop/sim/ in full.

Build Level 3: forking and snapshotting. This level is load-bearing for every result
in the paper. Correctness matters more than speed.

Add to src/twinloop/sim/engine.py:
- snapshot() -> SimState: a complete deep copy of all mutable state, including the
  full internal state of every RNG stream.
- restore(state: SimState) -> None: replaces current state entirely.
- fork() -> NetworkSim: returns a new NetworkSim that is an exact independent copy.
  Its SeedManager must be derived from the parent's via child(), using a fork counter
  so that repeated forks from the same parent at the same tick produce identical
  children, but forks at different ticks do not collide.

The fork must share nothing mutable with its parent. Audit every attribute: lists,
dicts, dataclass instances, numpy arrays, and generator state.

Tests in tests/test_fork.py, all of which must pass:
- Fork at tick 50, run parent and child 100 more ticks with no intervention, assert
  their metric sequences are identical.
- Fork at tick 50, mutate the child heavily by dropping a node and running 50 ticks,
  then assert the parent's state is bitwise identical to a snapshot taken before the
  fork was created.
- Fork twice from the same parent at the same tick, run both 100 ticks, assert the two
  children are identical to each other.
- Snapshot, run 100 ticks, restore, run 100 ticks again, assert the two 100-tick metric
  sequences are identical.
- Explicitly assert that the child's RNG produces a different sequence from the parent's
  once both have advanced, proving the streams are independent rather than shared.

Add a benchmark in tests/test_fork.py printing the wall-clock cost of one fork on the
small topology. Do not assert on it; we need the number for the overhead table later.

Constraints:
- No comments anywhere.
- Do not modify the step() update order.
- If deep copying proves ambiguous for any attribute, prefer explicit reconstruction
  over copy.deepcopy so the behaviour is obvious and auditable.

Run pytest and confirm all fork tests pass before finishing.
```

---

## Working agreement for the team

One person owns each level end to end, including its tests. Level ownership rotates so nobody becomes the only person who understands a subsystem.

Levels 1 through 6 are strictly sequential. From level 7 onward, levels 7, 8, and 9 can proceed in parallel if the interfaces from levels 5 and 6 are frozen. Levels 10 through 12 are sequential again.

No level merges to main without its exit test passing. This is the only rule that prevents the project quietly rotting from a broken foundation.