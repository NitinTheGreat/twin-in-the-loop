import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from twinloop.actions.schema import NoOp
from twinloop.agent.graph import GraphRuntime, build_graph, run_episode
from twinloop.agent.llm_agent import LLMAgent
from twinloop.agent.null_agent import NullAgent
from twinloop.agent.rule_agent import RuleAgent
from twinloop.config import (
    ExperimentConfig,
    GraphConfig,
    LLMConfig,
    SimConfig,
    TopologyConfig,
    TwinConfig,
)
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import ProviderResponse
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.telemetry.summarizer import Summarizer
from twinloop.twin.validator import TwinValidator, TwinVerdict


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.seen_prompts = []

    def complete(self, messages, model, temperature, timeout):
        self.seen_prompts.append("\n".join(m["content"] for m in messages))
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ProviderResponse(text=text, tokens_in=8, tokens_out=8)


class SpyValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, sim, action, obs):
        self.calls += 1
        return TwinVerdict(True, "spy approve", 0, 0)


class StubApprove:
    def validate(self, sim, action, obs):
        return TwinVerdict(True, "stub approve", 0, 0)


class StubReject:
    def validate(self, sim, action, obs):
        return TwinVerdict(False, "stub predicted availability loss", 10, 0)


class StubRejectOnce:
    def __init__(self):
        self.count = 0

    def validate(self, sim, action, obs):
        self.count += 1
        if self.count == 1:
            return TwinVerdict(False, "stub rejection reason zeta", 10, 0)
        return TwinVerdict(True, "stub approve", 0, 0)


class FailOnceAgent:
    name = "flaky"

    def __init__(self):
        self.failed = False

    def set_context(self, context):
        return None

    def decide(self, obs, feedback=None):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated interruption")
        return NoOp()


def _config(episode_ticks=40, interval=10, retry_cap=2, horizon=15):
    return ExperimentConfig(
        sim=SimConfig(episode_ticks=episode_ticks),
        graph=GraphConfig(decision_interval_ticks=interval, retry_cap=retry_cap),
        twin=TwinConfig(horizon_ticks=horizon),
    )


def _schedule():
    return FaultSchedule([FaultEvent("node_cpu_saturation", "edge2", 12, 200, 4.5)])


def _sim(seed=1):
    return NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=seed, schedule=_schedule()
    )


def _llm_agent(cache_dir, responses):
    provider = FakeProvider(responses)
    config = LLMConfig(cache_dir=str(cache_dir), log_path=str(Path(cache_dir) / "calls.jsonl"))
    cache = ResponseCache(Path(cache_dir) / "cache.json")
    client = LLMClient(
        provider, config, cache=cache, budget=BudgetGuard(100000, 10**12), log_path=str(Path(cache_dir) / "calls.jsonl")
    )
    return LLMAgent(client, config), provider


def _twin(horizon=15, fidelity=1.0, tolerance=0.0, log_dir="."):
    return TwinValidator(
        TwinConfig(fidelity=fidelity, horizon_ticks=horizon, tolerance_margin=tolerance),
        seed=0,
        log_path=str(Path(log_dir) / "twin.jsonl"),
    )


def test_all_six_arms_run_end_to_end(tmp_path):
    config = _config()
    arms = [
        (NullAgent(), None, None),
        (RuleAgent(), None, None),
        (_llm_agent(tmp_path / "a2", ['{"action": {"type": "no_op"}}'])[0], None, None),
        (
            _llm_agent(tmp_path / "a3", ['{"action": {"type": "no_op"}}'])[0],
            _twin(log_dir=tmp_path),
            None,
        ),
        (RuleAgent(), _twin(log_dir=tmp_path), None),
        (
            _llm_agent(tmp_path / "a5", ['{"action": {"type": "no_op"}}'])[0],
            _twin(fidelity=1.0, log_dir=tmp_path),
            None,
        ),
    ]
    for agent, validator, gate in arms:
        result = run_episode(
            _sim(), agent, validator, config, seed=1, gate=gate, checkpointer=MemorySaver()
        )
        assert len(result.decisions) == 4
        assert len(result.metrics) == 40


def test_gate_disabled_never_calls_validator(tmp_path):
    spy = SpyValidator()
    run_episode(_sim(), RuleAgent(), spy, _config(), seed=1, gate=False, checkpointer=MemorySaver())
    assert spy.calls == 0


def test_gate_enabled_always_approve_executes_all(tmp_path):
    result = run_episode(
        _sim(), RuleAgent(), StubApprove(), _config(), seed=1, checkpointer=MemorySaver()
    )
    assert result.decisions
    for decision in result.decisions:
        assert decision["rejections"] == 0
        assert decision["exhausted"] is False
        assert decision["applied_action"] == decision["proposed_action"]


def test_gate_always_reject_exhausts_and_records(tmp_path):
    config = _config()
    result = run_episode(
        _sim(), RuleAgent(), StubReject(), config, seed=1, checkpointer=MemorySaver()
    )
    assert result.exhaustions == len(result.decisions)
    for decision in result.decisions:
        assert decision["exhausted"] is True
        assert decision["retries"] == config.graph.retry_cap
        assert decision["applied_action"]["type"] == "no_op"


def test_feedback_reaches_agent(tmp_path):
    agent, provider = _llm_agent(
        tmp_path / "fb",
        [
            '{"action": {"type": "scale_service", "service_id": "svc0", "delta_replicas": 1}}',
            '{"action": {"type": "restart_service", "service_id": "svc0"}}',
        ],
    )
    config = _config(episode_ticks=10, interval=10)
    result = run_episode(
        _sim(), agent, StubRejectOnce(), config, seed=1, checkpointer=MemorySaver()
    )
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision["proposed_action"] != decision["applied_action"]
    assert decision["retries"] == 1
    assert any("zeta" in prompt for prompt in provider.seen_prompts)


def test_graph_state_is_json_serialisable(tmp_path):
    sink = []
    run_episode(
        _sim(),
        RuleAgent(),
        _twin(log_dir=tmp_path),
        _config(),
        seed=1,
        checkpointer=MemorySaver(),
        state_sink=sink,
    )
    assert sink
    for state in sink:
        json.dumps(state)


def test_decision_cadence(tmp_path):
    config = _config(episode_ticks=300, interval=10)
    result = run_episode(_sim(), NullAgent(), None, config, seed=1, checkpointer=MemorySaver())
    expected = len([t for t in range(300) if t % 10 == 0])
    assert expected == 30
    assert len(result.decisions) == expected


def test_non_mutation_through_loop(tmp_path):
    config = _config()
    forked = run_episode(
        _sim(),
        RuleAgent(),
        _twin(tolerance=1e9, log_dir=tmp_path),
        config,
        seed=1,
        checkpointer=MemorySaver(),
    )
    stubbed = run_episode(
        _sim(), RuleAgent(), StubApprove(), config, seed=1, checkpointer=MemorySaver()
    )
    assert forked.metrics == stubbed.metrics


def test_determinism_with_warm_cache(tmp_path):
    config = _config()
    cache_dir = tmp_path / "shared"

    agent1, _ = _llm_agent(cache_dir, ['{"action": {"type": "no_op"}}'])
    first = run_episode(_sim(), agent1, None, config, seed=1, checkpointer=MemorySaver())

    agent2, provider2 = _llm_agent(
        cache_dir,
        ['{"action": {"type": "migrate_service", "service_id": "svc0", "target_node_id": "edge1"}}'],
    )
    second = run_episode(_sim(), agent2, None, config, seed=1, checkpointer=MemorySaver())

    assert first.decisions == second.decisions
    assert first.metrics == second.metrics
    assert provider2.calls == 0


def test_checkpoint_resume(tmp_path):
    config = _config()
    sim = _sim()
    collector = Collector(summarize_topology(sim.topology), config.slo)
    summarizer = Summarizer(config.slo)
    agent = FailOnceAgent()
    runtime = GraphRuntime(sim, agent, None, collector, summarizer, config)
    metrics = sim.step()
    runtime.latest_obs = collector.observe(metrics)

    with SqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        graph = build_graph(runtime, config, False, checkpointer)
        thread = {"configurable": {"thread_id": "d1"}}
        init = {
            "tick": metrics.tick,
            "retry_count": 0,
            "rejections": 0,
            "exhausted": False,
            "feedback": None,
        }
        interrupted = False
        try:
            graph.invoke(init, config=thread)
        except RuntimeError:
            interrupted = True
        assert interrupted

        resumed = graph.invoke(None, config=thread)
        assert resumed["record"] is not None
        assert resumed["applied_action"]["type"] == "no_op"
