from __future__ import annotations

import argparse
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Z = st.NormalDist().inv_cdf(0.975) + st.NormalDist().inv_cdf(0.8)


def is_noop(row):
    return (row.get("proposed_action") or {}).get("type") == "no_op"


def composition(rows):
    candidates = [r for r in rows if not is_noop(r)]
    out = {"proposals": len(rows), "noop_proposals": len(rows) - len(candidates),
           "candidates": len(candidates)}
    groups = defaultdict(list)
    for r in candidates:
        groups["all"].append(r)
        groups[f"action:{r['proposed_action']['type']}"].append(r)
        groups[f"attempt:{'first' if not r.get('retry_index') else 'retry'}"].append(r)
    table = {}
    for key, subset in sorted(groups.items()):
        deltas = [r["counterfactual_harm_delta"] for r in subset]
        beneficial = [d for d in deltas if d < 0]
        harmful = [d for d in deltas if d > 0]
        table[key] = {
            "n": len(deltas),
            "beneficial": len(beneficial), "neutral": sum(1 for d in deltas if d == 0),
            "harmful": len(harmful),
            "harmful_above_threshold_3": sum(1 for d in deltas if d > 3),
            "available_benefit": -sum(beneficial), "available_harm": sum(harmful),
            "benefit_quantiles": quantiles([-d for d in beneficial]),
            "harm_quantiles": quantiles(harmful),
            "histogram": dict(sorted(histogram(deltas).items())),
        }
    out["by_group"] = table
    seen = {}
    for r in candidates:
        key = (r.get("seed"), r.get("tick"), r.get("retry_index"),
               json.dumps(r["proposed_action"], sort_keys=True), r["counterfactual_harm_delta"])
        seen[key] = r["counterfactual_harm_delta"]
    unique = list(seen.values())
    out["unique_candidates"] = {
        "n": len(unique), "beneficial": sum(1 for d in unique if d < 0),
        "neutral": sum(1 for d in unique if d == 0), "harmful": sum(1 for d in unique if d > 0),
        "available_benefit": -sum(d for d in unique if d < 0), "available_harm": sum(d for d in unique if d > 0),
        "beneficial_seeds": sorted({k[0] for k, d in seen.items() if d < 0}, key=str),
    }
    return out


def quantiles(values):
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]
    return {"min": ordered[0], "median": pick(0.5), "p90": pick(0.9), "max": ordered[-1]}


def histogram(deltas):
    bins = defaultdict(int)
    for d in deltas:
        if d <= -20:
            bins["a:<=-20"] += 1
        elif d <= -10:
            bins["b:-19..-10"] += 1
        elif d <= -4:
            bins["c:-9..-4"] += 1
        elif d < 0:
            bins["d:-3..-1"] += 1
        elif d == 0:
            bins["e:0"] += 1
        elif d <= 3:
            bins["f:1..3"] += 1
        elif d <= 9:
            bins["g:4..9"] += 1
        elif d <= 19:
            bins["h:10..19"] += 1
        else:
            bins["i:>=20"] += 1
    return bins


def gate_metrics(rows, gated):
    candidates = [r for r in rows if not is_noop(r)]
    if gated:
        approved = [r for r in candidates if r["twin_verdict"] is True]
        rejected = [r for r in candidates if r["twin_verdict"] is False]
    else:
        approved = [r for r in candidates if r.get("was_applied")]
        rejected = [r for r in candidates if not r.get("was_applied")]
    benefit_total = sum(max(-r["counterfactual_harm_delta"], 0) for r in candidates)
    harm_total = sum(max(r["counterfactual_harm_delta"], 0) for r in candidates)
    benefit_kept = sum(max(-r["counterfactual_harm_delta"], 0) for r in approved)
    harm_blocked = sum(max(r["counterfactual_harm_delta"], 0) for r in rejected)
    return {
        "candidates": len(candidates), "approved": len(approved), "rejected": len(rejected),
        "benefit_total": benefit_total, "benefit_approved": benefit_kept,
        "benefit_preserved": benefit_kept / benefit_total if benefit_total else None,
        "harm_total": harm_total, "harm_rejected": harm_blocked,
        "harm_prevented": harm_blocked / harm_total if harm_total else None,
        "beneficial_candidates": sum(1 for r in candidates if r["counterfactual_harm_delta"] < 0),
        "beneficial_approved": sum(1 for r in approved if r["counterfactual_harm_delta"] < 0),
        "harmful_candidates": sum(1 for r in candidates if r["counterfactual_harm_delta"] > 0),
        "harmful_rejected": sum(1 for r in rejected if r["counterfactual_harm_delta"] > 0),
        "net_approved_delta": sum(r["counterfactual_harm_delta"] for r in approved),
    }


def per_seed_sum(rows, seeds, predicate):
    totals = {seed: 0 for seed in seeds}
    for r in rows:
        if not is_noop(r) and predicate(r):
            totals[r["seed"]] += r["counterfactual_harm_delta"]
    return [totals[s] for s in seeds]


def detectability(mean, sd, n=10):
    if mean == 0:
        return {"seeds_for_80pct_power": None, "approx_power_at_n": 0.05}
    if sd == 0:
        return {"seeds_for_80pct_power": 2, "approx_power_at_n": 1.0}
    z = abs(mean) * math.sqrt(n) / sd - st.NormalDist().inv_cdf(0.975)
    return {"seeds_for_80pct_power": math.ceil((Z * sd / abs(mean)) ** 2),
            "approx_power_at_n": st.NormalDist().cdf(z)}


def arithmetic(data, agent):
    seeds = [int(s) for s in data["seeds"]]
    arms = data["agents"][agent]
    twin_rows = arms["twin@1.0"]["records"]
    reject_rows = arms["always_reject"]["records"]
    observed = [a - b for a, b in zip(arms["twin@1.0"]["per_seed"], arms["always_reject"]["per_seed"])]
    predicted = per_seed_sum(twin_rows, seeds, lambda r: r["twin_verdict"] is True)
    predicted_benefit_only = [-v for v in per_seed_sum(
        twin_rows, seeds, lambda r: r["twin_verdict"] is True and r["counterfactual_harm_delta"] < 0)]
    leaked_harm = per_seed_sum(twin_rows, seeds, lambda r: r["twin_verdict"] is True and r["counterfactual_harm_delta"] > 0)
    ceiling_twin_path = per_seed_sum(twin_rows, seeds, lambda r: r["counterfactual_harm_delta"] < 0)
    ceiling_reject_path = per_seed_sum(reject_rows, seeds, lambda r: r["counterfactual_harm_delta"] < 0)
    obs_mean, obs_sd = st.mean(observed), st.stdev(observed)
    pred_mean = st.mean(predicted)
    pred_sd = st.stdev(predicted)
    ceil_mean = st.mean(ceiling_reject_path)
    ceil_sd = st.stdev(ceiling_reject_path)
    return {
        "seeds": seeds, "observed_diff": observed, "observed_mean": obs_mean, "observed_sd": obs_sd,
        "predicted_diff": predicted, "predicted_mean": pred_mean, "predicted_sd": pred_sd,
        "approved_benefit_per_seed": predicted_benefit_only, "approved_harm_per_seed": leaked_harm,
        "ceiling_all_benefit_on_twin_path": ceiling_twin_path,
        "ceiling_all_benefit_on_reject_path": ceiling_reject_path,
        "ceiling_reject_path_mean": ceil_mean,
        "detect_predicted_with_observed_sd": detectability(pred_mean, obs_sd),
        "detect_predicted_with_predicted_sd": detectability(pred_mean, pred_sd),
        "detect_ceiling_with_its_sd": detectability(ceil_mean, ceil_sd),
        "detect_ceiling_with_observed_sd": detectability(ceil_mean, obs_sd),
        "pearson_predicted_observed": pearson(predicted, observed),
    }


def pearson(a, b):
    if st.pstdev(a) == 0 or st.pstdev(b) == 0:
        return None
    return st.correlation(a, b)


def analyze_ablation(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {"source": str(path), "seeds": data["seeds"], "gateway_faultable": data["gateway_faultable"],
              "agents": {}}
    for agent, arms in data["agents"].items():
        all_rows = [r for arm in arms.values() for r in arm["records"]]
        agent_out = {"composition_all_arms": composition(all_rows), "arms": {}}
        for name, arm in arms.items():
            gated = name != "ungated"
            agent_out["arms"][name] = {"composition": composition(arm["records"]),
                                       "gate": gate_metrics(arm["records"], gated),
                                       "gate_first_attempt": gate_metrics(
                                           [r for r in arm["records"] if not r.get("retry_index")], gated)}
        agent_out["arithmetic"] = arithmetic(data, agent)
        result["agents"][agent] = agent_out
    all_rows = [r for arms in data["agents"].values() for arm in arms.values() for r in arm["records"]]
    result["composition_all_agents"] = composition(all_rows)
    return result


def analyze_recorded():
    rows = []
    for path in sorted((ROOT / "results").glob("*/proposals.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    out = {"files": sorted({p.parent.name for p in (ROOT / "results").glob("*/proposals.jsonl")}),
           "composition_all": composition(rows), "by_agent": {}, "gates": {}}
    by_agent = defaultdict(list)
    by_arm = defaultdict(list)
    for r in rows:
        by_agent[r.get("agent_type")].append(r)
        by_arm[(r.get("arm"), r.get("agent_type"), r.get("fidelity"))].append(r)
    for agent, subset in by_agent.items():
        out["by_agent"][agent] = composition(subset)
    for (arm, agent, fidelity), subset in sorted(by_arm.items(), key=lambda kv: str(kv[0])):
        gated = subset[0].get("twin_verdict") is not None
        out["gates"][f"{arm}|{agent}|{fidelity}"] = gate_metrics(subset, gated)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablation", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = {"ablations": {}, "recorded_on_disk_older_simulator": analyze_recorded()}
    for item in args.ablation:
        label, path = item.split("=", 1)
        output["ablations"][label] = analyze_ablation(path)
    Path(args.output).write_text(json.dumps(output, indent=1), encoding="utf-8")
    fmt = lambda v: "n/e" if v is None else f"{v:.3f}"
    for label, abl in output["ablations"].items():
        print(f"\n################ {label}")
        c = abl["composition_all_agents"]
        print("ALL AGENTS proposals", c["proposals"], "noop", c["noop_proposals"], "candidates", c["candidates"])
        for agent, a in abl["agents"].items():
            ca = a["composition_all_arms"]
            print(f"\n== {agent}: proposals {ca['proposals']} noop {ca['noop_proposals']} candidates {ca['candidates']}")
            for key, g in ca["by_group"].items():
                print(f"   {key:28s} n={g['n']:4d} ben={g['beneficial']:4d} neu={g['neutral']:4d} harm={g['harmful']:4d} "
                      f"(>3: {g['harmful_above_threshold_3']}) availB={g['available_benefit']} availH={g['available_harm']} "
                      f"Bq={g['benefit_quantiles']} Hq={g['harm_quantiles']} hist={g['histogram']}")
            for name, arm in a["arms"].items():
                g = arm["gate"]
                f1 = arm["gate_first_attempt"]
                print(f"   GATE {name:15s} cand={g['candidates']:3d} appr={g['approved']:3d} rej={g['rejected']:3d} "
                      f"B={g['benefit_approved']}/{g['benefit_total']} BP={fmt(g['benefit_preserved'])} "
                      f"H={g['harm_rejected']}/{g['harm_total']} HP={fmt(g['harm_prevented'])} "
                      f"benN={g['beneficial_approved']}/{g['beneficial_candidates']} harmN={g['harmful_rejected']}/{g['harmful_candidates']} "
                      f"netApprovedD={g['net_approved_delta']} | first-attempt BP={fmt(f1['benefit_preserved'])} HP={fmt(f1['harm_prevented'])} "
                      f"B={f1['benefit_approved']}/{f1['benefit_total']} H={f1['harm_rejected']}/{f1['harm_total']}")
            ar = a["arithmetic"]
            print("   ARITH", json.dumps({k: v for k, v in ar.items()}))
    rec = output["recorded_on_disk_older_simulator"]
    print("\n################ recorded on disk (older simulator)")
    c = rec["composition_all"]
    print("proposals", c["proposals"], "noop", c["noop_proposals"], "candidates", c["candidates"])
    for agent, comp in rec["by_agent"].items():
        print(f"== {agent}: proposals {comp['proposals']} noop {comp['noop_proposals']} candidates {comp['candidates']}")
        for key, g in comp["by_group"].items():
            print(f"   {key:28s} n={g['n']:4d} ben={g['beneficial']:4d} neu={g['neutral']:4d} harm={g['harmful']:4d} "
                  f"availB={g['available_benefit']} availH={g['available_harm']}")
    for key, g in rec["gates"].items():
        print(f"   GATE {key:22s} cand={g['candidates']:3d} B={g['benefit_approved']}/{g['benefit_total']} BP={fmt(g['benefit_preserved'])} "
              f"H={g['harm_rejected']}/{g['harm_total']} HP={fmt(g['harm_prevented'])}")


if __name__ == "__main__":
    main()
