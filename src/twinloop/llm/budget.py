from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    def __init__(self, max_calls: int, max_tokens: int, path=None) -> None:
        if max_calls < 0 or max_tokens < 0:
            raise ValueError("budget limits must be nonnegative")
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0
        self.path = Path(path) if path is not None else None
        self.pending_call = False
        self.pending_tokens = 0
        self.uncertain_tokens = 0
        self.blocked_reason = None
        if self.path is not None and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if (saved["max_calls"], saved["max_tokens"]) != (max_calls, max_tokens):
                raise ValueError("persisted budget limits differ from requested limits")
            self.calls = saved["calls"]
            self.tokens = saved["tokens"]
            self.pending_call = saved["pending_call"]
            self.pending_tokens = saved.get("pending_tokens", 0)
            self.uncertain_tokens = saved.get("uncertain_tokens", 0)
            self.blocked_reason = saved.get("blocked_reason")
            if not isinstance(self.calls, int) or not isinstance(self.tokens, int):
                raise ValueError("persisted budget counters must be integers")
            if self.calls < 0 or self.tokens < 0 or not isinstance(self.pending_call, bool):
                raise ValueError("invalid persisted budget counters")
            if self.blocked_reason is not None and not isinstance(self.blocked_reason, str):
                raise ValueError("invalid persisted blocked reason")
        elif self.path is not None:
            self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({
            "max_calls": self.max_calls, "max_tokens": self.max_tokens,
            "calls": self.calls, "tokens": self.tokens, "pending_call": self.pending_call,
            "pending_tokens": self.pending_tokens, "uncertain_tokens": self.uncertain_tokens,
            "blocked_reason": self.blocked_reason,
        }, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def charge_call(self, token_reservation: int = 0) -> None:
        if self.blocked_reason:
            raise BudgetExceeded(f"provider circuit is blocked: {self.blocked_reason}")
        if self.path is not None and self.pending_call:
            raise BudgetExceeded("previous provider call has unresolved usage; reconcile budget ledger")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"token ceiling of {self.max_tokens} reached")
        if token_reservation < 0:
            raise ValueError("token reservation must be nonnegative")
        if self.tokens + token_reservation > self.max_tokens:
            raise BudgetExceeded(f"remaining token budget cannot cover {token_reservation} reserved tokens")
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(
                f"call ceiling of {self.max_calls} crossed"
            )
        self.calls += 1
        self.pending_call = True
        self.pending_tokens = token_reservation
        self._persist()

    def block(self, reason: str) -> None:
        self.blocked_reason = reason
        self._persist()

    def settle_failed_call(self) -> None:
        if self.pending_tokens <= 0:
            return
        self.tokens += self.pending_tokens
        self.uncertain_tokens += self.pending_tokens
        self.pending_tokens = 0
        self.pending_call = False
        self._persist()

    def charge_tokens(self, count: int, *, complete: bool = True) -> None:
        if count < 0:
            raise ValueError("token charge must be nonnegative")
        self.tokens += count
        self.pending_call = not complete
        self.pending_tokens = 0
        self._persist()
        if not complete and self.path is not None:
            raise BudgetExceeded("provider usage is incomplete; reconcile budget ledger")
        if self.tokens > self.max_tokens:
            raise BudgetExceeded(
                f"token ceiling of {self.max_tokens} crossed"
            )


@contextmanager
def _exclusive(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.005)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SharedBudgetGuard:
    policy = "shared_global_ledger"

    def __init__(self, max_calls: int, max_tokens: int, path, owner: str) -> None:
        if max_calls < 0 or max_tokens < 0:
            raise ValueError("budget limits must be nonnegative")
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".mutex")
        self.owner = str(owner)
        self._state: dict = {}
        with self._transaction():
            pass

    def _load(self) -> dict:
        if not self.path.exists():
            return {"max_calls": self.max_calls, "max_tokens": self.max_tokens, "calls": 0,
                    "tokens": 0, "uncertain_tokens": 0, "blocked_reason": None,
                    "in_flight": {}, "by_owner": {}}
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (state["max_calls"], state["max_tokens"]) != (self.max_calls, self.max_tokens):
            raise ValueError("persisted budget limits differ from requested limits")
        for key in ("calls", "tokens", "uncertain_tokens"):
            if not isinstance(state[key], int) or state[key] < 0:
                raise ValueError("invalid persisted budget counters")
        if state["blocked_reason"] is not None and not isinstance(state["blocked_reason"], str):
            raise ValueError("invalid persisted blocked reason")
        return state

    @contextmanager
    def _transaction(self):
        with _exclusive(self.lock_path):
            state = self._load()
            state["by_owner"].setdefault(self.owner, {"calls": 0, "tokens": 0, "uncertain_tokens": 0})
            yield state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{self.owner}.tmp")
            temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            for attempt in range(100):
                try:
                    temporary.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 99:
                        raise
                    time.sleep(0.01)
            self._state = state

    def refresh(self) -> dict:
        with self._transaction() as state:
            pass
        return dict(self._state)

    @property
    def calls(self) -> int:
        return self._state["calls"]

    @property
    def tokens(self) -> int:
        return self._state["tokens"]

    @property
    def uncertain_tokens(self) -> int:
        return self._state["uncertain_tokens"]

    @property
    def blocked_reason(self):
        return self._state["blocked_reason"]

    @property
    def pending_call(self) -> bool:
        return self.owner in self._state["in_flight"]

    @property
    def owner_usage(self) -> dict:
        return dict(self._state["by_owner"][self.owner])

    def charge_call(self, token_reservation: int = 0) -> None:
        if token_reservation < 0:
            raise ValueError("token reservation must be nonnegative")
        with self._transaction() as state:
            self._state = state
            if state["blocked_reason"]:
                raise BudgetExceeded(f"provider circuit is blocked: {state['blocked_reason']}")
            if self.owner in state["in_flight"]:
                raise BudgetExceeded("previous provider call has unresolved usage; reconcile budget ledger")
            if state["tokens"] >= self.max_tokens:
                raise BudgetExceeded(f"token ceiling of {self.max_tokens} reached")
            committed = state["tokens"] + sum(state["in_flight"].values())
            if committed + token_reservation > self.max_tokens:
                raise BudgetExceeded(f"remaining token budget cannot cover {token_reservation} reserved tokens")
            if state["calls"] + 1 > self.max_calls:
                raise BudgetExceeded(f"call ceiling of {self.max_calls} crossed")
            state["calls"] += 1
            state["by_owner"][self.owner]["calls"] += 1
            state["in_flight"][self.owner] = token_reservation

    def block(self, reason: str) -> None:
        with self._transaction() as state:
            state["blocked_reason"] = reason

    def settle_failed_call(self) -> None:
        with self._transaction() as state:
            reserved = state["in_flight"].pop(self.owner, 0)
            state["tokens"] += reserved
            state["uncertain_tokens"] += reserved
            state["by_owner"][self.owner]["tokens"] += reserved
            state["by_owner"][self.owner]["uncertain_tokens"] += reserved

    def charge_tokens(self, count: int, *, complete: bool = True) -> None:
        if count < 0:
            raise ValueError("token charge must be nonnegative")
        with self._transaction() as state:
            state["tokens"] += count
            state["by_owner"][self.owner]["tokens"] += count
            if complete:
                state["in_flight"].pop(self.owner, None)
            else:
                state["in_flight"][self.owner] = 0
        if not complete:
            raise BudgetExceeded("provider usage is incomplete; reconcile budget ledger")
        if self.tokens > self.max_tokens:
            raise BudgetExceeded(f"token ceiling of {self.max_tokens} crossed")
