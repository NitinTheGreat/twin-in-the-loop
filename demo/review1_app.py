from __future__ import annotations

import hashlib
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plotly.graph_objects as go
import streamlit as st

from twinloop.config import FaultConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.catalog import FAULT_TYPES
from twinloop.faults.schedule import FaultSchedule, targets_from_topology
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.telemetry.slo import SLOEvaluator
from twinloop.telemetry.summarizer import Summarizer


FAULT_COLORS = {
    "node_cpu_saturation": "#E8963A",
    "node_crash": "#C0392B",
    "link_degradation": "#8E7CC3",
    "link_failure": "#5B3A8E",
    "service_memory_leak": "#3A8EC0",
    "traffic_surge": "#2E9B57",
}

UTIL_SCALE = [[0.0, "#2E9B57"], [0.5, "#E8C33A"], [1.0, "#D5433B"]]


def build_world():
    topology = build_topology(TopologyConfig(), SimConfig())
    slo_config = SLOConfig()
    return topology, slo_config


def build_layout(topology):
    roles = {n.id: n.role for n in topology.nodes}
    positions: dict[str, tuple[float, float]] = {"gw0": (0.0, 0.0)}
    edges = [n.id for n in topology.nodes if n.role == "edge"]
    devices = [n.id for n in topology.nodes if n.role == "device"]
    for i, nid in enumerate(edges):
        angle = 2.0 * math.pi * i / max(len(edges), 1)
        positions[nid] = (1.15 * math.cos(angle), 1.15 * math.sin(angle))
    for j, nid in enumerate(devices):
        angle = 2.0 * math.pi * j / max(len(devices), 1) + math.pi / len(devices)
        positions[nid] = (2.5 * math.cos(angle), 2.5 * math.sin(angle))
    service_count: dict[str, int] = {}
    for service in topology.services:
        service_count[service.host_node_id] = service_count.get(service.host_node_id, 0) + 1
    links = [
        {
            "id": link.id,
            "a": link.endpoints[0],
            "b": link.endpoints[1],
            "base": link.base_latency_ms,
        }
        for link in topology.links
    ]
    return {
        "positions": positions,
        "roles": roles,
        "links": links,
        "service_count": service_count,
    }


def simulate(seed: int, length: int, fault_seed: int):
    topology, slo_config = build_world()
    targets = targets_from_topology(topology)
    schedule = FaultSchedule.generate(fault_seed, FaultConfig(), targets)
    sim = NetworkSim(topology, SimConfig(), seed=seed, schedule=schedule)
    collector = Collector(summarize_topology(topology), slo_config)
    summarizer = Summarizer(slo_config)

    service_ids = [s.id for s in topology.services]
    node_ids = [n.id for n in topology.nodes]

    frames = []
    p95_series: dict[str, list[float]] = {sid: [] for sid in service_ids}
    util_series: dict[str, list[float]] = {nid: [] for nid in node_ids}
    violation_flags: list[bool] = []
    hash_parts: list[str] = []

    for _ in range(length):
        metrics = sim.step()
        observation = collector.observe(metrics)
        text = summarizer.render(observation)
        active = sim._injector.active_events(metrics.tick) if sim._injector else []

        node_status = {nid: node.status for nid, node in sim.state.nodes.items()}
        link_status = {lid: link.status for lid, link in sim.state.links.items()}
        n_violations = sum(1 for s in observation.slo_status.values() if not s.compliant)

        frame = {
            "tick": metrics.tick,
            "node_util": dict(metrics.node_utilisation),
            "node_status": node_status,
            "link_latency": dict(metrics.link_latency),
            "link_status": link_status,
            "p95_ms": {sid: metrics.service_p95[sid] * 1000.0 for sid in service_ids},
            "compliant": {sid: observation.slo_status[sid].compliant for sid in service_ids},
            "n_violations": n_violations,
            "agent_text": text,
            "faults": [
                {"type": e.type, "target": e.target, "magnitude": e.magnitude}
                for e in active
            ],
        }
        frames.append(frame)

        for sid in service_ids:
            p95_series[sid].append(frame["p95_ms"][sid])
        for nid in node_ids:
            util_series[nid].append(metrics.node_utilisation[nid])
        violation_flags.append(n_violations > 0)

        for sid in service_ids:
            hash_parts.append(f"{metrics.tick}:{sid}:{metrics.service_p95[sid]:.6f}")
        for nid in node_ids:
            hash_parts.append(f"{metrics.tick}:{nid}:{metrics.node_utilisation[nid]:.6f}")

    metric_hash = hashlib.sha256("|".join(hash_parts).encode("utf-8")).hexdigest()

    return {
        "frames": frames,
        "events": [
            {
                "type": e.type,
                "target": e.target,
                "start_tick": e.start_tick,
                "duration": e.duration,
                "magnitude": e.magnitude,
            }
            for e in schedule.events
        ],
        "service_ids": service_ids,
        "node_ids": node_ids,
        "edge_ids": [n.id for n in topology.nodes if n.role in ("edge", "gateway")],
        "p95_series": p95_series,
        "util_series": util_series,
        "violation_flags": violation_flags,
        "metric_hash": metric_hash,
        "layout": build_layout(topology),
        "p95_target": slo_config.p95_target_ms,
    }


def fork_trajectories(seed: int, length: int, fault_seed: int, tick: int, horizon: int):
    topology, slo_config = build_world()
    targets = targets_from_topology(topology)
    schedule = FaultSchedule.generate(fault_seed, FaultConfig(), targets)
    sim = NetworkSim(topology, SimConfig(), seed=seed, schedule=schedule)
    for _ in range(tick):
        sim.step()

    hosts = {s.id: s.host_node_id for s in topology.services}
    hosting = []
    for nid in {h for h in hosts.values()}:
        if sim.state.nodes[nid].status == "healthy" and nid not in hosting:
            hosting.append(nid)
    down_node = sorted(hosting)[0] if hosting else "edge0"

    def roll(target):
        evaluator = SLOEvaluator(slo_config)
        ticks: list[int] = []
        series: list[int] = []
        for _ in range(horizon):
            metrics = target.step()
            status = evaluator.evaluate(metrics)
            ticks.append(metrics.tick)
            series.append(sum(1 for s in status.values() if not s.compliant))
        return ticks, series

    base = sim.fork()
    twin = sim.fork()
    mutant = sim.fork()
    mutant.state.nodes[down_node].status = "down"

    tb, parent = roll(base)
    _, identical = roll(twin)
    _, mutated = roll(mutant)

    return {
        "ticks": tb,
        "parent": parent,
        "identical": identical,
        "mutated": mutated,
        "down_node": down_node,
    }


@st.cache_data(show_spinner=False)
def cached_run(seed: int, length: int, fault_seed: int):
    return simulate(seed, length, fault_seed)


@st.cache_data(show_spinner=False)
def cached_determinism(seed: int, length: int, fault_seed: int):
    h1 = simulate(seed, length, fault_seed)["metric_hash"]
    h2 = simulate(seed, length, fault_seed)["metric_hash"]
    h3 = simulate(seed + 1, length, fault_seed)["metric_hash"]
    return h1, h2, h3


@st.cache_data(show_spinner=False)
def cached_fork(seed: int, length: int, fault_seed: int, tick: int, horizon: int):
    return fork_trajectories(seed, length, fault_seed, tick, horizon)


def latency_color(ratio: float) -> str:
    t = max(0.0, min(1.0, (ratio - 1.0) / 3.0))
    r = int(60 + t * (213 - 60))
    g = int(155 + t * (67 - 155))
    b = int(80 + t * (59 - 80))
    return f"rgb({r},{g},{b})"


def true_ranges(flags):
    ranges = []
    start = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        if not flag and start is not None:
            ranges.append((start, i - 1))
            start = None
    if start is not None:
        ranges.append((start, len(flags) - 1))
    return ranges


def topology_figure(data, frame):
    layout = data["layout"]
    positions = layout["positions"]
    fig = go.Figure()

    for link in layout["links"]:
        xa, ya = positions[link["a"]]
        xb, yb = positions[link["b"]]
        status = frame["link_status"].get(link["id"], "up")
        latency = frame["link_latency"].get(link["id"], link["base"])
        ratio = latency / link["base"] if link["base"] else 1.0
        if status != "up":
            fig.add_trace(
                go.Scatter(
                    x=[xa, xb],
                    y=[ya, yb],
                    mode="lines",
                    line=dict(color="#9AA0A6", width=2, dash="dot"),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=[(xa + xb) / 2.0],
                    y=[(ya + yb) / 2.0],
                    mode="text",
                    text=["✕"],
                    textfont=dict(size=26, color="#C0392B"),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=[xa, xb],
                    y=[ya, yb],
                    mode="lines",
                    line=dict(color=latency_color(ratio), width=2 + 4 * min(ratio - 1.0, 3.0) / 3.0),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )

    up_x, up_y, up_c, up_text = [], [], [], []
    down_x, down_y, down_text = [], [], []
    for nid, (x, y) in positions.items():
        status = frame["node_status"].get(nid, "healthy")
        util = frame["node_util"].get(nid, 0.0)
        svc = layout["service_count"].get(nid, 0)
        tag = f" · {svc} svc" if svc else ""
        if status == "down":
            down_x.append(x)
            down_y.append(y)
            down_text.append(f"<b>{nid}</b>{tag}<br>DOWN")
        else:
            up_x.append(x)
            up_y.append(y)
            up_c.append(util)
            up_text.append(f"<b>{nid}</b>{tag}<br>{util * 100:.0f}%")

    fig.add_trace(
        go.Scatter(
            x=up_x,
            y=up_y,
            mode="markers+text",
            marker=dict(
                size=54,
                color=up_c,
                colorscale=UTIL_SCALE,
                cmin=0.0,
                cmax=1.0,
                line=dict(color="#1F2933", width=2),
                colorbar=dict(title="Node util", thickness=18),
            ),
            text=up_text,
            textposition="middle center",
            textfont=dict(size=14, color="#0B0F14"),
            hoverinfo="text",
            showlegend=False,
        )
    )
    if down_x:
        fig.add_trace(
            go.Scatter(
                x=down_x,
                y=down_y,
                mode="markers+text",
                marker=dict(size=58, color="#2B2B2B", symbol="x", line=dict(color="#C0392B", width=3)),
                text=down_text,
                textposition="middle center",
                textfont=dict(size=14, color="#FFFFFF"),
                hoverinfo="text",
                showlegend=False,
            )
        )

    fig.update_layout(
        height=560,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[-3.1, 3.1]),
        yaxis=dict(visible=False, range=[-3.1, 3.1], scaleanchor="x", scaleratio=1),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        uirevision="topology",
    )
    return fig


def p95_figure(data, tick):
    fig = go.Figure()
    ticks = list(range(len(data["frames"])))
    for start, end in true_ranges(data["violation_flags"]):
        fig.add_vrect(x0=start - 0.5, x1=end + 0.5, fillcolor="#D5433B", opacity=0.10, line_width=0)
    for sid in data["service_ids"]:
        fig.add_trace(
            go.Scatter(x=ticks, y=data["p95_series"][sid], mode="lines", name=sid, line=dict(width=2.5))
        )
    fig.add_hline(
        y=data["p95_target"],
        line=dict(color="#C0392B", width=2.5, dash="dash"),
        annotation_text=f"SLO target {data['p95_target']:.0f}ms",
        annotation_position="top left",
        annotation_font_size=15,
    )
    fig.add_vline(x=tick, line=dict(color="#1F2933", width=2.5))
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="p95 response (ms)",
        xaxis_title="tick",
        legend=dict(orientation="h", y=1.15, font=dict(size=13)),
        font=dict(size=15),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def util_figure(data, tick):
    fig = go.Figure()
    ticks = list(range(len(data["frames"])))
    for nid in data["edge_ids"]:
        fig.add_trace(
            go.Scatter(x=ticks, y=[u * 100 for u in data["util_series"][nid]], mode="lines", name=nid, line=dict(width=2.5))
        )
    fig.add_vline(x=tick, line=dict(color="#1F2933", width=2.5))
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="node utilisation (%)",
        xaxis_title="tick",
        legend=dict(orientation="h", y=1.18, font=dict(size=13)),
        font=dict(size=15),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def timeline_figure(data, tick, length):
    fig = go.Figure()
    events = data["events"]
    labels = [f"{e['type']} → {e['target']}" for e in events]
    for i, event in enumerate(events):
        fig.add_trace(
            go.Bar(
                x=[event["duration"]],
                base=[event["start_tick"]],
                y=[labels[i]],
                orientation="h",
                marker=dict(color=FAULT_COLORS.get(event["type"], "#888888")),
                text=[f"×{event['magnitude']:.1f}"],
                textposition="inside",
                insidetextfont=dict(color="#FFFFFF", size=14),
                hovertext=[f"{labels[i]} · ticks {event['start_tick']}–{event['start_tick'] + event['duration']}"],
                hoverinfo="text",
                showlegend=False,
            )
        )
    fig.add_vline(x=tick, line=dict(color="#1F2933", width=3))
    fig.update_layout(
        height=90 + 60 * max(len(events), 1),
        margin=dict(l=10, r=10, t=20, b=10),
        barmode="overlay",
        xaxis=dict(title="tick", range=[0, length]),
        yaxis=dict(autorange="reversed"),
        font=dict(size=15),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def fork_line(ticks, a, b, name_a, name_b, color_a, color_b, dash_b):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ticks, y=a, mode="lines", name=name_a, line=dict(color=color_a, width=5)))
    fig.add_trace(go.Scatter(x=ticks, y=b, mode="lines", name=name_b, line=dict(color=color_b, width=3, dash=dash_b)))
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="services in SLO violation",
        xaxis_title="tick (fork horizon)",
        legend=dict(orientation="h", y=1.2, font=dict(size=14)),
        font=dict(size=15),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def caption(text):
    st.markdown(f"<p class='cap'>{text}</p>", unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Twin-in-the-Loop · Review 1", layout="wide")
    st.markdown(
        """
        <style>
        html, body, [class*="css"] { font-size: 17px; }
        .cap { font-size: 1.15rem; color: #4A5568; margin-top: -0.3rem; margin-bottom: 0.8rem; }
        h1 { font-size: 2.4rem !important; }
        h2 { font-size: 1.9rem !important; padding-top: 0.4rem; }
        .banner-ok { background: #123524; color: #E6FFF0; padding: 1.1rem 1.4rem; border-radius: 10px; border-left: 8px solid #2E9B57; font-size: 1.4rem; font-weight: 700; }
        .banner-bad { background: #3A1212; color: #FFE6E6; padding: 1.1rem 1.4rem; border-radius: 10px; border-left: 8px solid #C0392B; font-size: 1.4rem; font-weight: 700; }
        .hashbox { font-family: monospace; font-size: 1.05rem; word-break: break-all; padding: 0.6rem 0.8rem; border-radius: 8px; }
        .truthbox { background: #201A0E; color: #FFECCB; padding: 1rem 1.2rem; border-radius: 10px; border-left: 8px solid #E8963A; font-size: 1.2rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Twin-in-the-Loop — Project Review 1")
    st.markdown("**A deterministic edge-network simulator, its hidden faults, and exactly what an agent is allowed to see.** Levels 1–5.")

    with st.sidebar:
        st.header("Controls")
        seed = int(st.number_input("Master seed", value=6, min_value=0, max_value=9999, step=1))
        length = int(st.slider("Episode length (ticks)", min_value=100, max_value=300, value=200, step=10))
        fault_seed = int(st.number_input("Fault schedule seed", value=7, min_value=0, max_value=9999, step=1))
        run = st.button("Run episode", type="primary", width='stretch')

    if "committed" not in st.session_state:
        st.session_state.committed = {"seed": seed, "length": length, "fault_seed": fault_seed}
        st.session_state.tick = 0
        st.session_state.playing = False
    if run:
        st.session_state.committed = {"seed": seed, "length": length, "fault_seed": fault_seed}
        st.session_state.tick = 0
        st.session_state.playing = False

    committed = st.session_state.committed
    data = cached_run(committed["seed"], committed["length"], committed["fault_seed"])
    max_tick = len(data["frames"]) - 1
    if st.session_state.tick > max_tick:
        st.session_state.tick = max_tick

    ctrl = st.columns([1, 1, 6])
    with ctrl[0]:
        if st.button("▶ Play", width='stretch'):
            st.session_state.playing = True
    with ctrl[1]:
        if st.button("⏸ Pause", width='stretch'):
            st.session_state.playing = False
    tick = st.slider("Selected tick", min_value=0, max_value=max_tick, value=st.session_state.tick)
    st.session_state.tick = tick
    frame = data["frames"][tick]

    st.header("1 · Topology")
    caption("The network at this moment: circles are machines shaded green→red by how busy they are, lines are the links between them shaded by latency. A machine marked ✕ has crashed; a dotted line is a failed link.")
    st.plotly_chart(topology_figure(data, frame), width='stretch')

    st.header("2 · Telemetry")
    caption("How each service and machine behaves over the whole episode. The dashed red line is the promised response-time target; pink bands mark stretches where at least one service broke it. The dark vertical line is the tick you are viewing.")
    st.plotly_chart(p95_figure(data, tick), width='stretch')
    st.plotly_chart(util_figure(data, tick), width='stretch')

    st.header("3 · Fault timeline")
    caption("Every fault scripted into this episode, each bar covering the ticks it is active. This is the operator's omniscient view — the ground truth of what is actually wrong — and it is exactly what we hide from the agent.")
    st.markdown("<div class='truthbox'>OPERATOR'S OMNISCIENT VIEW · GROUND TRUTH · the agent never sees this panel</div>", unsafe_allow_html=True)
    st.plotly_chart(timeline_figure(data, tick, committed["length"]), width='stretch')

    st.header("4 · What the agent sees")
    caption("The whole point of the project: side by side, the true fault (left) and the only thing the agent receives (right). The agent must diagnose from symptoms alone — the fault's name and location are never handed to it.")
    agent_text = frame["agent_text"]
    leak_count = sum(agent_text.count(name) for name in FAULT_TYPES)
    if leak_count == 0:
        st.markdown(
            "<div class='banner-ok'>✔ AGENT VIEW CONTAINS NO FAULT LABELS — live scan found "
            f"{leak_count} fault-type names in the text the agent receives.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div class='banner-bad'>✘ LEAK — {leak_count} fault-type names appeared in the agent view.</div>",
            unsafe_allow_html=True,
        )
    st.write("")
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Ground truth (hidden)")
        if frame["faults"]:
            for fault in frame["faults"]:
                st.markdown(
                    f"<div class='truthbox'><b>{fault['type']}</b><br>target <b>{fault['target']}</b> · magnitude ×{fault['magnitude']:.1f}</div>",
                    unsafe_allow_html=True,
                )
                st.write("")
        else:
            st.markdown("<div class='truthbox'>No active fault at this tick.</div>", unsafe_allow_html=True)
    with cols[1]:
        st.subheader("Rendered agent observation")
        st.code(agent_text, language="text")

    st.header("5 · Determinism & forking")
    caption("Why any of this can be trusted: the same seed always replays byte-for-byte, and a forked copy of the world starts out perfectly identical — the mechanism the digital twin will run on. This is a preview of how the twin will work.")

    st.subheader("Same seed → same world")
    h1, h2, h3 = cached_determinism(committed["seed"], committed["length"], committed["fault_seed"])
    dcols = st.columns(2)
    with dcols[0]:
        st.markdown(f"Seed **{committed['seed']}**, run A")
        st.markdown(f"<div class='hashbox' style='background:#12241A;color:#CFF5E1;'>{h1}</div>", unsafe_allow_html=True)
        st.markdown(f"Seed **{committed['seed']}**, run B")
        st.markdown(f"<div class='hashbox' style='background:#12241A;color:#CFF5E1;'>{h2}</div>", unsafe_allow_html=True)
        st.markdown("**Identical.**" if h1 == h2 else "**MISMATCH.**")
    with dcols[1]:
        st.markdown(f"Seed **{committed['seed'] + 1}** (different)")
        st.markdown(f"<div class='hashbox' style='background:#241212;color:#F5CFCF;'>{h3}</div>", unsafe_allow_html=True)
        st.markdown("**Different seed, different world.**" if h3 != h1 else "**Unexpected match.**")

    st.subheader(f"Fork at tick {tick} → identical, then diverge on mutation")
    fork = cached_fork(committed["seed"], committed["length"], committed["fault_seed"], tick, 40)
    fcols = st.columns(2)
    with fcols[0]:
        st.markdown("**Parent vs untouched fork** — perfectly overlaid.")
        st.plotly_chart(
            fork_line(fork["ticks"], fork["parent"], fork["identical"], "parent", "fork (untouched)", "#2E9B57", "#1F77B4", "dash"),
            width='stretch',
        )
    with fcols[1]:
        st.markdown(f"**Parent vs mutated fork** — took **{fork['down_node']}** down in the fork only.")
        st.plotly_chart(
            fork_line(fork["ticks"], fork["parent"], fork["mutated"], "parent (unchanged)", "fork (node down)", "#2E9B57", "#C0392B", "solid"),
            width='stretch',
        )

    if st.session_state.playing and tick < max_tick:
        st.session_state.tick = tick + 1
        time.sleep(0.12)
        st.rerun()
    elif tick >= max_tick:
        st.session_state.playing = False


if __name__ == "__main__":
    main()
