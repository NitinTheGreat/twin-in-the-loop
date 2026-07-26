from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langgraph.checkpoint.memory import MemorySaver

from twinloop.agent.graph import GraphRuntime, build_graph
from twinloop.config import ExperimentConfig


def main() -> None:
    config = ExperimentConfig()
    runtime = GraphRuntime(None, None, None, None, None, config)
    graph = build_graph(runtime, config, gate_enabled=True, checkpointer=MemorySaver())

    mermaid = graph.get_graph().draw_mermaid()
    out = Path(__file__).resolve().parents[1] / "docs" / "graph.mmd"
    out.write_text(mermaid, encoding="utf-8")
    print(f"wrote {out}")
    print(mermaid)


if __name__ == "__main__":
    main()
