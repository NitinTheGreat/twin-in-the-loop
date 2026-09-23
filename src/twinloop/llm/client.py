from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError

from .budget import BudgetExceeded, BudgetGuard
from .cache import CacheMiss, ResponseCache, cache_key
from .providers import ProviderDeadlineError, ProviderHTTPError


@dataclass
class LLMRecord:
    model: str
    latency_ms: float
    tokens_in: int
    tokens_out: int
    cache_hit: bool
    prompt_chars: int
    response_chars: int
    tokens_thinking: int = 0
    usage_metadata: dict = field(default_factory=dict)
    model_version: Optional[str] = None
    usage_complete: bool = False
    cache_key: Optional[str] = None
    event: str = "response"
    provider_attempts: int = 0
    provider_retry_errors: list = field(default_factory=list)


def canonical_prompt(messages: list[dict]) -> str:
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


def provider_error_details(error, api_key=None):
    def redact(value):
        message = str(value)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        return message[:2048]

    if isinstance(error, (ProviderHTTPError, ProviderDeadlineError)) and error.code is not None:
        return {"http_status": error.code,
                "provider_status": redact(error.status) if error.status is not None else None,
                "provider_message": redact(error.message)}
    if not isinstance(error, HTTPError):
        return {}
    details = {"http_status": error.code, "provider_status": None,
               "provider_message": redact(error.reason)}
    try:
        body = error.read(65536).decode("utf-8", errors="replace")
        payload = json.loads(body)
        provider_error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(provider_error, dict):
            if provider_error.get("status") is not None:
                details["provider_status"] = redact(provider_error["status"])
            if provider_error.get("message") is not None:
                details["provider_message"] = redact(provider_error["message"])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return details


class LLMClient:
    def __init__(
        self,
        provider,
        config,
        cache: Optional[ResponseCache] = None,
        budget: Optional[BudgetGuard] = None,
        log_path=None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.cache = cache or ResponseCache(
            Path(config.cache_dir) / "cache.json",
            bypass=config.cache_bypass,
            cache_only=config.cache_only,
        )
        self.budget = budget or BudgetGuard(config.max_calls, config.max_tokens)
        self.log_path = Path(log_path) if log_path is not None else Path(config.log_path)
        self.records: list[LLMRecord] = []

    def complete(self, messages: list[dict], *, timeout_seconds=None) -> tuple[str, LLMRecord]:
        timeout = self.config.timeout_seconds
        if timeout_seconds is not None:
            timeout = min(timeout, timeout_seconds)
        if timeout <= 0:
            raise TimeoutError("no provider time remaining")
        prompt = canonical_prompt(messages)
        key = cache_key(self.config.model, self.config.temperature, prompt)

        cached = self.cache.get(key)
        if cached is not None:
            record = LLMRecord(
                model=self.config.model,
                latency_ms=0.0,
                tokens_in=cached["tokens_in"],
                tokens_out=cached["tokens_out"],
                cache_hit=True,
                prompt_chars=len(prompt),
                response_chars=len(cached["text"]),
                tokens_thinking=cached.get("tokens_thinking", 0),
                usage_metadata=cached.get("usage_metadata", {}),
                model_version=cached.get("model_version"),
                usage_complete=cached.get("usage_complete", False),
                cache_key=key,
            )
            self._log(record)
            return cached["text"], record

        if self.cache.cache_only:
            raise CacheMiss(f"cache-only mode: no entry for prompt {key[:12]}")

        reserve = getattr(self.provider, "max_token_reservation", None)
        reservation = reserve(messages) if reserve is not None else 0
        try:
            self.budget.charge_call(reservation)
        except BudgetExceeded:
            self._write_log({"event": "budget_error", "error_type": "provider_budget_exhausted",
                             "cache_key": key, "stage": "before_call", "latency_ms": 0.0})
            raise
        start = time.perf_counter()
        try:
            response = self.provider.complete(
                messages,
                self.config.model,
                self.config.temperature,
                timeout,
            )
        except Exception as error:
            timed_out = isinstance(error, TimeoutError) or (
                isinstance(error, URLError) and isinstance(error.reason, TimeoutError))
            self.budget.settle_failed_call()
            details = provider_error_details(error, getattr(self.provider, "api_key", None))
            if details.get("http_status") in {400, 401, 402, 403, 404}:
                self.budget.block(
                    f"HTTP {details['http_status']} {details.get('provider_status') or ''}: "
                    f"{details['provider_message']}")
            self._write_log({
                "event": "provider_error", "error_type": "timeout" if timed_out else "provider_error",
                "exception_class": type(error).__name__, "cache_key": key,
                "model": self.config.model, "latency_ms": (time.perf_counter() - start) * 1000.0,
                "cache_hit": False, "prompt_chars": len(prompt),
                "reserved_tokens": reservation, "usage_unknown": True,
                "provider_attempts": getattr(self.provider, "last_attempts", 1),
                "provider_retry_errors": list(getattr(self.provider, "last_retry_errors", [])),
                **details,
            })
            if isinstance(error, (ValueError, KeyError, TypeError, UnicodeError)):
                raise URLError("provider response could not be decoded") from error
            raise
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.cache.put(
            key,
            {
                "text": response.text,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "tokens_thinking": response.tokens_thinking,
                "usage_metadata": response.usage_metadata,
                "model_version": response.model_version,
                "usage_complete": response.usage_complete,
                "latency_ms": latency_ms,
            },
        )
        record = LLMRecord(
            model=self.config.model,
            latency_ms=latency_ms,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cache_hit=False,
            prompt_chars=len(prompt),
            response_chars=len(response.text),
            tokens_thinking=response.tokens_thinking,
            usage_metadata=response.usage_metadata,
            model_version=response.model_version,
            usage_complete=response.usage_complete,
            cache_key=key,
            provider_attempts=getattr(self.provider, "last_attempts", 1),
            provider_retry_errors=list(getattr(self.provider, "last_retry_errors", [])),
        )
        self._log(record)
        try:
            self.budget.charge_tokens(response.tokens_in + response.tokens_out,
                                      complete=response.usage_complete)
        except BudgetExceeded:
            self._write_log({"event": "budget_error", "error_type": "provider_budget_exhausted",
                             "cache_key": key, "stage": "after_response", "latency_ms": 0.0})
            raise
        return response.text, record

    def _log(self, record: LLMRecord) -> None:
        self.records.append(record)
        self._write_log(asdict(record))

    def _write_log(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
