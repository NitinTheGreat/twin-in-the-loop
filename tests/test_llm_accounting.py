import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from twinloop.config import LLMConfig
from twinloop.llm.budget import BudgetExceeded, BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.client import LLMClient
from google.genai import types as genai_types

from twinloop.llm.providers import GeminiProvider, ProviderResponse


class FixedProvider:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, messages, model, temperature, timeout):
        self.calls += 1
        return self.response


class RecordingGeminiClient:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        return genai_types.GenerateContentResponse.model_validate(self.payload)


def test_gemini_accounts_for_thinking_and_retains_provider_provenance():
    usage = {"promptTokenCount": 10, "candidatesTokenCount": 3,
             "thoughtsTokenCount": 17, "totalTokenCount": 30}
    payload = {"candidates": [{"content": {"parts": [{"text": "reply"}]}}],
               "usageMetadata": usage, "modelVersion": "resolved-model-001"}
    provider = GeminiProvider("test-project", client=RecordingGeminiClient(payload))
    reply = provider.complete([{"role": "user", "content": "test"}], "alias", 0, 5)
    assert reply.tokens_in == 10
    assert reply.tokens_out == 20
    assert reply.tokens_thinking == 17
    assert reply.usage_metadata == usage
    assert reply.model_version == "resolved-model-001"
    assert reply.usage_complete


def test_overrun_is_logged_cached_persisted_and_blocks_further_paid_calls(tmp_path):
    ledger = tmp_path / "budget.json"
    cache = ResponseCache(tmp_path / "cache.json")
    budget = BudgetGuard(10, 20, path=ledger)
    provider = FixedProvider(ProviderResponse("reply", 10, 15, tokens_thinking=12,
                                             model_version="model-001"))
    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    client = LLMClient(provider, config, budget=budget, cache=cache)
    messages = [{"role": "user", "content": "first"}]
    with pytest.raises(BudgetExceeded, match="token ceiling"):
        client.complete(messages)
    assert budget.calls == 1 and budget.tokens == 25
    saved = json.loads(ledger.read_text())
    assert saved["tokens"] == 25 and saved["pending_call"] is False
    events = [json.loads(line) for line in Path(config.log_path).read_text().splitlines()]
    logged = events[0]
    assert events[1]["event"] == "budget_error" and events[1]["stage"] == "after_response"
    assert logged["tokens_thinking"] == 12 and logged["model_version"] == "model-001"
    assert logged["cache_key"] in json.loads(cache.path.read_text())
    with pytest.raises(BudgetExceeded):
        client.complete([{"role": "user", "content": "second"}])
    assert provider.calls == 1
    assert client.complete(messages)[0] == "reply"
    assert client.records[-1].cache_hit and provider.calls == 1
    restored = BudgetGuard(10, 20, path=ledger)
    with pytest.raises(BudgetExceeded):
        restored.charge_call()


def test_budget_resume_preserves_calls_and_rejects_changed_limits(tmp_path):
    ledger = tmp_path / "budget.json"
    first = BudgetGuard(1, 100, path=ledger)
    first.charge_call()
    first.charge_tokens(17)
    resumed = BudgetGuard(1, 100, path=ledger)
    assert resumed.calls == 1 and resumed.tokens == 17
    with pytest.raises(BudgetExceeded, match="call ceiling"):
        resumed.charge_call()
    with pytest.raises(ValueError, match="limits differ"):
        BudgetGuard(2, 100, path=ledger)


def test_unresolved_call_fails_closed_after_restart(tmp_path):
    ledger = tmp_path / "budget.json"
    first = BudgetGuard(3, 100, path=ledger)
    first.charge_call()
    resumed = BudgetGuard(3, 100, path=ledger)
    assert resumed.calls == 1 and resumed.pending_call
    with pytest.raises(BudgetExceeded, match="unresolved usage"):
        resumed.charge_call()


def test_missing_provider_usage_fails_closed_with_persistent_guard(tmp_path):
    budget = BudgetGuard(3, 100, path=tmp_path / "budget.json")
    provider = FixedProvider(ProviderResponse("reply", 10, 10, usage_complete=False))
    client = LLMClient(provider, LLMConfig(log_path=str(tmp_path / "calls.jsonl")),
                       cache=ResponseCache(tmp_path / "cache.json"), budget=budget)
    with pytest.raises(BudgetExceeded, match="incomplete"):
        client.complete([{"role": "user", "content": "test"}])
    assert budget.tokens == 20 and budget.pending_call
    assert len(client.records) == 1
    with pytest.raises(BudgetExceeded, match="unresolved usage"):
        budget.charge_call()


def test_zero_token_budget_never_calls_provider(tmp_path):
    provider = FixedProvider(ProviderResponse("reply", 10, 10))
    client = LLMClient(provider, LLMConfig(log_path=str(tmp_path / "calls.jsonl")),
                       cache=ResponseCache(tmp_path / "cache.json"), budget=BudgetGuard(3, 0))
    with pytest.raises(BudgetExceeded):
        client.complete([{"role": "user", "content": "test"}])
    assert provider.calls == 0


def test_cache_replace_failure_preserves_readable_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    cache = ResponseCache(path)
    cache.put("first", {"text": "one", "tokens_in": 1, "tokens_out": 1})

    def fail_replace(self, target):
        raise OSError("controlled interruption")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="controlled interruption"):
        cache.put("second", {"text": "two", "tokens_in": 1, "tokens_out": 1})
    assert json.loads(path.read_text()) == {"first": {"text": "one", "tokens_in": 1, "tokens_out": 1}}


def test_timeout_journal_and_reserved_liability_allow_bounded_continuation(tmp_path):
    class TimeOutOnce(FixedProvider):
        def max_token_reservation(self, messages):
            return 60

        def complete(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("controlled timeout")
            return self.response

    provider = TimeOutOnce(ProviderResponse("reply", 10, 10))
    budget = BudgetGuard(5, 130, path=tmp_path / "budget.json")
    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    client = LLMClient(provider, config, budget=budget, cache=ResponseCache(tmp_path / "cache.json"))
    with pytest.raises(TimeoutError):
        client.complete([{"role": "user", "content": "first"}])
    assert not client.records
    assert budget.tokens == budget.uncertain_tokens == 60
    assert not budget.pending_call
    client.complete([{"role": "user", "content": "second"}])
    assert budget.tokens == 80 and budget.uncertain_tokens == 60
    assert len(client.records) == 1
    with pytest.raises(BudgetExceeded, match="reserved tokens"):
        client.complete([{"role": "user", "content": "third"}])
    assert provider.calls == 2
    events = [json.loads(row) for row in Path(config.log_path).read_text().splitlines()]
    assert [event["event"] for event in events] == ["provider_error", "response", "budget_error"]
    assert events[-1]["stage"] == "before_call"
    assert events[0]["error_type"] == "timeout" and events[0]["reserved_tokens"] == 60
    assert events[0]["cache_key"] and events[0]["usage_unknown"]


def test_gemini_output_limit_and_serialized_prompt_reservation():
    client = RecordingGeminiClient({"candidates": [{"content": {"parts": [{"text": "done"}]}}],
                                    "usageMetadata": {"promptTokenCount": 7, "totalTokenCount": 9}})
    provider = GeminiProvider("test-project", max_attempts=1, max_output_tokens=8192, client=client)
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "test"}]
    response = provider.complete(messages, "alias", 0, 5)
    request = client.requests[0]
    assert request["config"].max_output_tokens == 8192
    assert request["config"].system_instruction == "system"
    assert request["contents"] == [{"role": "user", "parts": [{"text": "test"}]}]
    sent = {"contents": request["contents"], "systemInstruction": {"parts": [{"text": "system"}]},
            "generationConfig": {"temperature": 0, "maxOutputTokens": 8192}}
    assert provider.max_token_reservation(messages) >= len(json.dumps(sent).encode()) + 8192
    assert response.tokens_in + response.tokens_out == 9 and response.usage_complete


def test_invalid_provider_payload_is_journaled_and_classifiable(tmp_path):
    class BrokenProvider:
        def complete(self, *args):
            raise KeyError("choices")

    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    client = LLMClient(BrokenProvider(), config, cache=ResponseCache(tmp_path / "cache.json"))
    with pytest.raises(URLError, match="could not be decoded"):
        client.complete([{"role": "user", "content": "test"}])
    record = json.loads(Path(config.log_path).read_text())
    assert record["error_type"] == "provider_error" and record["exception_class"] == "KeyError"
    assert not client.records


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_configuration_and_billing_failures_block_persistently_without_exposing_key(tmp_path, status):
    class RefusingProvider:
        api_key = "test-secret-key-never-log"
        calls = 0

        def max_token_reservation(self, messages):
            return 100

        def complete(self, *args):
            self.calls += 1
            body = {"error": {"code": status, "status": "RESOURCE_EXHAUSTED",
                              "message": f"Prepayment exhausted for key {self.api_key}"}}
            raise HTTPError("https://unused", status, f"failed {self.api_key}", {},
                            io.BytesIO(json.dumps(body).encode()))

    provider = RefusingProvider()
    ledger = tmp_path / "budget.json"
    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    budget = BudgetGuard(5, 1000, path=ledger)
    client = LLMClient(provider, config, budget=budget, cache=ResponseCache(tmp_path / "cache.json"))
    with pytest.raises(HTTPError):
        client.complete([{"role": "user", "content": "first"}])
    record = json.loads(Path(config.log_path).read_text())
    assert record["event"] == "provider_error" and record["http_status"] == status
    assert record["provider_status"] == "RESOURCE_EXHAUSTED"
    assert record["provider_message"] == "Prepayment exhausted for key [REDACTED]"
    assert provider.api_key not in Path(config.log_path).read_text()
    assert provider.api_key not in ledger.read_text()
    assert budget.calls == 1 and budget.blocked_reason
    restored = BudgetGuard(5, 1000, path=ledger)
    assert restored.blocked_reason == budget.blocked_reason
    with pytest.raises(BudgetExceeded, match="provider circuit is blocked"):
        restored.charge_call(100)
    with pytest.raises(BudgetExceeded, match="provider circuit is blocked"):
        client.complete([{"role": "user", "content": "second"}])
    assert provider.calls == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_http_errors_do_not_block_the_provider_circuit(tmp_path, status):
    class TransientProvider:
        calls = 0

        def max_token_reservation(self, messages):
            return 100

        def complete(self, *args):
            self.calls += 1
            raise HTTPError("https://unused", status, "try again", {}, io.BytesIO(b"not json"))

    provider = TransientProvider()
    budget = BudgetGuard(5, 1000, path=tmp_path / "budget.json")
    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    client = LLMClient(provider, config, budget=budget, cache=ResponseCache(tmp_path / "cache.json"))
    for value in ("first", "second"):
        with pytest.raises(HTTPError):
            client.complete([{"role": "user", "content": value}])
    assert provider.calls == 2 and budget.blocked_reason is None
    assert budget.calls == 2 and budget.uncertain_tokens == 200
    events = [json.loads(line) for line in Path(config.log_path).read_text().splitlines()]
    assert all(event["http_status"] == status for event in events)
    assert all(event["provider_message"] == "try again" for event in events)


def test_vertex_sdk_billing_failure_blocks_budget_circuit(tmp_path):
    from twinloop.llm.providers import ProviderHTTPError

    class RefusingVertexProvider:
        def complete(self, *args):
            raise ProviderHTTPError(402, "FAILED_PRECONDITION", "Billing account disabled")

    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    budget = BudgetGuard(5, 1000, path=tmp_path / "budget.json")
    client = LLMClient(RefusingVertexProvider(), config, budget=budget,
                       cache=ResponseCache(tmp_path / "cache.json"))
    with pytest.raises(URLError):
        client.complete([{"role": "user", "content": "first"}])
    record = json.loads(Path(config.log_path).read_text())
    assert record["http_status"] == 402 and record["provider_status"] == "FAILED_PRECONDITION"
    assert "Billing account disabled" in budget.blocked_reason
