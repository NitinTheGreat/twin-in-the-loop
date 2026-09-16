"""Transport boundary tests use a fake opener and clock; no network or real sleeps."""
import urllib.error

import pytest

from twinloop.llm.providers import GeminiProvider, OpenAICompatibleProvider


@pytest.mark.parametrize("provider", [OpenAICompatibleProvider("http://unused"), GeminiProvider(None)])
def test_transport_retries_share_one_deadline(provider, monkeypatch):
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
        provider.complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert timeouts == [5, 1]
    assert sum(sleeps) == 1 and clock[0] == 5


@pytest.mark.parametrize("provider", [OpenAICompatibleProvider("http://unused"), GeminiProvider(None)])
def test_transport_timeout_is_not_retried(provider, monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError(TimeoutError("read timeout"))

    monkeypatch.setattr("twinloop.llm.providers.urllib.request.urlopen", open_request)
    monkeypatch.setattr("twinloop.llm.providers.time.sleep", lambda _: pytest.fail("must not sleep"))
    with pytest.raises(TimeoutError):
        provider.complete([{"role": "user", "content": "test"}], "test", 0, 5)
    assert len(calls) == 1
