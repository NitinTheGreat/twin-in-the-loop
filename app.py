from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from twinloop.dashboard import viz
from twinloop.dashboard.driver import compare_arms, run_instrumented_episode


def _load_dotenv():
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

st.set_page_config(page_title="Twin-in-the-Loop", layout="wide", page_icon="🛰️")

AGENT_HELP = {
    "null": "Does nothing. The floor everything else is measured against.",
    "rule": "A classic threshold-based operator: if a node is overloaded, migrate; if a service leaks memory, restart; and so on.",
    "llm": "A language model reads the symptoms, may call read-only tools, and proposes one repair action.",
}


def _sidebar():
    st.sidebar.title("🛰️ Twin-in-the-Loop")
    st.sidebar.caption("A self-healing edge network, and a digital twin that vets every fix before it is applied.")

    st.sidebar.subheader("1. Who is in charge?")
    agent_label = st.sidebar.radio(
        "Agent",
        ["Do nothing", "Rule-based operator", "AI (LLM) agent"],
        index=2,
        help="The decision maker that watches the network and proposes repairs.",
    )
    agent_kind = {"Do nothing": "null", "Rule-based operator": "rule", "AI (LLM) agent": "llm"}[agent_label]
    st.sidebar.caption(AGENT_HELP[agent_kind])

    st.sidebar.subheader("2. Use the digital twin?")
    gate = st.sidebar.toggle(
        "Validate each action on the twin first",
        value=True,
        help="When ON, every proposed action is simulated on a copy of the network before it is applied. Harmful actions are rejected.",
    )
    fidelity = 1.0
    if gate:
        fidelity = st.sidebar.slider(
            "Twin fidelity",
            0.0,
            1.0,
            1.0,
            0.05,
            help="1.0 = the twin is a perfect copy. Lower = the twin is blurrier and makes mistakes, like a real twin would.",
        )

    provider = "scripted"
    if agent_kind == "llm":
        st.sidebar.subheader("3. Which brain for the AI?")
        provider_label = st.sidebar.radio(
            "LLM provider",
            ["Scripted (offline, instant)", "Gemini (live, needs key)"],
            index=0,
            help="Scripted is a deterministic stand-in so you can explore instantly with no API. Gemini calls a real model.",
        )
        provider = "gemini" if provider_label.startswith("Gemini") else "scripted"
        if provider == "gemini" and not os.environ.get("GEMINI_API_KEY"):
            st.sidebar.warning("GEMINI_API_KEY not found in environment or .env")

    st.sidebar.subheader("4. Scenario")
    seed = st.sidebar.number_input("Seed (chooses the fault sequence)", min_value=0, max_value=9999, value=0, step=1)
    episode_ticks = st.sidebar.slider("Episode length (ticks)", 40, 300, 120, 10)
    interval = st.sidebar.select_slider("Decide every N ticks", options=[5, 10, 15, 20], value=10)

    run = st.sidebar.button("▶ Run episode", type="primary", width="stretch")
    compare = st.sidebar.button("📊 Compare all five arms", width="stretch")
    return dict(
        agent_kind=agent_kind,
        gate=gate,
        fidelity=fidelity,
        provider=provider,
        seed=int(seed),
        episode_ticks=int(episode_ticks),
        interval=int(interval),
        run=run,
        compare=compare,
    )


def _how_it_works():
    st.header("What am I looking at?")
    st.markdown(
        """
This is a small **edge computing network**: one gateway, four edge servers, a dozen IoT devices,
and six services running on the edge servers. Traffic flows in, and the servers must answer fast
enough to meet their **SLOs** (service-level objectives — a latency target and an availability target).

Things break. A **fault injector** silently causes real problems on a schedule:
- a server's CPU gets pinned (**saturation**),
- a server **crashes**,
- a network link **degrades** or **fails**,
- a service springs a **memory leak**,
- or a **traffic surge** hits.

An **agent** watches the telemetry and proposes a fix. Crucially, the agent sees **only the symptoms**
(latency, utilisation, drops) — it is **never told which fault happened**. It has to diagnose, like a real operator.

The catch: **fixes can make things worse.** Migrating a service causes downtime. Restarting drops the queue.
So before applying a fix, we simulate it on a **digital twin** — a separate, deliberately imperfect copy of the
network — and only apply the action if the twin predicts it will not hurt.

**The question this project answers:** how good does the twin have to be for this to pay off? Use the sidebar
to run an episode, then walk through the tabs to watch it happen.
        """
    )
    st.info("Tip: start with the **AI agent + twin at fidelity 1.0**, run it, then drop the fidelity and run again to see the twin start making mistakes.")


def _run_summary(view):
    cols = st.columns(5)
    cols[0].metric("SLO violation-ticks", view.total_violation_ticks, help="Total service-ticks spent breaking SLO. Lower is better.")
    cols[1].metric("Actions proposed", view.proposals)
    cols[2].metric("Truly harmful", view.harmful_proposals, help="Proposals that actually made things worse, by counterfactual ground truth.")
    cols[3].metric("Blocked by twin", view.harmful_blocked, help="Harmful proposals the twin caught and rejected.")
    cols[4].metric("LLM calls", view.llm_calls)


def _network_tab(view):
    st.subheader("The network, tick by tick")
    st.caption(
        "Edge servers are colored by CPU load (yellow→red). Services are diamonds: green = healthy, red = breaking SLO. "
        "A red ring means a node is down; red dotted links are broken. Drag the slider to scrub through time."
    )
    max_tick = len(view.ticks) - 1
    tick = st.slider("Tick", 0, max_tick, min(view.decisions[0].tick if view.decisions else 0, max_tick), key="net_tick")
    st.plotly_chart(viz.network_figure(view, tick), width="stretch")
    snap = view.ticks[tick]
    if snap.active_faults:
        st.warning(
            "Ground truth (hidden from the agent): "
            + ", ".join(f"**{f['type']}** on `{f['target']}`" for f in snap.active_faults)
        )
    else:
        st.success("No fault active at this tick.")


def _metrics_tab(view):
    st.subheader("How the network is doing over time")
    st.caption("Red shading marks when a fault was active. Dotted grey lines are the moments the agent made a decision.")
    st.plotly_chart(viz.metrics_figure(view), width="stretch")


def _decisions_tab(view):
    st.subheader("Every decision the agent made")
    if not view.decisions:
        st.info("No decisions in this episode.")
        return
    labels = [f"tick {d.tick}  →  applied {d.applied_action.get('type')}" for d in view.decisions]
    choice = st.selectbox("Pick a decision to inspect", range(len(view.decisions)), format_func=lambda i: labels[i])
    decision = view.decisions[choice]

    st.markdown(f"### Decision at tick {decision.tick}")
    left, right = st.columns([1, 1])
    with left:
        st.markdown("**What the agent saw** (symptoms only — no fault labels):")
        st.code(decision.observation_text, language="text")
    with right:
        st.markdown("**SLO violation-ticks so far**")
        st.metric("before this decision", decision.slo_before)
        st.metric("shortly after", decision.slo_after)
        st.markdown(f"**Applied action:** `{decision.applied_action}`")
        if decision.exhausted:
            st.error("The agent's proposals were all rejected by the twin; it fell back to doing nothing.")

    st.markdown("---")
    for i, proposal in enumerate(decision.proposals):
        header = f"Proposal {i + 1}: {proposal.action.get('type')}"
        if proposal.approved is True:
            header += "  ✅ approved"
        elif proposal.approved is False:
            header += "  ⛔ rejected by twin"
        with st.expander(header, expanded=(i == 0)):
            if proposal.reasoning:
                st.markdown("**Agent's reasoning:**")
                for thought in proposal.reasoning:
                    st.markdown(f"> {thought}")
            if proposal.tools_called:
                st.markdown(f"**Tools it used to investigate:** {', '.join(proposal.tools_called)}")
            st.markdown(f"**Proposed action:** `{proposal.action}`")

            if proposal.approved is not None:
                st.markdown(f"**Twin's verdict:** {proposal.twin_reason}")
                if proposal.twin_action_p95:
                    st.plotly_chart(viz.twin_trajectory_figure(proposal), width="stretch")

            harm_word = "HARMFUL" if proposal.harmful else "not harmful"
            colour = "🔴" if proposal.harmful else "🟢"
            st.markdown(
                f"**Ground truth (what really would have happened):** {colour} this action was **{harm_word}** "
                f"— it changed SLO violation-ticks by **{proposal.harm_delta:+d}** versus doing nothing "
                f"(action {proposal.cf_action_vt} vs no-op {proposal.cf_noop_vt})."
            )
            if proposal.harmful and proposal.approved is False:
                st.success("The twin correctly caught and blocked this harmful action.")
            elif proposal.harmful and proposal.approved is True:
                st.error("The twin approved a harmful action — a miss (this happens more at low fidelity).")
            elif (not proposal.harmful) and proposal.approved is False:
                st.warning("The twin blocked an action that was actually safe — an over-cautious rejection.")


def _compare_tab():
    st.subheader("The five experiment arms, head to head")
    st.markdown(
        """
- **A0 — Do nothing:** the floor.
- **A1 — Rule operator:** a conventional baseline.
- **A2 — AI agent, no twin:** the risky condition; it acts on every idea.
- **A3 — AI agent + twin:** our system, shown at low fidelity and at perfect fidelity.
- **A4 — Rule operator + twin:** isolates whether the *twin* helps, separate from the *AI*.

The key columns: **harmful** proposals that were actually damaging, and how many the twin **blocked**.
        """
    )
    rows = st.session_state.get("compare_rows")
    if rows is None:
        st.info("Click **Compare all five arms** in the sidebar to run this (offline, a few seconds).")
        return
    st.plotly_chart(viz.comparison_figure(rows), width="stretch")
    st.dataframe(rows, width="stretch", hide_index=True)
    st.caption("Illustrative at a single seed — the real paper averages over 30+ seeds. The pattern to notice: the perfect twin blocks the most harm.")


def main():
    controls = _sidebar()

    if controls["run"]:
        with st.spinner("Simulating the network and the agent's decisions..."):
            view = run_instrumented_episode(
                agent_kind=controls["agent_kind"],
                gate_enabled=controls["gate"],
                fidelity=controls["fidelity"],
                provider_name=controls["provider"],
                seed=controls["seed"],
                episode_ticks=controls["episode_ticks"],
                interval=controls["interval"],
            )
        st.session_state["view"] = view

    if controls["compare"]:
        with st.spinner("Running all five arms offline..."):
            st.session_state["compare_rows"] = compare_arms(
                seed=controls["seed"], episode_ticks=controls["episode_ticks"], interval=controls["interval"]
            )

    view = st.session_state.get("view")
    if view is not None:
        st.markdown(f"## Result — **{view.arm_label}**, seed {view.seed}")
        _run_summary(view)

    tabs = st.tabs(["ℹ️ How it works", "🕸️ Network", "📈 Metrics", "🧠 Decisions", "📊 Compare arms"])
    with tabs[0]:
        _how_it_works()
    with tabs[1]:
        if view is not None:
            _network_tab(view)
        else:
            st.info("Configure a scenario in the sidebar and press **Run episode**.")
    with tabs[2]:
        if view is not None:
            _metrics_tab(view)
        else:
            st.info("Run an episode to see the metrics.")
    with tabs[3]:
        if view is not None:
            _decisions_tab(view)
        else:
            st.info("Run an episode to inspect the agent's decisions.")
    with tabs[4]:
        _compare_tab()


main()
