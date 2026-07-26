from __future__ import annotations

from ..config import TwinConfig

SIGMA_MAX = 0.3
LAG_MAX = 12
DRIFT_MAX = 0.3
FORECAST_MAX = 0.4
SIMPLIFY_BELOW = 0.5


class _DeterministicWork:
    def exponential(self, mean):
        return mean


def deterministic_service_prepare(fork) -> None:
    for sid in list(fork._service_streams):
        fork._service_streams[sid] = _DeterministicWork()


def fidelity_to_config(
    fidelity: float, horizon_ticks: int = 30, tolerance_margin: float = 0.0
) -> TwinConfig:
    degradation = 1.0 - fidelity
    return TwinConfig(
        fidelity=fidelity,
        sigma_obs=degradation * SIGMA_MAX,
        lag_ticks=int(round(degradation * LAG_MAX)),
        drift_pct=degradation * DRIFT_MAX,
        forecast_err=degradation * FORECAST_MAX,
        simplify_queueing=fidelity < SIMPLIFY_BELOW,
        horizon_ticks=horizon_ticks,
        tolerance_margin=tolerance_margin,
    )


def apply_fidelity(state, config: TwinConfig, generator) -> None:
    sigma = config.sigma_obs
    drift = config.drift_pct
    forecast = config.forecast_err
    lag_factor = min(0.95, max(0.0, config.lag_ticks * 0.05))

    for node in state.nodes.values():
        if drift > 0.0:
            node.cpu_capacity *= 1.0 + generator.uniform(-drift, drift)
        if sigma > 0.0:
            node.cpu_reserved = max(
                0.0, node.cpu_reserved * (1.0 + generator.normal(0.0, sigma))
            )
        if lag_factor > 0.0:
            node.cpu_reserved *= 1.0 - lag_factor

    for service in state.services.values():
        if drift > 0.0:
            service.cpu_demand_per_req *= 1.0 + generator.uniform(-drift, drift)
        excess = service.mem_footprint - service.baseline_mem
        if sigma > 0.0:
            excess *= 1.0 + generator.normal(0.0, sigma)
        if lag_factor > 0.0:
            excess *= 1.0 - lag_factor
        service.mem_footprint = service.baseline_mem + max(0.0, excess)

    for link in state.links.values():
        excess = link.latency_multiplier - 1.0
        if sigma > 0.0:
            excess *= 1.0 + generator.normal(0.0, sigma)
        if lag_factor > 0.0:
            excess *= 1.0 - lag_factor
        link.latency_multiplier = 1.0 + max(0.0, excess)
        if lag_factor > 0.0:
            link.loss_rate *= 1.0 - lag_factor

    for workload in state.workloads.values():
        if forecast > 0.0:
            workload.rate *= 1.0 + generator.uniform(-forecast, forecast)
