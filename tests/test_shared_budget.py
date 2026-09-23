import json
import multiprocessing

import pytest

from twinloop.llm.budget import BudgetExceeded, SharedBudgetGuard


def _charge_until_exhausted(path, owner, results):
    guard = SharedBudgetGuard(40, 10**9, path, owner)
    count = 0
    while True:
        try:
            guard.charge_call(10)
        except BudgetExceeded:
            break
        guard.charge_tokens(5)
        count += 1
    results.put((owner, count))


def test_concurrent_workers_never_exceed_the_single_global_call_ceiling(tmp_path):
    path = tmp_path / "_budget.json"
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [context.Process(target=_charge_until_exhausted, args=(str(path), f"w{i}", results))
               for i in range(4)]
    for worker in workers:
        worker.start()
    counts = dict(results.get(timeout=120) for _ in workers)
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0
    ledger = json.loads(path.read_text())
    assert sum(counts.values()) == 40
    assert ledger["calls"] == 40 and ledger["tokens"] == 200 and ledger["in_flight"] == {}
    assert {owner: usage["calls"] for owner, usage in ledger["by_owner"].items()} == counts


def test_in_flight_reservations_of_other_workers_count_against_the_token_ceiling(tmp_path):
    path = tmp_path / "_budget.json"
    first = SharedBudgetGuard(10, 100, path, "w0")
    second = SharedBudgetGuard(10, 100, path, "w1")
    first.charge_call(60)
    with pytest.raises(BudgetExceeded, match="cannot cover"):
        second.charge_call(50)
    first.charge_tokens(10)
    second.charge_call(50)
    assert second.calls == 2 and second.tokens == 10


def test_unresolved_call_blocks_only_its_owner_after_restart(tmp_path):
    path = tmp_path / "_budget.json"
    SharedBudgetGuard(10, 1000, path, "w0").charge_call(5)
    with pytest.raises(BudgetExceeded, match="unresolved usage"):
        SharedBudgetGuard(10, 1000, path, "w0").charge_call(5)
    SharedBudgetGuard(10, 1000, path, "w1").charge_call(5)


def test_failed_call_settles_reservation_as_uncertain_usage(tmp_path):
    guard = SharedBudgetGuard(10, 1000, tmp_path / "_budget.json", "w0")
    guard.charge_call(30)
    guard.settle_failed_call()
    assert guard.tokens == 30 and guard.uncertain_tokens == 30 and not guard.pending_call
    assert guard.owner_usage == {"calls": 1, "tokens": 30, "uncertain_tokens": 30}


def test_block_by_one_worker_stops_every_worker(tmp_path):
    path = tmp_path / "_budget.json"
    first = SharedBudgetGuard(10, 1000, path, "w0")
    second = SharedBudgetGuard(10, 1000, path, "w1")
    first.block("HTTP 402")
    with pytest.raises(BudgetExceeded, match="circuit is blocked"):
        second.charge_call(1)


def test_changed_limits_are_rejected(tmp_path):
    path = tmp_path / "_budget.json"
    SharedBudgetGuard(10, 1000, path, "w0")
    with pytest.raises(ValueError, match="limits differ"):
        SharedBudgetGuard(11, 1000, path, "w1")
