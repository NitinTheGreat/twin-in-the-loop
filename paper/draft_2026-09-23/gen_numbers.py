import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "numbers.tex"
SWEEP = json.loads((ROOT / "docs/research/extended_fidelity_sweep.json").read_text(encoding="utf-8"))
LLM = json.loads((ROOT / "docs/research/llm_descriptive_study.json").read_text(encoding="utf-8"))
COMP = json.loads((ROOT / "docs/research/candidate_composition_stats.json").read_text(encoding="utf-8"))
MM1_SOURCE = (ROOT / "tests/test_sim.py").read_text(encoding="utf-8")

COND = {"i": "discrete_switch", "ii": "queueing_held_constant"}
AGENT = {"Rule": "rule", "Scr": "llm"}
LEVELS = ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]
LEVEL_NAME = {"0.0": "Zero", "0.2": "Two", "0.4": "Four", "0.6": "Six", "0.8": "Eight", "1.0": "Ten"}
macros = {}


def put(name, value):
    if not re.fullmatch(r"[A-Za-z]+", name):
        raise ValueError(name)
    if name in macros:
        raise ValueError(f"duplicate {name}")
    macros[name] = value


def num(x, d=3, sign=False):
    text = f"{x:+.{d}f}" if sign else f"{x:.{d}f}"
    return text.replace("-", "\\ensuremath{-}") if not sign else text.replace("-", "\\ensuremath{-}").replace("+", "\\ensuremath{+}")


def ci(pair, d=3, sign=True):
    return f"[{num(pair[0], d, sign)}, {num(pair[1], d, sign)}]"


def pval(p):
    if p >= 0.001:
        return f"{p:.4f}".rstrip("0") if p < 0.01 else f"{p:.3f}"
    mantissa, exponent = f"{p:.1e}".split("e")
    return f"\\ensuremath{{{mantissa}\\times 10^{{{int(exponent)}}}}}"


def pct(x, d=1):
    return f"{100 * x:.{d}f}\\%"


def intc(n):
    return f"{int(n):,}".replace(",", "{,}")


cond_i = SWEEP["conditions"][COND["i"]]
put("NSeeds", str(SWEEP["n_seeds"]))
put("NFidelity", str(len(SWEEP["fidelities"])))
proto = cond_i["protocol"]
put("EpisodeTicks", str(proto["episode_ticks"]))
put("DecisionInterval", str(proto["decision_interval_ticks"]))
put("RetryCap", str(proto["retry_cap"]))
put("Horizon", str(proto["horizon_ticks"]))
put("GateTau", str(proto["tolerance_margin"]).rstrip("0").rstrip(".") if isinstance(proto["tolerance_margin"], float) else str(proto["tolerance_margin"]))
put("HarmTheta", str(proto["harm_threshold_ticks"]))
put("FaultsPerEpisode", str(proto["fault"]["faults_per_episode"]))
put("FaultStartMin", str(proto["fault"]["min_start_tick"]))
put("FaultStartMax", str(proto["fault"]["max_start_tick"]))
put("BootResamples", intc(proto["bootstrap_resamples"]))
put("FaultDurMin", str(proto["fault"]["min_duration"]))
put("FaultDurMax", str(proto["fault"]["max_duration"]))
put("SwitchThreshold", re.search(r"< ([0-9.]+)", SWEEP["queueing_switch"]["rule"]).group(1))
put("EpisodesPerCondition", intc(cond_i["runtime"]["episodes"]))
put("EpisodesTotal", intc(cond_i["runtime"]["episodes"] + SWEEP["conditions"][COND["ii"]]["runtime"]["episodes"]))
put("RandomGatePRule", f"{proto['random_gate_p']['rule']:.2f}")
put("RandomGatePScr", f"{proto['random_gate_p']['llm']:.3f}")
baseline = sum(cond_i["a0_per_seed"]) / len(cond_i["a0_per_seed"])
put("BaselineTicks", f"{baseline:.1f}")

for ak, agent in AGENT.items():
    for ck, cond in COND.items():
        ag = SWEEP["conditions"][cond]["agents"][agent]
        sbs = SWEEP["side_by_side"][agent][cond]
        C = ck.upper()
        for lv in LEVELS:
            L = LEVEL_NAME[lv]
            i = LEVELS.index(lv)
            put(f"{ak}Net{L}{C}", num(sbs["net_sum_d_curve"][i], sign=True))
            put(f"{ak}NetCI{L}{C}", ci(sbs["net_sum_d_ci95"][i]))
            arm = ag["arms"][f"twin@{lv}"]
            put(f"{ak}BP{L}{C}", num(arm["benefit_preserved"]["point"]))
            put(f"{ak}HP{L}{C}", num(arm["harm_prevented"]["point"]))
            comp = ag["episode_comparisons"][f"twin@{lv} - always_reject"]
            put(f"{ak}Ep{L}{C}", num(comp["mean"], sign=True))
            put(f"{ak}EpCI{L}{C}", ci(comp["t_ci95"]))
            put(f"{ak}EpP{L}{C}", pval(comp["wilcoxon"]["p_value"]))
            put(f"{ak}EpHolm{L}{C}", pval(comp["wilcoxon_holm_p"]))
            put(f"{ak}EpNZ{L}{C}", str(comp["n_nonzero"]))
            put(f"{ak}EpBetter{L}{C}", str(comp["better"]))
            put(f"{ak}EpWorse{L}{C}", str(comp["worse"]))
            put(f"{ak}EpPower{L}{C}", f"{comp['observed_power_at_n']:.2f}")
            put(f"{ak}EpNeed{L}{C}", intc(comp["seeds_for_80pct_power_normal"]))
        put(f"{ak}CrossN{C}", str(len(sbs["observed_crossings"])))
        if sbs["observed_crossings"]:
            put(f"{ak}Cross{C}", num(sbs["observed_crossings"][0]))
            put(f"{ak}CrossCI{C}", ci(sbs["bootstrap_single_crossing_ci95"], sign=False))
            put(f"{ak}CrossSingle{C}", intc(sbs["bootstrap_crossing_counts"]["1"]))
        put(f"{ak}Monotone{C}", "yes" if sbs["monotone_decreasing"] else "no")
        for name, key in (("Ungated", "ungated - always_reject"), ("Random", "random - always_reject"),
                          ("Oracle", "oracle - always_reject"), ("TenVsZero", "twin@1.0 - twin@0.0")):
            comp = ag["episode_comparisons"][key]
            put(f"{ak}{name}Ep{C}", num(comp["mean"], sign=True))
            put(f"{ak}{name}EpCI{C}", ci(comp["t_ci95"]))
            put(f"{ak}{name}EpP{C}", pval(comp["wilcoxon"]["p_value"]))
            put(f"{ak}{name}EpHolm{C}", pval(comp["wilcoxon_holm_p"]))
            put(f"{ak}{name}EpNZ{C}", str(comp["n_nonzero"]))
            put(f"{ak}{name}EpBetter{C}", str(comp["better"]))
            put(f"{ak}{name}EpWorse{C}", str(comp["worse"]))
        ra = ag["arms"]["always_reject"]["composition"]
        put(f"{ak}RejAvailBenefit{C}", intc(ra["available_benefit"]))
        put(f"{ak}RejAvailBenefitPerSeed{C}", f"{ra['available_benefit'] / SWEEP['n_seeds']:.2f}")
        put(f"{ak}RejSeedsBenefit{C}", str(ra["seeds_with_beneficial_candidate"]))
        put(f"{ak}RejBeneficial{C}", intc(ra["beneficial"]))
        put(f"{ak}RejHarmfulAboveThree{C}", intc(ra["harmful_above_3"]))
        put(f"{ak}AnyArmSeedsBenefit{C}", str(ag["seeds_with_beneficial_candidate_any_arm"]))
        put(f"{ak}RejEpMean{C}", f"{ag['arms']['always_reject']['episode_mean_violation_ticks']:.1f}")

for ak, agent in AGENT.items():
    steps = SWEEP["side_by_side"][agent][COND["ii"]]["adjacent_level_changes"]["net_sum_d_approved"]
    step = next(s for s in steps if s["from"] == "twin@0.4" and s["to"] == "twin@0.6")
    put(f"{ak}StepFourSixII", num(step["change"], sign=True))
    put(f"{ak}StepFourSixCIII", ci(step["ci95"]))
    delta = SWEEP["side_by_side"][agent]["per_arm_condition_delta"]["twin@0.4"]["net_sum_d_delta_continuous_minus_discrete"]
    put(f"{ak}SwitchFour", num(delta["mean"], sign=True))
    put(f"{ak}SwitchFourCI", ci(delta["bootstrap_ci95"]))
    put(f"{ak}SwitchFourP", f"{delta['wilcoxon']['p_value']:.2f}")
    delta0 = SWEEP["side_by_side"][agent]["per_arm_condition_delta"]["twin@0.0"]["net_sum_d_delta_continuous_minus_discrete"]
    put(f"{ak}SwitchZero", num(delta0["mean"], sign=True))
    unaffected = [a for a, v in SWEEP["side_by_side"][agent]["per_arm_condition_delta"].items() if v["identical_episodes"]]
    put(f"{ak}IdenticalArms", str(len(unaffected)))

put("HolmFamilySize", str(len(cond_i["agents"]["rule"]["episode_comparisons"])))
rule_ten = SWEEP["conditions"][COND["i"]]["agents"]["rule"]["episode_comparisons"]["twin@1.0 - always_reject"]
rej_mean = SWEEP["conditions"][COND["i"]]["agents"]["rule"]["arms"]["always_reject"]["episode_mean_violation_ticks"]
put("RuleTenPctBaseline", f"{100 * abs(rule_ten['mean']) / rej_mean:.1f}\\%")
put("RuleTenPctSeeds", f"{100 * rule_ten['n_nonzero'] / SWEEP['n_seeds']:.1f}\\%")

families = {}
for agent in AGENT.values():
    for cond in COND.values():
        for key, comp in SWEEP["conditions"][cond]["agents"][agent]["episode_comparisons"].items():
            if key == "always_reject - A0":
                continue
            families[(agent, key, tuple(comp["diffs"]))] = comp["wilcoxon"]["p_value"]
m = len(families)
put("BonfFamily", str(m))
put("BonfRuleTen", pval(min(1.0, rule_ten["wilcoxon"]["p_value"] * m)))
rule_eight = SWEEP["conditions"][COND["i"]]["agents"]["rule"]["episode_comparisons"]["twin@0.8 - always_reject"]
put("BonfRuleEight", pval(min(1.0, rule_eight["wilcoxon"]["p_value"] * m)))
put("BonfAlphaPerTest", pval(0.05 / m))

tt = SWEEP["tau_theta"][COND["i"]]
for ak, agent in AGENT.items():
    corpus = tt[agent]["1.0"]["corpus"]
    put(f"{ak}TTCandidates", intc(corpus["candidates"]))
    put(f"{ak}TTBeneficial", intc(corpus["beneficial"]))
    for cell in tt[agent]["1.0"]["cells"]:
        key = f"{ak}TT{['Zero','One','Two','Three'][cell['tau']]}{['','One','','Three','','Five'][cell['theta']]}"
        put(f"{key}Recall", num(cell["recall"]["point"]))
        put(f"{key}FPR", num(cell["fpr"]["point"]))
        put(f"{key}FPRCI", ci(cell["fpr"]["ci95"], sign=False))
        put(f"{key}FP", intc(cell["false_positives"]["total"]))
        put(f"{key}FPDef", intc(cell["false_positives"]["definitional"]))
        put(f"{key}FPPred", intc(cell["false_positives"]["predictive"]))
        put(f"{key}DefShare", pct(cell["false_positives"]["definitional_share"]))
        put(f"{key}Neg", intc(cell["counts"]["negatives"]))
        put(f"{key}Pos", intc(cell["counts"]["positives"]))
        put(f"{key}Net", num(cell["net_sum_d_approved"]["mean_per_seed"], sign=True))
        put(f"{key}NetCI", ci(cell["net_sum_d_approved"]["bootstrap_ci95"]))

pub = COMP["ablations"]["published"]["agents"]["llm"]["arithmetic"]
idx = next(i for i, v in enumerate(pub["predicted_diff"]) if v != 0 and pub["observed_diff"][i] > 0)
put("SignSeed", str(pub["seeds"][idx]))
put("SignPredicted", num(pub["predicted_diff"][idx], 0, sign=True))
put("SignObserved", num(pub["observed_diff"][idx], 0, sign=True))
put("SignAblationSeeds", str(len(pub["seeds"])))
for name, key in (("Immune", "exposed_immune"), ("Faultable", "exposed_faultable")):
    ar = COMP["ablations"][key]["agents"]["rule"]["arithmetic"]
    j = next(i for i, v in enumerate(ar["observed_diff"]) if v != 0)
    put(f"Mig{name}Seed", str(ar["seeds"][j]))
    put(f"Mig{name}Predicted", num(ar["predicted_diff"][j], 0, sign=True))
    put(f"Mig{name}Observed", num(ar["observed_diff"][j], 0, sign=True))
    put(f"Mig{name}Ratio", f"{ar['observed_diff'][j] / ar['predicted_diff'][j]:.1f}")

prov = LLM["provenance"]
put("LLMSeeds", str(prov["n_seeds"]))
put("LLMModel", prov["model"])
put("LLMTemperature", f"{prov['temperature']:.1f}")
put("LLMReactSteps", str(prov["protocol"]["react_max_steps"]))
put("LLMReactTimeout", f"{prov['protocol']['react_timeout_seconds']:.0f}")
for name, key in (("Ungated", "ungated - always_reject"), ("TwinTen", "twin@1.0 - always_reject"),
                  ("TwinSix", "twin@0.6 - always_reject"), ("TenVsSix", "twin@1.0 - twin@0.6")):
    c = LLM["episode_comparisons"][key]
    put(f"LLM{name}Mean", num(c["mean"], sign=True))
    put(f"LLM{name}CI", ci(c["t_ci95"]))
    put(f"LLM{name}P", pval(c["wilcoxon"]["p_value"]))
    put(f"LLM{name}Holm", pval(c["wilcoxon_holm_p"]))
    put(f"LLM{name}NZ", str(c["n_nonzero"]))
    put(f"LLM{name}Better", str(c["better"]))
    put(f"LLM{name}Worse", str(c["worse"]))
    put(f"LLM{name}Power", f"{c['observed_power_at_n']:.2f}")
    put(f"LLM{name}Need", str(c["seeds_for_80pct_power_normal"]))
LLM_ARM = {"ungated": "Ungated", "always_reject": "Reject", "twin@1.0": "TwinTen", "twin@0.6": "TwinSix"}
OUTCOME = {"decision_produced": "Produced", "deliberate_no_op": "NoOp", "final_parse_failure": "Parse",
           "tool_budget_exhausted": "ToolBudget", "validation_exhausted": "Validation", "timeout": "Timeout"}
for arm, an in LLM_ARM.items():
    a = LLM["arms"][arm]
    fa = a["outcomes"]["per_decision_point"]
    for outcome, on in OUTCOME.items():
        put(f"LLM{an}First{on}", pct(fa["counts"][outcome] / fa["total"]))
    put(f"LLM{an}FirstN", str(fa["total"]))
    comp = a["composition"]
    put(f"LLM{an}AvailBenefit", intc(comp["available_benefit"]))
    put(f"LLM{an}AvailBenefitPerSeed", f"{comp['available_benefit'] / prov['n_seeds']:.2f}")
    put(f"LLM{an}SeedsBenefit", str(comp["seeds_with_beneficial_candidate"]))
    put(f"LLM{an}Candidates", intc(a["candidates"]))
    mix = a["action_mix"]
    for t, tn in (("scale_service", "Scale"), ("restart_service", "Restart"), ("migrate_service", "Migrate")):
        put(f"LLM{an}Mix{tn}", f"{mix['percent_of_produced'][t]:.1f}\\%")
firsts = [LLM["arms"][a]["outcomes"]["per_decision_point"] for a in LLM_ARM]
parse = [f["counts"]["final_parse_failure"] / f["total"] for f in firsts]
put("LLMParseMin", pct(min(parse)))
put("LLMParseMax", pct(max(parse)))
produced = [f["counts"]["decision_produced"] / f["total"] for f in firsts]
put("LLMProducedMin", pct(min(produced)))
put("LLMProducedMax", pct(max(produced)))
usage = LLM["usage"]["total"]
put("LLMBilledCalls", intc(usage["billed"]["calls"]))
put("LLMCachedCalls", intc(usage["served_from_cache"]["calls"]))
put("LLMRateCardCost", f"{usage['metered_token_cost_usd']:.2f}")
ref = LLM["sweep_reference_action_mix"]
for ak, agent in AGENT.items():
    r = ref[agent]["ungated"]
    put(f"{ak}UngatedMixScale", f"{r['percent'].get('scale_service', 0.0):.1f}\\%")
    put(f"{ak}UngatedCandidates", intc(r["candidates"]))

mm1 = {k: float(re.search(rf"^\s*{k}\s*=\s*([0-9.]+)", MM1_SOURCE, re.M).group(1)) for k in ("capacity", "demand", "arrival")}
rho = mm1["arrival"] / (mm1["capacity"] / mm1["demand"])
put("MMRho", f"{rho:.1f}")
put("MMLq", f"{rho ** 2 / (1 - rho):.2f}")
put("MMTolerance", f"{100 * float(re.search(r'/ expected < ([0-9.]+)', MM1_SOURCE).group(1)):.0f}\\%")
put("MMMeasureTicks", intc(int(re.search(r"measure = ([0-9]+)", MM1_SOURCE).group(1))))

lines = ["\\newcommand{\\" + k + "}{" + v + "\\xspace}" if False else "\\newcommand{\\" + k + "}{" + v + "}" for k, v in macros.items()]
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{len(macros)} macros written to {OUT}")
