from __future__ import annotations

import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots


HEALTHY = "#2e9e5b"
WARN = "#e8a33d"
BAD = "#d64545"
GATEWAY = "#5a6b8c"
DEVICE = "#9aa7bd"
MUTED = "#c8d0de"


def _positions(view):
    positions = {"gw0": (0.0, 0.0)}
    edges = [nid for nid, role in view.nodes if role == "edge"]
    devices = [nid for nid, role in view.nodes if role == "device"]
    for i, edge in enumerate(edges):
        angle = 2 * math.pi * i / max(1, len(edges)) - math.pi / 2
        positions[edge] = (1.9 * math.cos(angle), 1.9 * math.sin(angle))
    for j, device in enumerate(devices):
        angle = 2 * math.pi * j / max(1, len(devices)) - math.pi / 2
        positions[device] = (3.6 * math.cos(angle), 3.6 * math.sin(angle))
    return positions


def _service_positions(view, snapshot, positions):
    grouped = {}
    for sid, _ in view.services:
        host = snapshot.service_host.get(sid, "gw0")
        grouped.setdefault(host, []).append(sid)
    result = {}
    for host, sids in grouped.items():
        hx, hy = positions.get(host, (0.0, 0.0))
        norm = math.hypot(hx, hy) or 1.0
        ux, uy = hx / norm, hy / norm
        px, py = -uy, ux
        for k, sid in enumerate(sorted(sids)):
            offset = (k - (len(sids) - 1) / 2) * 0.32
            result[sid] = (hx + 0.55 * ux + offset * px, hy + 0.55 * uy + offset * py)
    return result


def network_figure(view, tick_index):
    snapshot = view.ticks[tick_index]
    positions = _positions(view)
    fig = go.Figure()

    for lid, a, b in view.links:
        xa, ya = positions.get(a, (0, 0))
        xb, yb = positions.get(b, (0, 0))
        down = snapshot.link_status.get(lid, "up") != "up"
        latency = snapshot.link_latency.get(lid, 5.0)
        elevated = latency > 8.0
        color = BAD if down else (WARN if elevated else MUTED)
        fig.add_trace(
            go.Scatter(
                x=[xa, xb],
                y=[ya, yb],
                mode="lines",
                line=dict(color=color, width=3 if (down or elevated) else 1, dash="dot" if down else "solid"),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    node_x, node_y, node_color, node_text, node_border, node_size, node_symbol = ([] for _ in range(7))
    for nid, role in view.nodes:
        x, y = positions[nid]
        node_x.append(x)
        node_y.append(y)
        status = snapshot.node_status.get(nid, "healthy")
        util = snapshot.node_util.get(nid, 0.0)
        if role == "gateway":
            node_color.append(GATEWAY)
            node_symbol.append("square")
            node_size.append(26)
        elif role == "edge":
            node_color.append(util)
            node_symbol.append("circle")
            node_size.append(34)
        else:
            node_color.append(0.0)
            node_symbol.append("circle")
            node_size.append(12)
        node_border.append(BAD if status == "down" else (WARN if status == "degraded" else "#ffffff"))
        node_text.append(f"{nid} ({role})<br>util {util * 100:.0f}%<br>status {status}")

    edge_mask = [role == "edge" for _, role in view.nodes]
    fig.add_trace(
        go.Scatter(
            x=[x for x, m in zip(node_x, edge_mask) if m],
            y=[y for y, m in zip(node_y, edge_mask) if m],
            mode="markers+text",
            marker=dict(
                size=[s for s, m in zip(node_size, edge_mask) if m],
                color=[c for c, m in zip(node_color, edge_mask) if m],
                colorscale="YlOrRd",
                cmin=0.0,
                cmax=1.0,
                line=dict(color=[c for c, m in zip(node_border, edge_mask) if m], width=3),
                colorbar=dict(title="edge<br>util", x=1.02, len=0.5),
            ),
            text=[nid for (nid, role) in view.nodes if role == "edge"],
            textposition="middle center",
            textfont=dict(size=9, color="#20242b"),
            hovertext=[t for t, m in zip(node_text, edge_mask) if m],
            hoverinfo="text",
            showlegend=False,
        )
    )
    for want_role, color, size, symbol in [("gateway", GATEWAY, 26, "square"), ("device", DEVICE, 12, "circle")]:
        mask = [role == want_role for _, role in view.nodes]
        fig.add_trace(
            go.Scatter(
                x=[x for x, m in zip(node_x, mask) if m],
                y=[y for y, m in zip(node_y, mask) if m],
                mode="markers",
                marker=dict(size=size, color=color, symbol=symbol, line=dict(color=[c for c, m in zip(node_border, mask) if m], width=2)),
                hovertext=[t for t, m in zip(node_text, mask) if m],
                hoverinfo="text",
                showlegend=False,
            )
        )

    svc_pos = _service_positions(view, snapshot, positions)
    sx, sy, scolor, stext = [], [], [], []
    for sid, _ in view.services:
        x, y = svc_pos.get(sid, (0, 0))
        sx.append(x)
        sy.append(y)
        compliant = snapshot.service_compliant.get(sid, True)
        scolor.append(HEALTHY if compliant else BAD)
        stext.append(
            f"{sid} on {snapshot.service_host.get(sid)}<br>p95 {snapshot.service_p95_ms.get(sid, 0):.0f} ms"
            f"<br>drop {snapshot.service_drop.get(sid, 0) * 100:.0f}%<br>{'within SLO' if compliant else 'SLO VIOLATION'}"
        )
    fig.add_trace(
        go.Scatter(
            x=sx,
            y=sy,
            mode="markers+text",
            marker=dict(size=16, color=scolor, symbol="diamond", line=dict(color="#ffffff", width=1)),
            text=[sid for sid, _ in view.services],
            textposition="top center",
            textfont=dict(size=8, color="#3a3f47"),
            hovertext=stext,
            hoverinfo="text",
            showlegend=False,
        )
    )

    fault_note = ""
    if snapshot.active_faults:
        fault_note = " | GROUND TRUTH faults (hidden from agent): " + ", ".join(
            f"{f['type']} on {f['target']}" for f in snapshot.active_faults
        )
    fig.update_layout(
        title=f"Network at tick {snapshot.tick} — {snapshot.violations_now} service(s) in SLO violation{fault_note}",
        title_font_size=13,
        xaxis=dict(visible=False, range=[-4.4, 4.4]),
        yaxis=dict(visible=False, range=[-4.4, 4.4], scaleanchor="x", scaleratio=1),
        height=560,
        margin=dict(l=10, r=10, t=50, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _fault_spans(view):
    spans = []
    start = None
    for snap in view.ticks:
        active = bool(snap.active_faults)
        if active and start is None:
            start = snap.tick
        if not active and start is not None:
            spans.append((start, snap.tick))
            start = None
    if start is not None:
        spans.append((start, view.ticks[-1].tick + 1))
    return spans


def metrics_figure(view, current_tick=None):
    ticks = [t.tick for t in view.ticks]
    mean_p95 = [
        (sum(t.service_p95_ms.values()) / len(t.service_p95_ms)) if t.service_p95_ms else 0.0
        for t in view.ticks
    ]
    edge_ids = [nid for nid, role in view.nodes if role == "edge"]
    mean_util = [
        (sum(t.node_util[e] for e in edge_ids) / len(edge_ids)) * 100 if edge_ids else 0.0
        for t in view.ticks
    ]
    cumulative = [t.cumulative_violation_ticks for t in view.ticks]

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=("Mean p95 latency (ms)", "Mean edge utilisation (%)", "Cumulative SLO violation-ticks"),
    )
    fig.add_trace(go.Scatter(x=ticks, y=mean_p95, line=dict(color="#3b7dd8"), name="p95"), row=1, col=1)
    fig.add_trace(go.Scatter(x=ticks, y=mean_util, line=dict(color="#e8a33d"), name="util"), row=2, col=1)
    fig.add_trace(go.Scatter(x=ticks, y=cumulative, line=dict(color="#d64545"), name="violations"), row=3, col=1)

    for start, end in _fault_spans(view):
        for row in (1, 2, 3):
            fig.add_vrect(x0=start, x1=end, fillcolor="#d64545", opacity=0.08, line_width=0, row=row, col=1)

    for decision in view.decisions:
        fig.add_vline(x=decision.tick, line=dict(color="#9aa7bd", width=1, dash="dot"), row=1, col=1)

    if current_tick is not None:
        for row in (1, 2, 3):
            fig.add_vline(x=current_tick, line=dict(color="#20242b", width=2), row=row, col=1)

    fig.update_layout(
        height=520,
        showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
        title="Shaded red = a fault is active (ground truth). Dotted lines = agent decision points.",
        title_font_size=12,
    )
    return fig


def twin_trajectory_figure(proposal):
    horizon = list(range(len(proposal.twin_action_p95)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=horizon, y=proposal.twin_noop_p95, name="if we do nothing", line=dict(color="#5a6b8c")))
    fig.add_trace(go.Scatter(x=horizon, y=proposal.twin_action_p95, name="if we take this action", line=dict(color="#3b7dd8")))
    verdict = "APPROVED" if proposal.approved else "REJECTED"
    fig.update_layout(
        title=f"Twin's forecast over {len(horizon)} ticks — {verdict}",
        title_font_size=12,
        xaxis_title="ticks ahead",
        yaxis_title="predicted mean p95 (ms)",
        height=280,
        margin=dict(l=10, r=10, t=40, b=30),
        legend=dict(orientation="h", y=-0.3),
    )
    return fig


def comparison_figure(rows):
    labels = [r["arm"] for r in rows]
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Total SLO violation-ticks (lower is better)", "Harmful proposals vs blocked by twin"),
    )
    fig.add_trace(go.Bar(x=labels, y=[r["violation_ticks"] for r in rows], marker_color="#3b7dd8", name="violation-ticks"), row=1, col=1)
    fig.add_trace(go.Bar(x=labels, y=[r["harmful"] for r in rows], marker_color="#d64545", name="harmful"), row=1, col=2)
    fig.add_trace(go.Bar(x=labels, y=[r["blocked"] for r in rows], marker_color="#2e9e5b", name="blocked by twin"), row=1, col=2)
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=50, b=80), barmode="group", legend=dict(orientation="h", y=-0.25))
    fig.update_xaxes(tickangle=-30)
    return fig
