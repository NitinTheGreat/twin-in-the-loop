"""Deterministic protocol tests; these do not measure a real model's repair skill."""
import copy
import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from twinloop.actions.schema import NoOp, RestartService
from twinloop.agent.base import context_from_sim
from twinloop.agent.graph import run_episode
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import ExperimentConfig, GraphConfig, LLMConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import ProviderResponse
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology

TOOL = '{"tool":"get_topology","tool_input":{}}'
REPAIR = '{"action":{"type":"restart_service","service_id":"svc0"}}'
HOLD = '{"action":{"type":"no_op"}}'
INVALID = '{"action":{"type":"restart_service","service_id":"ghost"}}'


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class Provider:
    def __init__(self, replies, clock=None, durations=()):
        self.replies = replies
        self.messages = []
        self.timeouts = []
        self.clock = clock
        self.durations = durations

    def complete(self, messages, model, temperature, timeout):
        index = len(self.messages)
        self.messages.append(copy.deepcopy(messages))
        self.timeouts.append(timeout)
        if self.clock and index < len(self.durations):
            self.clock.now += self.durations[index]
        reply = self.replies[min(index, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        # A controlled instruction-following provider, not a claim about real models.
        if reply == "follow_final":
            reply = REPAIR if "FINAL DECISION" in messages[-1]["content"] else TOOL
        return ProviderResponse(reply, 8, 8)


def setup(tmp_path, replies, **options):
    cfg = LLMConfig(cache_bypass=True, log_path=str(tmp_path / "calls.jsonl"), **options)
    provider = Provider(replies)
    agent = LLMAgent(LLMClient(provider, cfg), cfg)
    scfg = SimConfig()
    sim = NetworkSim(build_topology(TopologyConfig(), scfg), scfg, seed=11)
    obs = Collector(summarize_topology(sim.topology), SLOConfig()).observe(sim.step())
    agent.set_context(context_from_sim(sim))
    return agent, provider, sim, obs


@pytest.mark.parametrize("replies,outcome,calls,tool_count,action_type", [
    ([REPAIR], "decision_produced", 1, 0, RestartService),
    ([HOLD], "deliberate_no_op", 1, 0, NoOp),
    ([TOOL, REPAIR], "decision_produced", 2, 1, RestartService),
    ([TOOL] * 3 + ["follow_final"], "decision_produced", 4, 3, RestartService),
    ([TOOL] * 4 + [REPAIR], "tool_budget_exhausted", 4, 3, NoOp),
    ([TOOL] * 3 + [HOLD], "deliberate_no_op", 4, 3, NoOp),
    ([TOOL] * 3 + ["broken"], "final_parse_failure", 4, 3, NoOp),
    ([TOOL] * 3 + [INVALID], "validation_exhausted", 4, 3, NoOp),
    ([TimeoutError("controlled timeout")], "timeout", 1, 0, NoOp),
])
def test_matched_budget_outcomes(tmp_path, replies, outcome, calls, tool_count, action_type):
    agent, provider, _, obs = setup(tmp_path, replies)
    action = agent.decide(obs)
    assert isinstance(action, action_type)
    trace = agent.last_trace
    assert trace["outcome"] == outcome
    failed = outcome not in {"decision_produced", "deliberate_no_op"}
    assert trace["fallback"] is failed and trace["exhausted"] is failed
    assert trace["steps"] == len(provider.messages) == calls <= 4
    assert len(trace["tools_called"]) == tool_count
    if calls == 4:
        assert "FINAL DECISION" in provider.messages[-1][-1]["content"]
        evidence = [m for m in provider.messages[-1] if m["content"].startswith("TOOL RESULT")]
        assert len(evidence) == 3
        for result in evidence:
            assert '"id": "svc0"' in result["content"]


def test_provider_budget_exhaustion_is_not_deliberate_hold(tmp_path):
    agent, provider, _, obs = setup(tmp_path, [TOOL], max_calls=1)
    assert isinstance(agent.decide(obs), NoOp)
    assert len(provider.messages) == 1
    assert agent.last_trace["outcome"] == "provider_budget_exhausted"
    assert agent.last_trace["fallback"] is True


def test_token_budget_exhaustion_and_provider_error_are_explicit(tmp_path):
    from urllib.error import URLError
    for replies, options, outcome in [([REPAIR], {"max_tokens": 0}, "provider_budget_exhausted"),
                                      ([URLError("connection refused")], {}, "provider_error")]:
        agent, provider, _, obs = setup(tmp_path, replies, **options)
        assert isinstance(agent.decide(obs), NoOp)
        assert agent.last_trace["outcome"] == outcome
        assert agent.last_trace["fallback"] is True
        assert len(provider.messages) == (0 if options.get("max_tokens") == 0 else 1)


def test_finalization_time_reserved_and_timeout_propagated(tmp_path, monkeypatch):
    agent, provider, _, obs = setup(tmp_path, [TOOL, "follow_final"])
    clock = Clock()
    monkeypatch.setattr("twinloop.agent.llm_agent.time.perf_counter", clock)
    provider.clock, provider.durations = clock, [15, 1]
    assert isinstance(agent.decide(obs), RestartService)
    assert provider.timeouts == [15, 5]
    assert "FINAL DECISION" in provider.messages[1][-1]["content"]
    assert agent.last_trace["steps"] == 2


def test_overrun_does_not_start_another_call(tmp_path, monkeypatch):
    agent, provider, _, obs = setup(tmp_path, [TOOL, REPAIR])
    clock = Clock()
    monkeypatch.setattr("twinloop.agent.llm_agent.time.perf_counter", clock)
    provider.clock, provider.durations = clock, [21]
    assert isinstance(agent.decide(obs), NoOp)
    assert len(provider.messages) == 1
    assert agent.last_trace["outcome"] == "timeout"


def test_graph_retains_failure_provenance(tmp_path):
    agent, _, sim, _ = setup(tmp_path, [TOOL])
    cfg = ExperimentConfig(sim=SimConfig(episode_ticks=1), graph=GraphConfig(decision_interval_ticks=1))
    proposals = []
    result = run_episode(sim, agent, None, cfg, 11, checkpointer=MemorySaver(),
        decision_hook=lambda index, record, rows, snapshot: proposals.extend(rows))
    assert result.decisions[0]["decision_outcome"] == "tool_budget_exhausted"
    assert result.decisions[0]["decision_fallback"] is True
    assert proposals[0]["decision_trace"]["outcome"] == "tool_budget_exhausted"
    # The plant safely held; this does not turn a failed decision into an intentional hold.
    assert result.decisions[0]["applied_action"] == {"type": "no_op"}
    json.dumps(result.decisions, allow_nan=False)


@pytest.mark.parametrize("budget", [1, 2, 3, 4])
def test_last_call_is_reserved_for_every_budget(tmp_path, budget):
    agent, provider, _, obs = setup(tmp_path, ["follow_final"], react_max_steps=budget)
    assert isinstance(agent.decide(obs), RestartService)
    assert len(provider.messages) == budget
    assert agent.last_trace["tools_called"] == ["get_topology"] * (budget - 1)


def test_final_prompt_preserves_distinct_evidence_and_feedback(tmp_path):
    from twinloop.agent.base import TwinFeedback
    replies = [
        '{"tool":"get_node_metrics","tool_input":{"node_id":"edge0"}}',
        '{"tool":"get_service_history","tool_input":{"service_id":"svc0"}}',
        '{"tool":"get_link_metrics","tool_input":{"link_id":"l_gw_edge0"}}', HOLD,
    ]
    agent, provider, _, obs = setup(tmp_path, replies)
    agent._last_proposed = RestartService(service_id="svc0")
    agent.decide(obs, TwinFeedback(False, "restart would lose queued work"))
    final = "\n".join(m["content"] for m in provider.messages[-1])
    for name in ("get_node_metrics", "get_service_history", "get_link_metrics"):
        assert f"TOOL RESULT {name}:" in final
    assert "restart would lose queued work" in final
    assert agent.last_trace["feedback_changed"] is True
    assert agent.last_trace["outcome"] == "deliberate_no_op"


def test_final_repeats_rejected_action_is_validation_failure(tmp_path):
    from twinloop.agent.base import TwinFeedback
    agent, provider, _, obs = setup(tmp_path, [TOOL] * 3 + [REPAIR])
    agent._last_proposed = RestartService(service_id="svc0")
    assert isinstance(agent.decide(obs, TwinFeedback(False, "unsafe restart")), NoOp)
    assert agent.last_trace["outcome"] == "validation_exhausted"
    assert agent.last_trace["rejected"] == 1
    assert len(provider.messages) == 4


@pytest.mark.parametrize("last", [INVALID, '{"action":{"type":"unknown"}}', '{"tool":123,"tool_input":[]}'])
def test_invalid_exploration_still_has_final_opportunity(tmp_path, last):
    agent, provider, _, obs = setup(tmp_path, [last, REPAIR], max_retries=0)
    assert isinstance(agent.decide(obs), RestartService)
    assert "FINAL DECISION" in provider.messages[-1][-1]["content"]
    assert agent.last_trace["fallback"] is False


def test_experiment_json_retains_failure_and_retry_provenance(tmp_path):
    from twinloop.experiment.arms import RunSpec
    from twinloop.experiment.runner import run_single
    from twinloop.llm.budget import BudgetGuard
    from twinloop.llm.cache import ResponseCache
    from twinloop.config import TwinConfig
    cfg = ExperimentConfig(sim=SimConfig(episode_ticks=1), twin=TwinConfig(horizon_ticks=1),
                           llm=LLMConfig(cache_bypass=True))
    provider = Provider([TOOL])
    _, episode, records = run_single(cfg, RunSpec("A2", "llm", False, 1, None), tmp_path,
        lambda: provider, BudgetGuard(100, 10000), ResponseCache(tmp_path / "cache.json", bypass=True),
        True, MemorySaver())
    assert len(records) == 1
    assert records[0]["decision_outcome"] == "tool_budget_exhausted"
    assert records[0]["decision_fallback"] is True
    assert records[0]["decision_trace"]["steps"] == 4
    assert records[0]["was_applied"] is True  # Applied safe fallback, not a successful model decision.
    json.dumps(records, allow_nan=False)


def test_graph_stores_each_attempt_trace_separately(tmp_path):
    from twinloop.twin.validator import TwinVerdict

    class RejectOnce:
        calls = 0

        def validate(self, sim, action, obs):
            self.calls += 1
            return TwinVerdict(self.calls > 1, "controlled rejection", 0, 0)

    agent, _, sim, _ = setup(tmp_path, [REPAIR, TOOL, TOOL, TOOL, TOOL])
    cfg = ExperimentConfig(sim=SimConfig(episode_ticks=1), graph=GraphConfig(decision_interval_ticks=1))
    rows = []
    episode = run_episode(sim, agent, RejectOnce(), cfg, 11, checkpointer=MemorySaver(),
        decision_hook=lambda i, r, proposals, s: rows.extend(proposals))
    assert [r["decision_trace"]["outcome"] for r in rows] == ["decision_produced", "tool_budget_exhausted"]
    assert [r["was_applied"] for r in rows] == [False, True]
    assert episode.decisions[0]["decision_fallback"] is True


def test_dashboard_paths_expose_failure_category(tmp_path, monkeypatch):
    from twinloop.dashboard.driver import run_instrumented_episode
    from twinloop.dashboard.live import run_stream
    monkeypatch.chdir(tmp_path)
    # Replace only the transport; actual agent, graph alternatives and logging execute.
    provider = Provider([TOOL])
    monkeypatch.setattr("twinloop.dashboard.driver.ScriptedProvider.complete", provider.complete)
    monkeypatch.setattr("twinloop.dashboard.live.ScriptedProvider.complete", provider.complete)
    view = run_instrumented_episode(agent_kind="llm", gate_enabled=False, provider_name="scripted",
                                    seed=11, episode_ticks=1, cache_dir=str(tmp_path))
    assert view.decisions[0].proposals[0].decision_trace["outcome"] == "tool_budget_exhausted"
    events = list(run_stream(agent_kind="llm", gate=False, provider_name="scripted",
                            seed=11, ticks=1, counterfactual=True))
    proposal = next(e for e in events if e["type"] == "proposal")
    truth = next(e for e in events if e["type"] == "truth")["rows"][0]
    assert proposal["decision_outcome"] == truth["decision_outcome"] == "tool_budget_exhausted"
    assert proposal["decision_fallback"] is truth["decision_fallback"] is True
