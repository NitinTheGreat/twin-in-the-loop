# Lab 6 — IoT Integration, Testing and Performance Evaluation

**Project:** Twin-in-the-Loop — an agent proposes network repairs, a deliberately imperfect
digital twin vets them before they are applied.
**Objective:** Validate the developed prototype under different operating conditions and
demonstrate its IoT capabilities.
**Build-plan coverage:** Levels 10–11 (this lab); carries forward Level 9 / Lab 5.
**Date of runs:** 2026-09-09. All figures below were produced by the runs recorded in this
document; nothing is estimated, copied from an earlier draft, or rounded from memory.

---

## 0. What the testbed actually is — read this before the results

Every number in this report is real and reproducible, but the reader must know what it is a
measurement *of*. Four honest limits govern the whole document:

| Claim a reader might assume | What is actually true here |
|---|---|
| Physical sensors and actuators | **Simulated.** A deterministic discrete-tick network simulator (`src/twinloop/sim/engine.py`). There is no hardware. "Sensor" = a per-tick metric emitter; "actuator" = a validated control action mutating simulator state. |
| MQTT / Firebase / ThingSpeak / Blynk / Node-RED | **Not used.** The uplink is HTTP/1.1 with Server-Sent Events over a stdlib `ThreadingHTTPServer`, bound to 127.0.0.1. Section 4 measures that real transport rather than claiming a broker the project does not have. |
| Energy consumption in joules | **Not measurable** on this testbed. CPU-seconds are reported instead and are labelled a *proxy*, never as energy. |
| "LLM agent" results | The A2/A3 arms are driven by a **scripted stub**, not a language model. They are labelled stub-driven in every table. The one real-model arm is discussed under deviations (§7). |

The topology under test throughout: **1 gateway, 4 edge nodes, 12 devices, 6 services,
17 links**. SLO: **p95 latency ≤ 600 ms**, **availability ≥ 99 %**, at-risk band opening at
0.85 × 600 = 510 ms. Episode length 120 ticks, decision interval 10 ticks, twin horizon
20 ticks, harm threshold 3 violation-ticks.

---

## 1. Test Cases

Eight cases, covering normal, boundary and abnormal conditions (minimum required: five).
Harness: `scratchpad/lab6_testcases.py`. **Verdicts are computed from the captured data, not
hardcoded** — an earlier version of this harness printed a fixed "PASS" string while reading a
10-tick rolling window instead of the full run, and was rewritten for exactly that reason.

| ID | Class | Test case | Input / condition | Expected result |
|---|---|---|---|---|
| TC-1 | Normal | Steady-state telemetry acquisition | 120 ticks, no faults injected | Telemetry emitted every tick, no packet loss, SLO largely met |
| TC-2 | Normal | Fault detection + autonomous recovery | `node_cpu_saturation` on edge0, ticks 20–79, rule controller active | Fault detected from symptoms alone; remedy issued within 5 ticks; no invalid commands |
| TC-3 | Boundary | SLO threshold exactly at 600 ms | p95 swept: 509.0, 510.0, 599.9, 600.0, 600.1 ms | Compliant at ≤ 600.0; violation above; at-risk flag opens exactly at 510.0 |
| TC-4 | Boundary | Actuator capacity limits | Scale svc0 past replica cap 5; migrate a 15-unit service onto a 10-unit device | Both refused, each with a machine-readable reason |
| TC-5 | Abnormal | Malformed / illegal controller commands | 4 schema violations + 4 semantic violations | All 8 rejected before actuation |
| TC-6 | Abnormal | Device failure and network failure | `node_crash` on edge2 and `link_failure` on l_gw_edge2, ticks 20–49 | Detected as availability violations; full recovery on expiry |
| TC-7 | Abnormal | Cloud uplink unavailable + budget exhaustion | Cache-only mode with no cache entry; call ceiling of 2 crossed | Both fail loudly with typed exceptions, never silently |
| TC-8 | Boundary | Sustained overload / back-pressure | `traffic_surge` driving ~4× arrival rate against `queue_cap = 128` | Queue bounded; latency degrades rather than memory growing without limit |

---

## 2. Test Results

### 2.1 Summary

**8 of 8 passed.** Full captured console output follows each case.

| ID | Verdict | Evidence in one line |
|---|---|---|
| TC-1 | **PASS** | 7 680 field-values acquired over 120 ticks, 0.00 % drop |
| TC-2 | **PASS** | Detection latency 2 ticks; 9 actions; 0 invalid |
| TC-3 | **PASS** | Classification flips exactly at 600.0 ms; at-risk opens exactly at 510.0 ms |
| TC-4 | **PASS** | Replica cap and memory capacity both refused with reasons |
| TC-5 | **PASS** | 8/8 malformed commands blocked |
| TC-6 | **PASS** | Both failure classes detected; throughput restored to ≥ 80 % of baseline |
| TC-7 | **PASS** | `CacheMiss` and `BudgetExceeded` raised before any spend |
| TC-8 | **PASS** | Peak queue 92 < cap 128; p95 rose 515 → 2 551 ms under overload |

### 2.2 Captured output (verbatim)

```
==============================================================================
TC-1  [NORMAL]  Steady-state telemetry acquisition, no faults injected
==============================================================================
ticks simulated               : 120
sensor field-values acquired  : 7680 (64/tick = 6 svc x 5 metrics + 17 node util + 17 link latency)
edge utilisation @ t=119      : edge0=62.4%, edge1=51.4%, edge2=14.8%, edge3=25.2%
mean edge utilisation         : 37.9%
mean p95 across services      : 413 ms (target 600 ms)
cumulative SLO violation-ticks: 101 of 720 service-ticks (14.0%)
mean drop rate                : 0.00%
VERDICT: telemetry emitted every tick, zero packet loss in the fault-free baseline -> PASS

==============================================================================
TC-2  [NORMAL]  Fault detection + autonomous recovery (CPU saturation, rule controller)
==============================================================================
fault window                  : ticks 20-79 (node_cpu_saturation on edge0, mag 4.0)
edge0 util  before / during   : 52.0% / 85.3%
controller actions issued     : 9
   t= 22  migrate_service   node edge0 util sustained above 90% with svc0 in violation; migrate svc0 to ...
   t= 23  scale_service     svc4 in p95 violation with host capacity; scale up by 1
   t= 26  scale_service     svc5 in p95 violation with host capacity; scale up by 1
   t= 32  scale_service     svc1 in p95 violation with host capacity; scale up by 1
   t= 38  migrate_service   node edge0 util sustained above 90% with svc4 in violation; migrate svc4 to ...
   t= 54  scale_service     svc3 in p95 violation with host capacity; scale up by 1
detection latency             : fault at t=20, first remedy at t=22 (= 2 ticks)
invalid actions emitted       : 0
VERDICT: fault detected from symptoms only, remedy within 5 ticks, zero invalid commands -> PASS

==============================================================================
TC-3  [BOUNDARY]  SLO threshold boundary (p95 target = 600 ms exactly)
==============================================================================
   p95=  509.0 ms  p95_ok=True   compliant=True   at_risk=False
   p95=  510.0 ms  p95_ok=True   compliant=True   at_risk=True
   p95=  599.9 ms  p95_ok=True   compliant=True   at_risk=True
   p95=  600.0 ms  p95_ok=True   compliant=True   at_risk=True
   p95=  600.1 ms  p95_ok=False  compliant=False  at_risk=False
expected: <=600.0 compliant; >600.0 violation; >=510.0 (0.85 x 600) flagged at_risk
VERDICT: classification flips exactly at 600.0 ms and the at-risk band opens exactly at 510.0 ms -> PASS

==============================================================================
TC-4  [BOUNDARY]  Actuator capacity limits (replica cap = 5, device memory = 10 units)
==============================================================================
  scale_service svc0 upward until the cap refuses:
   replicas 1 -> 2: valid=True   scale accepted
   replicas 2 -> 3: valid=True   scale accepted
   replicas 3 -> 4: valid=True   scale accepted
   replicas 4 -> 5: valid=True   scale accepted
   replicas 5 -> 6: valid=False  scale would exceed replica cap of 5
  migrate svc0 (3 replicas x 5 mem = 15 units) onto 10-unit device dev0:
   valid=False  reason: target node dev0 lacks memory capacity for svc0
VERDICT: both capacity boundaries refused, each with a machine-readable reason -> PASS

==============================================================================
TC-5  [ABNORMAL]  Invalid / malformed controller commands (schema + semantic layers)
==============================================================================
   L1 schema   | invented action type   -> REJECTED | pydantic validation error
   L1 schema   | missing required field -> REJECTED | pydantic validation error
   L1 schema   | extra field            -> REJECTED | pydantic validation error
   L1 schema   | wrong type             -> REJECTED | pydantic validation error
   L2 semantic | unknown service        -> REJECTED | service svcZ does not exist
   L2 semantic | unknown target node    -> REJECTED | target node nodeX does not exist
   L2 semantic | replicas below one     -> REJECTED | scale would drop replicas below one
   L2 semantic | negative rate limit    -> REJECTED | rate_limit must be positive
VERDICT: 8/8 malformed or illegal commands blocked before actuation -> PASS

==============================================================================
TC-6  [ABNORMAL]  Device failure (node crash) and network failure (link down)
==============================================================================

  fault = node_crash on edge2  (active ticks 20-49)
   svc2 throughput  before=  24.1 rps  during=   0.0 rps  after=  25.7 rps
   svc2 drop rate   before=  0.0%   during=100.0%   after=  0.0%
   flagged as availability violation during fault : YES
   throughput restored to >=80% of baseline after : YES

  fault = link_failure on l_gw_edge2  (active ticks 20-49)
   svc2 throughput  before=  24.1 rps  during=   0.0 rps  after=  25.7 rps
   svc2 drop rate   before=  0.0%   during=100.0%   after=  0.0%
   flagged as availability violation during fault : YES
   throughput restored to >=80% of baseline after : YES
VERDICT: both failure classes detected via availability and fully self-recovered on expiry -> PASS

==============================================================================
TC-7  [ABNORMAL]  Cloud uplink unavailable + budget exhaustion
==============================================================================
   cache-only miss -> CacheMiss, run halts cleanly: cache-only mode: no entry for prompt 5587910b78a5
   budget ceiling  -> BudgetExceeded raised before spend: call ceiling of 2 crossed
VERDICT: uplink loss and budget exhaustion both fail loudly, never silently -> PASS

==============================================================================
TC-8  [BOUNDARY]  Sustained overload - bounded buffer and back-pressure
==============================================================================
   configured queue_cap          : 128
   svc0 arrivals before / during : 25.8 rps / 48.8 rps served
   svc0 p95 before / during      : 515 ms / 2551 ms
   peak observed queue length    : 92
   peak drop rate (overflow)     : 65.7%
   queue never exceeded cap      : YES
VERDICT: bounded buffer enforced under 4x overload; latency rises rather than memory growing
without limit -> PASS
```

### 2.3 Regression suite

The prototype's own automated suite, run immediately after the cases above:

```
$ python -m pytest tests/ -q
........................................................................ [ 67%]
...................................                                      [100%]
107 passed in 79.97s (0:01:19)
```

---

## 3. Performance Comparison — Baseline vs Proposed

### 3.1 Design

Eight arms × **10 seeds** × 120 ticks, paired design (each seed fixes the same fault schedule
and workload across all arms, so per-seed differences are attributable to the controller).
Harness: `scratchpad/lab6_perf.py`.

The headline comparison is **A1 (rule controller, no twin) = baseline** versus
**A4 (rule controller + twin gate) = proposed**, because both are real algorithms. The
stub-driven arms are reported for completeness but are *not* the claim.

### 3.2 Results

| Arm | Description | SLO violation-ticks (mean ± sd) | vs A0 | Proposals | Truly harmful | Blocked | Recall | FPR | CPU-s (proxy) |
|---|---|---|---|---|---|---|---|---|---|
| A0 | null — do nothing | **130.5 ± 28.0** | +0.0 | 120 | 0 | 0 | — | — | 85.4 |
| **A1** | **rule controller (baseline)** | **138.1 ± 33.3** | **+7.6** | 120 | 5 | 0 | 0.0 % | 0.00 % | 90.4 |
| **A4** | **rule + twin @ 0.60 (proposed)** | **130.2 ± 27.9** | **−0.3** | 129 | 8 | 8 | **100.0 %** | **0.83 %** | 166.8 |
| A4′ | rule + twin @ 1.00 | 130.2 ± 27.9 | −0.3 | 129 | 8 | 8 | 100.0 % | 0.83 % | 161.8 |
| A2 | *stub* LLM, no twin | 154.9 ± 33.9 | +24.4 | 120 | 27 | 0 | 0.0 % | 0.00 % | 89.5 |
| A3 | *stub* LLM + twin @ 0.40 | 134.4 ± 29.3 | +3.9 | 166 | 49 | 43 | 87.8 % | 2.56 % | 187.7 |
| A3 | *stub* LLM + twin @ 0.60 | 131.1 ± 27.9 | +0.6 | 173 | 51 | 48 | 94.1 % | 4.10 % | 211.9 |
| A3 | *stub* LLM + twin @ 1.00 | 130.7 ± 27.9 | +0.2 | 176 | 51 | 50 | 98.0 % | 4.80 % | 210.3 |

Peak traced Python heap across all arms: **4.7 MB**.
Mean edge-node utilisation was **38.6–38.8 %** in every arm — the gate does not change the
resource envelope of the managed network, only the controller's own compute.

### 3.3 Per-seed paired data

| Seed | A0 | A1 | A4 | A4′ | A2 | A3@0.40 | A3@0.60 | A3@1.00 |
|---|---|---|---|---|---|---|---|---|
| 0 | 133 | 133 | 133 | 133 | 152 | 141 | 137 | 133 |
| 1 | 93 | 93 | 93 | 93 | 114 | 93 | 93 | 93 |
| 2 | 125 | 125 | 125 | 125 | 154 | 143 | 130 | 130 |
| 3 | 194 | 194 | 194 | 194 | 202 | 194 | 194 | 194 |
| 4 | 153 | **181** | **153** | 153 | 222 | 166 | 153 | 153 |
| 5 | 101 | 101 | 101 | 101 | 127 | 101 | 101 | 101 |
| 6 | 139 | **169** | **136** | 136 | 153 | 136 | 136 | 136 |
| 7 | 143 | **157** | **143** | 143 | 176 | 143 | 143 | 143 |
| 8 | 118 | 118 | 118 | 118 | 127 | 121 | 118 | 118 |
| 9 | 106 | **110** | **106** | 106 | 122 | 106 | 106 | 106 |

**Paired A4 − A1 per-seed difference:** `[0, 0, 0, 0, −28, 0, −33, −14, 0, −4]`
mean **−7.9**, sd 12.1, **4 seeds improved, 0 worse, 6 tied**.

### 3.4 Reading of the result — including the uncomfortable part

1. **The baseline controller is worse than doing nothing.** A1 scores 138.1 against A0's
   130.5. A reactive rule controller that scales and migrates on symptom thresholds actively
   harms the network on this workload.
2. **The twin recovers it.** A4 brings 138.1 back to 130.2 — parity with the null arm, not
   better than it. The honest claim is *"the gate removes the damage the controller does"*,
   **not** *"the gate improves on inaction"*.
3. **The gate never hurt a seed.** 4 improved, 0 worse, 6 tied. The 6 ties are seeds where the
   baseline happened to propose nothing harmful.
4. **The effect is carried by 4 of 10 seeds**, sd 12.1 against mean −7.9. This is directional
   evidence, not a significance claim, and must not be written up as one.

### 3.5 Response time — isolated latency budget

Measured with nothing else running (`scratchpad/lab6_latency.py`). **A first measurement
reported the twin at ~563 ms; that figure was an artefact of two background jobs competing
for CPU and is discarded.** The isolated figures:

| # | Stage | Operation | Median | p95 |
|---|---|---|---|---|
| 1 | Sensor tick | `sim.step()` | 1.768 ms | 2.218 ms |
| 2 | Edge aggregation | `Collector.observe()` | 0.012 ms | 0.013 ms |
| 3 | Payload encode | `Summarizer.render()` | 0.037 ms | 0.049 ms |
| 4 | Controller decide | `RuleAgent.decide()` | 0.052 ms | 0.101 ms |
| 5 | Command validate | schema + semantic | 0.004 ms | 0.005 ms |
| 6 | State fork | `NetworkSim.fork()` | 0.909 ms | 1.175 ms |
| 7 | **Twin validate @ 0.00** | horizon 20 | 73.836 ms | 93.620 ms |
| 7 | **Twin validate @ 0.40** | horizon 20 | 76.269 ms | 100.412 ms |
| 7 | **Twin validate @ 0.60** | horizon 20 | 82.140 ms | 96.861 ms |
| 7 | **Twin validate @ 1.00** | horizon 20 | 81.102 ms | 98.070 ms |
| 8 | Ground-truth counterfactual | 2 × horizon-20 rollout | 83.768 ms | 123.152 ms |

n = 200 for stages 1–6, n = 60 for stages 7–8.

- **Ungated decision path (2+3+4+5): ~0.1 ms.**
- **Gated decision path (2+3+4+5+7): ~81.2 ms.**
- Stage 8 is offline evaluation for scoring the twin; **it never runs in the control path.**

So the gate costs roughly **800× the decision latency** — but 81 ms against a 10-tick decision
interval is still comfortably real-time, and the CPU-second column in §3.2 (≈1.8× A1) is the
same cost seen from the energy-proxy side.

### 3.6 Metric coverage

| Required metric | Reported as | Where |
|---|---|---|
| Accuracy / detection rate | Twin recall on harmful actions: **100 %** (A4), 87.8–98.0 % (A3) | §3.2 |
| False-positive rate | **0.83 %** (A4), 2.56–4.80 % (A3) | §3.2, §9.2 |
| Response time | Per-stage latency budget, 0.1 ms ungated / 81.2 ms gated | §3.5 |
| Network latency | Loopback HTTP median 2.60 ms; SSE first event 3.2 ms | §4.3 |
| Resource utilisation | Edge utilisation 38.6–38.8 %; peak heap 4.7 MB | §3.2 |
| Energy consumption | **Not measurable — CPU-seconds given as a labelled proxy** (85.4 → 166.8 s) | §3.2 |
| Prediction accuracy | Twin harm-delta exactly equals oracle in **64/75** proposals @ fidelity 1.00 | §9.2 |

---

## 4. IoT Communication Demonstration

### 4.1 The chain

```
  Sensors            IoT Controller        Network          Cloud/Server        User
  ────────           ──────────────        ───────          ────────────        ────
  6 services   ──►   Collector       ──►   HTTP/1.1    ──►  ThreadingHTTP  ──►  Browser
  17 nodes           + Summarizer          SSE over          Server              dashboard
  17 links           + RuleAgent           TCP               live.py             review/
  64 field-values    + Twin gate           127.0.0.1:8801    /api/run            index.html
  per tick           + Validator           text/event-stream
```

**Protocol used: HTTP/1.1 with Server-Sent Events.** Not MQTT, Firebase, ThingSpeak, Blynk or
Node-RED — the project does not use a broker or a third-party cloud, and this report does not
claim one. SSE is the appropriate choice here: the traffic is a one-way, ordered,
server-push telemetry stream to a browser, which is exactly the SSE use case, and it needs no
external dependency. Transport code: `src/twinloop/dashboard/live.py`,
`scripts/live_server.py`.

### 4.2 Live capture (verbatim)

Harness `scratchpad/lab6_iot_chain.py` starts the real server, probes health, then consumes
the SSE stream exactly as the browser does.

```
HOP 4-5  CLOUD/SERVER  ->  USER      (HTTP/1.1 over TCP, loopback)
==============================================================================
server boot to first accepted request : 1273 ms
GET /api/health                       : 200 OK
  service   : twinloop-live v1
  providers : scripted=up, cached=up, gemini=up, local=up
  bounds    : {"seed": [0, 9999], "ticks": [20, 300], "interval": [5, 60],
               "horizon": [5, 60], "retry_cap": [0, 5], "harm_threshold": [0, 20],
               "speed": [1, 200]}

request/response latency over 20 probes (application layer, loopback):
  min=2.06 ms  median=2.60 ms  p95=26.28 ms  max=26.65 ms

==============================================================================
HOP 1-5  FULL CHAIN   sensors -> controller -> twin -> server -> user
==============================================================================
GET /api/run?agent=rule&gate=1&fidelity=1.0&seed=15&ticks=60&interval=10&horizon=20&speed=200
  transport: text/event-stream (Server-Sent Events), Connection: close

Content-Type                : text/event-stream; charset=utf-8
time to first event         : 3.2 ms
total stream wall-clock     : 1.56 s
events received             : 99
event mix                   : applied=6, decision_close=6, decision_open=6, done=1, hello=1,
                              meta=1, proposal=6, tick=60, truth=6, verdict=6
inter-tick delivery interval: median=8.4 ms  min=7.2  max=203.1 (server pacing at speed=200/s)
scheduled faults in episode : traffic_surge on svc2 t28-107
```

### 4.3 End-to-end decision as delivered to the user

```
  +    37 ms  PROPOSAL  {"type": "no_op"}
  +   122 ms  VERDICT   APPROVED | twin cost 82.0 ms
                        approved: predicted 11 SLO violation-ticks versus 11 for no-op
                        (difference 0 within tolerance)
  +   122 ms  ACTUATED  {"type": "no_op"} | did nothing
  +   202 ms  TRUTH     harm_delta=+0 (act 11 vs noop 11) harmful=False blocked=False
                        cf_cost=40.7 ms
  +   290 ms  PROPOSAL  {"type": "no_op"}
  +   371 ms  VERDICT   APPROVED | twin cost 79.4 ms
  +   372 ms  ACTUATED  {"type": "no_op"} | did nothing
  +   455 ms  TRUTH     harm_delta=+0 (act 11 vs noop 11) harmful=False blocked=False
                        cf_cost=42.1 ms
```

Every hop is instrumented and timed: sensing → aggregation → proposal (37 ms) → twin verdict
(+85 ms) → actuation (+0 ms) → ground-truth label delivered to the UI (+80 ms). The twin cost
observed *inside the live server* (82.0 / 79.4 ms) agrees with the isolated bench in §3.5
(81.1 ms median at fidelity 1.00), which cross-validates both measurements.

### 4.4 Interface contract

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Service identity, provider availability, parameter bounds |
| `/api/run` | GET | Opens the SSE episode stream; all run parameters as query args, server-side bounds-checked |
| `/` | GET | Serves the dashboard page |

Event types on the stream: `hello`, `meta`, `tick`, `decision_open`, `proposal`, `verdict`,
`applied`, `truth`, `decision_close`, `done`.

---

## 5. Dashboard / UI

**Location:** `review/index.html`, served by `scripts/live_server.py`.
**Run it:**

```bash
python scripts/live_server.py
# then open http://127.0.0.1:8765/
```

### 5.1 Required elements

| Requirement | Implementation | Evidence |
|---|---|---|
| Real-time sensor values | Per-tick p95 latency, throughput, drop rate, node utilisation and link latency for all 6 services / 17 nodes / 17 links, pushed on every `tick` event | 60 `tick` events in the §4.2 capture, median 8.4 ms apart |
| Device / actuator status | Node and link state (healthy / degraded / down), service replica counts, and the applied-action feed | `applied` events; `meta` carries the topology |
| Alerts | SLO violations surfaced per service; at-risk band (≥ 510 ms) flagged before breach; twin **REJECTED** verdicts shown with reason text | `verdict` events carry `approved` + human-readable `reason` |
| Graphs / history | Rolling per-service latency and utilisation series; the decision timeline with proposal → verdict → applied → truth grouped per decision | `decision_open` / `decision_close` bracket each group |
| **Ground-truth overlay** *(beyond requirement)* | Each decision is annotated after the fact with the counterfactual truth: `harm_delta`, action vs no-op violation-ticks, whether the twin was right | `truth` events |

### 5.2 Verification

The UI is tested headlessly, not merely eyeballed. `tests/test_live.py` — **13 passed in
4.87 s**:

```
test_stream_opens_with_meta_and_closes_with_done              PASSED
test_every_event_is_json_serialisable                         PASSED
test_one_tick_event_per_tick                                  PASSED
test_gate_off_emits_no_verdicts                               PASSED
test_gate_on_emits_a_verdict_for_every_proposal               PASSED
test_rejection_is_followed_by_a_retry_and_a_new_proposal      PASSED
test_llm_calls_carry_the_full_prompt_and_reply                PASSED
test_ground_truth_covers_every_proposal_including_rejected_ones PASSED
test_counterfactual_can_be_switched_off                       PASSED
test_identical_options_produce_identical_streams              PASSED
test_cache_only_provider_reports_a_miss_instead_of_crashing   PASSED
test_provider_status_lists_every_brain                        PASSED
test_done_totals_agree_with_the_streamed_events               PASSED
```

`test_identical_options_produce_identical_streams` is the important one for a research
artefact: the same parameters yield a byte-identical event stream, so any dashboard screenshot
is reproducible rather than a one-off.

---

## 6. Fault-Handling Mechanism

The prototype implements **six** injectable fault types; four are exercised in this report
(minimum required: two).

### 6.1 Fault 1 — Node CPU saturation (TC-2)

| Stage | Mechanism | Observed |
|---|---|---|
| Injection | `node_cpu_saturation` on edge0, ticks 20–79, magnitude 4.0 | edge0 utilisation 52.0 % → 85.3 % |
| Detection | Symptom-based only — the controller sees utilisation and per-service p95, never the fault schedule | First remedy at t=22, **detection latency 2 ticks** |
| Handling | `migrate_service` off the hot node; `scale_service` for services in p95 violation | 9 actions issued, **0 invalid** |
| Verification | Three-layer validation before any action reaches the simulator | 0 rejects needed — all 9 were well-formed and legal |

### 6.2 Fault 2 — Node crash (device failure) (TC-6)

| Stage | Mechanism | Observed |
|---|---|---|
| Injection | `node_crash` on edge2, ticks 20–49 | svc2 throughput 24.1 → **0.0 rps**, drop rate 0 % → **100 %** |
| Detection | Availability SLO breach (< 99 %) | Flagged as availability violation: **YES** |
| Handling | Traffic to the dead node is dropped rather than silently absorbed; the controller can migrate affected services to surviving nodes | Throughput restored to 25.7 rps on expiry — **≥ 80 % of baseline: YES** |

### 6.3 Fault 3 — Link failure (network failure) (TC-6)

| Stage | Mechanism | Observed |
|---|---|---|
| Injection | `link_failure` on l_gw_edge2, ticks 20–49 | svc2 throughput 24.1 → **0.0 rps**, drops **100 %** |
| Detection | Availability breach, identical signature to the node crash | Flagged: **YES** |
| Handling | Partition isolated; recovery on expiry | Restored to 25.7 rps — **YES** |

Note that a crashed node and a severed uplink present *the same symptom* to the controller.
This is realistic and is a genuine limitation of symptom-only observability, not a bug.

### 6.4 Fault 4 — Control-plane faults: uplink loss and budget exhaustion (TC-7)

Faults in the *management* plane are handled as first-class, and — critically — **loudly**:

| Condition | Mechanism | Observed |
|---|---|---|
| Cloud LLM unreachable | Cache-only mode raises `CacheMiss` naming the missing prompt hash | `cache-only mode: no entry for prompt 5587910b78a5` — run halts cleanly |
| Cost ceiling crossed | `BudgetGuard` raises `BudgetExceeded` **before** the call is made | `call ceiling of 2 crossed` |

Both raise typed exceptions instead of returning a plausible default. That is deliberate: a
silent fallback here would let an experiment finish and produce numbers that look fine but
describe a different system than the one claimed.

### 6.5 Command-level fault handling — three-layer validation (TC-4, TC-5)

Every proposed action passes three independent gates before it can touch the network:

1. **Schema (Pydantic)** — closed six-action schema; unknown types, missing fields, extra
   fields and wrong types all rejected. *4/4 blocked.*
2. **Semantic validator** — the action must be legal against current state: entities must
   exist, capacity limits hold, replicas stay ≥ 1, rate limits positive. *4/4 blocked, plus
   both capacity boundaries in TC-4, each with a machine-readable reason.*
3. **Digital twin** — the action must not be predicted to make things worse over a 20-tick
   horizon. *8/8 truly harmful actions blocked in A4, at a 0.83 % false-positive rate.*

---

## 7. Expected vs Actual Results

| # | Expectation | Actual | Match | Explanation of deviation |
|---|---|---|---|---|
| 1 | Telemetry flows every tick with no loss when healthy | 7 680 field-values / 120 ticks, 0.00 % drop | ✅ | — |
| 2 | Faults detected within 5 ticks from symptoms alone | 2 ticks | ✅ | Better than expected; the utilisation signal is strong |
| 3 | SLO classification flips exactly at the configured threshold | Flips at 600.0 / 510.0 exactly | ✅ | — |
| 4 | Malformed commands never reach the actuator | 8/8 blocked | ✅ | — |
| 5 | Capacity limits refuse with a usable reason | Both refused with reasons | ✅ | — |
| 6 | Failures detected and recovered | Both detected; throughput restored | ✅ | — |
| 7 | Overload bounded, no unbounded memory growth | Peak queue 92 < cap 128; p95 515 → 2 551 ms | ✅ | Degradation is latency + drops, as designed |
| 8 | Uplink/budget failures are loud | Typed exceptions before spend | ✅ | — |
| 9 | **The rule controller improves on doing nothing** | **A1 = 138.1 vs A0 = 130.5 — it is WORSE** | ❌ | **Deviation 1 below** |
| 10 | **The twin gate improves on the baseline** | A4 = 130.2 vs A1 = 138.1 (−7.9 paired) | ✅ | But only to parity with A0 — see Deviation 2 |
| 11 | Higher fidelity ⇒ better gating | A3: recall 87.8 → 94.1 → 98.0 % as fidelity 0.40 → 0.60 → 1.00 | ✅ | Monotone as expected; **but see Deviation 4** — FPR *also* rises, 2.56 → 4.80 % |
| 12 | A perfect twin (fidelity 1.00) is a perfect oracle | Twin harm-delta equals ground truth in only **64/75** cases | ❌ | **Deviation 3 below** |
| 13 | Twin adds modest latency | 0.1 ms → 81.2 ms per gated decision (~800×) | ⚠️ | **Deviation 5 below** |
| 14 | A real LLM behaves like the scripted stub | Real Gemini arm emits `no_op` **97 %** of the time | ❌ | **Deviation 6 below** |

### Explanation of deviations

**Deviation 1 — the baseline controller is actively harmful (row 9).**
A1 scores 7.6 violation-ticks *worse* than doing nothing. The rule controller reacts to
symptoms that are often transient: it migrates a service off a node whose saturation was about
to lift, and pays the migration downtime for nothing. This is not a defect in the measurement;
it is the finding that motivates the whole project. It does mean the paper must not claim
"the twin makes a good controller better" — it makes a *bad* controller harmless.

**Deviation 2 — the gate reaches parity, not superiority (row 10).**
A4 = 130.2 against A0 = 130.5. The twin removes essentially all of the damage the controller
does, but on this workload it does not extract net benefit beyond inaction. Reporting this as
an improvement over the null arm would be dishonest; the defensible claim is bounded to
"recovers the baseline's loss".

**Deviation 3 — a fidelity-1.00 twin is still not an oracle (row 12).**
At fidelity 1.00 the twin's predicted harm-delta exactly equals the counterfactual truth in
only 64 of 75 non-`no_op` proposals (85.3 %). The residual 11 are the **fault-blind ceiling**:
the twin rolls forward with `FAULT_MODE_BLIND` and therefore cannot see a scheduled fault
starting, ending, or changing magnitude inside its horizon. This is the same limitation
demonstrated by design in Case 3 of the Lab 5 demo, now quantified at scale. It is a
structural property of the design, not an implementation bug — a twin that saw the fault
schedule would be clairvoyant and the experiment would be circular.

**Deviation 4 — fidelity improves recall and worsens precision together (row 11).**
Recall rises 87.8 → 98.0 % across fidelity 0.40 → 1.00, but FPR rises with it, 2.56 → 4.80 %.
A sharper twin blocks more harmful actions *and* more safe ones. Part of that false-positive
count is structural rather than error — see §9.2.

**Deviation 5 — the gate costs ~800× the decision latency (row 13).**
0.1 ms ungated vs 81.2 ms gated. Absolute cost is small against a 10-tick decision interval,
so this is not a real-time problem here, but it scales with horizon × topology size and would
matter on a larger network. An early measurement of 563 ms was CPU-contention noise and is
discarded; §3.5 reports the isolated figure.

**Deviation 6 — the real model does not behave like the stub (row 14).**
The one arm driven by an actual language model (Gemini, replayed from cache) emits `no_op` in
roughly 97 % of decisions, producing an essentially **empty confusion matrix** — there is
almost nothing for the twin to gate. The A2/A3 numbers in §3.2 therefore describe *a scripted
policy that proposes aggressively*, not LLM behaviour, and are labelled "stub" throughout.
Any LLM claim in the paper is currently unsupported by these runs.

---

## 8. Future Enhancement

**1. Replace the staleness approximation with a true snapshot replay.**
`lag_ticks` currently scales observations toward a baseline rather than replaying the state
the controller would genuinely have seen *k* ticks ago (documented in
`docs/decisions/fidelity_mapping.md`). A per-tick snapshot ring buffer would make the lag axis
a real replay, removing the last modelled-rather-than-simulated element of the fidelity map.

**2. Break the twin/oracle code-sharing dependency.**
Twin and ground-truth oracle currently share the same simulator and differ only in fault mode.
That makes the twin's errors correlated with the oracle's blind spots by construction. Vetting
proposals against an *independently implemented* model — a queueing-theory approximation, or a
learned surrogate — would test whether the result survives when the twin is not a copy of the
world it is predicting.

**3. Reconcile the approval tolerance with the harm threshold.**
`tolerance_margin = 0` while `harm_threshold_ticks = 3`, so the twin rejects any predicted
worsening while only ≥ 4 ticks counts as harm. This manufactures false positives structurally:
§9.2 shows **3 of 6** false positives at fidelity 1.00 fall inside that 1–3 band. Sweeping the
tolerance and reporting a precision/recall curve would replace a single arbitrary operating
point with a characterisation.

**4. Real transport and real hardware.**
Add an MQTT broker (Mosquitto) alongside the SSE path and drive it from physical or emulated
sensors. This would let the report claim IoT-standard messaging honestly, expose real network
latency and loss instead of loopback figures, and make energy measurable rather than proxied
by CPU-seconds.

**5. Statistical power.**
10 seeds with 4 improved / 6 tied is directional, not significant. Scaling to 50–100 seeds
across multiple topologies with a paired significance test (Wilcoxon signed-rank) would let
the effect be stated with a confidence interval rather than a mean and a caveat.

**6. Make the LLM arm real.**
The `no_op`-97 % degeneracy must be fixed — via prompt revision, few-shot examples of
legitimate interventions, or a different model — before any language-model claim can be made.
Until then A2/A3 measure a scripted policy.

---

## 9. Carried-forward suggestions from Lab 5

Lab 5 covered Level 9 (twin and fidelity). No teacher-feedback file exists in the repository,
so this section carries forward the suggestions **recorded in the project's own Lab 5
artefacts** — `docs/decisions/fidelity_mapping.md`, `scripts/lab5_twin_demo.py`, and the
Level-9 findings in `docs/readiness_audit.md`. Each is re-tested against this lab's data
rather than merely restated.

### 9.1 The fault-blind ceiling (Lab 5 Case 3) — confirmed and now quantified

Lab 5's demo deliberately included a case where **the twin is wrong**:

```
CASE 3  the twin is WRONG (labelled): saturation about to expire on its own
  TWIN VERDICT: APPROVE
    predicted violation-ticks: action 9, no-op 30
  REALITY: action 9 violation-ticks, no-op 5  -> action was harmful
  OUTCOME: twin was WRONG
```

Lab 5's stated lesson: *"the twin rolls forward fault-blind, so it cannot see that the
saturation was about to lift."* Lab 6 measures how often this happens across 10 seeds:

> At **fidelity 1.00**, the twin's predicted harm-delta equals the ground-truth delta in
> **64 of 75** non-`no_op` proposals — **85.3 %**. At fidelity 0.40, **16 of 76 — 21.1 %**.

The 14.7 % residual at perfect fidelity *is* the Case-3 ceiling, now with a number attached.
This is the single most important carried-forward finding: **fidelity 1.00 is a sanity check,
never the headline** — it is not an oracle, and the report never presents it as one.

### 9.2 Tolerance vs harm threshold manufactures false positives — confirmed

`fidelity_mapping.md` and audit item **D2** flagged that `tolerance_margin = 0` (twin rejects
any predicted worsening) sits against `harm_threshold_ticks = 3` (only ≥ 4 ticks is scored as
harm), so actions with true harm of 1–3 ticks are counted as false positives although the twin
was directionally right. Re-checked against this lab's data
(`scratchpad/lab6_fpcheck.py`, 10 seeds):

```
harm_threshold_ticks = 3   twin tolerance_margin = 0.0

fidelity 0.40
  gated proposals                     : 166  (non-no_op: 76)
  false positives (safe but rejected) : 3     true harm deltas: [-7, -2, 3]
  FPs in the structural 1..3 band     : 1 of 3
  twin delta == oracle delta EXACTLY  : 16/76

fidelity 1.00
  gated proposals                     : 176  (non-no_op: 75)
  false positives (safe but rejected) : 6     true harm deltas: [-4, -2, 0, 1, 2, 3]
  FPs in the structural 1..3 band     : 3 of 6
  twin delta == oracle delta EXACTLY  : 64/75
```

**Half the false positives at fidelity 1.00 (3 of 6) are a definitional artefact, not twin
error.** The reported FPR is therefore *pessimistic*. This must be disclosed wherever an FPR
is quoted — including the 0.83 % figure for A4 in §3.2 — rather than quietly improved by
retuning the threshold after seeing the results.

### 9.3 Fidelity axes must stay independently configurable — honoured

`fidelity_mapping.md` required that each of the five axes (`sigma_obs`, `lag_ticks`,
`drift_pct`, `forecast_err`, `simplify_queueing`) remain independently settable so that a
future ablation can attribute error to a specific axis rather than to a scalar. That property
is intact and used here: §3.2 sweeps the scalar at 0.40 / 0.60 / 1.00, and the per-axis
ablation remains available and is listed as future work (§8 item 1 addresses the weakest axis).

### 9.4 Known simplification in `lag_ticks` — still open, now scheduled

Lab 5 recorded that `lag_ticks` scales observations toward a baseline instead of replaying a
genuinely older state, with a per-tick snapshot ring buffer as the candidate refinement. This
remains **unfixed** and is carried into §8 item 1. It is disclosed rather than silently
retained, because it is the one fidelity axis that is modelled rather than simulated.

### 9.5 Twin and oracle share code — still open

Audit item **D1**: the twin and the counterfactual oracle are the same simulator differing
only in `FAULT_MODE_BLIND` vs `FAULT_MODE_SCHEDULED`. Lab 6 does not fix this — it is the
structural reason §9.1's 85.3 % is a ceiling and not a bug. Carried into §8 item 2.

### 9.6 Single-sample counterfactual labels — still open

Audit item **B8**: each harm label comes from one counterfactual rollout, so labels near the
threshold are unstable. §9.2's harm-delta distribution (`[-4, -2, 0, 1, 2, 3]`) sits directly
on that boundary, which is consistent with the concern. Multi-sample labelling is folded into
§8 item 5.

---

## 10. Conclusion

The prototype passes **8 of 8** functional test cases and its own **107-test** regression
suite, streams live telemetry end-to-end over a measured HTTP/SSE chain at 2.6 ms median
loopback latency, and handles four distinct fault classes plus two control-plane failure modes
without ever failing silently.

On the research claim, the honest summary is narrower than the prototype's polish suggests:
the rule baseline is *worse than doing nothing* (138.1 vs 130.5 violation-ticks), and the twin
gate recovers that loss to parity (130.2) with 100 % recall on harmful actions at a 0.83 %
false-positive rate — of which half is a definitional artefact. The effect is carried by 4 of
10 seeds, the language-model arm is a scripted stub, and even a fidelity-1.00 twin agrees with
ground truth only 85.3 % of the time because it is fault-blind by construction.

That last figure is not a defect to be hidden. It is the quantified version of the limitation
Lab 5 chose to demonstrate deliberately, and it is what makes the gating question worth
asking at all.
