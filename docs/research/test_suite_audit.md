# Test suite audit and hardening

Audit date: 2026-09-16. Repository: `F:\twin-in-the-loop`.

The original green suite missed real input-validation and action-lifecycle defects.
It also allowed the LLM to spend its entire four-call allowance on tools and then
silently hold. Those defects are fixed, and the tests now distinguish deliberate
holds from failed decisions. Additional regressions cover routing through crashed
nodes, historical observations, completion-time memory telemetry and duplicate IDs.

The strongest evidence is that tests failed before the fixes, and six deliberately
broken implementations subsequently failed assertion checks in temporary copies.
The final passing count alone is not the basis for the assessment.

**Verification and evidence**

| Check | Result |
|---|---|
| Baseline, before edits: `.\.venv\Scripts\python.exe -m pytest -q -W error` | **132 passed in 63.89 s** |
| First new regression group, before production changes | **44 failed, 4 passed**; [JUnit evidence](test_audit_before.xml) |
| Additional simulator boundaries, before their fixes | **6 failed, 52 passed**; [JUnit evidence](test_audit_sim_before.xml) |
| Transport/provenance development run | Four transport failures plus one test-fixture cache failure; [JUnit evidence](test_audit_transport_before.xml) |
| Final: `.\.venv\Scripts\python.exe -m pytest -q -W error --junitxml=docs/research/test_audit_final.xml` | **263 passed in 90.69 s**, zero failures/errors/skips; [JUnit evidence](test_audit_final.xml) |
| `.\.venv\Scripts\python.exe -m compileall -q src scripts demo` | Passed |
| `git diff --check` | Passed; Git emitted only normal LF/CRLF conversion notices |
| Coverage availability | `python -m coverage --version` reported `No module named coverage`; no coverage package was installed and no line/branch percentage is claimed |
| Six deliberate mutations | **6 killed, 0 survived**, unmodified control passed; [machine-readable results](test_mutation_results.json) |

The 131 additional collected cases come from parameterized tests, not 131 distinct
test functions. All 132 pre-existing cases remain. No assertions were weakened,
no confirmed defect was marked xfail, and no live LLM/network calls were used.
The worktree was already dirty; unrelated report/paper changes were preserved.
Historical Priority 1 results remain historical; this report supersedes the older
statement that LLM finalization was deferred.

The first regression run included a graph-fixture scheduling mistake (the fixture
had advanced to tick 1 while decisions occurred every 10 ticks). That test was
corrected and the entire first group rerun before any production edit. The saved
first-group XML is the corrected run. Similarly, one later dashboard assertion was
using an existing cached response instead of its controlled provider. Its cache
was isolated; that failure is **not counted as a production defect**.

**Confirmed defects and production changes**

| Defect demonstrated | Corrected behavior and relevant files |
|---|---|
| NaN and positive infinity passed throttle validation | `actions/validator.py` accepts only finite positive limits. Negative infinity, zero and negative limits are also rejected. Admission caps are bounded by actual arrivals before integer conversion. |
| Direct execution bypassed semantic rejection | `actions/executor.py` validates before mutation and returns a failed `ActionResult` for invalid actions. Tests assert complete snapshot preservation. |
| Scaling by zero was accepted | Shared validation rejects it with an explicit reason. |
| A second downtime action could start on a service already being repaired | Shared validation rejects **all** service-changing actions while an action is pending. Executor enforcement covers every agent and direct callers. Restart/migration permutations and scale/throttle/reroute attempts are tested. |
| Invalid settings failed only later during simulation | `config.py` rejects zero tick/decision intervals, absent required topology entities, negative queue capacity, nonfinite numeric values, invalid fault ranges and invalid downtime settings at construction. Related capacities, fractions, histories and budgets have appropriate bounds. |
| Gateway count was silently ignored | The v1 builder is documented as supporting exactly one gateway; other values now fail construction. This follows the single-gateway v1 design in `docs/architecture.md` and `docs/build_plan.md`, without adding a new topology design. |
| Four tool calls exhausted the LLM loop without a decision | `agent/llm_agent.py` reserves the last call for action-only finalization, preserving earlier evidence. A forbidden final tool request is rejected without executing that tool. |
| Decision failure was indistinguishable from intentional holding in downstream records | Per-proposal traces and outcome/fallback fields survive graph retries, counterfactual labeling, JSON proposal records, dashboard views and live events. |
| Provider calls/retries could consume the finalization allowance | `llm/client.py` propagates the remaining call allowance. Both HTTP adapters in `llm/providers.py` share one deadline across retries and backoff. Wrapped socket timeouts are recognized without retrying them. |
| Paths through crashed transit nodes remained operational | `sim/routing.py` excludes down nodes operationally, and the simulator checks the full ordered path when admitting traffic. Structural feasibility can still include currently failed components. Local source-equals-host empty paths remain supported. |
| Migration changed old observations' placement | `telemetry/collector.py` creates a new topology summary instead of modifying the summary already held by earlier observations. |
| Memory sensors disagreed with live state on action completion | Closing memory usage is recalculated after restart/migration completion, matching closing placement and tool-visible service memory. Processing latency, throughput and CPU still describe the tick's work. |
| Duplicate custom-topology IDs were silently collapsed into dictionaries | `Topology` rejects duplicate node, link or service IDs, including at simulator construction after topology edits. |

Action schemas remain compatible; throttle and zero-scale rejection occurs in the
shared semantic validator. Construction validation does not make mutable config
objects immutable, nor does it provide full validation of arbitrary custom
dataclass state supplied by callers.

**Four-call behavior and interpretation**

Calls 1–3 may return tools or a valid action. An earlier valid action finishes
immediately. Call 4 receives a final instruction requiring `{"action": {...}}`
with all preceding results still in the conversation. The total allowance remains
four client turns per `LLMAgent.decide` invocation. A quarter of the configured
decision time is reserved by default: with 20 seconds total, exploration is given
at most the remaining time minus 5 seconds. Entering that reserve triggers early
finalization. A late returned answer is classified as a timeout.

The exact regression fixture returns tools on responses 1–4 and a restart on
response 5. Under the old loop it fell back to an unclassified no_op. Under the
new loop, its fourth tool request is rejected and classified; response 5 is never
requested. A separate controlled provider that follows the final instruction
returns the restart on call 4. This proves the protocol change, **not** that an
actual model will obey the instruction or choose a beneficial action.

| Outcome | Meaning |
|---|---|
| `decision_produced` | A schema-valid and semantically accepted action was produced |
| `deliberate_no_op` | The model explicitly selected a valid hold |
| `final_parse_failure` | Final output could not supply an action matching the schema |
| `tool_budget_exhausted` | A tool was requested during action-only finalization |
| `validation_exhausted` | Final action was invalid or repeated an action rejected by feedback |
| `timeout` | Time ran out or the provider reported a timeout |
| `provider_budget_exhausted` | Configured call/token allowance was exhausted |
| `provider_error` | A transport error prevented a decision |

All failure outcomes execute a safe no_op and carry `fallback=true` and
`exhausted=true` in the agent trace. Those flags are false for an intentional hold.
The existing graph `exhausted` field still describes outer gate-retry exhaustion;
the new `decision_outcome`, `decision_fallback` and per-attempt traces describe the
inner decision process. `was_applied=true` can mean a safe fallback was applied;
it must not be interpreted as model success without checking the trace.

`steps` counts attempted client turns, including a turn stopped by budget checking.
Cache hits do not invoke the provider. Existing graph rejection retries may invoke
`decide` again, and transport retries may make multiple HTTP attempts inside a
provider call. This change does not redefine either as a global four-HTTP-request
limit. Aggregate legacy LLM record counts describe recorded successful/cache
responses, not every failed transport attempt; per-attempt failure traces must be
used when interpreting reliability.

**New tests grouped by subsystem**

| File | Added cases | Behavioral evidence |
|---|---:|---|
| `tests/test_boundary_validation.py` | 36 | Nonfinite/nonpositive throttle rejection; finite positive controls; zero-scale rejection; two pending action kinds against five attempted action kinds; exact once-only request loss; constructor rejection and smallest valid settings |
| `tests/test_sim_boundaries.py` | 65 | Conservation across ten loss scenarios; pending-loss snapshot/restore; exact two/three-fault windows and reversed ordering; leak restart/regrowth; ordered route failures and mesh feasibility; failed destination rollback; one-tick downtime at fault boundaries; exact memory-capacity thresholds; completion telemetry; missing data/SLO boundaries; topology size/wraparound/duplicates |
| `tests/test_llm_finalization.py` | 26 | Immediate action/hold, tool sequences, final instruction/evidence, invalid final outputs, deliberate versus fallback no_op, call limits 1–4, fake-clock reserve/overrun, provider call/token failures, rejection feedback, graph retry trace isolation, experiment JSON and both dashboard paths |
| `tests/test_provider_deadlines.py` | 4 | Both adapters' retries share a deadline and do not retry wrapped timeouts; fake opener/clock/sleep, no network or wall-clock waits |

The conservation fixture starts with exactly two queued requests and one in-service
request. It asserts `opening + generated = completed + dropped + closing` each tick,
including combined throttle/link/action losses and a zero-capacity queue. It also
asserts exact initial drops for outage/action/memory cases and finite rates in
`[0, 1]`. This is independent of how the implementation computes the rate.

Fault fixtures assert complete expected status/numeric traces, non-default baseline
restoration, cleanup of both fault dictionaries, and fork/restore isolation. CPU,
crash, link degradation/failure, traffic surge and leaks all have composition tests.
Routing tests distinguish valid unchanged reroutes (accepted as idempotent) from
invalid paths and from unavailable alternatives. Tests cover source/host identity,
order, continuity, cycles, unknown/down links and down transit nodes.

Memory fixtures distinguish exactly 100 units from 100.0001, test replicas and
colocated services, and assert placement/route/telemetry/tool agreement after a
migration. The preserved Priority 1 paired-branch regression also checks a scored
improvement of **70 violation-ticks holding versus 3 after restart** on its specified
fixture. That exact result is not a guarantee for other workloads.

**Assessment of the existing suite**

Classification labels: **E** exact behavior or independently known expectation;
**I** invariant/property; **C** self-consistency; **S** structure/smoke;
**F** controlled fake/mock. Fakes can strongly validate orchestration while providing
no evidence about real model reasoning or network behavior. Multiple labels can
apply to one file.

| Existing subsystem/tests | Classification and strengths | Weaknesses, questionable interpretations and missing cases |
|---|---|---|
| Simulator, `test_sim.py` | E: M/M/1 mean queue expectation with a stated 15% statistical tolerance. C: seeded replay. | Stability inequalities and replay can pass a consistently wrong simulator. The M/M/1 test covers one regime/seed, not all loads or network behavior. Added conservation and exact boundaries supply a different oracle. |
| Forks, `test_fork.py` | I: parent isolation, distinct queue instances, independent RNG consumption. C: continuation/restore equality. | Equality cannot establish physical correctness. `test_fork_benchmark` prints timing without a performance assertion: classify as smoke/instrumentation, not a speed guarantee. New pending-loss/composition fixtures add exact values. |
| Seeding, `test_seeding.py` | I/C: deterministic named streams, order independence and child separation. | Distinct sequences are not statistical-quality or randomness-distribution proofs. No such claim is made. |
| Faults, `test_faults.py` | E: default-state restoration. I: fault direction and parent isolation. C: seeded schedules. | Most effect tests compare window means or broad inequalities. They previously left overlap timing/order/baselines untested; exact two/three-event fixtures now cover these. |
| Actions, `test_actions.py` | E/I: no-op nonmutation, route update and replica bounds. | Migration checks only `downtime - 1` initial ticks, so alone it cannot catch early recovery. Restart/benefit inequalities are broad. Preserved exact downtime tests and new pending/config regressions address these gaps. |
| Agents, `test_agents.py` | E/I: null agent, cooldown, spike handling and valid rule actions across eight seeds. | Recovery remains fixture-dependent; determinism does not prove policy quality. No proof of optimal decisions or universal recovery. |
| Twin, `test_twin.py` | I: nonmutation and effect of tolerance. C: perfect twin/real rollout agreement and paired reproducibility. | Both branches share the simulator. Scalar fidelity monotonicity is an empirical regression on six seeds, not a theorem; a comment now makes that scope explicit. One nonmutation fixture uses an invalid reroute, so it is not evidence of successful rerouting. |
| Telemetry, `test_telemetry.py` | E: exact SLO flags/counts. I: no fault-label leakage and character budget. C: repeated summaries. | No-leakage tests cover tested object fields/strings, not arbitrary side channels. Missing completions, equality thresholds and immutable history needed the added cases. |
| LLM, `test_llm_agent.py` | E/I/F: parsing, retries, feedback, tool order, budgets and fault-blind prompts. C/F: warm-cache reproducibility. | Fake providers deliberately return known responses; no model competence is measured. No reserved-final-call or failure-provenance assertions existed. New protocol cases directly cover those failures. |
| Graph, `test_graph.py` | E/F: decision cadence, approve/reject branches and retry cap. I: nonmutation. S: JSON/checkpoint smoke. | Six short arms running proves wiring, not intervention value. Resume smoke is not full process-crash recovery. Added traces test failed decisions across actual graph retries. |
| Experiments, `test_experiment.py` | I: counterfactual nonperturbation and shared per-decision baseline. C: repeat sweeps/resume keys. F: LLM transport. | Original same-schedule-across-arms test generated the same schedule five times without running arms. It now captures schedules supplied to five actual `run_single` executions. Labels computed by the same rollout still need independent causal interpretation. |
| Dashboard, `test_dashboard.py` | S/F: figure data and Streamlit launch/click path. | No visual correctness, usability or real-model guarantee. Cache files could replace provider outputs; tests now use isolated temporary working directories. |
| Live dashboard, `test_live.py` | E/S/F: event order/counts, rejection flow, serialization and termination. C: repeated streams. | Event totals agreeing with each other is self-consistency. Cache-only miss and scripted-provider tests now start with isolated caches, removing dependence on developer files. New tests assert failure provenance in proposal/truth events. |
| Priority 1/diagnostics, `test_recovery_validity.py` | E/I: exact routes, downtime, loss counts, fault traces and 70-vs-3 objective. I/C: enumerator improvement and nonmutation. | Enumerator search is privileged and finite, and its outcomes inherit simulator assumptions. It is neither an operational agent nor proof no better repair exists outside its candidates/horizon. |

No old assertion needed weakening. The existing fidelity ordering remains valid for
its specified fixture, with its interpretation narrowed in a comment. The original
same-schedule test was strengthened, and cache fixtures were corrected. Earlier
Priority 1 changes already corrected obsolete zero-latency/route assumptions; those
were present in the 132-test baseline and were preserved.

The policy that a tick with **no completions is non-compliant even when truly idle**
is intentional in the existing brief and is now explicit in a regression. It should
be defended as an evaluation choice; passing this test does not establish that it
is the right operational SLO for every service.

**Mutation sensitivity**

Reproduce with `.\.venv\Scripts\python.exe scripts/audit_test_mutations.py`.
The script copies source/tests into a temporary directory, runs an unmodified
control, applies one mutation at a time, clears temporary bytecode and executes
focused tests. A collection/import error does not count as a kill. Source/test
hashes before and after confirm that the real worktree was not mutated.

| Deliberate regression | Test that detected it | Result |
|---|---|---|
| Missing p95 becomes zero | `test_no_completions_are_missing_not_zero_latency` | Killed |
| Migration omits route update | `test_exact_migration_probe_and_live_placement_tools` | Killed |
| Expiring crash restores health during another crash | `test_exact_overlapping_crashes_union` | Killed |
| Discarded action requests are omitted from next-tick losses | `test_pending_losses_snapshot_restore_count_exactly_once` | Killed |
| Downtime is one tick shorter | `test_exact_processing_downtime` | Killed |
| Final tool exhaustion is mislabeled deliberate no_op | `test_matched_budget_outcomes` | Killed |

These are six selected critical mutations, not an exhaustive mutation score.
No selected mutation survived; no import or collection failure was credited.

**Flakiness, scope and remaining gaps**

No observed flake occurred in the baseline, focused regression runs, mutation
controls or final suite. This is not a statistical flake-rate measurement. New
simulator fixtures use fixed seeds (primarily 23), fixed request counts or explicit
backlogs; existing analytic and policy tests remain seed-sensitive empirical
checks. Timeout tests advance fake clocks and never sleep. The dashboard cache
dependence found during development was removed from its test fixtures.

Remaining limits are material:

- No live model was evaluated. Real JSON compliance, response time, tool selection,
  repair quality, cost and end-to-end success rates remain unmeasured. Reserving a
  final call cannot force a model to comply or invent a useful action.
- Synchronous provider timeouts are cooperative socket timeouts. Retry scheduling
  is deadline-aware, but an arbitrary provider that ignores its timeout cannot be
  forcibly stopped by this loop. Late results are discarded. Hard wall-clock
  cancellation would require a cancellable transport or process boundary.
- Validation constrains supported configuration construction. Arbitrary direct
  mutation, malformed custom topology references, huge finite magnitudes causing
  overflow elsewhere and every possible custom workload are not comprehensively
  covered. Duplicate identifiers are specifically covered and rejected.
- Migration rollback keeps a consistent old route/placement if the destination
  becomes unavailable; it still lacks a dedicated asynchronous completion/failure
  event for the controller. Reservations for concurrent migrations of different
  services and changing destination capacity are not a fully modeled scheduler.
- The queue is a simplified stochastic service model. Link bandwidth contention,
  real networking protocols, persistent OOM-kill semantics and hardware behavior
  are not established by these tests. Missing/unfinished requests at episode end
  still need careful interpretation beyond per-tick accounting.
- Counterfactual labels and the twin share simulator code and coupled random
  streams. Tests prove isolation and specified bookkeeping; they do not establish
  operational predictability, a universal fidelity ordering, an optimal policy
  or an independent real-world causal oracle.
- Experiment resume identity still uses arm/seed/fidelity rather than a complete
  configuration/code digest. Full interrupted-process recovery and model/cache
  versioning remain research reproducibility work. Short-arm tests are not a new
  research sweep, and historical conclusions were not regenerated here.
- The diagnostic enumerator covers its declared candidates and finite horizons.
  Absence of a found improvement is not proof that no repair exists.

**Confidence assessment**

These are bounded qualitative judgments, not calibrated probabilities or project
completion percentages.

| Area | Assessment | Basis and limit |
|---|---|---|
| Simulator accounting | High for tested loss paths | Conservation, exact loss counts, zero-arrival/zero-cap boundaries and an action-loss mutation; not all custom workloads |
| Fault composition | High for the six modeled fault types in tested overlaps | Exact ordered traces, non-default baselines, cleanup, restarts and fork/restore; not arbitrary magnitude combinations |
| Action lifecycle | High for single-service pending/downtime rules | Shared executor guard, overlap permutations and one-/multi-tick boundaries; asynchronous outcome reporting remains limited |
| Routing | High for supported small topologies | Exact source-to-host paths, invalid routes, failed nodes/links, alternate feasibility and migration rollback; no real-network validation |
| LLM finalization | High for protocol enforcement; unmeasured for model usefulness | Four-call ceiling, action-only last turn, outcomes, mocked clocks and mutation kill; no live-model evidence |
| Experiment logging | Moderate | Failure provenance survives graph, retry, JSON and dashboard paths; resume identity and failed-transport aggregate counting still have limits |
| Overall system correctness | Moderate and scope-limited | Stronger independent behavioral evidence, but shared simulation assumptions, policy quality and research-design concerns remain |

For a faculty explanation: all tests originally passed because important inputs
and failure paths had never been asserted. Adding those assertions exposed real
bugs. After fixing them, the suite passes **and** detects six deliberate regressions.
That is stronger evidence of correctness for the tested behavior, while real-model
effectiveness and broader research claims remain separate questions.
