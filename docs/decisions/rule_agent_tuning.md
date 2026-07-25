# Rule Agent Tuning

Status: v1, Level 7

This document records the rule agent's parameters and the reasoning behind them, so the
baseline is defensible on inspection. The rule agent is a deliberately fair baseline. It
is not tuned until it never errs; it still causes harm in some situations, and that harm
is a genuine finding, not a bug (see the closing note).

All parameters live in `RuleAgentConfig` (in `src/twinloop/config.py`), never hardcoded.

## Parameters

| Parameter | Value | Reasoning |
|---|---|---|
| `node_util_threshold` | 0.90 | Matches the architecture's stated "cpu > 90%" trigger. Below this, contention is tolerable; above it, queues grow super-linearly, so it is the right point to consider shedding load off a node. |
| `link_latency_threshold_ms` | 12.0 | Baseline link latency is 5 ms. A degraded link runs at 2-4x. 12 ms sits above normal jitter but below a mild degradation, so genuine degradations are caught without firing on noise. |
| `sustain_ticks` | 3 | A condition must hold for 3 consecutive observed ticks before the agent acts. This rejects single-tick spikes, which are common under Poisson load and self-heal. Three ticks is long enough to filter noise, short enough to react before an episode is lost. |
| `mem_growth_window` | 5 | A memory leak is diagnosed only if the footprint rises monotonically across 5 ticks. Normal memory is flat, so this has no false positives in steady state, and a real leak grows every tick. |
| `cooldown_ticks` | 15 | After acting on a service, the agent will not touch it again for 15 ticks. This exceeds the typical migration downtime (~10 ticks for a 5-unit footprint), so an action is always given time to fully resolve and settle before the agent reconsiders it. This is the anti-thrash guarantee. |
| `scale_delta` | 1 | Scale up one replica at a time. Conservative; avoids overshooting node capacity in a single step. |
| `throttle_fraction` | 0.60 | As a last resort, admit 60% of the currently served rate. Aggressive enough to relieve latency, moderate enough that it does not gratuitously destroy availability. |

## Decision logic, in priority order

1. **Migrate** — a node's utilisation is sustained above threshold, a service on it is in
   SLO violation, and a less-loaded edge node has memory capacity: migrate the
   highest-load violating service to the least-loaded viable target.
2. **Restart** — a service's memory footprint has grown monotonically over the window:
   restart it. This is the correct remedy for a leak.
3. **Reroute** — a service is in violation and a link on its route has sustained high
   latency and a genuine alternative path exists: reroute. In a star topology there is
   usually no alternative, so this rarely fires, which is honest.
4. **Scale up** — a service is in p95 (latency) violation and its host can accommodate a
   replica: add capacity.
5. **Throttle** — a service is still in p95 violation with no structural remedy: throttle
   as a last resort, trading availability for latency.
6. **no_op** — otherwise. The agent returns no_op readily. Availability-only violations
   with no structural fix (for example a service made unreachable by a crashed node whose
   utilisation therefore reads low) intentionally fall through to no_op rather than
   invite a counterproductive throttle.

## Safeguards

- **Pending guard**: a service with an unresolved effect (migration or restart in
  progress) is never issued another action.
- **Cooldown**: enforced per target after any action.
- **Sustained signals**: every trigger requires `sustain_ticks` or `mem_growth_window`
  consecutive observations.
- **Self-validation**: every candidate action is checked with the semantic validator
  before emission, so the baseline never emits an invalid action.

## Honest limitations (kept on purpose)

Thresholds are linear tests on a nonlinear system, and migration has real downtime cost.
The agent will therefore sometimes act when riding out a transient would have been
better, and its migrations cause a burst of dropped requests during downtime that can, in
the short term, make an episode worse. It also cannot remedy every fault class (a crashed
node reads as low utilisation, so the migrate rule does not fire for it). These behaviours
are left as-is. Suppressing them would produce a dishonest baseline and would erase the
very phenomenon this project studies: that acting without validating consequences is
risky.
