import itertools
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.lines
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FIG = Path(__file__).resolve().parent / "figures"
FIG.mkdir(exist_ok=True)
SWEEP = json.loads((ROOT / "docs/research/extended_fidelity_sweep.json").read_text(encoding="utf-8"))
LLM = json.loads((ROOT / "docs/research/llm_descriptive_study.json").read_text(encoding="utf-8"))

OKABE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"]
plt.rcParams.update({"font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8, "xtick.labelsize": 8,
                     "ytick.labelsize": 8, "legend.fontsize": 8, "font.family": "serif",
                     "pdf.fonttype": 42, "axes.linewidth": 0.6, "lines.linewidth": 1.1})
SINGLE = 3.5
DOUBLE = 7.16
FID = np.array(SWEEP["fidelities"])
COND = {"discrete_switch": "switch active", "queueing_held_constant": "queueing held constant"}
AGENTS = {"rule": "Rule", "llm": "Scripted"}
report = {}


def undrawn_tick_labels(fig):
    hidden = set()
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            low, high = sorted(axis.get_view_interval())
            span = (high - low) or 1.0
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                if not (low - 1e-9 * span <= tick.get_loc() <= high + 1e-9 * span):
                    hidden.update({tick.label1, tick.label2})
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                if not tick.label2.get_visible() or not tick.label2On if hasattr(tick, "label2On") else False:
                    hidden.add(tick.label2)
    return hidden


def text_artists(fig):
    hidden = undrawn_tick_labels(fig)
    items = []
    for text in fig.findobj(matplotlib.text.Text):
        if text in hidden or not text.get_visible() or not text.get_text().strip():
            continue
        items.append(text)
    return items


def verify(fig, name):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    fig_box = fig.bbox
    texts = text_artists(fig)
    boxes = [(t, t.get_window_extent(renderer)) for t in texts]
    problems = []
    for t, b in boxes:
        if b.x0 < fig_box.x0 - 0.5 or b.y0 < fig_box.y0 - 0.5 or b.x1 > fig_box.x1 + 0.5 or b.y1 > fig_box.y1 + 0.5:
            problems.append(f"outside figure: {t.get_text()!r}")
        if t.get_fontsize() < 8 - 1e-6:
            problems.append(f"font below 8 pt: {t.get_text()!r} {t.get_fontsize()}")
    for (t1, b1), (t2, b2) in itertools.combinations(boxes, 2):
        if b1.overlaps(b2):
            inter_w = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
            inter_h = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
            if inter_w > 0.5 and inter_h > 0.5:
                problems.append(f"overlap: {t1.get_text()!r} / {t2.get_text()!r}")
    for legend in fig.legends + [ax.get_legend() for ax in fig.axes if ax.get_legend()]:
        lb = legend.get_window_extent(renderer)
        if lb.x0 < fig_box.x0 - 0.5 or lb.x1 > fig_box.x1 + 0.5 or lb.y0 < fig_box.y0 - 0.5 or lb.y1 > fig_box.y1 + 0.5:
            problems.append("legend outside figure")
        for ax in fig.axes:
            ab = ax.get_window_extent(renderer)
            if lb.overlaps(ab) and min(lb.x1, ab.x1) - max(lb.x0, ab.x0) > 0.5 and min(lb.y1, ab.y1) - max(lb.y0, ab.y0) > 0.5:
                problems.append("legend overlaps an axes area")
    for ax in fig.axes:
        labels = [t for t in ax.get_xticklabels() + ax.get_yticklabels() if t.get_visible() and t.get_text()]
        if not ax.get_xlabel() and ax.get_xticklabels() and any(t.get_text() for t in ax.get_xticklabels()) and ax.get_subplotspec().is_last_row():
            problems.append(f"missing x label on {name}")
    if problems:
        raise AssertionError(f"{name}: " + "; ".join(problems))
    report[name] = {"text_artists": len(boxes), "legends": len(fig.legends) + sum(1 for ax in fig.axes if ax.get_legend()),
                    "size_in": [round(float(v), 2) for v in fig.get_size_inches()], "status": "pass"}


def save(fig, name):
    verify(fig, name)
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png", dpi=200)
    plt.close(fig)


def fig_net_sum_d():
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 2.5), layout="constrained", sharex=True)
    styles = {"discrete_switch": dict(ls="-", marker="o"), "queueing_held_constant": dict(ls="--", marker="s", mfc="white")}
    for ax, (agent, label) in zip(axes, AGENTS.items()):
        for k, (cond, clabel) in enumerate(COND.items()):
            sbs = SWEEP["side_by_side"][agent][cond]
            y = np.array(sbs["net_sum_d_curve"])
            lo, hi = np.array(sbs["net_sum_d_ci95"]).T
            ax.fill_between(FID, lo, hi, color=OKABE[k], alpha=0.18, lw=0)
            ax.plot(FID, y, color=OKABE[k], ms=4, label=f"{clabel}", **styles[cond])
        ax.axhline(0, color="black", lw=0.6)
        ax.set_title(f"{label} controller")
        ax.set_xlabel("Twin fidelity $f$")
        ax.set_xticks(FID)
    axes[0].set_ylabel("Net $\\Sigma D$ per seed (violation-ticks)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, [f"Queueing {l}" if l.startswith("switch") else l.capitalize() for l in labels],
               loc="outside lower center", ncol=2, frameon=False)
    save(fig, "fig1_net_sum_d")


def fig_episode():
    fig, axes = plt.subplots(2, 1, figsize=(SINGLE, 4.0), layout="constrained", sharex=True)
    for ax, (agent, label) in zip(axes, AGENTS.items()):
        comps = SWEEP["conditions"]["discrete_switch"]["agents"][agent]["episode_comparisons"]
        rows = [comps[f"twin@{f:.1f} - always_reject"] for f in FID]
        mean = np.array([r["mean"] for r in rows])
        lo = np.array([r["t_ci95"][0] for r in rows])
        hi = np.array([r["t_ci95"][1] for r in rows])
        sig = np.array([r["wilcoxon_holm_p"] < 0.05 for r in rows])
        color = OKABE[0] if agent == "rule" else OKABE[1]
        ax.errorbar(FID, mean, yerr=[mean - lo, hi - mean], color=color, ls="-" if agent == "rule" else "--",
                    capsize=2, lw=1.0, marker=None)
        ax.plot(FID[sig], mean[sig], ls="none", marker="o", ms=5, color=color, label="Holm $p<0.05$")
        ax.plot(FID[~sig], mean[~sig], ls="none", marker="o", ms=5, mfc="white", color=color, label="not significant")
        ax.axhline(0, color="black", lw=0.6)
        ax.set_title(f"{label} controller")
        ax.set_ylabel("Twin $-$ reject-all\n(violation-ticks per episode)")
        ax.set_xticks(FID)
    axes[1].set_xlabel("Twin fidelity $f$")
    proxies = [matplotlib.lines.Line2D([], [], ls="none", marker="o", ms=5, color="black", label="Holm $p<0.05$"),
               matplotlib.lines.Line2D([], [], ls="none", marker="o", ms=5, mfc="white", color="black",
                                       label="not significant")]
    fig.legend(handles=proxies, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "fig2_episode_difference")


def fig_bp_hp():
    fig, axes = plt.subplots(1, 2, figsize=(SINGLE, 2.3), layout="constrained", sharey=True)
    for ax, (metric, title) in zip(axes, (("benefit_preserved", "Benefit preserved"), ("harm_prevented", "Harm prevented"))):
        for k, (agent, label) in enumerate(AGENTS.items()):
            arms = SWEEP["conditions"]["discrete_switch"]["agents"][agent]["arms"]
            vals = [arms[f"twin@{f:.1f}"][metric] for f in FID]
            y = np.array([v["point"] for v in vals])
            lo, hi = np.array([v["ci95"] for v in vals]).T
            ax.fill_between(FID, lo, hi, color=OKABE[k], alpha=0.18, lw=0)
            ax.plot(FID, y, color=OKABE[k], marker="o" if k == 0 else "s", ls="-" if k == 0 else "--",
                    mfc=None if k == 0 else "white", ms=4, label=label)
        ax.set_title(title)
        ax.set_xlabel("Twin fidelity $f$")
        ax.set_xticks([0, 0.5, 1.0])
        ax.set_ylim(0, 1.05)
    axes[0].set_ylabel("Fraction of available ticks")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "fig3_benefit_harm")


def fig_false_positives():
    cells = SWEEP["tau_theta"]["discrete_switch"]["llm"]["1.0"]["cells"]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.5), layout="constrained")
    x = np.arange(len(cells))
    definitional = np.array([c["false_positives"]["definitional"] for c in cells])
    predictive = np.array([c["false_positives"]["predictive"] for c in cells])
    ax.bar(x, predictive, color=OKABE[0], width=0.7, label="Predictive", edgecolor="black", lw=0.4)
    ax.bar(x, definitional, bottom=predictive, color=OKABE[4], width=0.7, hatch="///", label="Definitional",
           edgecolor="black", lw=0.4)
    diag = [i for i, c in enumerate(cells) if c["tau_equals_theta"]]
    ax.set_xticks(x, [f"{c['tau']},{c['theta']}" + ("*" if c["tau_equals_theta"] else "") for c in cells],
                  rotation=90)
    ax.set_xlabel("Cell ($\\tau$, $\\theta$); * marks $\\tau=\\theta$")
    ax.set_ylabel("False positives (count)")
    fig.legend(loc="outside lower center", ncol=2, frameon=False)
    save(fig, "fig4_false_positives")


ACTIONS = [("scale_service", "scale"), ("restart_service", "restart"), ("migrate_service", "migrate"),
           ("throttle_service", "throttle")]


def fig_action_mix():
    ref = LLM["sweep_reference_action_mix"]
    llm_mix = LLM["arms"]["ungated"]["action_mix"]
    bars = [("LLM", {k: llm_mix["percent_of_produced"].get(k, 0.0) for k, _ in ACTIONS}),
            ("Rule", {k: ref["rule"]["ungated"]["percent"].get(k, 0.0) for k, _ in ACTIONS}),
            ("Scripted", {k: ref["llm"]["ungated"]["percent"].get(k, 0.0) for k, _ in ACTIONS})]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.4), layout="constrained")
    hatches = ["", "///", "...", "xx"]
    bottom = np.zeros(len(bars))
    for k, (key, label) in enumerate(ACTIONS):
        vals = np.array([b[1][key] for b in bars])
        ax.bar(np.arange(len(bars)), vals, bottom=bottom, color=OKABE[k], hatch=hatches[k], edgecolor="black",
               lw=0.4, width=0.6, label=label)
        bottom += vals
    ax.set_xticks(np.arange(len(bars)), [b[0] for b in bars])
    ax.set_xlabel("Proposer (ungated arm)")
    ax.set_ylabel("Share of non-no-op proposals (%)")
    ax.set_ylim(0, 100)
    fig.legend(loc="outside right center", frameon=False)
    save(fig, "fig5_action_mix")


OUTCOMES = [("decision_produced", "valid action"), ("deliberate_no_op", "deliberate no-op"),
            ("final_parse_failure", "parse failure"), ("tool_budget_exhausted", "tool budget"),
            ("validation_exhausted", "validation"), ("timeout", "timeout")]


def fig_outcomes():
    arms = [("ungated", "ungated"), ("always_reject", "reject-all"), ("twin@1.0", "twin@1.0"), ("twin@0.6", "twin@0.6")]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.9), layout="constrained")
    bottom = np.zeros(len(arms))
    hatches = ["", "///", "...", "xx", "\\\\", "--"]
    for k, (key, label) in enumerate(OUTCOMES):
        vals = np.array([100 * LLM["arms"][a]["outcomes"]["per_decision_point"]["counts"][key]
                         / LLM["arms"][a]["outcomes"]["per_decision_point"]["total"] for a, _ in arms])
        ax.bar(np.arange(len(arms)), vals, bottom=bottom, color=OKABE[k], hatch=hatches[k], edgecolor="black",
               lw=0.4, width=0.6, label=label)
        bottom += vals
    ax.set_xticks(np.arange(len(arms)), [l for _, l in arms])
    ax.set_xlabel("Arm (live-model study)")
    ax.set_ylabel("First attempts (%)")
    ax.set_ylim(0, 100)
    fig.legend(loc="outside lower center", ncol=2, frameon=False)
    save(fig, "fig6_llm_outcomes")


for build in (fig_net_sum_d, fig_episode, fig_bp_hp, fig_false_positives, fig_action_mix, fig_outcomes):
    build()
(FIG / "verification.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps(report, indent=1))
