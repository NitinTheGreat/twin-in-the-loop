from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class ProviderResponse:
    text: str
    tokens_in: int
    tokens_out: int


class Provider(Protocol):
    def complete(
        self, messages: list[dict], model: str, temperature: float, timeout: float
    ) -> ProviderResponse: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _request_json(request, timeout, max_attempts):
    """Retries and backoff share the caller's remaining time allowance."""
    deadline = time.monotonic() + timeout
    for attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider deadline reached")
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in _RETRYABLE_STATUS or attempt == max_attempts - 1:
                raise
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError("provider request timed out") from error
            if attempt == max_attempts - 1:
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider deadline reached")
        time.sleep(min(2**attempt, remaining))
    raise RuntimeError("provider retries exhausted")


class OpenAICompatibleProvider:
    def __init__(
        self, base_url: str, api_key: Optional[str] = None, max_attempts: int = 4
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_attempts = max_attempts

    def complete(
        self, messages: list[dict], model: str, temperature: float, timeout: float
    ) -> ProviderResponse:
        payload = json.dumps(
            {"model": model, "messages": messages, "temperature": temperature}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )

        data = _request_json(request, timeout, self.max_attempts)
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ProviderResponse(
            text=text,
            tokens_in=usage.get("prompt_tokens", estimate_tokens(json.dumps(messages))),
            tokens_out=usage.get("completion_tokens", estimate_tokens(text)),
        )


class CloudProvider(OpenAICompatibleProvider):
    def __init__(self, config) -> None:
        super().__init__(config.base_url, os.environ.get(config.api_key_env))


class LocalProvider(OpenAICompatibleProvider):
    def __init__(self, config) -> None:
        super().__init__(config.base_url, None)


def _gemini_text(data: dict) -> str:
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


class GeminiProvider:
    def __init__(
        self,
        api_key: Optional[str],
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        max_attempts: int = 4,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts

    def complete(
        self, messages: list[dict], model: str, temperature: float, timeout: float
    ) -> ProviderResponse:
        system_texts = [m["content"] for m in messages if m["role"] == "system"]
        contents = []
        for message in messages:
            if message["role"] == "system":
                continue
            role = "model" if message["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message["content"]}]})

        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}

        body = json.dumps(payload).encode("utf-8")
        model_id = model.split("/")[-1]
        url = f"{self.base_url}/models/{model_id}:generateContent"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")

        data = _request_json(request, timeout, self.max_attempts)
        text = _gemini_text(data)
        usage = data.get("usageMetadata", {})
        return ProviderResponse(
            text=text,
            tokens_in=usage.get("promptTokenCount", estimate_tokens(body.decode("utf-8"))),
            tokens_out=usage.get("candidatesTokenCount", estimate_tokens(text)),
        )
