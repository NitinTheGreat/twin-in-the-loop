# Priority 1: corrected measurement and recovery opportunities

The six fixes in the attached brief are implemented. The ReAct terminal-turn / `no_op`
fallback change (Priority 2) is deliberately deferred as requested. No live LLM calls
were used, and the privileged search is not imported by any agent or experimental arm.

## Operational semantics

- Migration completes at the end of its last unavailable processing tick and updates
  placement and the ordered client-to-destination route together. If the destination
  goes down or loses connectivity during transfer, the old placement and route are
  retained. Telemetry carries the closing placement into the collector. Route
  validation requires the actual client source, contiguous ordered links, and the
  current host as the final endpoint; cycles and failed links are rejected.
- Faults use one baseline per affected field and compose all active contributions.
  CPU reservations add, capped at 98% of capacity; link loss adds, capped at 1;
  latency and arrival-rate multipliers multiply. Down status overrides degradation.
  Active leaks add their accumulated bytes. Expiration removes only that event's
  contribution. Restart clears accumulated leak bytes; active leaks resume on the
  following processing tick. Baselines and contributions survive snapshot/fork.
- Every request discarded by restart or migration is counted immediately and
  reported in the next tick. Crash/pressure losses include in-service requests.
  Per-service arrivals, completions, drops and outstanding requests are emitted.
  The accounting identity is `opening outstanding + arrivals = completed + dropped
  + closing outstanding`, with intervening action losses included in the opening
  population until their next report. Drop rate divides losses by that same at-risk
  population, so it remains in [0,1] and reports losses on zero-arrival ticks.
- A tick without completions has JSON `null` latency and is non-compliant, including
  an idle service with no arrivals, as required by the brief. Summaries, tools,
  dashboard charts and demos handle missing samples. Chart averages use observed
  latencies only and leave entirely missing intervals empty.
- Configured downtime now means exactly that many unavailable processing ticks:
  three for the default restart and ten for a five-unit-memory migration. This fixes
  the off-by-one rather than preserving it as a convention.
- Aggregate memory above host capacity makes all requests on that host fail until
  pressure is relieved. This is a hard-capacity-pressure model, not a persistent
  OOM-kill model. Restart reclaims memory but does not remove the leak cause.
  Node memory utilization and per-service memory are exposed to LLM tools.
- `has_alternative_path` distinguishes structural alternatives from operational
  alternatives. Counterfactual proposal records include per-service reroute
  feasibility for reroute proposals and relevant link faults. Episode summaries
  also retain link-fault scenario feasibility under initial placement, even when
  counterfactual logging is disabled. Impossible detours use the separate
  `topology-infeasible` category. `TopologyConfig.redundant_links` defaults to false;
  enabling it adds an edge mesh and second client attachments.
- Scaling retains its existing meaning: replica weights redistribute a fixed
  host's CPU; this patch does not add physical compute capacity.

## Diagnostic evidence

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/audit_fix1_demo.py --output docs/research/audit_fix1_results.json
```

The demo uses seed 0, the unchanged default topology/workload/SLO/action settings,
one fault at tick 0 lasting 80 ticks, and a 60-tick evaluation horizon. Decisions
occur at tick 1, except the pre-pressure memory-leak decision at tick 40. Values
are total service-violation-ticks, not elapsed outage ticks.

| Fault | Magnitude | Hold | Best | Benefit | Best available candidate |
|---|---:|---:|---:|---:|---|
| CPU saturation on edge0 | 4 | 145 | 95 | 50 | Migrate svc0 to gw0 |
| Node crash on edge0 | 1 | 145 | 95 | 50 | Migrate svc0 to gw0 |
| Link degradation on l_gw_edge0 | 3 | 145 | 95 | 50 | Migrate svc0 to gw0 |
| Link failure on l_gw_edge0 | 1 | 145 | 95 | 50 | Migrate svc0 to gw0 |
| Memory leak on svc0 | 2 | 107 | 55 | 52 | Restart svc0 |
| Traffic surge on svc0 | 3 | 94 | 79 | 15 | Migrate svc0 to gw0 |

**Every tested fault type has a genuine beneficial candidate on this fixture.**
This supports proceeding to Priority 2, without claiming that an observation-limited
controller can already find these candidates. The current action contract permits
gateway placement, so gw0 is legitimately included; a future edge-only contract
would require rerunning this search. Link detours remain topology-infeasible even
when relocation is useful. Traffic-surge improvement is partial and includes
relieving the colocated service.

The search enumerates all valid destinations, replica counts, and simple operational
paths, plus throttle fractions 0.25/0.5/0.75/1 of current demand, restart and hold.
The default demo searches single actions followed by holding. `--depth 2` searches
all feasible pairs at a ten-tick second-decision offset and is more expensive.
A small regression fixture verifies a two-action recovery improves loss from 12
(hold) to 7 (best single action) to 3 (best pair). These are candidate-set results,
not an optimal-policy proof or a guarantee across seeds or episode horizons.

An isolated memory regression uses a loose latency threshold to measure only the
availability consequence: pre-pressure restart reduces a 40-tick loss from 70
to 3 service-violation-ticks, including the full restart downtime. The table above
uses the default SLO, and independently confirms benefit under that objective.

## Existing-test corrections and verification

The original suite passed 107 tests before edits. The reroute mutation test previously
accepted svc2 traffic from dev0; it now uses svc2's actual client dev2 and an actual
alternative route on the opt-in redundant topology. The fidelity test's aggregate
latency helper now excludes missing completion samples and asserts that each
compared tick has at least one observed latency. It no longer assumes every service
emits a numeric latency during an outage. No other existing assertions were changed.

New regressions cover the exact migration, missing-client-link, overlapping-crash,
and zero-arrival-backlog probes; leak overlap and restart; composition across fault
types; request conservation, full downtime, memory sensing and scored benefit;
reroute feasibility/logging; and diagnostic search isolation, negative controls
and action sequences. Historical investigation artifacts are preserved; their
old measurements are not overwritten by the corrected results.

Final verification: **132 passed in 59.34 seconds** (107 existing + 25 new).
The six-case diagnostic demo and original investigation probes both completed.
Corrected probe output is archived in `audit_fix1_probe_results.json`; the original
`audit_probe_results.json` remains unchanged. `compileall` and `git diff --check`
also passed.
