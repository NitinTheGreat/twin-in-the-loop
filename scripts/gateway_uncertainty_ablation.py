from __future__ import annotations

import argparse
import itertools
from collections import defaultdict
import json
import math
import random
import shutil
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from langgraph.checkpoint.memory import MemorySaver

from twinloop.actions.schema import NoOp
from twinloop.config import (
    EvaluationConfig, ExperimentConfig, FaultConfig, GraphConfig, LLMConfig, SimConfig, TwinConfig,
)
from twinloop.experiment import runner as runner_mod
from twinloop.experiment.arms import RunSpec
from twinloop.experiment.runner import _schedule_for, run_single
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.providers import ProviderResponse
from twinloop.twin.rollout import FAULT_MODE_SCHEDULED, rollout
from twinloop.twin.validator import TwinValidator, TwinVerdict

SEEDS = list(range(10))
REAL_TWIN = TwinValidator
MATCHED_P = {"llm": 0.747, "rule": 0.30}
KEEP_FIELDS = (
    "seed", "tick", "decision_index", "retry_index", "proposed_action", "twin_verdict",
    "was_applied", "counterfactual_violation_ticks_action", "counterfactual_violation_ticks_noop",
    "counterfactual_harm_delta", "ground_truth_harmful",
)


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


class ScriptedProvider:
    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        if "TOOL RESULT" not in prompt and "VIOLATIONS:" in prompt:
            service = _first_violator(prompt)
            reply = ('{"thought":"inspect","tool":"get_service_history",'
                     '"tool_input":{"service_id":"' + service + '"}}')
        elif "VIOLATIONS:" not in prompt:
            reply = '{"thought":"healthy","action":{"type":"no_op"}}'
        else:
            service = _first_violator(prompt)
            reply = ('{"thought":"add capacity","action":{"type":"scale_service",'
                     '"service_id":"' + service + '","delta_replicas":1}}')
        return ProviderResponse(text=reply, tokens_in=max(1, len(prompt) // 4),
                                tokens_out=max(1, len(reply) // 4))


class AblationGate:
    MODE = None
    P = 0.0

    def __init__(self, config, seed=0, actions_config=None, slo_config=None, log_path=None):
        self.config = config
        self.actions_config = actions_config
        self.slo_config = slo_config
        self.records = []
        self.log_path = Path(log_path) if log_path else None
        self.rng = np.random.default_rng(seed)

    def validate(self, sim, action, obs=None) -> TwinVerdict:
        kind = getattr(action, "type", None) or action.__class__.__name__
        is_noop = isinstance(action, NoOp) or kind == "no_op"
        if self.MODE == "always_reject":
            approved = is_noop
        elif self.MODE == "random":
            approved = True if is_noop else bool(self.rng.random() >= self.P)
        elif self.MODE == "reject_migrate":
            approved = kind != "migrate_service"
        else:
            raise ValueError(self.MODE)
        return TwinVerdict(approved=approved, reason=f"ablation: {self.MODE}",
                           action_violation_ticks=0, noop_violation_ticks=0,
                           fidelity={"ablation": self.MODE}, cost_ms=0.0)


class OracleGate(REAL_TWIN):
    def validate(self, sim, action, obs=None) -> TwinVerdict:
        start = time.perf_counter()
        horizon = self.config.horizon_ticks
        acted = rollout(sim, action, horizon, FAULT_MODE_SCHEDULED, self.actions_config, self.slo_config)
        held = rollout(sim, NoOp(), horizon, FAULT_MODE_SCHEDULED, self.actions_config, self.slo_config)
        diff = acted.violation_ticks - held.violation_ticks
        return TwinVerdict(
            approved=diff <= self.config.tolerance_margin, reason=f"oracle: diff {diff}",
            action_violation_ticks=acted.violation_ticks, noop_violation_ticks=held.violation_ticks,
            fidelity={"oracle": True}, cost_ms=(time.perf_counter() - start) * 1000.0)


def gate_class(mode, p=0.0):
    return type("Gate", (AblationGate,), {"MODE": mode, "P": p})


def experiment_config(out, gateway_faultable):
    return ExperimentConfig(
        sim=SimConfig(episode_ticks=120),
        fault=FaultConfig(gateway_faultable=gateway_faultable),
        graph=GraphConfig(decision_interval_ticks=10, retry_cap=2),
        twin=TwinConfig(horizon_ticks=20, tolerance_margin=0.0),
        evaluation=EvaluationConfig(harm_threshold_ticks=3),
        llm=LLMConfig(cache_dir=str(out / "cache"), model="scripted-stub"),
    )


def run_arm(out, gateway_faultable, agent_kind, gate_enabled, fidelity=None, patch=None, seeds=SEEDS):
    runner_mod.TwinValidator = patch if patch is not None else REAL_TWIN
    config = experiment_config(out, gateway_faultable)
    budget = BudgetGuard(10**9, 10**12)
    cache = ResponseCache(out / "cache.json")
    per_seed, records = [], []
    try:
        for seed in seeds:
            summary, _, recs = run_single(
                config, RunSpec("AB", agent_kind, gate_enabled, seed, fidelity), out / "run",
                lambda: ScriptedProvider(), budget, cache, True, MemorySaver())
            per_seed.append(summary["slo_violation_ticks"])
            records += [{k: r.get(k) for k in KEEP_FIELDS} for r in recs]
    finally:
        runner_mod.TwinValidator = REAL_TWIN
    return {"per_seed": per_seed, "records": records}


def command_run(args):
    out = Path(args.workdir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    config = experiment_config(out, args.gateway_faultable)
    seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else SEEDS
    schedules = {}
    for seed in seeds:
        _, schedule = _schedule_for(config, seed)
        schedules[seed] = [vars(e) for e in schedule.events]
    result = {"gateway_faultable": args.gateway_faultable, "seeds": seeds,
              "protocol": {"episode_ticks": 120, "decision_interval_ticks": 10, "retry_cap": 2,
                           "horizon_ticks": 20, "tolerance_margin": 0.0, "harm_threshold_ticks": 3,
                           "fault": config.fault.model_dump()},
              "schedules": schedules, "agents": {}}
    started = time.perf_counter()
    result["null"] = run_arm(out, args.gateway_faultable, "null", False, seeds=seeds)
    print(f"null done {time.perf_counter() - started:.0f}s", flush=True)
    for agent_kind in ("rule", "llm"):
        arms = {}
        arms["ungated"] = run_arm(out, args.gateway_faultable, agent_kind, False, seeds=seeds)
        arms["always_reject"] = run_arm(out, args.gateway_faultable, agent_kind, True, 1.0,
                                        gate_class("always_reject"), seeds=seeds)
        arms["random"] = run_arm(out, args.gateway_faultable, agent_kind, True, 1.0,
                                 gate_class("random", MATCHED_P[agent_kind]), seeds=seeds)
        arms["reject_migrate"] = run_arm(out, args.gateway_faultable, agent_kind, True, 1.0,
                                         gate_class("reject_migrate"), seeds=seeds)
        for fidelity in (0.0, 0.6, 1.0):
            arms[f"twin@{fidelity:.1f}"] = run_arm(out, args.gateway_faultable, agent_kind, True, fidelity, seeds=seeds)
        arms["oracle"] = run_arm(out, args.gateway_faultable, agent_kind, True, 1.0, OracleGate, seeds=seeds)
        result["agents"][agent_kind] = arms
        print(f"{agent_kind} done {time.perf_counter() - started:.0f}s", flush=True)
    Path(args.output).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"wrote {args.output}")


def t_pdf(x, df):
    return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi) * (
        1 + x * x / df) ** (-(df + 1) / 2)


def t_cdf(x, df, steps=4000):
    upper = abs(x)
    h = upper / steps
    area = t_pdf(0, df) + t_pdf(upper, df)
    for i in range(1, steps):
        area += (4 if i % 2 else 2) * t_pdf(i * h, df)
    half = area * h / 3
    return 0.5 + half if x >= 0 else 0.5 - half


def t_quantile(q, df):
    low, high = 0.0, 50.0
    for _ in range(100):
        mid = (low + high) / 2
        if t_cdf(mid, df) < q:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def average_ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def wilcoxon_exact(diffs):
    nonzero = [d for d in diffs if d != 0]
    m = len(nonzero)
    if m == 0:
        return {"n_nonzero": 0, "w_plus": 0.0, "w_minus": 0.0, "p_value": 1.0}
    ranks = average_ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    total = sum(ranks)
    expected = total / 2
    observed = abs(w_plus - expected)
    extreme = 0
    for signs in itertools.product((0, 1), repeat=m):
        w = sum(r for r, s in zip(ranks, signs) if s)
        if abs(w - expected) >= observed - 1e-9:
            extreme += 1
    return {"n_nonzero": m, "w_plus": w_plus, "w_minus": total - w_plus,
            "p_value": extreme / 2 ** m}


def sign_flip_exact(diffs):
    n = len(diffs)
    observed = abs(sum(diffs))
    if observed == 0:
        return 1.0
    extreme = sum(1 for signs in itertools.product((1, -1), repeat=n)
                  if abs(sum(s * d for s, d in zip(signs, diffs))) >= observed - 1e-9)
    return extreme / 2 ** n


def wilcoxon_normal_p(diffs):
    nonzero = [d for d in diffs if d != 0]
    m = len(nonzero)
    if m == 0:
        return 1.0
    ranks = average_ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    mean = m * (m + 1) / 4
    ties = {}
    for value in (abs(d) for d in nonzero):
        ties[value] = ties.get(value, 0) + 1
    variance = m * (m + 1) * (2 * m + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48
    if variance <= 0:
        return 1.0
    z = (abs(w_plus - mean) - 0.5) / math.sqrt(variance)
    return 2 * (1 - st.NormalDist().cdf(max(z, 0.0)))


def seeds_needed(diffs, power=0.8, alpha=0.05, sims=2000):
    mean = st.mean(diffs)
    sd = st.stdev(diffs)
    if mean == 0:
        return {"normal_formula": None, "bootstrap_wilcoxon": None,
                "reason": "observed mean paired difference is exactly zero"}
    z = st.NormalDist().inv_cdf(1 - alpha / 2) + st.NormalDist().inv_cdf(power)
    formula = math.ceil((z * sd / abs(mean)) ** 2) if sd > 0 else len(diffs)
    rng = random.Random(20260916)
    found = None
    for n in (10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300, 500, 1000):
        hits = 0
        for _ in range(sims):
            sample = [rng.choice(diffs) for _ in range(n)]
            p = wilcoxon_exact(sample)["p_value"] if sum(1 for d in sample if d) <= 14 else wilcoxon_normal_p(sample)
            hits += p < alpha
        if hits / sims >= power:
            found = {"n": n, "power": hits / sims}
            break
    return {"normal_formula": formula, "bootstrap_wilcoxon": found}


def compare(a, b):
    diffs = [x - y for x, y in zip(a, b)]
    n = len(diffs)
    mean = st.mean(diffs)
    sd = st.stdev(diffs)
    se = sd / math.sqrt(n)
    tcrit = t_quantile(0.975, n - 1)
    t_stat = mean / se if se > 0 else (0.0 if mean == 0 else math.inf)
    t_p = 2 * (1 - t_cdf(abs(t_stat), n - 1)) if math.isfinite(t_stat) else (1.0 if mean == 0 else 0.0)
    rng = random.Random(7)
    boots = sorted(st.mean(rng.choice(diffs) for _ in range(n)) for _ in range(10000))
    wil = wilcoxon_exact(diffs)
    result = {
        "diffs": diffs, "mean": mean, "sd": sd,
        "t_ci95": [mean - tcrit * se, mean + tcrit * se],
        "bootstrap_ci95": [boots[249], boots[9749]],
        "zeros": sum(1 for d in diffs if d == 0),
        "better": sum(1 for d in diffs if d < 0), "worse": sum(1 for d in diffs if d > 0),
        "wilcoxon": wil, "sign_flip_p": sign_flip_exact(diffs),
        "paired_t": {"t": t_stat, "df": n - 1, "p_value": t_p},
    }
    result["significant"] = wil["p_value"] < 0.05
    result["seeds_needed"] = None if result["significant"] else seeds_needed(diffs)
    return result


COMPARISONS = (
    ("twin@1.0", "always_reject"),
    ("twin@0.6", "always_reject"),
    ("twin@0.0", "always_reject"),
    ("twin@1.0", "ungated"),
    ("twin@1.0", "A0"),
    ("always_reject", "ungated"),
    ("always_reject", "A0"),
    ("ungated", "A0"),
    ("random", "ungated"),
    ("twin@1.0", "twin@0.0"),
    ("oracle", "twin@1.0"),
)


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted = [0.0] * len(pvalues)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def action_label(action):
    kind = action.get("type")
    if kind == "migrate_service":
        return f"migrate {action['service_id']}->{action['target_node_id']}"
    if kind in ("scale_service",):
        return f"scale {action['service_id']} {action['delta_replicas']:+d}"
    if kind == "no_op":
        return "no_op"
    return f"{kind} {action.get('service_id')}"


def migration_exposure(records):
    summary = {}
    for scope, subset in (("proposed", records), ("applied", [r for r in records if r.get("was_applied")])):
        migrations = [r for r in subset if (r.get("proposed_action") or {}).get("type") == "migrate_service"]
        by_target = {"gateway": 0, "edge": 0, "other": 0}
        for r in migrations:
            target = r["proposed_action"]["target_node_id"]
            by_target["gateway" if target.startswith("gw") else "edge" if target.startswith("edge") else "other"] += 1
        summary[scope] = {"total_actions": len(subset), "migrations": len(migrations), **by_target}
    applied = [r for r in records if r.get("was_applied") and r.get("counterfactual_harm_delta") is not None]
    benefit_all = sum(max(0, -r["counterfactual_harm_delta"]) for r in applied)
    migration_rows = [r for r in applied if r["proposed_action"].get("type") == "migrate_service"]
    benefit_mig = sum(max(0, -r["counterfactual_harm_delta"]) for r in migration_rows)
    benefit_gw = sum(max(0, -r["counterfactual_harm_delta"]) for r in migration_rows
                     if r["proposed_action"]["target_node_id"].startswith("gw"))
    net_mig = sum(-r["counterfactual_harm_delta"] for r in migration_rows)
    net_gw = sum(-r["counterfactual_harm_delta"] for r in migration_rows
                 if r["proposed_action"]["target_node_id"].startswith("gw"))
    summary["benefit"] = {
        "avoided_ticks_all_applied_actions": benefit_all,
        "avoided_ticks_migrations": benefit_mig,
        "avoided_ticks_gateway_migrations": benefit_gw,
        "net_ticks_migrations": net_mig,
        "net_ticks_gateway_migrations": net_gw,
        "gateway_share_of_migration_benefit": benefit_gw / benefit_mig if benefit_mig else None,
        "migration_share_of_all_benefit": benefit_mig / benefit_all if benefit_all else None,
    }
    by_type = {}
    for r in applied:
        kind = r["proposed_action"].get("type")
        entry = by_type.setdefault(kind, {"applied": 0, "avoided": 0, "added": 0})
        entry["applied"] += 1
        entry["avoided"] += max(0, -r["counterfactual_harm_delta"])
        entry["added"] += max(0, r["counterfactual_harm_delta"])
    summary["applied_by_type"] = by_type
    return summary


def approved_non_noop(records):
    rows = []
    for r in records:
        action = r.get("proposed_action") or {}
        if action.get("type") != "no_op" and r.get("was_applied"):
            rows.append({"seed": r["seed"], "tick": r["tick"], "action": action_label(action),
                         "harm_delta": r["counterfactual_harm_delta"]})
    return rows


def command_analyze(args):
    output = {"conditions": {}}
    recorded = []
    for path in sorted((ROOT / "results").glob("*/proposals.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_source"] = path.parent.name
                recorded.append(row)
    output["recorded_runs_on_disk"] = {"files": sorted({r["_source"] for r in recorded}),
                                       "exposure": migration_exposure(recorded)}
    for label, path in (("gateway_unfaultable", args.default), ("gateway_faultable", args.faultable)):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        condition = {"schedules": data["schedules"], "agents": {}}
        all_records = []
        for agent_kind, arms in data["agents"].items():
            totals = {name: arm["per_seed"] for name, arm in arms.items()}
            totals["A0"] = data["null"]["per_seed"]
            comparisons = {f"{a} - {b}": compare(totals[a], totals[b]) for a, b in COMPARISONS}
            adjusted = holm([c["wilcoxon"]["p_value"] for c in comparisons.values()])
            for comp, adj in zip(comparisons.values(), adjusted):
                comp["wilcoxon_holm_p"] = adj
            for arm in arms.values():
                all_records += arm["records"]
            condition["agents"][agent_kind] = {
                "per_seed": totals,
                "means": {name: st.mean(v) for name, v in totals.items()},
                "comparisons": comparisons,
                "exposure_all_arms": migration_exposure([r for arm in arms.values() for r in arm["records"]]),
                "twin@1.0_applied_non_noop": approved_non_noop(arms["twin@1.0"]["records"]),
                "oracle_applied_non_noop": approved_non_noop(arms["oracle"]["records"]),
            }
        condition["exposure_all_runs"] = migration_exposure(all_records)
        output["conditions"][label] = condition
    Path(args.output).write_text(json.dumps(output, indent=1), encoding="utf-8")
    for label, condition in output["conditions"].items():
        print(f"\n######## {label}")
        for seed, events in condition["schedules"].items():
            print(f"seed {seed}: " + "; ".join(
                f"{e['type']}@{e['target']} t{e['start_tick']}+{e['duration']}" for e in events))
        for agent_kind, agent in condition["agents"].items():
            print(f"\n=== {agent_kind}")
            for name, values in agent["per_seed"].items():
                print(f"  {name:15s} mean={st.mean(values):7.2f} {values}")
            for name, comp in agent["comparisons"].items():
                w = comp["wilcoxon"]
                need = comp["seeds_needed"]
                print(f"  {name:28s} diffs={comp['diffs']} mean={comp['mean']:+.2f} sd={comp['sd']:.2f} "
                      f"tCI=[{comp['t_ci95'][0]:+.2f},{comp['t_ci95'][1]:+.2f}] "
                      f"bootCI=[{comp['bootstrap_ci95'][0]:+.2f},{comp['bootstrap_ci95'][1]:+.2f}] "
                      f"better/tie/worse={comp['better']}/{comp['zeros']}/{comp['worse']} "
                      f"W+={w['w_plus']} W-={w['w_minus']} n_nz={w['n_nonzero']} pW={w['p_value']:.4f} "
                      f"pHolm={comp['wilcoxon_holm_p']:.4f} pFlip={comp['sign_flip_p']:.4f} "
                      f"t={comp['paired_t']['t']:.3f} pT={comp['paired_t']['p_value']:.4f} need={need}")
            print("  exposure:", json.dumps(agent["exposure_all_arms"]))
            print("  twin@1.0 applied non-noop:", agent["twin@1.0_applied_non_noop"])
        print("\nexposure all runs:", json.dumps(condition["exposure_all_runs"]))
    print("\nrecorded on disk:", json.dumps(output["recorded_runs_on_disk"]))


SWEEP_FIDELITIES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
SWEEP_AGENTS = ("rule", "llm")
AGENT_LABEL = {"rule": "rule", "llm": "scripted"}
Z80 = st.NormalDist().inv_cdf(0.975) + st.NormalDist().inv_cdf(0.8)


def sweep_arms(agent_kind):
    arms = [("ungated", False, None, None), ("always_reject", True, 1.0, gate_class("always_reject")),
            ("random", True, 1.0, gate_class("random", MATCHED_P[agent_kind])),
            ("oracle", True, 1.0, OracleGate)]
    arms += [(f"twin@{f:.1f}", True, f, None) for f in SWEEP_FIDELITIES]
    return arms


def sweep_tasks(seeds):
    tasks = []
    for seed in seeds:
        tasks.append(("null", "A0", False, None, None, seed))
        for agent_kind in SWEEP_AGENTS:
            for name, gated, fidelity, patch in sweep_arms(agent_kind):
                tasks.append((agent_kind, name, gated, fidelity, patch, seed))
    return tasks


def shard_path(shards, agent_kind, arm, seed):
    return shards / f"{agent_kind}__{arm}__{seed:04d}.json"


def command_sweep(args):
    seeds = list(range(args.seed_start, args.seed_start + args.seed_count))
    shards = Path(args.shards)
    shards.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir) / f"worker{args.worker}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    config = experiment_config(work, False)
    budget = BudgetGuard(10**12, 10**15)
    cache = ResponseCache(work / "cache.json")
    tasks = [t for i, t in enumerate(sweep_tasks(seeds)) if i % args.workers == args.worker]
    done = 0
    for agent_kind, arm, gated, fidelity, patch, seed in tasks:
        target = shard_path(shards, agent_kind, arm, seed)
        if target.exists():
            continue
        runner_mod.TwinValidator = patch if patch is not None else REAL_TWIN
        started = time.perf_counter()
        try:
            summary, _, recs = run_single(
                config, RunSpec("SW", agent_kind, gated, seed, fidelity), work / "run",
                lambda: ScriptedProvider(), budget, cache, True, MemorySaver())
        finally:
            runner_mod.TwinValidator = REAL_TWIN
        payload = {"agent": agent_kind, "arm": arm, "seed": seed, "fidelity": fidelity,
                   "violation_ticks": summary["slo_violation_ticks"],
                   "wall_seconds": time.perf_counter() - started,
                   "records": [{k: r.get(k) for k in KEEP_FIELDS} for r in recs]}
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(target)
        done += 1
        if done % 25 == 0:
            print(f"worker {args.worker}: {done} episodes", flush=True)
    print(f"worker {args.worker} finished: {done} new episodes", flush=True)


def wilcoxon_exact_dp(diffs):
    nonzero = [d for d in diffs if d != 0]
    m = len(nonzero)
    if m == 0:
        return {"n_nonzero": 0, "w_plus": 0.0, "w_minus": 0.0, "p_value": 1.0}
    ranks = average_ranks([abs(d) for d in nonzero])
    doubled = [int(round(2 * r)) for r in ranks]
    total = sum(doubled)
    counts = np.zeros(total + 1)
    counts[0] = 1.0
    for r in doubled:
        shifted = np.zeros(total + 1)
        shifted[r:] = counts[:total + 1 - r]
        counts = counts + shifted
        counts = counts / counts.sum()
    w_plus2 = sum(r for r, d in zip(doubled, nonzero) if d > 0)
    center = total / 2
    values = np.arange(total + 1)
    p = float(counts[np.abs(values - center) >= abs(w_plus2 - center) - 1e-9].sum())
    return {"n_nonzero": m, "w_plus": w_plus2 / 2, "w_minus": (total - w_plus2) / 2, "p_value": min(1.0, p)}


def bootstrap_power(diffs, n, sims, rng):
    if all(d == 0 for d in diffs):
        return 0.0
    hits = 0
    for _ in range(sims):
        sample = [diffs[i] for i in rng.integers(0, len(diffs), n)]
        hits += wilcoxon_normal_p(sample) < 0.05
    return hits / sims


def episode_comparison(a, b, rng):
    diffs = [x - y for x, y in zip(a, b)]
    n = len(diffs)
    mean = st.mean(diffs)
    sd = st.stdev(diffs)
    se = sd / math.sqrt(n)
    tcrit = t_quantile(0.975, n - 1)
    idx = rng.integers(0, n, (5000, n))
    boots = np.asarray(diffs, dtype=float)[idx].mean(axis=1)
    wil = wilcoxon_exact_dp(diffs)
    out = {"diffs": diffs, "mean": mean, "sd": sd, "t_ci95": [mean - tcrit * se, mean + tcrit * se],
           "bootstrap_ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
           "n_nonzero": wil["n_nonzero"], "better": sum(1 for d in diffs if d < 0),
           "worse": sum(1 for d in diffs if d > 0), "wilcoxon": wil,
           "significant_unadjusted": wil["p_value"] < 0.05,
           "observed_power_at_n": bootstrap_power(diffs, n, 1000, rng)}
    out["seeds_for_80pct_power_normal"] = (math.ceil((Z80 * sd / abs(mean)) ** 2)
                                           if mean != 0 and sd > 0 else None)
    return out


def per_seed_candidate_arrays(records_by_seed, seeds, gated):
    keys = ("benefit_total", "benefit_approved", "harm_total", "harm_rejected", "net_approved",
            "n_candidates", "n_beneficial", "n_neutral", "n_harmful", "n_approved")
    arrays = {k: np.zeros(len(seeds)) for k in keys}
    for i, seed in enumerate(seeds):
        for r in records_by_seed.get(seed, []):
            if (r.get("proposed_action") or {}).get("type") == "no_op":
                continue
            d = r["counterfactual_harm_delta"]
            approved = (r["twin_verdict"] is True) if gated else bool(r.get("was_applied"))
            arrays["n_candidates"][i] += 1
            arrays["n_beneficial"][i] += d < 0
            arrays["n_neutral"][i] += d == 0
            arrays["n_harmful"][i] += d > 0
            arrays["benefit_total"][i] += max(-d, 0)
            arrays["harm_total"][i] += max(d, 0)
            if approved:
                arrays["n_approved"][i] += 1
                arrays["benefit_approved"][i] += max(-d, 0)
                arrays["net_approved"][i] += d
            else:
                arrays["harm_rejected"][i] += max(d, 0)
    return arrays


def ratio_with_ci(numerator, denominator, idx):
    den = float(denominator.sum())
    point = float(numerator.sum()) / den if den else None
    num_b = numerator[idx].sum(axis=1)
    den_b = denominator[idx].sum(axis=1)
    ok = den_b > 0
    result = {"point": point, "ci95": None, "estimable_resamples": float(ok.mean()),
              "numerator": float(numerator.sum()), "denominator": den}
    if point is not None and ok.any():
        vals = num_b[ok] / den_b[ok]
        result["ci95"] = [float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))]
    return result


def mean_with_ci(values, idx):
    means = values[idx].mean(axis=1)
    n = len(values)
    sd = float(values.std(ddof=1))
    tcrit = t_quantile(0.975, n - 1)
    mean = float(values.mean())
    return {"total": float(values.sum()), "mean_per_seed": mean, "sd_per_seed": sd,
            "t_ci95": [mean - tcrit * sd / math.sqrt(n), mean + tcrit * sd / math.sqrt(n)],
            "bootstrap_ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
            "nonzero_seeds": int((values != 0).sum())}


def sweep_quantiles(values):
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]
    return {"min": ordered[0], "median": pick(0.5), "p90": pick(0.9), "max": ordered[-1]}


def composition_detail(records_by_seed, seeds):
    deltas, seeds_with_benefit = [], set()
    for seed in seeds:
        for r in records_by_seed.get(seed, []):
            if (r.get("proposed_action") or {}).get("type") == "no_op":
                continue
            d = r["counterfactual_harm_delta"]
            deltas.append((d, r["proposed_action"]["type"]))
            if d < 0:
                seeds_with_benefit.add(seed)
    by_type = defaultdict(lambda: {"beneficial": 0, "neutral": 0, "harmful": 0, "benefit": 0, "harm": 0})
    for d, kind in deltas:
        entry = by_type[kind]
        entry["beneficial" if d < 0 else "neutral" if d == 0 else "harmful"] += 1
        entry["benefit"] += max(-d, 0)
        entry["harm"] += max(d, 0)
    beneficial = [-d for d, _ in deltas if d < 0]
    harmful = [d for d, _ in deltas if d > 0]
    return {"candidates": len(deltas), "beneficial": len(beneficial),
            "neutral": sum(1 for d, _ in deltas if d == 0), "harmful": len(harmful),
            "harmful_above_3": sum(1 for d, _ in deltas if d > 3),
            "available_benefit": sum(beneficial), "available_harm": sum(harmful),
            "benefit_quantiles": sweep_quantiles(beneficial), "harm_quantiles": sweep_quantiles(harmful),
            "by_action_type": {k: dict(v) for k, v in sorted(by_type.items())},
            "seeds_with_beneficial_candidate": len(seeds_with_benefit),
            "seeds_with_beneficial_candidate_list": sorted(seeds_with_benefit)}


def crossings(curve, fidelities):
    points = []
    for i in range(len(curve) - 1):
        a, b = curve[i], curve[i + 1]
        if (a > 0 > b) or (a < 0 < b):
            points.append(fidelities[i] + (fidelities[i + 1] - fidelities[i]) * a / (a - b))
        elif b == 0 and a != 0:
            points.append(fidelities[i + 1])
    return points


def load_shards(shards):
    data = defaultdict(lambda: defaultdict(dict))
    for path in sorted(Path(shards).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        data[payload["agent"]][payload["arm"]][payload["seed"]] = payload
    return data


def adjacent_changes(arrays_by_name, names, idx):
    result = {}
    for metric in ("net_sum_d_approved", "benefit_preserved", "harm_prevented"):
        steps = []
        for lo, hi in zip(names[:-1], names[1:]):
            a, b = arrays_by_name[lo], arrays_by_name[hi]
            if metric == "net_sum_d_approved":
                diff_b = b["net_approved"][idx].mean(axis=1) - a["net_approved"][idx].mean(axis=1)
                point = float(b["net_approved"].mean() - a["net_approved"].mean())
            else:
                num, den = (("benefit_approved", "benefit_total") if metric == "benefit_preserved"
                            else ("harm_rejected", "harm_total"))
                da, db = a[den][idx].sum(axis=1), b[den][idx].sum(axis=1)
                ok = (da > 0) & (db > 0)
                diff_b = b[num][idx].sum(axis=1)[ok] / db[ok] - a[num][idx].sum(axis=1)[ok] / da[ok]
                pa = a[num].sum() / a[den].sum() if a[den].sum() else None
                pb = b[num].sum() / b[den].sum() if b[den].sum() else None
                point = None if pa is None or pb is None else float(pb - pa)
            ci = ([float(np.quantile(diff_b, 0.025)), float(np.quantile(diff_b, 0.975))]
                  if len(diff_b) and point is not None else None)
            steps.append({"from": lo, "to": hi, "change": point, "ci95": ci,
                          "excludes_zero": bool(ci and (ci[0] > 0 or ci[1] < 0))})
        result[metric] = steps
    return result


def command_sweep_analyze(args):
    data = load_shards(args.shards)
    rng = np.random.default_rng(20260917)
    null = data["null"]["A0"]
    seeds = sorted(null)
    for agent_kind in SWEEP_AGENTS:
        for name, *_ in sweep_arms(agent_kind):
            missing = [s for s in seeds if s not in data[agent_kind][name]]
            if missing:
                raise SystemExit(f"missing shards for {agent_kind} {name}: {missing[:10]}")
    idx = rng.integers(0, len(seeds), (5000, len(seeds)))
    wall = defaultdict(list)
    for agent_kind in list(SWEEP_AGENTS) + ["null"]:
        for arm, shards_by_seed in data[agent_kind].items():
            wall[f"{agent_kind}|{arm}"] += [p["wall_seconds"] for p in shards_by_seed.values()]
    output = {"seeds": seeds, "n_seeds": len(seeds), "fidelities": list(SWEEP_FIDELITIES),
              "protocol": {"episode_ticks": 120, "decision_interval_ticks": 10, "retry_cap": 2,
                           "horizon_ticks": 20, "tolerance_margin": 0.0, "harm_threshold_ticks": 3,
                           "gateway_faultable": False, "fault": FaultConfig().model_dump(),
                           "random_gate_p": MATCHED_P, "bootstrap_resamples": 5000,
                           "bootstrap_unit": "seed"},
              "runtime": {"episodes": sum(len(v) for v in wall.values()),
                          "total_episode_seconds": sum(sum(v) for v in wall.values()),
                          "per_arm_mean_seconds": {k: st.mean(v) for k, v in sorted(wall.items())}},
              "a0_per_seed": [null[s]["violation_ticks"] for s in seeds], "agents": {}}
    for agent_kind in SWEEP_AGENTS:
        agent = {"label": AGENT_LABEL[agent_kind], "arms": {}}
        totals, arrays_by_name, union = {}, {}, set()
        for name, gated, fidelity, _ in sweep_arms(agent_kind):
            by_seed = {s: data[agent_kind][name][s]["records"] for s in seeds}
            arrays = per_seed_candidate_arrays(by_seed, seeds, gated)
            comp = composition_detail(by_seed, seeds)
            union.update(comp["seeds_with_beneficial_candidate_list"])
            totals[name] = [data[agent_kind][name][s]["violation_ticks"] for s in seeds]
            arrays_by_name[name] = arrays
            agent["arms"][name] = {
                "fidelity": fidelity, "gated": gated,
                "episode_mean_violation_ticks": st.mean(totals[name]),
                "benefit_preserved": ratio_with_ci(arrays["benefit_approved"], arrays["benefit_total"], idx),
                "harm_prevented": ratio_with_ci(arrays["harm_rejected"], arrays["harm_total"], idx),
                "net_sum_d_approved": mean_with_ci(arrays["net_approved"], idx),
                "benefit_available": mean_with_ci(arrays["benefit_total"], idx),
                "harm_available": mean_with_ci(arrays["harm_total"], idx),
                "candidates": int(arrays["n_candidates"].sum()),
                "approved": int(arrays["n_approved"].sum()),
                "composition": comp,
            }
        agent["seeds_with_beneficial_candidate_any_arm"] = len(union)
        twin_names = [f"twin@{f:.1f}" for f in SWEEP_FIDELITIES]
        curve = [float(arrays_by_name[n]["net_approved"].mean()) for n in twin_names]
        boot_curves = np.stack([arrays_by_name[n]["net_approved"][idx].mean(axis=1) for n in twin_names], axis=1)
        boot_cross = [crossings(list(row), list(SWEEP_FIDELITIES)) for row in boot_curves]
        single = [c[0] for c in boot_cross if len(c) == 1]
        agent["net_sum_d_curve"] = {
            "fidelities": list(SWEEP_FIDELITIES), "mean_per_seed": curve,
            "observed_crossings": crossings(curve, list(SWEEP_FIDELITIES)),
            "bootstrap_crossing_counts": {str(k): sum(1 for c in boot_cross if len(c) == k) for k in range(6)},
            "bootstrap_single_crossing_ci95": ([float(np.quantile(single, 0.025)), float(np.quantile(single, 0.975))]
                                               if single else None),
            "bootstrap_single_crossing_median": float(np.median(single)) if single else None,
            "adjacent_level_changes": adjacent_changes(arrays_by_name, twin_names, idx),
        }
        reject = np.asarray(totals["always_reject"], dtype=float)
        episode_diffs = np.stack([np.asarray(totals[n], dtype=float) - reject for n in twin_names], axis=1)
        episode_curve = [float(v) for v in episode_diffs.mean(axis=0)]
        episode_boot = [crossings(list(row), list(SWEEP_FIDELITIES)) for row in episode_diffs[idx].mean(axis=1)]
        episode_single = [c[0] for c in episode_boot if len(c) == 1]
        agent["episode_diff_curve_vs_reject_all"] = {
            "fidelities": list(SWEEP_FIDELITIES), "mean_per_seed": episode_curve,
            "observed_crossings": crossings(episode_curve, list(SWEEP_FIDELITIES)),
            "bootstrap_crossing_counts": {str(k): sum(1 for c in episode_boot if len(c) == k) for k in range(6)},
            "bootstrap_single_crossing_ci95": ([float(np.quantile(episode_single, 0.025)),
                                                float(np.quantile(episode_single, 0.975))] if episode_single else None),
        }
        totals["A0"] = output["a0_per_seed"]
        pairs = [(n, "always_reject") for n in twin_names] + [
            ("oracle", "always_reject"), ("ungated", "always_reject"), ("random", "always_reject"),
            ("twin@1.0", "twin@0.0"), ("oracle", "twin@1.0"), ("always_reject", "A0")]
        results = {f"{a} - {b}": episode_comparison(totals[a], totals[b], rng) for a, b in pairs}
        for comp, adj in zip(results.values(), holm([c["wilcoxon"]["p_value"] for c in results.values()])):
            comp["wilcoxon_holm_p"] = adj
            comp["significant_holm"] = adj < 0.05
        agent["episode_comparisons"] = results
        agent["per_seed_violation_ticks"] = totals
        output["agents"][agent_kind] = agent
    Path(args.output).write_text(json.dumps(output, indent=1), encoding="utf-8")
    print_sweep_summary(output)


def print_sweep_summary(output):
    fmt = lambda v: "n/e" if v is None else f"{v:.3f}"
    fci = lambda c: "n/e" if c is None else f"[{c[0]:.3f}, {c[1]:.3f}]"
    rt = output["runtime"]
    print(f"seeds={output['n_seeds']} episodes={rt['episodes']} episode_seconds={rt['total_episode_seconds']:.0f}")
    for k, v in rt["per_arm_mean_seconds"].items():
        print(f"  cost {k:26s} {v:.2f}s")
    for agent in output["agents"].values():
        print(f"\n===== {agent['label']}  seeds with >=1 beneficial candidate in any arm: "
              f"{agent['seeds_with_beneficial_candidate_any_arm']}")
        for name, arm in agent["arms"].items():
            bp, hp = arm["benefit_preserved"], arm["harm_prevented"]
            net, comp = arm["net_sum_d_approved"], arm["composition"]
            print(f"  {name:14s} ep={arm['episode_mean_violation_ticks']:.2f} cand={arm['candidates']} appr={arm['approved']} "
                  f"BP={fmt(bp['point'])} {fci(bp['ci95'])} ({bp['numerator']:.0f}/{bp['denominator']:.0f}) "
                  f"HP={fmt(hp['point'])} {fci(hp['ci95'])} ({hp['numerator']:.0f}/{hp['denominator']:.0f}) "
                  f"net total={net['total']:.0f} mean={net['mean_per_seed']:.3f} t={fci(net['t_ci95'])} "
                  f"boot={fci(net['bootstrap_ci95'])} nz={net['nonzero_seeds']} | "
                  f"b/n/h={comp['beneficial']}/{comp['neutral']}/{comp['harmful']} (>3 {comp['harmful_above_3']}) "
                  f"B={comp['available_benefit']} H={comp['available_harm']} Bq={comp['benefit_quantiles']} "
                  f"Hq={comp['harm_quantiles']} seedsB={comp['seeds_with_beneficial_candidate']} "
                  f"types={comp['by_action_type']}")
        cur = agent["net_sum_d_curve"]
        print("  CURVE", [round(v, 3) for v in cur["mean_per_seed"]], "crossings", cur["observed_crossings"],
              "boot counts", cur["bootstrap_crossing_counts"], "single CI", cur["bootstrap_single_crossing_ci95"],
              "median", cur["bootstrap_single_crossing_median"])
        ep = agent["episode_diff_curve_vs_reject_all"]
        print("  EPISODE CURVE", [round(v, 3) for v in ep["mean_per_seed"]], "crossings", ep["observed_crossings"],
              "boot counts", ep["bootstrap_crossing_counts"], "single CI", ep["bootstrap_single_crossing_ci95"])
        for metric, steps in cur["adjacent_level_changes"].items():
            print("  STEPS", metric, [(s["from"], s["to"], None if s["change"] is None else round(s["change"], 3),
                                      None if s["ci95"] is None else [round(x, 3) for x in s["ci95"]],
                                      s["excludes_zero"]) for s in steps])
        for name, c in agent["episode_comparisons"].items():
            w = c["wilcoxon"]
            print(f"  EP {name:28s} mean={c['mean']:+.3f} sd={c['sd']:.2f} t={fci(c['t_ci95'])} "
                  f"boot={fci(c['bootstrap_ci95'])} nz={c['n_nonzero']} B/W={c['better']}/{c['worse']} "
                  f"W+={w['w_plus']} W-={w['w_minus']} p={w['p_value']:.4g} holm={c['wilcoxon_holm_p']:.4g} "
                  f"sigHolm={c['significant_holm']} power={c['observed_power_at_n']:.3f} "
                  f"need={c['seeds_for_80pct_power_normal']}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--gateway-faultable", action="store_true")
    run.add_argument("--workdir", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--seeds", default=None)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--default", required=True)
    analyze.add_argument("--faultable", required=True)
    analyze.add_argument("--output", required=True)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--shards", required=True)
    sweep.add_argument("--workdir", required=True)
    sweep.add_argument("--worker", type=int, required=True)
    sweep.add_argument("--workers", type=int, required=True)
    sweep.add_argument("--seed-start", type=int, default=0)
    sweep.add_argument("--seed-count", type=int, default=150)
    sweep_analyze = sub.add_parser("sweep-analyze")
    sweep_analyze.add_argument("--shards", required=True)
    sweep_analyze.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "run":
        command_run(args)
    elif args.command == "analyze":
        command_analyze(args)
    elif args.command == "sweep":
        command_sweep(args)
    else:
        command_sweep_analyze(args)


if __name__ == "__main__":
    main()
