import json

import httpx
import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from google.oauth2.credentials import Credentials

from twinloop.config import LLMConfig
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import (
    GEMINI_MAX_ATTEMPTS, SERVER_DEADLINE_MARGIN_SECONDS, GeminiProvider, ProviderDeadlineError,
    ProviderHTTPError,
)

MESSAGES = [{"role": "user", "content": "test"}]
RETRYABLE = [(429, "RESOURCE_EXHAUSTED"), (500, "INTERNAL"), (502, "BAD_GATEWAY"), (503, "UNAVAILABLE"),
             (504, "DEADLINE_EXCEEDED"), (499, "CANCELLED")]
REPLY = {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}],
         "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "thoughtsTokenCount": 2,
                           "totalTokenCount": 6}}


def api_error(code, status):
    body = {"error": {"code": code, "status": status, "message": f"{status} test"}}
    return genai_errors.ServerError(code, body) if code >= 500 else genai_errors.ClientError(code, body)


class ScriptedModels:
    def __init__(self, outcomes, clock, seconds_per_attempt):
        self.outcomes = list(outcomes)
        self.clock = clock
        self.seconds_per_attempt = seconds_per_attempt
        self.timeouts = []

    def generate_content(self, model, contents, config):
        self.timeouts.append(config.http_options.timeout / 1000)
        self.clock[0] += self.seconds_per_attempt
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return genai_types.GenerateContentResponse.model_validate(REPLY)


class FakeClient:
    def __init__(self, models):
        self.models = models


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("twinloop.llm.providers.time.monotonic", lambda: now[0])
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", sleep)
    return now, sleeps


def provider_with(outcomes, now, seconds_per_attempt=1.0):
    models = ScriptedModels(outcomes, now, seconds_per_attempt)
    return GeminiProvider("test-project", client=FakeClient(models)), models


@pytest.mark.parametrize("code,status", RETRYABLE)
def test_retryable_failures_are_retried_within_the_shared_deadline(clock, code, status):
    now, sleeps = clock
    provider, models = provider_with([api_error(code, status), api_error(code, status), "ok"], now)
    response = provider.complete(MESSAGES, "model", 0, 20)
    assert response.text == "ok" and response.tokens_thinking == 2
    assert provider.last_attempts == 3
    assert provider.last_retry_errors == [f"{code} {status}"] * 2
    assert sleeps == [1, 2]
    assert models.timeouts == [20, 18, 15]


@pytest.mark.parametrize("code,status", RETRYABLE)
def test_retries_stop_at_the_historical_maximum_of_four_attempts(clock, code, status):
    now, sleeps = clock
    provider, models = provider_with([api_error(code, status)] * GEMINI_MAX_ATTEMPTS, now)
    expected = ProviderDeadlineError if code in (499, 504) else ProviderHTTPError
    with pytest.raises(expected) as raised:
        provider.complete(MESSAGES, "model", 0, 30)
    assert raised.value.code == code and raised.value.status == status
    assert provider.last_attempts == GEMINI_MAX_ATTEMPTS == 4
    assert sleeps == [1, 2, 4] and len(models.timeouts) == 4


@pytest.mark.parametrize("code,status", RETRYABLE)
def test_no_retry_when_the_remaining_budget_cannot_fit_another_attempt(clock, code, status):
    now, sleeps = clock
    provider, models = provider_with([api_error(code, status), "ok"], now, seconds_per_attempt=3.5)
    with pytest.raises(ProviderDeadlineError) as raised:
        provider.complete(MESSAGES, "model", 0, 5)
    assert isinstance(raised.value, TimeoutError)
    assert raised.value.code == code
    assert provider.last_attempts == 1 and sleeps == [] and len(models.timeouts) == 1


def test_client_timeout_is_not_retried(clock):
    now, sleeps = clock
    provider, models = provider_with([httpx.ReadTimeout("read timeout"), "ok"], now)
    with pytest.raises(TimeoutError):
        provider.complete(MESSAGES, "model", 0, 20)
    assert provider.last_attempts == 1 and sleeps == [] and len(models.timeouts) == 1


@pytest.mark.parametrize("code,status", [(400, "INVALID_ARGUMENT"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND")])
def test_configuration_errors_are_not_retried(clock, code, status):
    now, sleeps = clock
    provider, _ = provider_with([api_error(code, status), "ok"], now)
    with pytest.raises(ProviderHTTPError):
        provider.complete(MESSAGES, "model", 0, 20)
    assert provider.last_attempts == 1 and sleeps == []


def test_client_journal_records_attempts_and_server_deadline_status(clock, tmp_path):
    now, _ = clock
    provider, _ = provider_with([api_error(504, "DEADLINE_EXCEEDED"), "ok"], now)
    config = LLMConfig(log_path=str(tmp_path / "calls.jsonl"))
    client = LLMClient(provider, config, budget=BudgetGuard(10, 10**6), cache=ResponseCache(tmp_path / "c.json"))
    client.complete(MESSAGES, timeout_seconds=20)
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["provider_attempts"] == 2 and row["provider_retry_errors"] == ["504 DEADLINE_EXCEEDED"]
    failing, _ = provider_with([api_error(504, "DEADLINE_EXCEEDED")], now, seconds_per_attempt=4.5)
    client = LLMClient(failing, config, budget=BudgetGuard(10, 10**6), cache=ResponseCache(tmp_path / "d.json"))
    with pytest.raises(TimeoutError):
        client.complete([{"role": "user", "content": "other"}], timeout_seconds=5)
    error_row = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert error_row["error_type"] == "timeout" and error_row["http_status"] == 504
    assert error_row["provider_status"] == "DEADLINE_EXCEEDED" and error_row["provider_attempts"] == 1


def test_wire_timeout_is_seconds_and_server_deadline_is_not_shorter_than_client_budget():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=REPLY)

    client = genai.Client(vertexai=True, project="p", location="global", credentials=Credentials(token="offline"),
                          http_options=genai_types.HttpOptions(
                              httpx_client=httpx.Client(transport=httpx.MockTransport(handler))))
    GeminiProvider("p", client=client).complete(MESSAGES, "gemini-3.6-flash", 0, 12.4)
    timeouts = seen[0].extensions["timeout"]
    assert 12.3 < timeouts["read"] <= 12.4 and 12.3 < timeouts["connect"] <= 12.4
    assert int(seen[0].headers["x-server-timeout"]) == 13 + SERVER_DEADLINE_MARGIN_SECONDS
    assert seen[0].url.host == "aiplatform.googleapis.com"
