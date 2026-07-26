from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from ..actions.executor import execute_action
from ..actions.schema import NoOp
from ..actions.validator import validate_action
from ..telemetry.collector import Collector, summarize_topology
from ..telemetry.summarizer import Summarizer
from .base import TwinFeedback, context_from_sim


class DecisionState(TypedDict, total=False):
    tick: int
    observation_text: str
    proposal: Optional[dict]
    verdict: Optional[dict]
    feedback: Optional[dict]
    retry_count: int
    rejections: int
    exhausted: bool
    applied_action: Optional[dict]
    action_valid: Optional[bool]
    record: Optional[dict]


@dataclass
class EpisodeResult:
    arm: str = ""
    seed: int = 0
    decision_interval: int = 0
    gate_enabled: bool = False
    decisions: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    violation_ticks: int = 0
    proposals: int = 0
    rejections: int = 0
    retries: int = 0
    exhaustions: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0


class GraphRuntime:
    def __init__(self, sim, agent, validator, collector, summarizer, config) -> None:
        self.sim = sim
        self.agent = agent
        self.validator = validator
        self.collector = collector
        self.summarizer = summarizer
        self.config = config
        self.actions_config = config.actions
        self.slo_config = config.slo
        self.latest_obs = None
        self.obs = None
        self.proposal = None
        self.first_proposal = None
        self.verdict = None
        self.result = None


def _action_dict(action) -> dict:
    return action.model_dump()


def _verdict_dict(verdict) -> dict:
    return {
        "approved": verdict.approved,
        "reason": verdict.reason,
        "action_violation_ticks": verdict.action_violation_ticks,
        "noop_violation_ticks": verdict.noop_violation_ticks,
    }


def _feedback_dict(feedback: TwinFeedback) -> dict:
    return {
        "approved": feedback.approved,
        "reason": feedback.reason,
        "predicted_metrics": feedback.predicted_metrics,
    }


def _feedback_from_dict(data) -> Optional[TwinFeedback]:
    if not data:
        return None
    return TwinFeedback(
        approved=data["approved"],
        reason=data["reason"],
        predicted_metrics=data.get("predicted_metrics", {}),
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _arm_name(agent, gate_enabled, config) -> str:
    base = getattr(agent, "name", "agent")
    if not gate_enabled:
        return base
    fidelity = getattr(config.twin, "fidelity", None)
    if fidelity is not None and fidelity >= 1.0:
        return f"{base}+perfect_twin"
    return f"{base}+twin"


def build_graph(runtime: GraphRuntime, config, gate_enabled: bool, checkpointer):
    retry_cap = config.graph.retry_cap
    interval = config.graph.decision_interval_ticks

    def observe(state: DecisionState) -> dict:
        obs = runtime.latest_obs
        context = context_from_sim(runtime.sim)
        if hasattr(runtime.agent, "set_context"):
            runtime.agent.set_context(context)
        runtime.obs = obs
        runtime.first_proposal = None
        runtime.result = None
        runtime.verdict = None
        return {
            "tick": obs.tick,
            "observation_text": runtime.summarizer.render(obs),
            "proposal": None,
            "verdict": None,
            "feedback": None,
            "retry_count": 0,
            "rejections": 0,
            "exhausted": False,
            "applied_action": None,
            "action_valid": None,
        }

    def propose(state: DecisionState) -> dict:
        feedback = _feedback_from_dict(state.get("feedback"))
        action = runtime.agent.decide(runtime.obs, feedback)
        runtime.proposal = action
        if runtime.first_proposal is None:
            runtime.first_proposal = action
        return {"proposal": _action_dict(action)}

    def validate(state: DecisionState) -> dict:
        verdict = runtime.validator.validate(runtime.sim, runtime.proposal, runtime.obs)
        runtime.verdict = verdict
        update = {"verdict": _verdict_dict(verdict)}
        if verdict.approved:
            return update
        update["rejections"] = state.get("rejections", 0) + 1
        if state.get("retry_count", 0) < retry_cap:
            update["retry_count"] = state.get("retry_count", 0) + 1
            update["feedback"] = _feedback_dict(_feedback_from_verdict(verdict))
            return update
        runtime.proposal = NoOp()
        update["exhausted"] = True
        update["proposal"] = _action_dict(NoOp())
        return update

    def route_validate(state: DecisionState) -> str:
        if state["verdict"]["approved"] or state.get("exhausted"):
            return "execute"
        return "propose"

    def execute(state: DecisionState) -> dict:
        action = runtime.proposal
        semantic = validate_action(action, runtime.sim.state, runtime.actions_config)
        applied = action if semantic.valid else NoOp()
        result = execute_action(runtime.sim, applied, runtime.actions_config)
        runtime.result = result
        return {"applied_action": _action_dict(applied), "action_valid": semantic.valid}

    def finalise(state: DecisionState) -> dict:
        verdict = state.get("verdict")
        record = {
            "tick": state["tick"],
            "agent": getattr(runtime.agent, "name", "agent"),
            "gate_enabled": gate_enabled,
            "decision_interval": interval,
            "prompt_version": getattr(runtime.agent, "prompt_version", None),
            "observation_digest": _digest(state.get("observation_text", "")),
            "proposed_action": _action_dict(runtime.first_proposal)
            if runtime.first_proposal is not None
            else None,
            "applied_action": state.get("applied_action"),
            "action_valid": state.get("action_valid"),
            "twin_verdict": verdict["approved"] if verdict else None,
            "twin_reason": verdict["reason"] if verdict else None,
            "retries": state.get("retry_count", 0),
            "rejections": state.get("rejections", 0),
            "exhausted": state.get("exhausted", False),
            "action_cost": runtime.result.cost if runtime.result is not None else 0.0,
        }
        return {"record": record}

    builder = StateGraph(DecisionState)
    builder.add_node("observe", observe)
    builder.add_node("propose", propose)
    builder.add_node("execute", execute)
    builder.add_node("finalise", finalise)
    builder.add_edge(START, "observe")
    builder.add_edge("observe", "propose")
    if gate_enabled:
        builder.add_node("validate", validate)
        builder.add_edge("propose", "validate")
        builder.add_conditional_edges(
            "validate", route_validate, {"execute": "execute", "propose": "propose"}
        )
    else:
        builder.add_edge("propose", "execute")
    builder.add_edge("execute", "finalise")
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _feedback_from_verdict(verdict) -> TwinFeedback:
    return TwinFeedback(
        approved=verdict.approved,
        reason=verdict.reason,
        predicted_metrics={
            "action_violation_ticks": verdict.action_violation_ticks,
            "noop_violation_ticks": verdict.noop_violation_ticks,
        },
    )


def _drive(runtime, graph, config, seed, gate_enabled, state_sink):
    result = EpisodeResult(
        arm=_arm_name(runtime.agent, gate_enabled, config),
        seed=seed,
        decision_interval=config.graph.decision_interval_ticks,
        gate_enabled=gate_enabled,
    )
    interval = config.graph.decision_interval_ticks
    total = config.sim.episode_ticks
    for _ in range(total):
        metrics = runtime.sim.step()
        obs = runtime.collector.observe(metrics)
        runtime.latest_obs = obs
        result.metrics.append(metrics)
        if metrics.tick % interval == 0:
            thread = {"configurable": {"thread_id": f"{seed}-{metrics.tick}"}}
            init = {
                "tick": metrics.tick,
                "retry_count": 0,
                "rejections": 0,
                "exhausted": False,
                "feedback": None,
            }
            if state_sink is not None:
                final = None
                for streamed in graph.stream(init, config=thread, stream_mode="values"):
                    state_sink.append(streamed)
                    final = streamed
            else:
                final = graph.invoke(init, config=thread)
            record = final["record"]
            result.decisions.append(record)
            result.proposals += 1
            result.rejections += record["rejections"]
            result.retries += record["retries"]
            result.exhaustions += 1 if record["exhausted"] else 0
    result.violation_ticks = runtime.collector.evaluator.total_violation_ticks
    client = getattr(runtime.agent, "client", None)
    if client is not None:
        result.llm_calls = len(client.records)
        result.llm_tokens = sum(r.tokens_in + r.tokens_out for r in client.records)
    return result


def run_episode(
    sim, agent, validator, config, seed, gate=None, checkpointer=None, state_sink=None
):
    gate_enabled = (validator is not None) if gate is None else gate
    collector = Collector(summarize_topology(sim.topology), config.slo)
    summarizer = Summarizer(config.slo)
    runtime = GraphRuntime(sim, agent, validator, collector, summarizer, config)

    if checkpointer is not None:
        graph = build_graph(runtime, config, gate_enabled, checkpointer)
        return _drive(runtime, graph, config, seed, gate_enabled, state_sink)

    Path(config.graph.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(config.graph.checkpoint_path) as sqlite_checkpointer:
        graph = build_graph(runtime, config, gate_enabled, sqlite_checkpointer)
        return _drive(runtime, graph, config, seed, gate_enabled, state_sink)
