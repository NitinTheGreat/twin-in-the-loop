from __future__ import annotations


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    def __init__(self, max_calls: int, max_tokens: int) -> None:
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0

    def charge_call(self) -> None:
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(
                f"call ceiling of {self.max_calls} crossed"
            )
        self.calls += 1

    def charge_tokens(self, count: int) -> None:
        if self.tokens + count > self.max_tokens:
            raise BudgetExceeded(
                f"token ceiling of {self.max_tokens} crossed"
            )
        self.tokens += count
