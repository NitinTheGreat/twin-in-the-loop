from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .budget import BudgetGuard
from .cache import CacheMiss, ResponseCache, cache_key


@dataclass
class LLMRecord:
    model: str
    latency_ms: float
    tokens_in: int
    tokens_out: int
    cache_hit: bool
    prompt_chars: int
    response_chars: int


def canonical_prompt(messages: list[dict]) -> str:
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


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

    def complete(self, messages: list[dict]) -> tuple[str, LLMRecord]:
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
            )
            self._log(record)
            return cached["text"], record

        if self.cache.cache_only:
            raise CacheMiss(f"cache-only mode: no entry for prompt {key[:12]}")

        self.budget.charge_call()
        start = time.perf_counter()
        response = self.provider.complete(
            messages,
            self.config.model,
            self.config.temperature,
            self.config.timeout_seconds,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.budget.charge_tokens(response.tokens_in + response.tokens_out)

        self.cache.put(
            key,
            {
                "text": response.text,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
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
        )
        self._log(record)
        return response.text, record

    def _log(self, record: LLMRecord) -> None:
        self.records.append(record)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record)) + "\n")
