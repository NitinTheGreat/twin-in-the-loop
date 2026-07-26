from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml
from pydantic import BaseModel, Field


class SimConfig(BaseModel):
    tick_seconds: float = 1.0
    episode_ticks: int = 300
    queue_cap: int = 128
    default_cpu_capacity: float = 100.0
    default_mem_capacity: float = 100.0
    default_link_bandwidth: float = 1000.0
    default_base_latency_ms: float = 5.0


class TopologyConfig(BaseModel):
    n_gateways: int = 1
    n_edge_servers: int = 4
    n_devices: int = 12
    n_services: int = 6
    edge_cpu_capacity: float = 100.0
    edge_mem_capacity: float = 100.0
    device_cpu_capacity: float = 10.0
    device_mem_capacity: float = 10.0


class FaultConfig(BaseModel):
    faults_per_episode: int = 3
    min_start_tick: int = 20
    max_start_tick: int = 260
    min_duration: int = 20
    max_duration: int = 80
    min_magnitude: float = 1.5
    max_magnitude: float = 4.0


class TwinConfig(BaseModel):
    fidelity: float = 1.0
    sigma_obs: float = 0.0
    lag_ticks: int = 0
    simplify_queueing: bool = False
    drift_pct: float = 0.0
    forecast_err: float = 0.0
    horizon_ticks: int = 30
    tolerance_margin: float = 0.0


class SLOConfig(BaseModel):
    p95_target_ms: float = 600.0
    availability_target: float = 0.99
    at_risk_fraction: float = 0.85
    history_window: int = 10
    summary_char_budget: int = 6000
    chars_per_token: float = 4.0


class ActionsConfig(BaseModel):
    migration_downtime_per_mem: float = 2.0
    migration_min_downtime: int = 2
    migration_transfer_cost: float = 1.0
    restart_downtime: int = 3
    restart_cost: float = 1.0
    replica_cap: int = 5


class RuleAgentConfig(BaseModel):
    node_util_threshold: float = 0.9
    link_latency_threshold_ms: float = 12.0
    sustain_ticks: int = 3
    mem_growth_window: int = 5
    cooldown_ticks: int = 15
    scale_delta: int = 1
    throttle_fraction: float = 0.6


class LLMConfig(BaseModel):
    provider: str = "local"
    model: str = "qwen2.5:7b-instruct"
    temperature: float = 0.0
    timeout_seconds: float = 30.0
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "LLM_API_KEY"
    max_retries: int = 2
    max_calls: int = 5000
    max_tokens: int = 5000000
    cache_dir: str = "results/llm_cache"
    cache_bypass: bool = False
    cache_only: bool = False
    react_max_steps: int = 4
    react_timeout_seconds: float = 20.0
    log_path: str = "results/logs/llm_calls.jsonl"
    prompt_version: str = "v1"


class GraphConfig(BaseModel):
    decision_interval_ticks: int = 10
    retry_cap: int = 2
    checkpoint_path: str = "results/checkpoints/graph.sqlite"


class EvaluationConfig(BaseModel):
    harm_threshold_ticks: int = 3
    a4_fidelity: float = 0.6


class AgentConfig(BaseModel):
    agent_type: str = "null"
    prompt_version: str = "v0"
    temperature: float = 0.0
    retry_cap: int = 2


class ExperimentConfig(BaseModel):
    name: str = "default"
    seeds: list[int] = Field(default_factory=lambda: list(range(30)))
    arms: list[str] = Field(default_factory=lambda: ["A0", "A1", "A2", "A3", "A4"])
    fidelity_levels: list[float] = Field(
        default_factory=lambda: [0.0, 0.25, 0.5, 0.75, 1.0]
    )
    timeout_seconds: float = 60.0
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
