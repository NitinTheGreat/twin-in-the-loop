import numpy as np

from twinloop.seeding import SeedManager


def _draw(generator, count=16):
    return generator.integers(0, 2**32, size=count)


def test_same_master_same_stream_identical():
    a = SeedManager(12345)
    b = SeedManager(12345)
    assert np.array_equal(_draw(a.stream("workload")), _draw(b.stream("workload")))


def test_different_stream_names_differ():
    manager = SeedManager(12345)
    left = _draw(manager.stream("workload"))
    right = _draw(manager.stream("faults"))
    assert not np.array_equal(left, right)


def test_different_masters_differ():
    left = _draw(SeedManager(1).stream("workload"))
    right = _draw(SeedManager(2).stream("workload"))
    assert not np.array_equal(left, right)


def test_stream_call_order_independent():
    manager = SeedManager(777)

    first = _draw(manager.stream("routing"))

    other = manager.stream("workload")
    _draw(other, count=1000)
    manager.stream("faults")

    second = _draw(manager.stream("routing"))

    assert np.array_equal(first, second)


def test_child_is_deterministic_and_distinct():
    parent_a = SeedManager(99)
    parent_b = SeedManager(99)
    child_a = parent_a.child("twin")
    child_b = parent_b.child("twin")
    assert child_a.master_seed == child_b.master_seed
    assert child_a.master_seed != parent_a.master_seed

    different_label = parent_a.child("counterfactual")
    assert different_label.master_seed != child_a.master_seed

    assert np.array_equal(
        _draw(child_a.stream("workload")), _draw(child_b.stream("workload"))
    )
