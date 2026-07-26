from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ArmSpec:
    arm_id: str
    agent_kind: str
    gate_enabled: bool
    fidelity: Optional[float]


@dataclass
class RunSpec:
    arm_id: str
    agent_kind: str
    gate_enabled: bool
    seed: int
    fidelity: Optional[float]


def default_arms(config) -> list[ArmSpec]:
    return [
        ArmSpec("A0", "null", False, None),
        ArmSpec("A1", "rule", False, None),
        ArmSpec("A2", "llm", False, None),
        ArmSpec("A3", "llm", True, None),
        ArmSpec("A4", "rule", True, config.evaluation.a4_fidelity),
    ]


def expand_runs(arms, seeds, fidelity_levels) -> list[RunSpec]:
    runs: list[RunSpec] = []
    for arm in arms:
        for seed in seeds:
            if arm.arm_id == "A3":
                for fidelity in fidelity_levels:
                    runs.append(
                        RunSpec(arm.arm_id, arm.agent_kind, True, seed, float(fidelity))
                    )
            elif arm.gate_enabled:
                runs.append(
                    RunSpec(arm.arm_id, arm.agent_kind, True, seed, arm.fidelity)
                )
            else:
                runs.append(
                    RunSpec(arm.arm_id, arm.agent_kind, False, seed, None)
                )
    return runs
