# Readiness Audit — Twin-in-the-Loop

Audit date: 2026-09-01
Scope: full repository (`docs/`, `src/twinloop/`, `tests/`, `configs/`, `scripts/`, `results/`), plus an
independent literature search. No paper draft exists (`paper/` is empty), so Part 5 is necessarily partial
and says so.

Everything below was checked against the code or measured by running it. Where I ran a probe, the numbers
are reproduced so the finding can be checked or refuted. Where I could not establish something, I say so
rather than guessing.

**Headline: NOT READY.** Three blockers, two of which are new findings from this audit (a silent
fault-revert corruption that fires in seed 22 of the 30-seed sweep, and a silent rule-agent defect that
disables one of its six remedies only in gated arms). The largest problem is not a bug at all: the only
results that show the headline effect were produced by a hard-coded stub provider, and the results
produced by a real model show no effect to measure.

---

## PART 1 — CLAIM VERIFICATION

### C1. The simulator is deterministic: identical seed produces identical episodes — **VERIFIED**

- `tests/test_sim.py::test_same_seed_identical_sequences` — two `NetworkSim(seed=42)`, 120 ticks each,
  asserts `first == second` over the full list of `TickMetrics` dataclasses. Dataclass equality compares
  every per-service and per-node float dict, so this is a bit-level assertion, not a smoke test.
- `tests/test_faults.py::test_schedule_deterministic_from_seed` and `::test_schedule_independent_of_sim_state`
  cover the fault schedule.
- `tests/test_experiment.py::test_determinism_across_sweeps` runs two full `run_sweep` calls over
  fault-driven episodes and compares `proposed_action`, `counterfactual_harm_delta` and
  `ground_truth_harmful` per proposal. That is the end-to-end version, and it does include faults.

Mechanism is sound: all randomness comes from named `SeedManager` streams (`sim/engine.py:189-198`), never
a global RNG, and `tests/test_seeding.py::test_stream_call_order_independent` pins order-independence.

Residual gap (minor): no test compares two full *fault-driven* `TickMetrics` traces directly; determinism
under faults is established indirectly through the sweep test. Cheap to close.

### C2. `fork()` produces a genuinely independent copy sharing no mutable state — **PARTIAL**

What holds, and it holds strongly. `tests/test_fork.py` is the best-tested area of the repository:

- `test_fork_continuation_identical` — 100 ticks of post-fork metrics equal.
- `test_parent_unchanged_after_child_mutation` — compares `sim.snapshot()` before and after, and
  `SimState.rng_states` is part of that snapshot, so RNG-state equality is asserted too.
- `test_fork_rng_independence` — draws 10 000 normals from every child stream, asserts the parent's next
  100 ticks match an untouched control.
- `test_fork_queue_contents_distinct_instances` — asserts `is not` identity on queued `Request` objects
  and on `in_service`.
- `test_fork_workload_mutation_isolation`; `tests/test_faults.py::test_fault_in_fork_does_not_affect_parent`.

What does not hold. The literal claim "shares no mutable state" is false. `sim/engine.py:376-379` assigns
three attributes by reference:

```python
child.config    = self.config
child.topology  = self.topology
child._injector = self._injector
```

`FaultInjector` is currently stateless — its per-episode state lives in `state.active_faults`, which *is*
deep-copied — so this is safe today. `Topology` is never mutated after `__init__` because the constructor
rebuilds every `Node`/`Link`/`Service` and calls `w.copy()` on workloads. But nothing pins this. If anyone
adds a counter or cache to `FaultInjector`, every fork silently couples to its parent and to every sibling
twin rollout, and no existing test would fail.

Verdict: the property currently holds by accident of implementation, not by construction or by test.

### C3. The agent cannot access fault ground truth — **PARTIAL (real leak found)**

What holds:

- `tests/test_telemetry.py::test_summarizer_never_leaks_fault_ground_truth` is genuinely strong: 200 ticks
  with all six fault types; asserts no fault-type name and no occurrence of "fault" in the rendered text;
  then walks the entire object graph reachable from the `Observation` asserting no `FaultEvent`,
  `FaultSchedule` or `FaultInjector` instance is reachable and that `id(sim.state.active_faults)` is absent.
- `tests/test_llm_agent.py::test_full_prompt_never_leaks_fault_ground_truth` drives 220 ticks and checks
  *every* prompt string, including tool results, for fault names, "fault", "active_faults", "cpu_reserved"
  and "latency_multiplier".
- `agent/base.py::_sanitized_state` zeroes `workloads` and `active_faults`, and deliberately overwrites
  `current_latency_ms` with `base_latency_ms`.
- Retry-feedback prompts are clean by construction: the feedback text is `TwinValidator._reason(...)`,
  which emits only "predicted higher p95 response latency" or "predicted loss of availability from dropped
  requests…". No fault vocabulary.

What does not hold. `node.status` and `link.status` are exposed verbatim to the agent through
`LLMAgent._topology()` and `LLMAgent._node_metrics()`, and those fields are written **only** by the fault
injector:

| status value | written only by | exposed via |
|---|---|---|
| `node.status == "degraded"` | `NodeCpuSaturation.apply` (`faults/catalog.py`) | `get_topology`, `get_node_metrics` |
| `node.status == "down"` | `NodeCrash.apply` | `get_topology`, `get_node_metrics` |
| `link.status == "down"` | `LinkFailure.apply` | `get_topology`, `get_link_metrics` |

Measured directly (seed 9, three injected faults):

```
tick  30: node statuses != healthy -> {'edge0': 'degraded'}   get_node_metrics(edge0).status = 'degraded'
tick  70: node statuses != healthy -> {'edge1': 'down'}
tick 110: link statuses != up      -> {'l_gw_edge3': 'down'}
```

Three of the six fault types are therefore uniquely identifiable from a single categorical field that the
injector itself writes and that exists for no other purpose. That is not a symptom the agent must
diagnose; it is a label. The leak test misses it because it greps for the *word* "fault" and for
fault-type identifiers, and `"degraded"` is neither.

The rule agent uses the same signal: `RuleAgent._pick_target` skips nodes where `node.status == "down"`.

Defensible — a real NMS does report a node as down — but it must not be described as "symptoms only", and
it should be ablated.

### C4. The twin is fault-blind — **VERIFIED**

- `twin/rollout.py` sets `fork._injector = None` when `fault_mode == FAULT_MODE_BLIND`;
  `TwinValidator._build_twin` nulls it a second time. Both twin rollouts (action and no-op) use
  `FAULT_MODE_BLIND`.
- With no injector, `state.active_faults` is inert: its only reader is `FaultInjector.apply_tick`.
  Consequence: a fault active at fork time persists unchanged for the whole horizon and a future fault
  never appears — exactly the intended semantics.
- `tests/test_twin.py::test_twin_is_not_clairvoyant` asserts both directions: `blind.metrics != real.metrics`,
  and for every tick after the fault's scheduled expiry,
  `blind.node_utilisation["edge2"] > real.node_utilisation["edge2"]` — the blind twin demonstrably fails to
  notice the fault ending.
- No other leak path: the validator receives only `sim` and the action; `obs` is accepted but unused.

### C5. Fidelity is a real, effective parameter; axes independent; scalar mapping monotonic — **PARTIAL**

Per-axis effectiveness: **verified**. `tests/test_twin.py::test_each_fidelity_axis_degrades_accuracy` turns
on each of the five axes alone, averages mean-p95 MAE against a scheduled-mode real rollout over 4 seeds,
and asserts every axis raises the error above baseline. All five axes are exercised, including
`simplify_queueing` (which swaps `_service_streams` for `_DeterministicWork`).

Monotonicity: **partial**. `test_scalar_fidelity_sweep_is_monotonic` tests three points (1.0, 0.6, 0.2)
over 6 seeds. The published sweep uses six points (0.0…1.0). Three points is not a monotonicity test of
the curve that gets plotted. See Part 2.6 for why the error-monotonicity result is close to constructed.

Documentation accuracy: `docs/decisions/fidelity_mapping.md` correctly discloses that `lag_ticks` is a
scaling of current degradation toward baseline rather than a true replay from N ticks ago. Good practice;
keep it in the paper.

### C6. Counterfactual ground truth is sound — **VERIFIED structurally, PARTIAL on soundness**

All three structural clauses hold and each has a direct test:

- Every proposal including rejected ones: `experiment/counterfactual.py::evaluate_decision` iterates the
  full `proposals` list, which `graph.py::propose` appends to on every retry.
  `tests/test_experiment.py::test_rejected_proposals_have_ground_truth` asserts rejected proposals carry a
  non-null `harm_delta`.
- Shared baseline within a decision: `noop_res` is computed once before the loop.
  `tests/test_experiment.py::test_shared_baseline_within_decision` groups by tick and asserts
  `len(set(noops)) == 1`, and first asserts at least one tick has more than one proposal, so the test
  cannot pass vacuously.
- Scheduled vs blind: `evaluate_decision` passes `FAULT_MODE_SCHEDULED`; the twin passes
  `FAULT_MODE_BLIND`. `tests/test_experiment.py::test_scheduled_differs_from_blind` asserts the two produce
  different violation-tick counts on the same state.

Where soundness is weaker. The label is a **single paired sample**. Both branches fork from the same
`pre_action_sim` and so start with identical RNG state — the right design — but any action that changes
queue occupancy or service capacity changes how many draws are consumed from `_service_streams`, so the
branches decorrelate as the horizon runs.

Measured with a paired replicate design (both branches of each replicate advanced identically, so only the
shared random path differs between replicates):

```
seed  0 scale svc0 +1 : as-implemented = +10 | 25 replicates mean=+11.7 sd=2.6 range=[+6,+18] | label flips 0/25
seed  0 restart svc0  : as-implemented =  -1 | 25 replicates mean= +1.4 sd=2.0 range=[-2, +5] | label flips 5/25
seed  1 restart svc0  : as-implemented =  +3 | 25 replicates mean= +1.7 sd=1.8 range=[-3, +5] | label flips 4/25
seed  2 restart svc0  : as-implemented =  +2 | 25 replicates mean= +1.9 sd=1.8 range=[-2, +5] | label flips 6/25
seed 15 restart svc0  : as-implemented =  +2 | 25 replicates mean= +1.5 sd=1.9 range=[-1, +5] | label flips 3/25
```

Reading: for large-effect actions (`scale_service`, true effect ≈ +11) the label is stable and the
methodology is fine. For actions whose true effect sits near `harm_threshold_ticks = 3`
(`restart_service`, true effect ≈ +1.5) the single-sample label flips in **12–24 % of replicates**. In
`results/sweep`, 4 of 27 restarts are labelled harmful — a materially uncertain set.

A real threat to the classifier analysis, and fixable: evaluate each proposal over k paired replicates and
label on the mean, or report the label with replicate-level uncertainty.

### C7. Measurement does not disturb what it measures — **VERIFIED**

`tests/test_experiment.py::test_trajectory_non_perturbation` runs the same `RunSpec` twice through
`run_single`, once with `counterfactual=True` and once with `False`, and asserts
`on_result.metrics == off_result.metrics` (full trace) **and** that the applied-action sequences match.
That is precisely the claim.

Mechanism confirms it: `fork()` clones bit-generator state via `copy.deepcopy` rather than drawing from the
parent, and `seed_manager.child(label)` derives from a hash without consuming parent entropy.
`tests/test_twin.py::test_validation_never_mutates_sim` covers the same property for the twin path across
all six action types, comparing `sim.snapshot()` including RNG state.

### C8. Actions carry honest costs; migration causes real downtime and can be net harmful — **PARTIAL**

Costs are honest and downtime is real:

- `tests/test_actions.py::test_migration_downtime_then_improvement` asserts throughput is exactly 0.0 for
  every tick of the downtime window and the drop rate exceeds 0.9, then asserts eventual improvement.
- `test_restart_clears_leak_and_leak_returns_if_fault_active`;
  `test_throttle_trades_latency_for_availability` (asserts p95 falls *and* drop rate rises);
  `test_scale_up_improves_contended_service`.
- A harmful migration is reachable and rejected in
  `tests/test_twin.py::test_rejects_harmful_migration_with_availability_reason`.

What is unsupported. **Migration harm has never been observed in any experiment.** Across every logged
result set, `migrate_service` was proposed twice in total (`results/sweep`), and both were *beneficial*
(harm deltas −15 and −23). Action mixes:

```
results/sweep      : no_op 1628, restart_service 27, scale_service 25, migrate_service 2
results/lab6b_demo : no_op  177,                     scale_service 62
results/smoke_sweep: no_op  123,                     scale_service 37
```

Observed harm rates: `scale_service` 8/25 (sweep), 32/62 (lab6b), 23/37 (smoke); `restart_service` 4/27;
`migrate_service` 0/2. The harm being measured is almost entirely **scale-up harm**, arising from the
fair-share CPU model in `NetworkSim._allocated_cpu`: adding a replica to service A raises A's own capacity
but lowers every co-hosted service's share, because `allocated = available / total_replicas_on_node`. A
defensible model — but it means the risk phenomenon the paper demonstrates is CPU-share dilution, not the
migration-downtime story the architecture leads with.

### C9. The rule baseline is fair, not a strawman — **PARTIAL, with a defect that makes it unfair**

What holds: every threshold is in `RuleAgentConfig` and justified in `docs/decisions/rule_agent_tuning.md`;
`_available()` enforces both the pending guard and a 15-tick cooldown; `_valid()` runs the semantic
validator on every candidate so the baseline cannot emit an invalid action; `_sustained_node`,
`_sustained_link` and `_sustained_p95_violation` correctly require `sustain_ticks` consecutive *ticks* from
`obs.history`.

Two defects, both measured:

**(a) Cadence mismatch on the memory rule.** `RuleAgent._record_mem` appends one sample per `decide()` call,
and `decide()` is called once per *decision*, i.e. every `decision_interval_ticks = 10` ticks — not once per
tick. So `mem_growth_window = 5`, documented as "5 ticks", is in fact 50 ticks of wall time.

```
decide EVERY tick (as documented)  : restart_service proposals = 14, first at tick 23
decide every 10 ticks (as run)     : restart_service proposals =  9, first at tick 50
```

The leak remedy fires 27 ticks late — more than double the documented latency — in every A1 and A4 run.

**(b) A retry silently disables the memory rule entirely.** `_record_mem` is called on *every* `decide()`,
including the retry calls the twin gate triggers. A retry at the same tick appends a duplicate footprint,
`recent[i+1] > recent[i]` is then false at the duplicate, and the monotonic-growth test can never pass:

```
decide EVERY tick, one retry each tick: restart_service proposals = 0   (was 14)
```

Invisible: no exception, no failing test, and a plausible-looking log in which the rule agent simply never
restarts anything. It applies **only to gated arms (A4)**, so it introduces an untracked asymmetry between
A1 and A4 — precisely the comparison A4 exists to make.

### C10. Twin tolerance and ground-truth harm threshold are independent — **PARTIAL**

Independent as configuration: yes. `TwinConfig.tolerance_margin` (default 0.0) and
`EvaluationConfig.harm_threshold_ticks` (default 3) are separate Pydantic fields, neither derived from the
other, set from different call sites (`runner.py`, `counterfactual.py`).
`tests/test_experiment.py::test_threshold_independence` pins that `harm_delta` is threshold-invariant and
that the two thresholds can disagree.

Independent as *decision rules*: no. Both are the same comparison — (violation-ticks with action) minus
(violation-ticks with no-op) over the same horizon from the same fork point, computed by the same function
`twin/rollout.py::rollout` with the same `SLOEvaluator`. They differ only in (i) `fault_mode` and (ii) the
constant compared against. At `fidelity = 1.0` difference (i) is the *only* remaining difference, and it
vanishes whenever no fault starts or ends inside the horizon.

Measured on logged gated proposals (non-`no_op` only, so `no_op`'s trivial delta-0 does not inflate it):

```
lab6b_demo  fid=1.0 : twin_diff == cf_delta EXACTLY in 14/19 proposals
smoke_sweep fid=1.0 : twin_diff == cf_delta EXACTLY in 10/11 proposals
lab6b_demo  fid=0.4 : twin_diff == cf_delta EXACTLY in  1/19 proposals
```

At perfect fidelity the twin is not predicting the label; it is recomputing it. See Part 2.

### C11. All arms face identical fault schedules for a given seed — **VERIFIED**

`runner.py::_schedule_for` builds the topology from config and calls
`FaultSchedule.generate(seed, config.fault, targets_from_topology(topology))`. The schedule is a pure
function of `(seed, FaultConfig, topology shape)` — no arm, agent or fidelity input.
`tests/test_experiment.py::test_fault_schedule_identical_across_arms` asserts five regenerations are equal
and that a different seed differs.

Stronger than claimed: `NetworkSim(..., seed=run.seed)` means the arrival, service and routing streams are
also identical across arms, so arms share the workload realisation as well as the fault sequence.

### C12. Results reproducible without re-querying any model — **VERIFIED**

`tests/test_graph.py::test_determinism_with_warm_cache` is a genuine proof, not a smoke test: it runs an
episode with a provider returning `no_op`, then runs the same episode with a provider returning
`migrate_service`, sharing the cache directory, and asserts `first.decisions == second.decisions`,
`first.metrics == second.metrics`, **and `provider2.calls == 0`**. The second provider was never consulted.
Key is `sha256(model | temperature | canonical_prompt)` (`llm/cache.py`).

Caveat that belongs in Part 6, not here: the cache lives under `results/`, which is `.gitignore`d, so the
property is true of the code and false of the distributed artefact.

### C13. The graph is arm-agnostic — **VERIFIED**

One `build_graph` function; the only arm-dependent branch is `if gate_enabled:`, which adds the `validate`
node and the conditional edge, versus a direct `propose → execute` edge. No agent type or twin is
referenced anywhere in the node bodies; the agent arrives as `runtime.agent` and is called through
`decide()`. `tests/test_graph.py::test_all_six_arms_run_end_to_end` runs six agent/validator combinations
through the identical `run_episode`; `::test_gate_disabled_never_calls_validator` asserts a spy validator
receives zero calls when gated off.

Two housekeeping divergences from `docs/architecture.md` §5.7:

- `default_arms()` returns A0–A4 only. A5 (LLM + fidelity 1.0) exists only implicitly as A3 at the top of
  the fidelity sweep.
- `configs/base.yaml` sets `arms: [A0…A5]`, but `run_sweep` never reads `config.arms` — it always calls
  `default_arms(config)` and filters by the `arm_ids` argument. That YAML key is dead config and will
  silently mislead anyone who edits it.

---

## PART 2 — THE TAUTOLOGY AND CIRCULARITY HUNT

### 2.1 Twin and ground-truth evaluator share the decision rule and the code — **REAL PROBLEM**

| stage | twin (`TwinValidator.validate`) | oracle (`evaluate_decision`) |
|---|---|---|
| starting state | `sim.fork()` at decision tick | `pre_action_sim` = `sim.fork()` at the same tick, same state |
| degradation | `apply_fidelity(twin.state, …)` | none |
| rollout engine | **`twin/rollout.py::rollout`** | **`twin/rollout.py::rollout`** (same function) |
| action application | **`actions/executor.py::execute_action`** | **same** |
| scoring | **`telemetry/slo.py::SLOEvaluator`** | **same** |
| horizon | `config.twin.horizon_ticks` | `config.twin.horizon_ticks` (**same value**) |
| fault mode | `FAULT_MODE_BLIND` | `FAULT_MODE_SCHEDULED` |
| queueing | `_DeterministicWork` if `simplify_queueing` | exponential |
| decision | `action_vt − noop_vt <= tolerance_margin` (0) | `action_vt − noop_vt > harm_threshold_ticks` (3) |

Shared modules: `twin/rollout.py`, `actions/executor.py`, `telemetry/slo.py`, `sim/engine.py::fork`.
Divergence at `fidelity = 1.0` reduces to **fault mode alone**, and that only bites when a fault starts or
ends inside the horizon.

Measured consequence (C10): at fidelity 1.0 the twin's predicted delta equals the oracle's delta *exactly*
in 14/19 and 10/11 non-`no_op` gated proposals. The reported ~93–100 % recall at fidelity 1.0 is
arithmetic, not evidence.

`docs/decisions/fidelity_mapping.md` already states that fidelity 1.0 is "the sanity-check configuration,
never the headline result", which is the right instinct — but that is not enough for a paper. The paper
must state the shared-code structure explicitly and must never present a fidelity-1.0 precision/recall
number as a result. The honest framing: *fidelity 1.0 is a unit test of the harness, and the only
informative region of the curve is fidelity < 1.*

### 2.2 Is "harmful" defined so the twin can necessarily detect it? — **REAL PROBLEM, partially**

`harmful ⇔ delta > 3`; `twin rejects ⇔ delta_twin > 0`. If `delta_twin == delta` (which holds at high
fidelity), then *every* harmful action is necessarily rejected — recall is 1.0 by construction — while
every action with a true delta of 1, 2 or 3 is necessarily a false positive. Precision at high fidelity is
capped by the tolerance/threshold gap, not by twin quality.

The observed sweep contains such a case: `results/sweep` fidelity 0.6 has one false positive with
`counterfactual_harm_delta = 2` — rejected by the twin, labelled safe by the oracle, purely because
0 < 2 ≤ 3.

Disclose, and preferably re-run the precision/recall curve at `tolerance_margin == harm_threshold_ticks` as
a sensitivity check so the reader can see how much of the FP count is definitional.

### 2.3 Do fault-generation choices make some actions predictably correct? — **DEFENSIBLE, must disclose**

Faults are drawn uniformly over type × target × start × duration × magnitude from a seeded stream, with no
coupling to the agent or action set. Node-fault targets are restricted to the four `edge` nodes — a
modelling choice, not a leak.

Two structural facts do make certain actions predictably right or wrong:

- `service_memory_leak` has almost no latency signature — memory does not enter the service-rate path in
  `NetworkSim.step` — so it is only diagnosable by watching `mem_footprint` directly, which the rule agent
  does and the LLM agent effectively cannot (the summarizer never emits memory).
- `node_crash` and `link_failure` present as *low* utilisation and *zero* latency, so the migrate rule
  (which triggers on high utilisation) can never fire for them.

Both are legitimate and `docs/decisions/rule_agent_tuning.md` already discloses the second honestly. Keep
the disclosure.

### 2.4 Are harmful-action rates an artefact of tuned cost parameters? — **NOT AS TUNED, but IS AS ATTRIBUTED**

Cost parameters (`ActionsConfig`): `migration_downtime_per_mem = 2.0`, `migration_min_downtime = 2`,
`migration_transfer_cost = 1.0`, `restart_downtime = 3`, `restart_cost = 1.0`, `replica_cap = 5`.

I found no evidence these were tuned against results — they are round numbers, they predate the experiment
commits in git history, and `docs/decisions/` contains no cost-tuning note. So the literal answer is: no.

But the harm that *is* observed does not come from these parameters at all. It comes from the fair-share
CPU model in `_allocated_cpu`, which is not in config, is not documented as a design decision, and is the
sole mechanism by which `scale_service` — responsible for 8/25, 32/62 and 23/37 of all observed harm —
causes harm. A reviewer will ask why scaling up hurts, and the answer must be in the paper.

Recommendation: add a `docs/decisions/` note on the CPU-share model, and run a sensitivity check on both
`migration_downtime_per_mem` and the share model to show the fidelity crossover is not an artefact of either.

### 2.5 Is the fidelity mapping constructed so degradation must hurt? — **DEFENSIBLE, but weaker than it looks**

`fidelity_to_config` makes all five axes strictly increasing in `d = 1 − fidelity`, and `apply_fidelity`
applies each as a strictly increasing perturbation. So *prediction error* rising monotonically with `d` is
close to a mathematical consequence, not a finding.

What is **not** constructed, and is the actual contribution, is that *validation effectiveness*
(precision/recall/net harm prevented) degrades monotonically. Error → effectiveness is not a monotone map:
a noisier twin can be systematically conservative and catch more harm. The sweep data shows this
non-monotonicity is real — precision moves non-monotonically across fidelity levels, which the review page
already flags as noise at 5 seeds.

Verdict: the *error*-monotonicity claim is circular and should be presented as a property of the mapping,
not a result. The *effectiveness* curve is the real claim and is not circular.

### 2.6 Does the prompt encode the correct answer? — **NON-ISSUE**

`agent/prompts/llm_agent_v1.txt` names the six actions and gives two pieces of general guidance: "Prefer
no_op when no action clearly helps" and "Migration in particular causes downtime that drops traffic".
Neither maps a symptom to a fault or to a correct action. No fault vocabulary appears. Ordinary operator
guidance; fine.

One second-order effect worth noting rather than fixing: the "prefer no_op" instruction plausibly
contributes to the real-model result in which 96.8 % of proposals were `no_op` (Part 4). If a reviewer asks
whether the agent was prompted into inaction, the honest answer is "possibly", and a prompt ablation would
settle it.

### 2.7 Twin and oracle share the same starting fork — **DEFENSIBLE, disclose**

`graph.py::observe` forks `pre_action_sim`; `graph.py::validate` forks `runtime.sim`. No `step()` occurs
between them, so the two paths begin from *identical* state, not merely comparable state. That is the right
design for a clean contrast, and it also means the twin is asked an easier question than a production twin
would face (a real twin syncs from telemetry and would never start from the exact live state). The
`sigma_obs` and `lag_ticks` axes exist to model precisely this, which is the correct answer — say so in the
paper.

---

## PART 3 — NOVELTY AUDIT AGAINST THE LITERATURE

Searched independently rather than checking the project's citations (there are none — no draft exists).

### The five closest works

**1. Leveraging LLM Agents and Digital Twins for Fault Handling in Process Plants — arXiv:2505.02076.**
Multi-agent workflow (monitoring, planning, action synthesis, **simulation, validation**, reprompting) over
a Digital Process Plant Twin exposing a simulation service for **pre-execution testing** of corrective
control actions, plus a Graph-RAG layer.
*This is the same architecture as this project's contribution*, including the reprompt-on-rejection loop.
Differences: process plants not networks; a single qualitative case study (pipe clogging); no counterfactual
ground truth; no fidelity study; no baselines.

**2. Aether: Network Validation Using Agentic AI and Digital Twin — arXiv:2604.18233.**
Five specialised network-operations agents plus a network digital twin for verification and testing of
network changes. Evaluated on synthetic scenarios **and past incidents from a major ISP**: 100 % error
detection, 92–96 % diagnostic coverage, 6–7 minutes per change.
*The closest work in the same domain.* Differences: it validates network changes for correctness, not
agent-proposed remediations for net SLO harm; no counterfactual harm quantification; no fidelity sweep. Its
empirical base (real ISP incidents) is considerably stronger than this project's.

**3. AI Infrastructure Sovereignty / sustainability-constrained orchestration — arXiv:2602.10900,
arXiv:2604.09705.** Describe a digital twin as an explicit **mandatory validation gate between an
optimisation agent and the execution layer**, with failed candidates returned to the agent with constraint
tightening to trigger re-optimisation. That is this project's gate-plus-retry loop, stated almost verbatim,
in datacentre/compute-network orchestration.

**4. CARE: Pre-Execution Command Verification for Shell-Executing LLM Agents — arXiv:2607.21642.**
Pre-execution verification of agent actions before they touch a real system. Different substrate (shell
commands, static/heuristic verification rather than simulation) but the same safety pattern, in the same
2026 framing.

**5. The Causal Impact of Tool Affordance on Safety Alignment in LLM Agents — arXiv:2603.20320.**
Sandbox with a "Soft World" mode (violations recorded but allowed) versus a "Hard World" mode (prohibited
calls intercepted and blocked) — structurally the same experimental device as A2-versus-A3, though harm is
defined by policy violation rather than by simulated outcome.

Enclosing context: **Toward Safe LLM Agents: A Survey of Specification, Verification and Enforcement
(arXiv:2608.14590, Aug 2026)** — the survey this work will be positioned against; and **Toward
Pre-Deployment Assurance for Enterprise AI Agents (arXiv:2606.04037)** — ontology-grounded simulation with
graduated Approved/Conditional/Rejected verdicts.

On fidelity specifically: **"Investigating the influence of fidelity on the capability of a digital twin to
detect material extrusion failures" (J. Intelligent Manufacturing, 2023)** varies six fidelity attributes
and measures detection capability — structurally the same study design as this project's fidelity sweep, in
additive manufacturing, without an agent. **"Quantitative metrics for validation and decision-making in
digital twins: a railway braking system" (Proc. Design Society)** does fidelity metrics for decision-making.

On guardrail scoring: precision/recall over blocked agent actions is already the standard evaluation idiom
(e.g. POLICYGUARD-style online interceptors reporting block rates on violating proposals).

### Does "unaddressed" still hold as of September 2026?

**No, not as stated.** "A digital twin as a mandatory validation gate between an LLM agent and a managed
network" is published, in this domain, at least twice (Aether; the sovereignty/orchestration line), and in
adjacent domains more. The 2026 preprint flow is fast and the gate pattern is now common enough to have a
survey. Any claim of the form "we are the first to place a twin between an LLM agent and the network" will
not survive review.

What I could **not** find, after targeted searching, is any work that:

- derives **counterfactual ground truth by forking the real simulator** and replaying *every* proposal,
  including the ones the gate rejected, to obtain an exact per-action harm label; and
- uses that label to score the gate **as a classifier** across a **swept twin-fidelity axis**, producing a
  crossover point at which validation stops paying for itself.

### Strongest and weakest claimed contributions

**Strongest: the counterfactual labelling methodology.** Evaluating rejected proposals is the move that
makes the gate measurable at all. Every guardrail paper I found has the same blind spot — you cannot compute
recall for a blocker without knowing what the blocked actions would have done, and the standard answer is
human annotation or policy rules. Forking a deterministic simulator gives an exact label. That is a genuine
methodological contribution and it is what the paper should lead with.

**Weakest: the gate itself.** "Twin validates LLM agent actions before they reach the network" is not new as
of 2026. Presenting it as the contribution invites immediate rejection with three citations.

### The strongest hostile-reviewer argument, and whether it succeeds

> "This is Aether/2505.02076 with a synthetic simulator instead of a real network. The architecture is the
> known simulate-before-execute pattern; the retry-on-rejection loop is in the sovereignty papers; the
> precision/recall framing is standard guardrail evaluation; the fidelity sweep replicates a 2023
> manufacturing result in a new domain. The only genuinely new element is that the twin and the oracle are
> the same simulator, which is possible only because everything is synthetic — and at fidelity 1.0 the
> authors' own numbers show the twin and the oracle computing the same value, so the method is validated
> mainly against itself."

**This argument largely succeeds against the current framing, and partly succeeds against any framing.**
The architecture half is simply correct and cannot be defended. The methodology half is answerable — the
counterfactual-labelling contribution is real and the fidelity-effectiveness curve is not circular — but only
if the paper (i) leads with methodology rather than architecture, (ii) discloses the shared-code structure
and drops fidelity-1.0 numbers from the headline, and (iii) has results from a real model that actually
exercise the gate. As of today it has none of the three.

---

## PART 4 — RESULTS READINESS

### What exists, and what produced it — stated unambiguously

| result set | episodes | ticks | provider actually used | evidence |
|---|---|---|---|---|
| `results/sweep` | 56 | 300 | **real Gemini** for A2/A3 (367 live calls, median 2.2–3.4 s) | live latencies 1132–15 698 ms |
| `results/lab6b_demo` | 18 | 120 | **scripted stub** | 203 "live" calls, median latency **0.037 ms** |
| `results/smoke_sweep` | 12 | 120 | **scripted stub** | median latency **0.036 ms** |
| `results/dashboard_cache` | — | — | **scripted stub** (818 calls) + 48 real Gemini | median 0.039 ms |
| `results/gemini_native`, `_smoke`, `_smoke2` | 2 each | 30 | real Gemini | median ~2.2 s |
| `results/progress_demo` | 3 | 60 | scripted stub (all cache hits) | — |

**A provenance defect, and it is serious.** `results/lab6b_demo/llm_calls.jsonl` and
`results/smoke_sweep/llm_calls.jsonl` record `"model": "qwen2.5:7b-instruct"` on **every** call, and no qwen
model was ever contacted — a 7B model cannot answer in 37 microseconds. The cause is
`scripts/run_experiments.py`: `--provider scripted` swaps in a local `ScriptedProvider` but leaves
`config.llm.model` at its default, and `LLMClient` writes `self.config.model` into the log. The logs
therefore assert a model that was never called. If any of this data reaches a paper as
"qwen2.5:7b-instruct results", that is a fabrication — an unintentional one, but a fabrication.

**The scripted stub is not an agent.** `ScriptedProvider` (both copies — `scripts/run_experiments.py` and
`dashboard/driver.py`) is a three-branch string matcher whose only non-`no_op` output is
`scale_service(first_violating_service, +1)`. That is why `lab6b_demo` and `smoke_sweep` contain exactly two
action types.

### The decisive problem: the real-model data has no effect to measure

Confusion matrices computed from the logs (gated proposals only):

```
results/sweep (real Gemini, 300 ticks)
   fid=0.00 : tp=0 fp=0 fn=0 tn=60     precision=n/a recall=n/a
   fid=0.25 : tp=0 fp=0 fn=0 tn=60     precision=n/a recall=n/a
   fid=0.50 : tp=0 fp=0 fn=0 tn=60     precision=n/a recall=n/a
   fid=0.60 : tp=1 fp=1 fn=1 tn=59     precision=0.50 recall=0.50      <- this row is A4 (rule agent)
   fid=0.75 : tp=0 fp=0 fn=0 tn=60     precision=n/a recall=n/a
   fid=1.00 : tp=0 fp=0 fn=0 tn=60     precision=n/a recall=n/a
```

Five of six fidelity levels are **entirely empty of positives**. The single non-degenerate row is not even
the LLM arm. The reason is in the action mix: 1628 of 1682 proposals (96.8 %) are `no_op`, and A3's applied
action mix is `{no_op: 300}` — the real model never proposed anything for the twin to reject. This is
consistent with the known failure recorded on the review page: in the recorded Gemini run, 12 of 12
decisions spent all four ReAct turns on tool calls and never emitted an action, after which
`LLMAgent.decide` falls through to `NoOp()`.

```
results/lab6b_demo (scripted stub, 120 ticks)     results/smoke_sweep (scripted stub, 120 ticks)
   fid=0.4 : tp=8  fp=1 fn=2 tn=34  P=0.89 R=0.80    fid=0.4 : tp=5  fp=1 fn=2 tn=22  P=0.83 R=0.71
   fid=0.6 : tp=0  fp=0 fn=0 tn=36  (A4, no harm)    fid=0.6 : tp=0  fp=0 fn=0 tn=24  (A4, no harm)
   fid=1.0 : tp=13 fp=1 fn=1 tn=35  P=0.93 R=0.93    fid=1.0 : tp=10 fp=0 fn=0 tn=24  P=1.00 R=1.00
```

**So: every number that shows the effect was generated by a stub that always proposes the same action, and
every number generated by a real model shows no effect.** The review page's five-seed arm comparison is in
the same category — its action mixes (`A2: scale_service 44, no_op 16`) are the stub's signature, not
Gemini's.

### What could and could not be supported today

Supportable from existing data:

- The simulator's properties: determinism, fork independence, fault signatures, action costs. All
  test-backed and reproducible.
- A *mechanism* figure: one worked decision showing observation → proposal → twin forecast → verdict →
  counterfactual. Honest as an illustration, provided the provider is named.
- Cost/overhead numbers: twin validation ~80–110 ms median versus ~2.2–3.4 s median for a live Gemini call.
  Real, measured, and a genuinely useful practicality result.

Not supportable:

- Any precision/recall or net-harm-prevented figure. The real-model matrices are empty; the stub matrices
  are not evidence about an LLM agent.
- The fidelity–effectiveness curve and its crossover point — the headline figure of the whole project.
- Any A2-versus-A3 harm-reduction claim.
- Any claim about migration harm (never observed).
- Any claim attributed to `qwen2.5:7b-instruct`.

### Degenerate and misleading metrics at current volume

- Precision and recall are **undefined** (0/0) at five of six fidelity levels in the only real-model dataset.
- `results/sweep` A1 has 12 seeds, A2/A3/A4 have 2 seeds, A0 has 30. Cross-arm comparison at these
  imbalances is not meaningful, yet `summaries.jsonl` invites exactly that.
- Effect sizes versus spread: A0 mean 330.1 violation-ticks with **sd 51.0** across 30 seeds; the A0→A3
  difference is 33.6 ticks — well inside one standard deviation. With n = 2 for A3 the 95 % CI is roughly
  ±100 ticks, about three times the effect.
- 1637 of 1682 harm deltas are exactly 0 (they are `no_op` versus `no_op`). The effective sample for the
  classifier analysis in the real-model dataset is ~26 non-zero proposals, of which 3 exceed the threshold.
- Per C6, near-threshold labels flip in 12–24 % of paired replicates, so even those 3 are uncertain.

### What must still be run

Assumptions: 300-tick episodes, decision every 10 ticks = 30 decisions/episode; measured real-model
behaviour ≈ 4 ReAct calls per decision plus retries ⇒ ~125 calls/episode (the code's own
`estimated_llm_calls = decisions × 2` under-counts by ~6×, which should be fixed before anyone budgets from
it); measured live latency median ~2.7 s.

| set | episodes | LLM calls | wall-clock (LLM-bound) | notes |
|---|---|---|---|---|
| A0, A1 (no LLM) | 30 + 30 | 0 | ~1–2 h | 30/12 already done |
| A2 × 30 seeds | 30 | ~3 750 | ~3 h | |
| A3 × 30 seeds × 6 fidelities | 180 | ~22 500 | ~17 h | dominant cost |
| A4 × 30 seeds | 30 | 0 | ~1 h | |
| **total** | **~300** | **~26 000** | **~22 h** wall-clock | cache makes reruns free |

Token cost: measured ~6 900 in / ~210 out per decision ⇒ ~207 M input and ~6.3 M output tokens for the full
set. At Flash-class pricing that is roughly of order tens of US dollars, but **do not budget from that
figure without re-deriving it against current published rates** — I did not verify pricing, and the model
identifier itself needs checking (Part 5).

**This estimate is worthless until the agent actually acts.** Running 26 000 calls of an agent that emits
`no_op` 97 % of the time reproduces the empty confusion matrix at 30× the cost. Fix the ReAct exhaustion
first, verify on 2 seeds that A3 produces a non-degenerate action mix, then commit to the sweep.

### Minimum publishable set versus ideal

**Minimum:** 30 seeds × {A0, A1, A2, A4} + 30 seeds × A3 at 4 fidelity levels {0.25, 0.5, 0.75, 1.0} = 210
episodes, ~19 000 LLM calls. Requires: a working agent, ≥ 3 replicates per counterfactual label, paired
per-seed statistics.

**Ideal:** add fidelity 0.0 and 0.6; add a per-axis ablation (5 axes × 3 levels × 30 seeds, no LLM needed if
the rule agent is the actor); add a second model to show the result is not model-specific; add a prompt
ablation for the "prefer no_op" instruction.

### Seeds and significance

30 seeds is the architecture's own target and is the right order. But the design is **paired** — every arm
sees the identical fault schedule and workload realisation for a given seed — and paired designs should be
analysed as such. Use a paired test (Wilcoxon signed-rank on per-seed violation-ticks, or a paired
bootstrap) rather than comparing arm means under independent-sample assumptions. With per-seed sd ≈ 51 but
*paired* differences likely far smaller, the paired analysis is what makes 30 seeds sufficient. Nothing in
the repository currently computes any statistic at all.

---

## PART 5 — PLAGIARISM AND ATTRIBUTION RISK

**There is no draft.** `paper/` exists and is empty; `git ls-files paper` returns nothing. No prose exists to
check for phrasing proximity to source abstracts, and there are no citations to verify. This section
reports the attribution risks already present in the artefact, and the checks that must be repeated once
text exists.

### Citations

None exist. When they do, every claim in Part 3 must be attributed. In particular, the following must be
cited rather than presented as novel: twin-as-pre-execution-validator for LLM agents (arXiv:2505.02076);
agentic-AI + network digital twin validation (arXiv:2604.18233); the validate–reject–reprompt gate
(arXiv:2602.10900); precision/recall over blocked agent actions (standard guardrail evaluation); and
fidelity-versus-detection-capability study design (J. Intelligent Manufacturing 2023).

### Borrowed methods, metrics and benchmarks requiring attribution

- **M/M/1 queue validation.** `tests/test_sim.py::test_mm1_queue_length` validates the engine against the
  closed-form `Lq = ρ²/(1−ρ)`. Standard queueing theory; attribute to a standard text (Kleinrock) if it
  appears in the paper. Not a plagiarism risk, but currently uncited.
- **Precision/recall/F1 and the confusion matrix** applied to a blocker. Standard, but framing a safety gate
  as a binary classifier scored against counterfactual labels should acknowledge the guardrail-evaluation
  literature rather than presenting it as an invention.
- **ReAct.** `LLMAgent.decide` implements a thought/tool/action loop and `LLMConfig.react_max_steps` names
  it. ReAct (Yao et al.) is **not attributed anywhere in the repository**. It must be cited.
- **LangGraph** state-machine orchestration — cited implicitly by dependency, but the paper should say the
  loop is a LangGraph `StateGraph` with a conditional edge.
- No datasets or benchmarks are used; all data is generated. No third-party code appears to have been
  copied — I found no vendored files, no attribution headers, and no code blocks whose style diverges from
  the rest of the repository.

### Licences

Verified from installed package metadata, not from memory:

| package | version | licence |
|---|---|---|
| pydantic | 2.12.5 | MIT |
| PyYAML | 6.0.3 | MIT |
| numpy | 2.4.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | 3.0.2 | BSD-3-Clause |
| matplotlib | 3.10.7 | Matplotlib licence (BSD-compatible, PSF-derived) |
| pytest | 9.0.2 | MIT |
| streamlit | 1.56.0 | Apache-2.0 |
| plotly | 6.9.0 | MIT |
| langgraph | 1.2.9 | MIT |
| langgraph-checkpoint-sqlite | 3.1.0 | MIT |

All permissive; none prohibits research use or publication. Repository licence is MIT (`LICENSE`, © 2026
Nitin Kumar Pandey) and is compatible with all of the above. Streamlit's Apache-2.0 requires the notice be
preserved if binaries are redistributed — irrelevant for a paper, relevant if an artefact bundle is shipped.

`pandas` and `matplotlib` are declared in `requirements.txt` but **imported by zero files**. Either use them
in `analysis/` or drop them; a reviewer running a dependency audit will notice.

### Two attribution risks that are already live

1. **The model-provenance defect (Part 4).** Logs claiming `qwen2.5:7b-instruct` for calls served by a local
   stub. This is the single highest-risk item in the repository for a research-integrity complaint, because
   the misattribution is already written to disk in a machine-readable field. Fix before any of this data is
   cited, and consider regenerating the affected files.
2. **The model identifier `gemini-3.6-flash`** is hardcoded in two places (`scripts/run_experiments.py`,
   `dashboard/driver.py`). I did not verify that this is a real published Google model ID. Before it appears
   in a paper, confirm the exact identifier against the provider's model list and record the API version and
   date of access — an unverifiable model name in a methods section is an easy reviewer challenge.

### Figures

No figures exist (`results/figures/` is empty and `analysis/plots.py` was never written). The Mermaid graph
in `docs/graph.mmd` is machine-generated by LangGraph's own exporter, which is fine and should be credited
as such. When figures are made, the fidelity-versus-effectiveness plot will structurally resemble the
fidelity-versus-detection-capability plots in the manufacturing literature; that is convergent design, not
copying, but cite the precedent.

---

## PART 6 — REPRODUCIBILITY AND ARTEFACT QUALITY

### Could an independent party reproduce every result from the repository alone? — **No.**

Missing, in order of severity:

1. **`results/` is in `.gitignore` and zero files under it are tracked** (`git ls-files results` → 0). Every
   log, every summary, and — critically — the entire LLM response cache exist only on the author's machine.
   The review page states the exchanges are "replayed from the content-addressed response cache committed in
   the repository". That is false: nothing under `results/` is committed. Without the cache, a third party
   cannot reproduce a single LLM-arm number without paying for a fresh sweep, and cannot reproduce them
   exactly at all if the model has changed.
2. **No analysis code.** `src/twinloop/analysis/` contains only an empty `__init__.py`. The architecture
   specifies `metrics.py` and `plots.py`. Every number quoted anywhere — the confusion matrices, the fidelity
   curve, the arm comparison — was computed by ad-hoc code outside the repository. Level 12's exit criterion
   ("figures regenerate from logs alone") is not met and cannot currently be met.
3. **No `paper/` content, no figures.**
4. **No pinned Python version or lockfile in the repo** (`uv.lock` exists but is untracked; `pyproject.toml`
   contains only pytest settings — no `[project]` table, no dependency declaration). `requirements.txt` pins
   exact versions, which helps.
5. **Two divergent implementations of the decision loop.** `agent/graph.py::_drive` (used by the experiment
   runner) and `dashboard/driver.py::run_instrumented_episode` (used by the review page, the live server and
   `compare_arms`) independently implement propose → validate → retry → execute. They are not tested against
   each other and already disagree in at least one place: the graph sets `was_applied` only when
   `not exhausted and semantic.valid`, whereas the driver sets it on approval before the semantic check. Any
   figure produced by the driver is therefore not guaranteed to match the runner's logs.

### Are configuration values, seeds and model identifiers recorded?

Mostly yes, and the schema is good. `PROPOSAL_FIELDS` (`experiment/logging.py`) carries `episode_id, arm,
seed, fidelity, tick, decision_index, proposal_index, agent_type, prompt_version`, both twin predictions,
both counterfactual outcomes, the harm delta and label, `was_applied`, `retry_index`, `exhausted`, and full
cost fields. A well-designed record.

Gaps:

- The **model identifier is wrong** for stub-provider runs (Parts 4 and 5).
- `horizon_ticks`, `tolerance_margin`, `harm_threshold_ticks` and `retry_cap` are **not** written into the
  proposal record (only `decision_interval_ticks` is). Two sweeps run with different horizons or thresholds
  produce indistinguishable logs.
- No git commit SHA, no config hash, no timestamp in any record.
- `episode_summary(**fields)` accepts arbitrary kwargs with no schema, so summary fields can drift silently
  between runs.

### Parameters hardcoded that should be in config

- `twin/fidelity.py`: `SIGMA_MAX 0.3`, `LAG_MAX 12`, `DRIFT_MAX 0.3`, `FORECAST_MAX 0.4`,
  `SIMPLIFY_BELOW 0.5` — module constants. Documented in `docs/decisions/fidelity_mapping.md`, which is
  good, but they define the x-axis of the headline figure and belong in config.
- `twin/fidelity.py::apply_fidelity`: the staleness coefficient `0.05` and cap `0.95` are inline literals.
- `twin/validator.py::_dominant_harm`: the `0.05` drop-rate margin that decides which reason string the
  agent is told — this directly shapes retry behaviour and is invisible.
- `telemetry/summarizer.py`: hot-node threshold `0.85`, elevated-link factor `1.5`.
- `sim/engine.py::build_topology`: `arrival_rate=25.0`, `cpu_demand_per_req=1.0`, `mem_footprint=5.0` are
  function defaults, not `TopologyConfig` fields — so workload intensity, the single most important driver
  of how often SLOs are breached, is not in any config file.
- `dashboard/driver.py`: `config.llm.model = "gemini-3.6-flash"` overwrites config unconditionally.
- **Dead config:** `configs/base.yaml` sets `arms: [A0…A5]` and an `agent:` block; `run_sweep` reads neither
  (`config.arms` is never accessed; the `agent:` block is superseded by `RunSpec.agent_kind`).

### Does the test suite protect the claims?

Largely yes, and it is better than most research code. 107 tests pass in ~82 s. `test_fork.py`,
`test_twin.py` and `test_experiment.py` contain assertions that would actually fail if the corresponding
property broke — full-trace equality, snapshot equality including RNG state, object-identity checks, and
cross-run projections.

Tests that look protective but are not:

- **`tests/test_faults.py::test_all_faults_revert_cleanly`** — tests one fault at a time. It passes while
  the interleaved-overlap corruption below is live.
- **`tests/test_llm_agent.py::test_full_prompt_never_leaks_fault_ground_truth`** — reads as a complete leak
  audit but only exercises `get_topology` (its `ToolThenNoopProvider` calls exactly that one tool), and only
  greps for fault-type strings and the word "fault". It does not catch `node.status == "degraded"`, which is
  a 1:1 label for one fault type and is in the prompt (C3).
- **`tests/test_experiment.py::test_determinism_across_sweeps`** — uses a `FakeProvider` that always returns
  `no_op`, so it compares two identical trivial trajectories. It cannot detect nondeterminism in any action
  path.
- **`tests/test_experiment.py::test_threshold_independence`** — its name promises the C10 property, but its
  final assertion, `verdict.approved == (action_vt − noop_vt <= 0)`, actually *confirms* that the twin uses
  the same comparison as the oracle. It documents the coupling rather than refuting it.
- **`tests/test_dashboard.py::test_figures_build`** — asserts only that Plotly figure objects have `.data`.
  It would pass on an empty or wrong plot.
- **No test module for Level 12 (analysis)** — there is nothing to test. The "one test module per level"
  framing does not hold: 13 modules cover 11 levels plus the dashboard and the live layer.

### Silent failure modes — deliberately searched for

**(1) Interleaved same-target faults corrupt state permanently. CONFIRMED, fires in a real seed.**

`FaultInjector.apply_tick` saves the pre-fault value at `apply` and restores it at `revert`. With two
overlapping faults on the same target, whichever ends first restores the value from *before both*, and
whichever ends second restores the value from *between* them. Nested overlaps happen to survive; interleaved
overlaps do not. Reproduced with two `link_degradation` events on `l_gw_edge0` (10→35, 20→45):

```
after tick 34: multiplier=6.000 loss=0.500 active=[0,1]
after tick 35: multiplier=1.000 loss=0.000 active=[1]   <- fault 1 still "active", link silently healed
after tick 45: multiplier=2.000 loss=0.200 active=[]    <- no active fault, link permanently degraded
after tick 60: multiplier=2.000 loss=0.200 active=[]
```

Not hypothetical. **Seed 22 is in the 30-seed A0 sweep** and contains two interleaved `service_memory_leak`
events on `svc3` (53→102 and 93→116):

```
after tick  92: svc3 mem_footprint=127.11  active=[0]
after tick 101: svc3 mem_footprint=168.32  active=[0,1]
after tick 102: svc3 mem_footprint=  6.53  active=[1]     <- leak silently cured mid-fault
after tick 116: svc3 mem_footprint=130.17  active=[]      <- permanent, no fault active
after tick 299: svc3 mem_footprint=130.17  active=[]

end of episode: host edge3 mem_capacity=100.0, services on it use 130.2
  -> every migrate_service to edge3 and every scale_service on edge3 is now permanently invalid
```

For the last 184 ticks of that episode the environment is corrupted and the action space is silently
narrowed, with no crash, no warning, and no failing test. Across seeds 0–29 exactly one seed (22) triggers
it — small, but it is in the data, and it is exactly what a reviewer re-running the artefact would hit.

**(2) A twin retry permanently disables the rule agent's restart rule. CONFIRMED.** See C9(b): 14 restart
proposals → **0** when one retry occurs per decision, because `_record_mem` appends a duplicate sample and
breaks strict monotonicity. Silent, and it affects only gated arms.

**(3) The memory-window cadence mismatch. CONFIRMED.** See C9(a): documented as 5 ticks, is 5 decisions
(50 ticks). Detection moves from tick 23 to tick 50.

**(4) The twin validates an action the executor may refuse.** `graph.py::validate` runs before any semantic
check, and `rollout` calls `execute_action` directly with no validation. If a proposal is semantically
invalid, the twin models it as *succeeding* (e.g. a migration to a node without memory), returns a verdict
about an action that will never happen, and `execute` then substitutes `NoOp`. The counterfactual has the
same shape — it labels an action that was never applicable. Currently masked because both agents
self-validate before returning, but nothing enforces that for a future agent, and `execute_action` would
raise `KeyError` on an unknown `service_id`.

**(5) Cooldown starts on proposal, not on application.** `RuleAgent.decide` sets
`self._last_action_tick[action.service_id] = ctx.tick` at proposal time. Under the gate, a *rejected*
proposal still locks that service for 15 ticks. A1 and A4 are therefore not the same agent under different
gating — another untracked A1/A4 asymmetry.

**(6) `estimated_llm_calls` under-counts by roughly 6×.** `runner.py::_planned_decisions × 2` ignores ReAct
steps and retries; measured is ~4 calls per decision minimum. Anyone budgeting a cloud sweep from
`--dry-run` will under-provision badly.

**(7) `LLMAgent._validate` returns `ValidationResult(True, "no context")` when no context is set.**
Fail-open. If `set_context` is ever missed, every action is silently accepted.

---

## PART 7 — VERDICT AND BLOCKERS

# VERDICT: NOT READY

Not because the engineering is weak — it is unusually good for research code, the fork/determinism/
counterfactual machinery is properly tested, and the honesty in `docs/decisions/` is better than most
published work. It is not ready because **the experiment has not produced a result.** The only dataset
generated by a real language model has an empty confusion matrix at five of six fidelity levels, and every
dataset that shows the effect was generated by a 30-line string matcher that always proposes the same
action. There is currently nothing to write a results section about.

### BLOCKERS — must be fixed before any paper text is written

**B1. The agent does not act. (Severity: fatal)**
In the real-model run, 96.8 % of proposals are `no_op` and A3's applied-action mix is `{no_op: 300}`. The
recorded cause is ReAct exhaustion — all four turns consumed by tool calls, then fall-through to `NoOp`.
*Fix:* raise `react_max_steps` (8–10), and force a decision on the final turn by injecting a "you must now
choose an action" message when `step == max_steps − 1`. Then verify on 2 seeds that A3's action mix contains
a meaningful fraction of non-`no_op` proposals **before** committing to a 22-hour sweep.

**B2. No usable results exist. (Severity: fatal)**
Re-run the minimum set from Part 4 (210 episodes, ~19 000 calls) *after* B1, B3 and B4 are fixed. Do not
cite `lab6b_demo`, `smoke_sweep`, `dashboard_cache` or `progress_demo` as agent results at all — relabel them
as harness demonstrations with the stub named explicitly.

**B3. Interleaved same-target faults corrupt the environment permanently. (Severity: high)**
Reproduced above; fires in seed 22 of the 30-seed sweep. *Fix:* make `revert` restore a value computed from
the remaining active faults rather than a snapshot taken at `apply` time — for multiplicative faults divide
out the applied factor, for additive faults subtract it, for status faults restore only when no other fault
holds the target. Add a test with interleaved (not merely nested) overlaps on the same target for every
fault type. Re-run every affected seed.

**B4. A twin retry silently disables the rule agent's restart rule; the memory window is 10× its documented
length. (Severity: high — it makes A4 an unfair comparison, and A4 is the arm that isolates the
contribution)**
*Fix:* move `_record_mem` out of `decide()` into a per-tick observation hook keyed on `obs.tick`, so a sample
is recorded once per tick and a retry cannot append a duplicate. Then either restore the documented 5-tick
window or update `docs/decisions/rule_agent_tuning.md` to state the real window. Also move the cooldown
stamp from proposal time to application time so A1 and A4 run the same agent.

**B5. The logs misattribute stub-generated calls to `qwen2.5:7b-instruct`. (Severity: high — research
integrity)**
*Fix:* set `config.llm.model = "scripted-stub"` (or similar) whenever the scripted provider is selected, in
both `scripts/run_experiments.py` and `dashboard/driver.py`; add an explicit `provider` field to
`LLMRecord`; and regenerate or clearly quarantine the affected files.

**B6. The novelty claim as currently framed is already published. (Severity: high)**
*Fix:* reframe before writing. Lead with the counterfactual-labelling methodology and the
fidelity–effectiveness curve; present the gate architecture as adopted from prior work with citations to
arXiv:2505.02076, arXiv:2604.18233 and arXiv:2602.10900.

**B7. No analysis code; `results/` is untracked. (Severity: medium-high — blocks artefact evaluation)**
*Fix:* write `analysis/metrics.py` and `analysis/plots.py` so every figure regenerates from
`proposals.jsonl` alone, add a test that regenerates a figure from a fixture log, and commit the final
`results/` logs plus the LLM cache (un-ignore them or add a `results/final/` exception). Record the git SHA
and a config hash in every summary record.

**B8. Counterfactual labels are single-sample and unstable near the threshold. (Severity: medium)**
*Fix:* evaluate each proposal over k ≥ 5 paired replicates and label on the mean, or report labels with
replicate uncertainty and exclude the ambiguous band from precision/recall. Measured flip rate for
near-threshold actions is 12–24 %.

### MUST DISCLOSE — defensible, but required in threats-to-validity

**D1. Twin and oracle share code and differ only in fault mode.**
> "The twin validator and the counterfactual oracle share the same rollout engine, executor and SLO
> evaluator, and use the same prediction horizon; they differ only in fault visibility (the twin rolls
> forward fault-blind, the oracle with the true schedule) and in the constant each compares against. At
> fidelity 1.0 these coincide whenever no fault transition falls inside the horizon: in our logs the twin's
> predicted delta equalled the counterfactual delta exactly in 24 of 30 non-trivial gated proposals.
> Fidelity 1.0 is therefore reported as a harness sanity check, not as a result, and all substantive claims
> are drawn from fidelity < 1."

**D2. Approval tolerance and harm threshold differ, which manufactures false positives.**
> "The twin rejects when predicted harm exceeds a tolerance of 0 violation-ticks, while ground truth labels
> an action harmful only above 3. Actions whose true harm lies in (0, 3] are therefore recorded as false
> positives by construction. We report this asymmetry explicitly and include a sensitivity analysis at
> matched thresholds."

**D3. Three of six fault types are identifiable from node/link status exposed to the agent.**
> "The agent's topology and node-metric tools expose node and link status. These fields are written only by
> the fault injector, so `degraded`, node `down` and link `down` uniquely identify CPU saturation, node crash
> and link failure respectively. We consider this consistent with the information a real network management
> system exposes, but note that the diagnosis task is correspondingly easier for those three fault classes
> than for memory leaks and traffic surges."

**D4. Observed harm is CPU-share dilution from scaling, not migration downtime.**
> "Although migration carries the largest modelled cost, migrations were rarely proposed and were never
> harmful in our runs. Observed harm arises almost entirely from scale-up actions under a fair-share CPU
> model in which adding a replica reduces the share available to co-hosted services."

**D5. The twin starts from the exact live state.**
> "The twin is forked from the live simulator at the decision tick, so it begins from exact rather than
> observed state. Observation noise and staleness are modelled as explicit fidelity axes rather than arising
> naturally; a deployed twin synchronising from telemetry would face additional error not captured here."

**D6. Staleness is approximated.** Already correctly documented in `docs/decisions/fidelity_mapping.md`;
carry that text into the paper verbatim.

**D7. Error monotonicity is a property of the mapping, not a finding.**
> "The scalar fidelity mapping is constructed so that every axis degrades monotonically; that prediction
> error rises with degradation is therefore expected by construction. The empirical question is whether
> *validation effectiveness* degrades monotonically, which is not implied by the mapping."

**D8. Custom simulator rather than ns-3/iFogSim.** Already argued in `docs/architecture.md` §7; keep it, and
add that the twin/oracle sharing the same engine is only possible because of it — which is both the method's
enabler and its main external-validity limit.

### SHOULD FIX — strengthens the paper, not blocking

- S1. Move `SIGMA_MAX`/`LAG_MAX`/`DRIFT_MAX`/`FORECAST_MAX`/`SIMPLIFY_BELOW`, the `0.05` staleness
  coefficient, the `0.05` drop-rate margin in `_dominant_harm`, and the summarizer's `0.85`/`1.5` thresholds
  into config.
- S2. Move `arrival_rate`, `cpu_demand_per_req`, `mem_footprint` into `TopologyConfig` — workload intensity
  should not be a function default.
- S3. Record `horizon_ticks`, `tolerance_margin`, `harm_threshold_ticks`, `retry_cap`, git SHA and a config
  hash in every proposal record.
- S4. Delete `config.arms` and the `agent:` block from `base.yaml`, or wire them up. Add A5 to
  `default_arms` or drop it from `docs/architecture.md`.
- S5. Collapse `dashboard/driver.py::run_instrumented_episode` onto `agent/graph.py::run_episode` with a
  state sink, or add a test asserting the two produce identical decisions for the same inputs.
- S6. Fix `estimated_llm_calls` to account for ReAct steps and retries.
- S7. Make `LLMAgent._validate` fail closed when no context is set.
- S8. Validate semantically *before* the twin, so the twin never scores an inapplicable action.
- S9. Extend the prompt-leak test to call all four tools and to assert on status vocabulary (`degraded`,
  `down`), plus a case with twin feedback in the prompt.
- S10. Add an interleaved-overlap test for every fault type (this is B3's regression test).
- S11. Freeze `child.topology`/`child.config` as frozen dataclasses, or deep-copy them in `fork()`, so C2
  holds by construction rather than by luck.
- S12. Drop `pandas` and `matplotlib` from `requirements.txt` or use them.
- S13. Add a `[project]` table to `pyproject.toml` and commit a lockfile.

### THE FIVE HARDEST QUESTIONS, AND WHETHER THE PROJECT CAN ANSWER THEM TODAY

**Q1. "Your twin and your ground-truth oracle are the same simulator running the same code. At perfect
fidelity, isn't your classifier just recomputing its own label?"**
**Cannot answer today.** The measurement (24/30 exact matches at fidelity 1.0) confirms the reviewer's
suspicion. The project *can* answer it after B6/D1: fidelity 1.0 is a harness check and the informative
region is fidelity < 1, where blind rollout, degraded state and simplified queueing make the twin's estimate
genuinely different — but that answer requires results at fidelity < 1 for the LLM arm, which do not exist.

**Q2. "What did a real language model actually do?"**
**Cannot answer today.** It emitted `no_op` 97 % of the time and exhausted its ReAct budget in 12 of 12
recorded decisions. The gate had nothing to gate. This is the question that decides the paper; B1 and B2
exist to make it answerable.

**Q3. "How is this different from Aether, or from the process-plant twin-validation paper?"**
**Partially answerable.** The methodological differentiator — counterfactual labelling of rejected proposals
and the fidelity-effectiveness curve — is real and, as far as I could establish, unclaimed. The architectural
differentiator does not exist. The project can answer this only after reframing (B6); the current framing
loses.

**Q4. "Your harm labels come from one simulated rollout per action. How much of your 'harmful' set is
noise?"**
**Partially answerable, honestly.** For large-effect actions the label is stable (0/25 flips for
`scale_service`, true effect ≈ +11 against sd 2.6). For near-threshold actions it is not (12–24 % flips for
`restart_service`, true effect ≈ +1.5 against a threshold of 3). The project can state this precisely
because it is now measured — but it needs B8 to make the classifier metrics defensible.

**Q5. "Would any validator do this, or does it have to be a digital twin? And does the benefit come from the
twin or from the LLM?"**
**Cannot answer today, and the instrument is broken.** A4 (rule + twin) exists precisely to answer the second
half — but B4 shows A4's agent is not the same agent as A1's (the restart rule is silently disabled by
retries, and the cooldown stamp fires on rejected proposals). Until B4 is fixed, the A1↔A4 contrast is
confounded. The first half has no arm at all: there is no cheap-static-checker baseline, and adding one (a
rule-based pre-check with no simulation) would substantially strengthen the paper.

---

*Prepared as a pre-writing readiness audit. Every claim above is traceable to a named file, a named test, or
a probe whose output is reproduced in the text. Findings I could not establish — the validity of the
`gemini-3.6-flash` identifier, current API pricing, and any comparison against a paper draft that does not
yet exist — are marked as such rather than asserted.*
