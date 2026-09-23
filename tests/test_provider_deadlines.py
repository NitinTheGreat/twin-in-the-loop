"""Transport boundary tests use a fake opener and clock; no network or real sleeps."""
import urllib.error

import httpx
import pytest
from google.genai import errors as genai_errors

from twinloop.llm.providers import GeminiProvider, OpenAICompatibleProvider


class FakeModels:
    def __init__(self, raise_error, clock=None):
        self.raise_error = raise_error
        self.clock = clock
        self.timeouts = []

    def generate_content(self, model, contents, config):
        seconds = config.http_options.timeout / 1000
        self.timeouts.append(seconds)
        if self.clock is not None:
            self.clock[0] += min(3, seconds)
        raise self.raise_error()


class FakeClient:
    def __init__(self, models):
        self.models = models


def test_transport_retries_share_one_deadline(monkeypatch):
    clock = [0.0]
    timeouts, sleeps = [], []

    def open_request(request, timeout):
        timeouts.append(timeout)
        clock[0] += min(3, timeout)
        raise urllib.error.HTTPError("unused", 503, "busy", {}, None)

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("twinloop.llm.providers.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", sleep)
    monkeypatch.setattr("twinloop.llm.providers.urllib.request.urlopen", open_request)
    with pytest.raises(TimeoutError):
        OpenAICompatibleProvider("http://unused").complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert timeouts == [5, 1]
    assert sum(sleeps) == 1 and clock[0] == 5


def test_gemini_retries_share_one_deadline(monkeypatch):
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("twinloop.llm.providers.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", sleep)
    models = FakeModels(lambda: genai_errors.ServerError(503, {"error": {"status": "UNAVAILABLE"}}), clock)
    provider = GeminiProvider("test-project", client=FakeClient(models))
    with pytest.raises(TimeoutError):
        provider.complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert models.timeouts == [5, 1]
    assert sum(sleeps) == 1 and clock[0] == 5


def test_gemini_timeout_is_not_retried(monkeypatch):
    models = FakeModels(lambda: httpx.ReadTimeout("read timeout"))
    provider = GeminiProvider("test-project", client=FakeClient(models))
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", lambda _: pytest.fail("must not sleep"))
    with pytest.raises(TimeoutError):
        provider.complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert len(models.timeouts) == 1


def test_gemini_terminal_http_error_is_a_url_error():
    models = FakeModels(lambda: genai_errors.ClientError(
        403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "denied"}}))
    provider = GeminiProvider("test-project", client=FakeClient(models))
    with pytest.raises(urllib.error.URLError) as raised:
        provider.complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert raised.value.code == 403 and raised.value.status == "PERMISSION_DENIED"
    assert len(models.timeouts) == 1


def test_transport_timeout_is_not_retried(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError(TimeoutError("read timeout"))

    monkeypatch.setattr("twinloop.llm.providers.urllib.request.urlopen", open_request)
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", lambda _: pytest.fail("must not sleep"))
    with pytest.raises(TimeoutError):
        OpenAICompatibleProvider("http://unused").complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert len(calls) == 1

