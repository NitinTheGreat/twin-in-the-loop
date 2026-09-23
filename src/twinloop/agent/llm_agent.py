from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError

from ..actions.schema import NoOp, ParseError, parse_action
from ..actions.validator import ValidationResult, validate_action
from ..config import ActionsConfig, LLMConfig, SLOConfig
from ..llm.structured import extract_json
from ..llm.budget import BudgetExceeded
from ..telemetry.summarizer import Summarizer
from ..sim.metrics import latency_ms
from .base import DecisionContext, TwinFeedback


def _load_prompt(version: str) -> str:
    path = Path(__file__).parent / "prompts" / f"llm_agent_{version}.txt"
    return path.read_text(encoding="utf-8")


class LLMAgent:
    name = "llm"

    def __init__(
        self,
        client,
        config: Optional[LLMConfig] = None,
        slo_config: Optional[SLOConfig] = None,
        actions_config: Optional[ActionsConfig] = None,
    ) -> None:
        self.client = client
        self.config = config or LLMConfig()
        self.actions_config = actions_config or ActionsConfig()
        self.summarizer = Summarizer(slo_config or SLOConfig())
        self.prompt_version = self.config.prompt_version
        self.template = _load_prompt(self.prompt_version)
        self._context: Optional[DecisionContext] = None
        self._last_proposed = None
        self.last_trace: dict = {}

    def set_context(self, context: DecisionContext) -> None:
        self._context = context

    def _validate(self, action) -> ValidationResult:
        ctx = self._context
        if ctx is None or ctx.state is None:
            return ValidationResult(True, "no context")
        return validate_action(action, ctx.state, self.actions_config)

    def _topology(self) -> dict:
        state = self._context.state
        return {
            "nodes": [
                {
                    "id": n.id,
                    "role": n.role,
                    "cpu_capacity": n.cpu_capacity,
                    "mem_capacity": n.mem_capacity,
                    "status": n.status,
                }
                for n in state.nodes.values()
            ],
            "services": [
                {
                    "id": s.id,
                    "host_node_id": s.host_node_id,
                    "replicas": s.replicas,
                    "status": s.status,
                }
                for s in state.services.values()
            ],
            "links": [
                {"id": link.id, "endpoints": list(link.endpoints), "status": link.status}
                for link in state.links.values()
            ],
        }

    def _node_metrics(self, node_id, obs) -> dict:
        state = self._context.state
        if node_id not in state.nodes:
            return {"error": f"unknown node {node_id}"}
        node = state.nodes[node_id]
        return {
            "id": node_id,
            "utilisation": obs.metrics.node_utilisation.get(node_id, 0.0),
            "status": node.status,
            "cpu_capacity": node.cpu_capacity,
            "mem_capacity": node.mem_capacity,
            "memory_utilisation": obs.metrics.node_memory_utilisation.get(node_id),
            "service_memory": {s.id: s.mem_footprint * max(s.replicas, 1)
                               for s in state.services.values() if s.host_node_id == node_id},
            "services": [
                s.id for s in state.services.values() if s.host_node_id == node_id
            ],
        }

    def _service_history(self, service_id, obs) -> dict:
        if service_id not in obs.slo_status:
            return {"error": f"unknown service {service_id}"}
        history = [
            {
                "tick": m.tick,
                "p95_ms": latency_ms(m.service_p95.get(service_id)),
                "throughput": m.service_throughput.get(service_id, 0.0),
                "drop_rate": m.service_drop_rate.get(service_id, 0.0),
                "queue_len": m.service_queue_len.get(service_id, 0),
            }
            for m in obs.history
        ]
        status = obs.slo_status[service_id]
        return {
            "id": service_id,
            "history": history,
            "compliant": status.compliant,
            "p95_ms": status.p95_ms,
        }

    def _link_metrics(self, link_id, obs) -> dict:
        state = self._context.state
        if link_id not in state.links:
            return {"error": f"unknown link {link_id}"}
        link = state.links[link_id]
        return {
            "id": link_id,
            "latency_ms": obs.metrics.link_latency.get(link_id, 0.0),
            "status": link.status,
            "endpoints": list(link.endpoints),
        }

    def _run_tool(self, name, args, obs) -> dict:
        if name == "get_topology":
            return self._topology()
        if name == "get_node_metrics":
            return self._node_metrics(args.get("node_id"), obs)
        if name == "get_service_history":
            return self._service_history(args.get("service_id"), obs)
        if name == "get_link_metrics":
            return self._link_metrics(args.get("link_id"), obs)
        return {"error": f"unknown tool {name}"}

    def _build_messages(self, obs, feedback: Optional[TwinFeedback]) -> list[dict]:
        lines = [
            f"Current network observation at tick {obs.tick}:",
            "",
            self.summarizer.render(obs),
            "",
        ]
        if feedback is not None and not feedback.approved:
            lines.append(
                f"SAFETY FEEDBACK: your previous action was rejected. Reason: {feedback.reason}"
            )
            if self._last_proposed is not None:
                lines.append(
                    f"The rejected action was: {json.dumps(self._last_proposed.model_dump())}"
                )
            lines.append("Choose a different action that avoids this problem.")
            lines.append("")
        lines.append("Respond now with a tool call or a final action as JSON.")
        return [
            {"role": "system", "content": self.template},
            {"role": "user", "content": "\n".join(lines)},
        ]

    def decide(self, obs, feedback: Optional[TwinFeedback] = None):
        messages = self._build_messages(obs, feedback)
        rejected_action = self._last_proposed if feedback is not None and not feedback.approved else None
        tools_called, reasoning = [], []
        malformed = rejected = steps = 0
        action = None
        outcome = "final_parse_failure"
        final_call = False
        clock_source = getattr(self, "clock", time.perf_counter)
        clock_offsets = []
        clock_origin = None

        def clock():
            nonlocal clock_origin
            current = clock_source()
            if clock_origin is None:
                clock_origin = current
            clock_offsets.append(current - clock_origin)
            return current

        deadline = clock() + self.config.react_timeout_seconds
        reserve = self.config.react_timeout_seconds * self.config.react_final_reserve_fraction

        for index in range(self.config.react_max_steps):
            remaining = deadline - clock()
            if remaining <= 0:
                outcome = "timeout"
                break
            final_call = (
                index == self.config.react_max_steps - 1
                or remaining <= reserve
                or malformed + rejected > self.config.max_retries
            )
            if final_call:
                messages.append({
                    "role": "user",
                    "content": (
                        'FINAL DECISION: tools are disabled. Use all evidence above and return '
                        'one JSON object {"action": {...}} using the action schema. '
                        'An intentional no_op is permitted. Do not request another tool.'
                    ),
                })
            timeout = remaining if final_call else remaining - reserve
            steps += 1
            try:
                text, _ = self.client.complete(messages, timeout_seconds=timeout)
            except BudgetExceeded:
                outcome = "provider_budget_exhausted"
                break
            except TimeoutError:
                outcome = "timeout"
                break
            except URLError as error:
                outcome = "timeout" if isinstance(error.reason, TimeoutError) else "provider_error"
                break

            if clock() >= deadline:
                outcome = "timeout"
                break
            payload = extract_json(text)
            if payload is not None and "thought" in payload:
                reasoning.append(str(payload["thought"]))
            message = "Reply with a single JSON tool call or action object."
            if payload is None:
                malformed += 1
                outcome = "final_parse_failure"
                message = "Your response was not valid JSON. " + message
            elif "tool" in payload:
                if final_call:
                    outcome = "tool_budget_exhausted"
                    break
                name = payload["tool"]
                args = payload.get("tool_input", {}) or {}
                if not isinstance(name, str) or not isinstance(args, dict):
                    malformed += 1
                    outcome = "final_parse_failure"
                    message = "Tool name must be a string and tool_input must be an object."
                else:
                    result = self._run_tool(name, args, obs)
                    tools_called.append(name)
                    message = f"TOOL RESULT {name}: {json.dumps(result)}"
            elif "action" in payload:
                parsed = parse_action(payload["action"])
                if isinstance(parsed, ParseError):
                    malformed += 1
                    outcome = "final_parse_failure"
                    message = f"That action failed schema parsing: {parsed.message}. Reply with a valid JSON action object."
                else:
                    verdict = self._validate(parsed)
                    if not verdict.valid or (rejected_action is not None and parsed == rejected_action):
                        rejected += 1
                        outcome = "validation_exhausted"
                        reason = verdict.reason if not verdict.valid else "same action already rejected"
                        message = f"That action was rejected by the validator: {reason}. Choose a different, valid action."
                    else:
                        action = parsed
                        outcome = "deliberate_no_op" if isinstance(action, NoOp) else "decision_produced"
            else:
                malformed += 1
                outcome = "final_parse_failure"

            if action is not None or final_call:
                break
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": message})

        fallback = action is None
        if fallback:
            action = NoOp()
        self._last_proposed = action
        self.last_trace = {
            "prompt_version": self.prompt_version,
            "tools_called": tools_called,
            "reasoning": reasoning,
            "steps": steps,
            "malformed": malformed,
            "rejected": rejected,
            "exhausted": fallback,
            "fallback": fallback,
            "outcome": outcome,
            "final_call": final_call,
            "action_type": action.type,
            "feedback_present": feedback is not None,
            "feedback_changed": action != rejected_action if rejected_action is not None else None,
            "clock_offsets": clock_offsets,
        }
        return action
