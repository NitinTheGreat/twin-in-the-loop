import copy
import hashlib
import json
from pathlib import Path

import pytest

from twinloop.actions.schema import NoOp, ParseError, RestartService, parse_action
from twinloop.actions.validator import validate_action
from twinloop.agent.base import context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import LLMConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import ProviderResponse
from twinloop.llm.structured import extract_json
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology


CORPUS_CASES = json.loads(
    (Path(__file__).parent / "fixtures/llm_control_character_responses.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("newline", ["\n", "\t", "\r", "\r\n"])
def test_literal_control_characters_preserve_reasoning_and_action(newline):
    text = '{"thought":"first' + newline + 'second","action":{"type":"restart_service","service_id":"svc0"}}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    parsed = extract_json(text)
    assert parsed["thought"] == "first" + newline + "second"
    assert parse_action(parsed["action"]) == RestartService(service_id="svc0")


@pytest.mark.parametrize("text", [
    '{"thought":"unescaped "quote" here","action":{"type":"no_op"}}',
    '{"action":{"type":"no_op"},}',
    '{"action":{"type":"no_op"}',
    '{"action":{"type":"no_op"}} {"action":{"type":"restart_service","service_id":"svc0"}}',
    '{"thought":"invalid\\qescape","action":{"type":"no_op"}}',
    '{"thought" "missing colon","action":{"type":"no_op"}}',
])
def test_no_ambiguous_json_repair(text):
    assert extract_json(text) is None


@pytest.mark.parametrize("action", [
    '{"type":"not_an_action"}',
    '{"type":"restart_service"}',
    '{"type":"restart_service","service_id":123}',
    '{"type":"no_op","service_id":"svc0"}',
    '{"type": ["no_op"]}',
])
def test_relaxed_string_decoding_preserves_action_schema_enforcement(action):
    parsed = extract_json('{"thought":"first\nsecond","action":' + action + '}')
    assert isinstance(parse_action(parsed["action"]), ParseError)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Provider:
    def __init__(self, replies, clock=None, durations=()):
        self.replies = list(replies)
        self.clock = clock
        self.durations = durations
        self.calls = []

    def complete(self, messages, model, temperature, timeout):
        index = len(self.calls)
        self.calls.append(copy.deepcopy(messages))
        if self.clock is not None:
            self.clock.now += self.durations[index]
        reply = self.replies[index]
        if isinstance(reply, Exception):
            raise reply
        return ProviderResponse(reply, 8, 8)


def context():
    config = SimConfig()
    sim = NetworkSim(build_topology(TopologyConfig(), config), config, seed=11)
    obs = Collector(summarize_topology(sim.topology), SLOConfig()).observe(sim.step())
    return obs, context_from_sim(sim)


def agent_for(provider, tmp_path, cache_only=False):
    config = LLMConfig(cache_dir=str(tmp_path), log_path=str(tmp_path / "calls.jsonl"), cache_only=cache_only)
    cache = ResponseCache(tmp_path / "cache.json", cache_only=cache_only)
    client = LLMClient(provider, config, cache=cache, budget=BudgetGuard(100, 10000))
    agent = LLMAgent(client, config)
    obs, ctx = context()
    agent.set_context(ctx)
    return agent, obs


@pytest.mark.parametrize("control", ["\n", "\t", "\r"])
@pytest.mark.parametrize("action,outcome,expected_type", [
    ('{"type":"restart_service","service_id":"svc0"}', "decision_produced", RestartService),
    ('{"type":"restart_service","service_id":"ghost"}', "validation_exhausted", NoOp),
    ('{"type":"restart_service","service_id":123}', "final_parse_failure", NoOp),
])
def test_literal_control_response_still_passes_schema_and_state_validation(tmp_path, control, action, outcome, expected_type):
    reply = '{"thought":"first' + control + 'second","action":' + action + '}'
    provider = Provider([reply] * 4)
    agent, obs = agent_for(provider, tmp_path)
    parsed_action = parse_action(extract_json(reply)["action"])
    if outcome == "validation_exhausted":
        assert isinstance(parsed_action, RestartService)
        verdict = validate_action(parsed_action, agent._context.state, agent.actions_config)
        assert verdict.valid is False
        assert verdict.reason == "service ghost does not exist"
    result = agent.decide(obs)
    assert isinstance(result, expected_type)
    assert agent.last_trace["outcome"] == outcome
    assert agent.last_trace["fallback"] is (outcome != "decision_produced")
    if outcome == "decision_produced":
        assert result == RestartService(service_id="svc0")
    elif outcome == "validation_exhausted":
        assert agent.last_trace["malformed"] == 0
        assert agent.last_trace["rejected"] == 4


@pytest.mark.parametrize("reply", [
    '{"thought":"first\nsecond","action":{"type":"no_op"},}',
    '{"thought":"unescaped "quote"","action":{"type":"no_op"}}',
    '{"action":{"type":"restart_service","service_id":"svc0"}',
    '{"thought":"invalid\\qescape","action":{"type":"no_op"}}',
    '{"action":{"type":"no_op"}} {"action":{"type":"no_op"}}',
])
def test_other_malformed_output_is_classified_as_final_parse_failure(tmp_path, reply):
    provider = Provider([reply] * 4)
    agent, obs = agent_for(provider, tmp_path)
    assert extract_json(reply) is None
    assert isinstance(agent.decide(obs), NoOp)
    assert agent.last_trace["outcome"] == "final_parse_failure"
    assert agent.last_trace["fallback"] is True
    assert agent.last_trace["malformed"] == 4
    assert agent.last_trace["rejected"] == 0


@pytest.mark.parametrize("case", CORPUS_CASES, ids=lambda case: case["id"])
def test_actual_cached_response_regression(tmp_path, case):
    text = case["text"]
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == case["text_sha256"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    payload = extract_json(text)
    assert payload == case["expected_payload"]
    if payload is not None:
        action = parse_action(payload["action"])
        assert not isinstance(action, ParseError)
        assert action.model_dump() == case["expected_action"]
    else:
        provider = Provider([text] * 4)
        agent, obs = agent_for(provider, tmp_path)
        assert isinstance(agent.decide(obs), NoOp)
        assert agent.last_trace["outcome"] == case["expected_outcome"] == "final_parse_failure"
        assert agent.last_trace["fallback"] is True
        assert agent.last_trace["rejected"] == 0


@pytest.mark.parametrize("durations,outcome", [([15, 1], "decision_produced"), ([21], "timeout")])
def test_clock_checkpoints_replay_cached_finalization_and_late_response(tmp_path, durations, outcome):
    replies = ['{"tool":"get_topology","tool_input":{}}',
               '{"action":{"type":"restart_service","service_id":"svc0"}}']
    clock = Clock()
    provider = Provider(replies, clock, durations)
    agent, obs = agent_for(provider, tmp_path)
    agent.clock = clock
    live_action = agent.decide(obs)
    live_trace = copy.deepcopy(agent.last_trace)
    assert live_trace["outcome"] == outcome
    assert live_trace["clock_offsets"][0] == 0.0
    assert live_trace["clock_offsets"] == sorted(live_trace["clock_offsets"])
    offline_provider = Provider([])
    replay, obs = agent_for(offline_provider, tmp_path, cache_only=True)
    replay.clock = iter(live_trace["clock_offsets"]).__next__
    assert replay.decide(obs) == live_action
    assert replay.last_trace == live_trace
    assert offline_provider.calls == []
    assert replay.client.budget.calls == 0


def test_clock_offsets_are_reset_for_each_decision(tmp_path):
    provider = Provider(['{"action":{"type":"no_op"}}'] * 2)
    agent, obs = agent_for(provider, tmp_path)
    clock = Clock()
    agent.clock = clock
    agent.decide(obs)
    first = agent.last_trace["clock_offsets"]
    clock.now = 400.0
    agent.decide(obs)
    assert agent.last_trace["clock_offsets"] == first == [0.0, 0.0, 0.0]
    assert agent.last_trace["clock_offsets"] is not first


def test_unsupported_provider_exception_is_not_silently_classified(tmp_path):
    provider = Provider([RuntimeError("unexpected bug")])
    agent, obs = agent_for(provider, tmp_path)
    with pytest.raises(RuntimeError, match="unexpected bug"):
        agent.decide(obs)
