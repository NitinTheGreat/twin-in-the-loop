import pytest

from twinloop.actions.schema import MigrateService, NoOp, RestartService
from twinloop.agent.base import TwinFeedback, context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import LLMConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.catalog import FAULT_TYPES
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.llm.budget import BudgetExceeded, BudgetGuard
from twinloop.llm.cache import CacheMiss, ResponseCache
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import ProviderResponse
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.seen_prompts = []

    def complete(self, messages, model, temperature, timeout):
        self.seen_prompts.append("\n".join(m["content"] for m in messages))
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ProviderResponse(text=text, tokens_in=10, tokens_out=10)


class ToolThenNoopProvider:
    def __init__(self):
        self.calls = 0
        self.seen_prompts = []

    def complete(self, messages, model, temperature, timeout):
        text = "\n".join(m["content"] for m in messages)
        self.seen_prompts.append(text)
        self.calls += 1
        if "TOOL RESULT" in text:
            reply = '{"thought": "done", "action": {"type": "no_op"}}'
        else:
            reply = '{"thought": "inspect", "tool": "get_topology", "tool_input": {}}'
        return ProviderResponse(text=reply, tokens_in=10, tokens_out=10)


def _config(tmp_path, **overrides):
    return LLMConfig(
        cache_dir=str(tmp_path),
        log_path=str(tmp_path / "calls.jsonl"),
        **overrides,
    )


def _client(provider, tmp_path, config=None, cache=None):
    config = config or _config(tmp_path)
    cache = cache or ResponseCache(
        tmp_path / "cache.json", bypass=config.cache_bypass, cache_only=config.cache_only
    )
    budget = BudgetGuard(config.max_calls, config.max_tokens)
    client = LLMClient(
        provider, config, cache=cache, budget=budget, log_path=tmp_path / "calls.jsonl"
    )
    return client, config


def _setup(seed=1, schedule=None, ticks=6):
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=seed, schedule=schedule
    )
    collector = Collector(summarize_topology(sim.topology), SLOConfig())
    obs = None
    for _ in range(ticks):
        obs = collector.observe(sim.step())
    return obs, context_from_sim(sim)


def test_schema_valid_response_produces_action(tmp_path):
    provider = FakeProvider(
        ['{"thought": "reset", "action": {"type": "restart_service", "service_id": "svc0"}}']
    )
    client, config = _client(provider, tmp_path)
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    action = agent.decide(obs)
    assert isinstance(action, RestartService)
    assert action.service_id == "svc0"
    assert provider.calls == 1


def test_malformed_json_retries_then_succeeds(tmp_path):
    provider = FakeProvider(["this is not json", '{"action": {"type": "no_op"}}'])
    client, config = _client(provider, tmp_path, config=_config(tmp_path, react_max_steps=5))
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    action = agent.decide(obs)
    assert isinstance(action, NoOp)
    assert agent.last_trace["malformed"] >= 1
    assert provider.calls == 2


def test_retry_cap_exhaustion_returns_no_op(tmp_path):
    provider = FakeProvider(["garbage without braces"])
    client, config = _client(
        provider, tmp_path, config=_config(tmp_path, react_max_steps=6, max_retries=2)
    )
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    action = agent.decide(obs)
    assert isinstance(action, NoOp)
    assert agent.last_trace["exhausted"] is True
    assert agent.last_trace["malformed"] >= 3


def test_nonexistent_service_caught_by_validator(tmp_path):
    provider = FakeProvider(
        [
            '{"action": {"type": "restart_service", "service_id": "ghost"}}',
            '{"action": {"type": "restart_service", "service_id": "svc0"}}',
        ]
    )
    client, config = _client(provider, tmp_path, config=_config(tmp_path, react_max_steps=5))
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    action = agent.decide(obs)
    assert isinstance(action, RestartService) and action.service_id == "svc0"
    assert agent.last_trace["rejected"] >= 1
    assert any("does not exist" in prompt for prompt in provider.seen_prompts)


def test_cache_hit_returns_action_without_provider(tmp_path):
    warm_provider = FakeProvider(['{"action": {"type": "no_op"}}'])
    client, config = _client(warm_provider, tmp_path)
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    agent.decide(obs)
    assert warm_provider.calls == 1

    cold_provider = FakeProvider(
        ['{"action": {"type": "migrate_service", "service_id": "svc0", "target_node_id": "edge1"}}']
    )
    cache = ResponseCache(tmp_path / "cache.json")
    budget = BudgetGuard(config.max_calls, config.max_tokens)
    client2 = LLMClient(
        cold_provider, config, cache=cache, budget=budget, log_path=tmp_path / "calls.jsonl"
    )
    agent2 = LLMAgent(client2, config)
    obs2, ctx2 = _setup()
    agent2.set_context(ctx2)
    action = agent2.decide(obs2)
    assert isinstance(action, NoOp)
    assert cold_provider.calls == 0


def test_cache_only_raises_on_miss(tmp_path):
    provider = FakeProvider(['{"action": {"type": "no_op"}}'])
    config = _config(tmp_path, cache_only=True)
    cache = ResponseCache(tmp_path / "empty.json", cache_only=True)
    client = LLMClient(
        provider, config, cache=cache, budget=BudgetGuard(10, 10**9), log_path=tmp_path / "c.jsonl"
    )
    with pytest.raises(CacheMiss):
        client.complete([{"role": "user", "content": "hello"}])


def test_budget_guard_raises_on_call_ceiling(tmp_path):
    provider = FakeProvider(["a", "b"])
    config = _config(tmp_path, max_calls=1)
    cache = ResponseCache(tmp_path / "cache.json")
    budget = BudgetGuard(1, 10**9)
    client = LLMClient(provider, config, cache=cache, budget=budget, log_path=tmp_path / "c.jsonl")
    client.complete([{"role": "user", "content": "one"}])
    with pytest.raises(BudgetExceeded):
        client.complete([{"role": "user", "content": "two"}])


def test_tool_calls_recorded_in_order(tmp_path):
    provider = FakeProvider(
        [
            '{"thought": "check node", "tool": "get_node_metrics", "tool_input": {"node_id": "edge0"}}',
            '{"thought": "check svc", "tool": "get_service_history", "tool_input": {"service_id": "svc0"}}',
            '{"thought": "hold", "action": {"type": "no_op"}}',
        ]
    )
    client, config = _client(provider, tmp_path, config=_config(tmp_path, react_max_steps=5))
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    action = agent.decide(obs)
    assert isinstance(action, NoOp)
    assert agent.last_trace["tools_called"] == ["get_node_metrics", "get_service_history"]


def test_feedback_rejection_prompts_reason_and_differs(tmp_path):
    provider = FakeProvider(
        [
            '{"action": {"type": "migrate_service", "service_id": "svc0", "target_node_id": "edge1"}}',
            '{"action": {"type": "restart_service", "service_id": "svc0"}}',
        ]
    )
    client, config = _client(provider, tmp_path)
    agent = LLMAgent(client, config)
    obs, ctx = _setup()
    agent.set_context(ctx)
    first = agent.decide(obs)

    feedback = TwinFeedback(approved=False, reason="migration would overload edge1")
    agent.set_context(ctx)
    second = agent.decide(obs, feedback)

    assert isinstance(first, MigrateService)
    assert second != first
    assert any("overload edge1" in prompt for prompt in provider.seen_prompts)
    assert agent.last_trace["feedback_changed"] is True


def _all_faults():
    return FaultSchedule(
        [
            FaultEvent("node_cpu_saturation", "edge0", 20, 30, 4.0),
            FaultEvent("node_crash", "edge1", 55, 25, 1.0),
            FaultEvent("link_degradation", "l_gw_edge2", 90, 30, 3.0),
            FaultEvent("link_failure", "l_gw_edge3", 125, 25, 1.0),
            FaultEvent("service_memory_leak", "svc0", 160, 30, 8.0),
            FaultEvent("traffic_surge", "svc2", 195, 25, 3.0),
        ]
    )


def test_full_prompt_never_leaks_fault_ground_truth(tmp_path):
    provider = ToolThenNoopProvider()
    config = _config(tmp_path, cache_bypass=True, react_max_steps=4)
    cache = ResponseCache(tmp_path / "cache.json", bypass=True)
    client = LLMClient(
        provider, config, cache=cache, budget=BudgetGuard(100000, 10**12), log_path=tmp_path / "c.jsonl"
    )
    agent = LLMAgent(client, config)
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=9, schedule=_all_faults()
    )
    collector = Collector(summarize_topology(sim.topology), SLOConfig())
    for _ in range(220):
        obs = collector.observe(sim.step())
        agent.set_context(context_from_sim(sim))
        agent.decide(obs)

    assert provider.seen_prompts
    for prompt in provider.seen_prompts:
        lowered = prompt.lower()
        for fault_type in FAULT_TYPES:
            assert fault_type not in prompt
        assert "fault" not in lowered
        assert "active_faults" not in lowered
        assert "cpu_reserved" not in lowered
        assert "latency_multiplier" not in lowered


def test_determinism_through_warm_cache(tmp_path):
    schedule = _all_faults()
    config = _config(tmp_path)

    def _run(provider, cache):
        client = LLMClient(
            provider, config, cache=cache, budget=BudgetGuard(100000, 10**12), log_path=tmp_path / "c.jsonl"
        )
        agent = LLMAgent(client, config)
        sim = NetworkSim(
            build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=5, schedule=schedule
        )
        collector = Collector(summarize_topology(sim.topology), SLOConfig())
        actions = []
        for _ in range(80):
            obs = collector.observe(sim.step())
            agent.set_context(context_from_sim(sim))
            action = agent.decide(obs)
            actions.append((action.type, getattr(action, "service_id", None)))
        return actions

    provider1 = FakeProvider(['{"action": {"type": "no_op"}}'])
    cache = ResponseCache(tmp_path / "cache.json")
    first = _run(provider1, cache)

    provider2 = FakeProvider(['{"action": {"type": "no_op"}}'])
    cache2 = ResponseCache(tmp_path / "cache.json")
    second = _run(provider2, cache2)

    assert first == second
    assert provider2.calls == 0
