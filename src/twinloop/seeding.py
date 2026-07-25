from __future__ import annotations

import hashlib

from numpy.random import Generator, PCG64, SeedSequence


def _tag(namespace: str, name: str) -> int:
    digest = hashlib.sha256(f"{namespace}\x00{name}".encode("utf-8")).digest()
    return int.from_bytes(digest, "big")


class SeedManager:
    def __init__(self, master_seed: int) -> None:
        self._master_seed = int(master_seed)

    @property
    def master_seed(self) -> int:
        return self._master_seed

    def _entropy(self, namespace: str, name: str) -> list[int]:
        return [self._master_seed, _tag(namespace, name)]

    def seed_sequence(self, name: str) -> SeedSequence:
        return SeedSequence(self._entropy("stream", name))

    def stream(self, name: str) -> Generator:
        return Generator(PCG64(self.seed_sequence(name)))

    def child(self, label: str) -> "SeedManager":
        sequence = SeedSequence(self._entropy("child", label))
        words = sequence.generate_state(4)
        derived = 0
        for word in words:
            derived = (derived << 32) | int(word)
        return SeedManager(derived)

    def __repr__(self) -> str:
        return f"SeedManager(master_seed={self._master_seed})"
