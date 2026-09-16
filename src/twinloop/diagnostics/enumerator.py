"""PRIVILEGED / DIAGNOSTIC ONLY: scheduled, full-state candidate search.

This is not an agent and must never be wired into an experimental arm. It uses
the exact fault schedule and cloned RNG, with no fidelity degradation. Values
are fixed-horizon service-violation-ticks, with hold after the final action.
All host targets, replica counts, and simple operational paths are enumerated;
continuous throttle rates use a declared grid. Optional depth-two search tries
every feasible pair on that grid at one prespecified second decision offset.
Absence of benefit is evidence for this candidate set, horizon and realization,
not proof of optimality over all timings, rates, or closed-loop policies.
"""
from dataclasses import dataclass, field

from ..actions.executor import execute_action
from ..actions.schema import (MigrateService, NoOp, RerouteTraffic, RestartService,
                              ScaleService, ThrottleService)
from ..actions.validator import validate_action
from ..config import ActionsConfig, SLOConfig
from ..sim.routing import paths, reroute_feasibility
from ..telemetry.slo import SLOEvaluator


def candidates(sim, config, throttle_fractions=(.25, .5, .75, 1.0)):
    """Enumerate the six action families; suppress changes while a service is pending."""
    state = sim.state
    yield NoOp()
    pending = {effect.service_id for effect in state.pending}
    for sid, service in sorted(state.services.items()):
        if sid in pending:
            continue
        choices = [RestartService(service_id=sid)]
        choices += [MigrateService(service_id=sid, target_node_id=nid)
                    for nid, node in sorted(state.nodes.items())
                    if node.role in ("edge", "gateway", "device") and nid != service.host_node_id]
        choices += [ScaleService(service_id=sid, delta_replicas=r - service.replicas)
                    for r in range(1, config.replica_cap + 1) if r != service.replicas]
        choices += [RerouteTraffic(service_id=sid, path_hint=path)
                    for path in paths(state, service.source_node_id, service.host_node_id)
                    if path != state.routes[sid]]
        rates = {sim.state.workloads[sid].rate * f for f in throttle_fractions}
        choices += [ThrottleService(service_id=sid, rate_limit=rate)
                    for rate in sorted(rates) if rate > 0 and rate != service.rate_limit]
        for action in choices:
            if validate_action(action, state, config).valid:
                yield action


@dataclass
class DiagnosticReport:
    diagnostic_only: bool = True
    information: str = "PRIVILEGED: exact state, scheduled faults, cloned RNG"
    objective: str = "fixed-horizon service-violation-ticks; hold continuation"
    horizon: int = 0
    max_depth: int = 1
    sequence_interval: int = 10
    throttle_fractions: tuple = (.25, .5, .75, 1.0)
    candidates_evaluated: int = 0
    hold_violation_ticks: int = 0
    best_violation_ticks: int = 0
    benefit_vs_hold: int = 0
    beneficial_opportunity: bool = False
    best_sequence: list = field(default_factory=list)
    best_service_violation_ticks: dict = field(default_factory=dict)
    reroute_feasibility: dict = field(default_factory=dict)


def _advance(sim, ticks, config):
    evaluator = SLOEvaluator(config)
    for _ in range(ticks):
        evaluator.evaluate(sim.step())
    return evaluator


def enumerate_opportunities(sim, horizon=60, actions_config=None, slo_config=None,
                            max_depth=1, sequence_interval=10,
                            throttle_fractions=(.25, .5, .75, 1.0)):
    """Offline search without mutating the input simulator or consuming its RNG."""
    if horizon < 1 or max_depth not in (1, 2):
        raise ValueError("positive horizon and depth 1 or 2 required")
    if max_depth == 2 and not 0 < sequence_interval < horizon:
        raise ValueError("second decision must be inside the horizon")
    actions_config = actions_config or ActionsConfig()
    slo_config = slo_config or SLOConfig()
    baseline = _advance(sim.fork(), horizon, slo_config)
    report = DiagnosticReport(
        horizon=horizon, max_depth=max_depth, sequence_interval=sequence_interval,
        throttle_fractions=tuple(throttle_fractions),
        hold_violation_ticks=baseline.total_violation_ticks,
        best_violation_ticks=baseline.total_violation_ticks,
        best_service_violation_ticks=dict(baseline.violation_ticks),
        best_sequence=[{"offset": 0, "action": NoOp().model_dump()}],
        reroute_feasibility=reroute_feasibility(sim.state, sim.state.services))
    for first in candidates(sim, actions_config, throttle_fractions):
        branch = sim.fork()
        execute_action(branch, first, actions_config)
        prefix = _advance(branch, sequence_interval if max_depth == 2 else 0, slo_config)
        seconds = candidates(branch, actions_config, throttle_fractions) if max_depth == 2 else [NoOp()]
        for second in seconds:
            final = branch.fork()
            if max_depth == 2:
                execute_action(final, second, actions_config)
            tail = _advance(final, horizon - (sequence_interval if max_depth == 2 else 0), slo_config)
            score = prefix.total_violation_ticks + tail.total_violation_ticks
            report.candidates_evaluated += 1
            if score < report.best_violation_ticks:
                report.best_violation_ticks = score
                report.best_sequence = [{"offset": 0, "action": first.model_dump()}]
                if max_depth == 2:
                    report.best_sequence.append({"offset": sequence_interval, "action": second.model_dump()})
                report.best_service_violation_ticks = {
                    sid: prefix.violation_ticks.get(sid, 0) + tail.violation_ticks.get(sid, 0)
                    for sid in sim.state.services}
    report.benefit_vs_hold = report.hold_violation_ticks - report.best_violation_ticks
    report.beneficial_opportunity = report.benefit_vs_hold > 0
    return report
