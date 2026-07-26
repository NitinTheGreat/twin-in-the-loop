# Twin Fidelity Mapping

Status: v1, Level 9

The twin is never assumed perfect. Fidelity is a first-class, swept parameter. This
document records the five degradation axes, how a single scalar maps onto them, and why
each axis corresponds to a real imperfection of a production digital twin.

Fidelity randomness is drawn from the validator's own `SeedManager` stream
(`fidelity::<tick>`), never from the simulator's streams, so changing fidelity cannot
perturb the real simulator's trajectory. The twin is always built by forking the real
simulator, degrading the fork, and rolling it forward fault-blind; it never mutates the
real simulator.

## The five axes

| Axis | Parameter | What it degrades | Why it is a real twin imperfection |
|---|---|---|---|
| Observation noise | `sigma_obs` | Gaussian multiplicative noise on the twin's starting degradation read: node `cpu_reserved`, leaked `mem_footprint` above baseline, and link latency multiplier. | A twin syncs from monitoring, and monitoring is noisy. The starting state is an estimate, not the truth. |
| Staleness | `lag_ticks` | Scales current degradation down toward its baseline by `min(0.95, lag*0.05)`, so a stale twin underestimates how bad things currently are. | A twin syncs periodically. Between syncs its view is old, and if conditions are worsening it lags reality. |
| Parameter drift | `drift_pct` | Node CPU capacities and per-service CPU demand each differ from the truth by a random offset within +/- `drift_pct`. | A twin's model of hardware and service resource profiles is calibrated once and drifts from reality over time. |
| Structural simplification | `simplify_queueing` | The twin uses deterministic service times (mean) instead of the exponential model the real simulator uses. | A twin trades fidelity for speed and tractability, often with a coarser queueing model than reality. |
| Workload forecast error | `forecast_err` | The twin's assumed per-service arrival rates differ from the true rates by a random offset within +/- `forecast_err`. | A twin must forecast future demand to roll forward, and demand forecasts are never exact. |

Each axis is independently configurable through `TwinConfig` for ablation studies.

## The scalar mapping

A single `fidelity` in `[0, 1]` maps monotonically onto all five for headline plots, via
`fidelity_to_config`. Let `d = 1 - fidelity` be the degradation amount:

| Axis | Value at scalar `fidelity` |
|---|---|
| `sigma_obs` | `d * 0.30` |
| `lag_ticks` | `round(d * 12)` |
| `drift_pct` | `d * 0.30` |
| `forecast_err` | `d * 0.40` |
| `simplify_queueing` | `True` when `fidelity < 0.5` |

At `fidelity = 1.0` every axis is off, so the twin is an exact fork of reality and its
prediction matches a scheduled-mode rollout exactly. This is the sanity-check
configuration, never the headline result. As fidelity falls, every axis grows, and
prediction error rises monotonically. The crossover fidelity at which validation stops
paying for itself is the single most quotable result the project targets.

The constants (`SIGMA_MAX`, `LAG_MAX`, `DRIFT_MAX`, `FORECAST_MAX`, `SIMPLIFY_BELOW`) live
in `src/twinloop/twin/fidelity.py` and are the only tuning knobs of the mapping.

## Known simplification

`lag_ticks` is modelled as a scaling of current degradation toward baseline rather than a
true replay of the state from `N` ticks ago, which would require a per-tick snapshot ring
buffer. This is a faithful first-order approximation of staleness (a stale twin
underestimates current degradation) and is noted here as a candidate refinement.
