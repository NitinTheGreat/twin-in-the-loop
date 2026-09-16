from __future__ import annotations

from pathlib import Path
from typing import Annotated, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class FiniteConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class SimConfig(FiniteConfig):
    tick_seconds: PositiveFloat = 1.0
    episode_ticks: PositiveInt = 300
    queue_cap: NonNegativeInt = 128
    default_cpu_capacity: PositiveFloat = 100.0
    default_mem_capacity: PositiveFloat = 100.0
    default_link_bandwidth: PositiveFloat = 1000.0
    default_base_latency_ms: NonNegativeFloat = 5.0


class TopologyConfig(FiniteConfig):
    redundant_links: bool = False
    n_gateways: int = Field(default=1, ge=1, le=1, description="The v1 topology supports exactly one gateway.")
    n_edge_servers: PositiveInt = 4
    n_devices: PositiveInt = 12
    n_services: PositiveInt = 6
    edge_cpu_capacity: PositiveFloat = 100.0
    edge_mem_capacity: PositiveFloat = 100.0
    device_cpu_capacity: PositiveFloat = 10.0
    device_mem_capacity: PositiveFloat = 10.0


class FaultConfig(FiniteConfig):
    faults_per_episode: NonNegativeInt = 3
    min_start_tick: NonNegativeInt = 20
    max_start_tick: NonNegativeInt = 260
    min_duration: PositiveInt = 20
    max_duration: PositiveInt = 80
    min_magnitude: PositiveFloat = 1.5
    max_magnitude: PositiveFloat = 4.0
    gateway_faultable: bool = False

    @model_validator(mode="after")
    def ordered_ranges(self):
        for name in ("start_tick", "duration", "magnitude"):
            if getattr(self, f"min_{name}") > getattr(self, f"max_{name}"):
                raise ValueError(f"min_{name} must be <= max_{name}")
        return self


class TwinConfig(FiniteConfig):
    fidelity: Fraction = 1.0
    sigma_obs: NonNegativeFloat = 0.0
    lag_ticks: NonNegativeInt = 0
    simplify_queueing: bool = False
    drift_pct: NonNegativeFloat = 0.0
    forecast_err: NonNegativeFloat = 0.0
    horizon_ticks: PositiveInt = 30
    tolerance_margin: float = 0.0


class SLOConfig(FiniteConfig):
    p95_target_ms: PositiveFloat = 600.0
    availability_target: Fraction = 0.99
    at_risk_fraction: Fraction = 0.85
    history_window: PositiveInt = 10
    summary_char_budget: PositiveInt = 6000
    chars_per_token: PositiveFloat = 4.0


class ActionsConfig(FiniteConfig):
    """Downtime counts full unavailable processing ticks, including the final tick."""
    migration_downtime_per_mem: NonNegativeFloat = 2.0
    migration_min_downtime: PositiveInt = 2
    migration_transfer_cost: NonNegativeFloat = 1.0
    restart_downtime: int = Field(default=3, ge=1, description="Full unavailable processing ticks; completes at the end of the last tick.")
    restart_cost: NonNegativeFloat = 1.0
    replica_cap: PositiveInt = 5


class RuleAgentConfig(FiniteConfig):
    node_util_threshold: Fraction = 0.9
    link_latency_threshold_ms: PositiveFloat = 12.0
    sustain_ticks: PositiveInt = 3
    mem_growth_window: Annotated[int, Field(ge=2)] = 5
    cooldown_ticks: NonNegativeInt = 15
    scale_delta: PositiveInt = 1
    throttle_fraction: Annotated[float, Field(gt=0, le=1)] = 0.6


class LLMConfig(FiniteConfig):
    provider: str = "local"
    model: str = "qwen2.5:7b-instruct"
    temperature: NonNegativeFloat = 0.0
    timeout_seconds: PositiveFloat = 30.0
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "LLM_API_KEY"
    max_retries: NonNegativeInt = 2
    max_calls: NonNegativeInt = 5000
    max_tokens: NonNegativeInt = 5000000
    cache_dir: str = "results/llm_cache"
    cache_bypass: bool = False
    cache_only: bool = False
    react_max_steps: PositiveInt = 4
    react_timeout_seconds: PositiveFloat = 20.0
    react_final_reserve_fraction: Annotated[float, Field(gt=0, lt=1)] = 0.25
    log_path: str = "results/logs/llm_calls.jsonl"
    prompt_version: str = "v1"


class GraphConfig(FiniteConfig):
    decision_interval_ticks: PositiveInt = 10
    retry_cap: NonNegativeInt = 2
    checkpoint_path: str = "results/checkpoints/graph.sqlite"


class EvaluationConfig(FiniteConfig):
    harm_threshold_ticks: NonNegativeInt = 3
    a4_fidelity: Fraction = 0.6


class AgentConfig(FiniteConfig):
    agent_type: str = "null"
    prompt_version: str = "v0"
    temperature: float = 0.0
    retry_cap: int = 2


class ExperimentConfig(FiniteConfig):
    name: str = "default"
    seeds: list[int] = Field(default_factory=lambda: list(range(30)))
    arms: list[str] = Field(default_factory=lambda: ["A0", "A1", "A2", "A3", "A4"])
    fidelity_levels: list[Fraction] = Field(
        default_factory=lambda: [0.0, 0.25, 0.5, 0.75, 1.0]
    )
    timeout_seconds: PositiveFloat = 60.0
    sim: SimConfig = Field(default_factory=SimConfig)
    topology: TopologyConfig = Field(default_factory=TopologyConfig)
    fault: FaultConfig = Field(default_factory=FaultConfig)
    twin: TwinConfig = Field(default_factory=TwinConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    slo: SLOConfig = Field(default_factory=SLOConfig)
    actions: ActionsConfig = Field(default_factory=ActionsConfig)
    rule_agent: RuleAgentConfig = Field(default_factory=RuleAgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


def load_config(path: Union[str, Path]) -> ExperimentConfig:
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return ExperimentConfig.model_validate(data)
