from __future__ import annotations

import json
import os
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


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", estimate_tokens(json.dumps(messages)))
        tokens_out = usage.get("completion_tokens", estimate_tokens(text))
        return ProviderResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out)


class CloudProvider(OpenAICompatibleProvider):
    def __init__(self, config) -> None:
        super().__init__(config.base_url, os.environ.get(config.api_key_env))


class LocalProvider(OpenAICompatibleProvider):
    def __init__(self, config) -> None:
        super().__init__(config.base_url, None)
