from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from ..actions.executor import execute_action
from ..actions.schema import NoOp
from ..actions.validator import validate_action
from ..agent.base import context_from_sim
from ..agent.llm_agent import LLMAgent
from ..agent.null_agent import NullAgent
from ..agent.rule_agent import RuleAgent
from ..config import ExperimentConfig, GraphConfig, SimConfig, TwinConfig
from ..experiment.counterfactual import evaluate_decision
from ..faults.schedule import FaultSchedule, targets_from_topology
from ..llm.budget import BudgetGuard
from ..llm.cache import CacheMiss, ResponseCache
from ..llm.client import LLMClient
from ..llm.providers import GeminiProvider, LocalProvider, ProviderResponse
from ..sim.engine import NetworkSim, build_topology
from ..telemetry.collector import Collector, summarize_topology
from ..telemetry.summarizer import Summarizer
from ..twin.fidelity import fidelity_to_config
from ..twin.validator import TwinValidator, feedback_from_verdict

GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_CACHE = "results/dashboard_cache/cache.json"
LIVE_LOG = "results/logs/live_calls.jsonl"
TWIN_LOG = "results/logs/live_twin.jsonl"


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


class ScriptedProvider:
    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        if "VIOLATIONS:" not in prompt:
            reply = (
                '{"thought": "Every service is within its SLO right now, so the safest '
                'and cheapest choice is to do nothing.", "action": {"type": "no_op"}}'
            )
        elif "TOOL RESULT" not in prompt:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "' + victim + ' is breaching its SLO. Before acting I will '
                'pull its recent history to see whether latency or availability is the problem.", '
                '"tool": "get_service_history", "tool_input": {"service_id": "' + victim + '"}}'
            )
        else:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "Latency is elevated but availability looks intact and the host has '
                'spare capacity, so I will add a replica rather than risk migration downtime.", '
                '"action": {"type": "scale_service", "service_id": "' + victim + '", "delta_replicas": 1}}'
            )
        return ProviderResponse(
            text=reply, tokens_in=max(1, len(prompt) // 4), tokens_out=max(1, len(reply) // 4)
        )


class DeadProvider:
    def complete(self, *args, **kwargs):
        raise CacheMiss("cached-replay mode reached a prompt that is not in the cache")


class StreamingClient(LLMClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exchanges = []

    def complete(self, messages, *a, **k):
        text, record = super().complete(messages, *a, **k)
        self.exchanges.append(
            {
                "messages": [dict(m) for m in messages],
                "response": text,
                "model": record.model,
                "tokens_in": record.tokens_in,
                "tokens_out": record.tokens_out,
                "latency_ms": round(record.latency_ms, 1),
                "cache_hit": record.cache_hit,
                "prompt_chars": record.prompt_chars,
            }
        )
        return text, record


def provider_status(root=None):
    root = Path(root or ".")
    cache = root / GEMINI_CACHE
    return {
        "scripted": {
            "available": True,
            "label": "Scripted stand-in",
            "note": "Deterministic, offline, instant. Same agent code path, no network.",
        },
        "cached": {
            "available": cache.exists(),
            "label": "Recorded Gemini transcript",
            "note": "Replays the real recorded run from the response cache. Needs seed 0, 120 ticks, interval 10.",
        },
        "gemini": {
            "available": bool(os.environ.get("GEMINI_API_KEY")),
            "label": "Gemini, live",
            "note": "Calls the real API. Uses your GEMINI_API_KEY and costs money.",
        },
        "local": {
            "available": True,
            "label": "Local model via Ollama",
            "note": "Expects an OpenAI-compatible server at localhost:11434. Fails clearly if absent.",
        },
    }


def _build_agent(kind, provider_name, cfg):
    if kind == "null":
        return NullAgent(), None
    if kind == "rule":
        return RuleAgent(cfg.rule_agent, cfg.actions), None

    if provider_name == "cached":
        cfg.llm.model = GEMINI_MODEL
        cache = ResponseCache(GEMINI_CACHE, cache_only=True)
        provider = DeadProvider()
    elif provider_name == "gemini":
        cfg.llm.model = GEMINI_MODEL
        cache = ResponseCache(GEMINI_CACHE)
        provider = GeminiProvider(os.environ.get("GEMINI_API_KEY"))
    elif provider_name == "local":
        cache = ResponseCache("results/live_local_cache/cache.json")
        provider = LocalProvider(cfg.llm)
    else:
        cfg.llm.model = "scripted-stub"
        cache = ResponseCache("results/live_scripted_cache/cache.json")
        provider = ScriptedProvider()

    client = StreamingClient(
        provider,
        cfg.llm,
        cache=cache,
        budget=BudgetGuard(cfg.llm.max_calls, cfg.llm.max_tokens),
        log_path=LIVE_LOG,
    )
    return LLMAgent(client, cfg.llm, cfg.slo, cfg.actions), client


def _mean(xs):
    xs = list(xs)
    return float(np.mean(xs)) if xs else 0.0


def _traj(metrics):
    return [round(_mean(m.service_p95.values()) * 1000, 1) for m in metrics]


def run_stream(
    agent_kind="llm",
    provider_name="scripted",
    gate=True,
    fidelity=1.0,
    seed=15,
    ticks=120,
    interval=10,
    horizon=20,
    retry_cap=2,
    harm_threshold=3,
    arrival_rate=25.0,
    counterfactual=True,
):
    cfg = ExperimentConfig(
        sim=SimConfig(episode_ticks=ticks),
        graph=GraphConfig(decision_interval_ticks=interval, retry_cap=retry_cap),
        twin=TwinConfig(fidelity=fidelity, horizon_ticks=horizon),
    )
    cfg.evaluation.harm_threshold_ticks = harm_threshold

    agent, client = _build_agent(agent_kind, provider_name, cfg)
    topology = build_topology(cfg.topology, cfg.sim, arrival_rate=arrival_rate)
    schedule = FaultSchedule.generate(seed, cfg.fault, targets_from_topology(topology))
    sim = NetworkSim(topology, cfg.sim, seed=seed, schedule=schedule)
    collector = Collector(summarize_topology(topology), cfg.slo)
    summarizer = Summarizer(cfg.slo)

    validator = None
    if gate:
        validator = TwinValidator(
            fidelity_to_config(fidelity, horizon, cfg.twin.tolerance_margin),
            seed=seed,
            actions_config=cfg.actions,
            slo_config=cfg.slo,
            log_path=TWIN_LOG,
        )

    yield {
        "type": "meta",
        "config": {
            "agent": agent_kind,
            "provider": provider_name if agent_kind == "llm" else None,
            "model": cfg.llm.model if agent_kind == "llm" else None,
            "gate": gate,
            "fidelity": fidelity if gate else None,
            "seed": seed,
            "ticks": ticks,
            "interval": interval,
            "horizon": horizon,
            "retry_cap": retry_cap,
            "harm_threshold": harm_threshold,
            "counterfactual": counterfactual,
        },
        "fidelity_axes": validator._fidelity_dict() if validator else None,
        "topology": {
            "nodes": [{"id": n.id, "role": n.role} for n in topology.nodes],
            "links": [{"id": l.id, "a": l.endpoints[0], "b": l.endpoints[1]} for l in topology.links],
            "services": [{"id": s.id, "host": s.host_node_id} for s in topology.services],
        },
        "faults": [
            {
                "type": e.type,
                "target": e.target,
                "start": e.start_tick,
                "end": e.start_tick + e.duration,
                "magnitude": round(e.magnitude, 2),
            }
            for e in schedule.events
            if e.start_tick < ticks
        ],
    }

    totals = {
        "proposals": 0, "harmful": 0, "blocked": 0, "safe_blocked": 0,
        "retries": 0, "exhausted": 0, "llm_calls": 0,
        "tokens_in": 0, "tokens_out": 0, "twin_ms": 0.0, "twin_n": 0,
    }

    for _ in range(ticks):
        metrics = sim.step()
        obs = collector.observe(metrics)
        tick = metrics.tick
        active = [
            {"type": e.type, "target": e.target}
            for e in sim._injector.active_events(tick)
        ]

        yield {
            "type": "tick",
            "tick": tick,
            "p95": round(_mean(metrics.service_p95.values()) * 1000, 1),
            "violations": sum(1 for s in obs.slo_status.values() if not s.compliant),
            "cum": collector.evaluator.total_violation_ticks,
            "sp": {k: round(v * 1000, 1) for k, v in metrics.service_p95.items()},
            "sd": {k: round(v, 3) for k, v in metrics.service_drop_rate.items()},
            "so": {k: (1 if v.compliant else 0) for k, v in obs.slo_status.items()},
            "sh": {k: s.host_node_id for k, s in sim.state.services.items()},
            "u": {k: round(v, 3) for k, v in metrics.node_utilisation.items()},
            "ns": {k: n.status for k, n in sim.state.nodes.items() if n.status != "healthy"},
            "ll": {k: round(v, 2) for k, v in metrics.link_latency.items() if v > 5.01},
            "ls": {k: l.status for k, l in sim.state.links.items() if l.status != "up"},
            "f": [f["type"] + " on " + f["target"] for f in active],
            "decision": tick % interval == 0,
        }

        if tick % interval != 0:
            continue

        pre = sim.fork() if counterfactual else None
        agent.set_context(context_from_sim(sim))
        if client is not None:
            client.exchanges = []

        yield {
            "type": "decision_open",
            "tick": tick,
            "observation": summarizer.render(obs),
            "slo_before": collector.evaluator.total_violation_ticks,
            "faults_now": [f["type"] + " on " + f["target"] for f in active],
        }

        feedback = None
        records = []
        retries = 0
        applied = None
        exhausted = False

        while True:
            try:
                action = agent.decide(obs, feedback)
            except CacheMiss as error:
                yield {
                    "type": "error",
                    "tick": tick,
                    "message": str(error),
                    "hint": "The recorded transcript only covers seed 0, 120 ticks, decision interval 10, twin off. Switch the brain to the scripted stand-in, or use those exact settings.",
                }
                return
            except Exception as error:
                yield {
                    "type": "error",
                    "tick": tick,
                    "message": f"{type(error).__name__}: {error}",
                    "hint": "The model call failed. Check the provider is reachable and the key is set.",
                }
                return

            trace = getattr(agent, "last_trace", None)
            if client is not None:
                for turn, exchange in enumerate(client.exchanges):
                    totals["llm_calls"] += 1
                    totals["tokens_in"] += exchange["tokens_in"]
                    totals["tokens_out"] += exchange["tokens_out"]
                    yield {"type": "llm_call", "tick": tick, "retry": retries, "turn": turn, **exchange}
                client.exchanges = []

            reasoning = (
                list(trace.get("reasoning", []))
                if trace
                else ([getattr(agent, "last_reason", "")] if getattr(agent, "last_reason", "") else [])
            )
            yield {
                "type": "proposal",
                "tick": tick,
                "retry": retries,
                "action": action.model_dump(),
                "reasoning": reasoning,
                "tools": list(trace.get("tools_called", [])) if trace else [],
                "steps": trace.get("steps") if trace else None,
                "agent_exhausted": bool(trace.get("exhausted")) if trace else False,
            }
            totals["proposals"] += 1

            verdict = None
            if gate:
                verdict = validator.validate(sim, action, obs)
                totals["twin_ms"] += verdict.cost_ms
                totals["twin_n"] += 1
                yield {
                    "type": "verdict",
                    "tick": tick,
                    "retry": retries,
                    "approved": verdict.approved,
                    "reason": verdict.reason,
                    "action_vt": verdict.action_violation_ticks,
                    "noop_vt": verdict.noop_violation_ticks,
                    "action_p95": _traj(verdict.action_metrics),
                    "noop_p95": _traj(verdict.noop_metrics),
                    "cost_ms": round(verdict.cost_ms, 1),
                }

            records.append(
                {"action": action, "retry_index": retries, "verdict": verdict, "was_applied": False}
            )

            if verdict is None or verdict.approved:
                applied = action
                records[-1]["was_applied"] = True
                break
            if retries >= retry_cap:
                applied = NoOp()
                exhausted = True
                totals["exhausted"] += 1
                yield {"type": "exhausted", "tick": tick, "retry_cap": retry_cap}
                break
            retries += 1
            totals["retries"] += 1
            feedback = feedback_from_verdict(verdict)
            yield {"type": "retry", "tick": tick, "retry": retries, "reason": verdict.reason}

        semantic = validate_action(applied, sim.state, cfg.actions)
        final = applied if semantic.valid else NoOp()
        result = execute_action(sim, final, cfg.actions)
        yield {
            "type": "applied",
            "tick": tick,
            "action": final.model_dump(),
            "semantically_valid": semantic.valid,
            "cost": result.cost,
            "reason": result.reason,
            "exhausted": exhausted,
        }

        if counterfactual:
            truths = evaluate_decision(pre, records, cfg)
            rows = []
            for record, gt in zip(records, truths):
                harmful = bool(gt.harmful)
                blocked = record["verdict"] is not None and not record["verdict"].approved
                if harmful:
                    totals["harmful"] += 1
                    if blocked:
                        totals["blocked"] += 1
                elif blocked:
                    totals["safe_blocked"] += 1
                rows.append(
                    {
                        "retry": record["retry_index"],
                        "action": record["action"].model_dump(),
                        "cf_action_vt": gt.cf_action_violation_ticks,
                        "cf_noop_vt": gt.cf_noop_violation_ticks,
                        "harm": gt.harm_delta,
                        "harmful": harmful,
                        "was_applied": record["was_applied"],
                        "blocked": blocked,
                        "cf_ms": round(gt.cf_wallclock_ms, 1),
                    }
                )
            yield {"type": "truth", "tick": tick, "rows": rows}

        yield {"type": "decision_close", "tick": tick}

    yield {
        "type": "done",
        "violation_ticks": collector.evaluator.total_violation_ticks,
        "totals": {
            **totals,
            "twin_ms": round(totals["twin_ms"] / totals["twin_n"], 1) if totals["twin_n"] else None,
        },
    }
