from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional


class CacheMiss(Exception):
    pass


def cache_key(model: str, temperature: float, prompt: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"{model}\x00{temperature}\x00{prompt}".encode("utf-8"))
    return digest.hexdigest()


class ResponseCache:
    def __init__(
        self, path, bypass: bool = False, cache_only: bool = False
    ) -> None:
        self.path = Path(path)
        self.bypass = bypass
        self.cache_only = cache_only
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str) -> Optional[dict]:
        if self.bypass:
            return None
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        if self.bypass:
            return
        self._data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, sort_keys=True), encoding="utf-8")
