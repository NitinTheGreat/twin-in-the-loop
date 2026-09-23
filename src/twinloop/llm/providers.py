from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Protocol

import httpx
from google import genai
from google.auth import exceptions as auth_exceptions
from google.genai import errors as genai_errors
from google.genai import types as genai_types


@dataclass
class ProviderResponse:
    text: str
    tokens_in: int
    tokens_out: int
    tokens_thinking: int = 0
    usage_metadata: dict = field(default_factory=dict)
    model_version: Optional[str] = None
    usage_complete: bool = True


class Provider(Protocol):
    def complete(
        self, messages: list[dict], model: str, temperature: float, timeout: float
    ) -> ProviderResponse: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
GEMINI_RETRYABLE_STATUS = _RETRYABLE_STATUS | {499}
_SERVER_DEADLINE_STATUS = {(504, "DEADLINE_EXCEEDED"), (499, "CANCELLED")}
GEMINI_MAX_ATTEMPTS = 4
MIN_ATTEMPT_SECONDS = 1.0
SERVER_DEADLINE_MARGIN_SECONDS = 3


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
            usage_metadata=usage,
            model_version=data.get("model"),
            usage_complete="prompt_tokens" in usage and "completion_tokens" in usage,
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


class ProviderHTTPError(urllib.error.URLError):
    def __init__(self, code: int, status: Optional[str], message: str) -> None:
        super().__init__(f"HTTP {code} {status or ''}: {message}")
        self.code = code
        self.status = status
        self.message = message


class ProviderDeadlineError(TimeoutError):
    def __init__(self, message: str, code: Optional[int] = None, status: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


class GeminiProvider:
    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        max_attempts: int = GEMINI_MAX_ATTEMPTS,
        max_output_tokens: Optional[int] = None,
        client=None,
        thinking_config: Optional[dict] = None,
    ) -> None:
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
        self.max_attempts = max_attempts
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self.max_output_tokens = max_output_tokens
        self._client = client
        self.thinking_config = thinking_config
        self.last_attempts = 0
        self.last_retry_errors = []

    @property
    def client(self):
        if self._client is None:
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    def _payload(self, messages, temperature):
        system_texts = [m["content"] for m in messages if m["role"] == "system"]
        contents = []
        for message in messages:
            if message["role"] == "system":
                continue
            role = "model" if message["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message["content"]}]})
        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if self.max_output_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = self.max_output_tokens
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
        return payload

    def max_token_reservation(self, messages):
        if self.max_output_tokens is None:
            return 0
        body = json.dumps(self._payload(messages, 0.0)).encode("utf-8")
        return len(body) + 1024 + self.max_output_tokens

    def _config(self, payload, remaining):
        generation = payload["generationConfig"]
        system = payload.get("systemInstruction")
        return genai_types.GenerateContentConfig(
            temperature=generation["temperature"],
            max_output_tokens=generation.get("maxOutputTokens"),
            system_instruction=system["parts"][0]["text"] if system else None,
            http_options=genai_types.HttpOptions(
                timeout=max(1, int(remaining * 1000)),
                headers={"X-Server-Timeout": str(math.ceil(remaining) + SERVER_DEADLINE_MARGIN_SECONDS)}),
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self.thinking_config,
        )

    def _generate(self, payload, model_id, timeout):
        deadline = time.monotonic() + timeout
        self.last_attempts = 0
        self.last_retry_errors = []
        for attempt in range(self.max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("provider deadline reached")
            self.last_attempts = attempt + 1
            try:
                return self.client.models.generate_content(
                    model=model_id, contents=payload["contents"],
                    config=self._config(payload, remaining))
            except genai_errors.APIError as error:
                server_deadline = (error.code, error.status) in _SERVER_DEADLINE_STATUS
                if error.code not in GEMINI_RETRYABLE_STATUS:
                    raise ProviderHTTPError(error.code, error.status, error.message) from error
                if attempt == self.max_attempts - 1:
                    if server_deadline:
                        raise ProviderDeadlineError(
                            f"provider deadline enforced by server: HTTP {error.code} {error.status}",
                            error.code, error.status) from error
                    raise ProviderHTTPError(error.code, error.status, error.message) from error
                failure = error
                self.last_retry_errors.append(f"{error.code} {error.status}")
            except httpx.TimeoutException as error:
                raise TimeoutError("provider request timed out") from error
            except (httpx.TransportError, auth_exceptions.TransportError) as error:
                if attempt == self.max_attempts - 1:
                    raise urllib.error.URLError(error) from error
                failure = error
                self.last_retry_errors.append(type(error).__name__)
            except auth_exceptions.GoogleAuthError as error:
                raise ProviderHTTPError(401, "UNAUTHENTICATED", str(error)) from error
            backoff = 2**attempt
            if deadline - time.monotonic() - backoff < MIN_ATTEMPT_SECONDS:
                raise ProviderDeadlineError("provider deadline leaves no time for another attempt",
                                            getattr(failure, "code", None),
                                            getattr(failure, "status", None)) from failure
            time.sleep(backoff)
        raise RuntimeError("provider retries exhausted")

    def complete(
        self, messages: list[dict], model: str, temperature: float, timeout: float
    ) -> ProviderResponse:
        payload = self._payload(messages, temperature)
        body = json.dumps(payload).encode("utf-8")
        model_id = model.split("/")[-1]

        response = self._generate(payload, model_id, timeout)
        data = response.model_dump(mode="json", by_alias=True, exclude_none=True,
                                   exclude={"sdk_http_response"})
        text = _gemini_text(data)
        usage = data.get("usageMetadata", {})
        tokens_in = usage.get("promptTokenCount", estimate_tokens(body.decode("utf-8")))
        tokens_out = usage.get("candidatesTokenCount", estimate_tokens(text)) + usage.get("thoughtsTokenCount", 0)
        if "totalTokenCount" in usage and "promptTokenCount" in usage:
            tokens_out = max(tokens_out, usage["totalTokenCount"] - tokens_in)
        return ProviderResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thinking=usage.get("thoughtsTokenCount", 0),
            usage_metadata=usage,
            model_version=data.get("modelVersion"),
            usage_complete="promptTokenCount" in usage and (
                "candidatesTokenCount" in usage or "totalTokenCount" in usage),
        )
