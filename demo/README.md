# Demo — Project Review 1

`review1_app.py` is a Streamlit walkthrough of Levels 1–5: the deterministic network
simulator, the seeded fault injector, the telemetry/SLO layer, and the observation the
agent is allowed to see. It is a **read-only consumer** of `src/twinloop/` — it imports
the library and never modifies it.

## What it shows

Five panels, top to bottom:

1. **Topology** — the network at the selected tick, nodes shaded by utilisation, links by
   latency, crashed nodes and failed links visibly distinct.
2. **Telemetry** — per-service p95 response time against the SLO target, per-node
   utilisation, with SLO-violation stretches shaded and the current tick marked.
3. **Fault timeline** — every scheduled fault as a bar over its active window. This is the
   operator's omniscient ground-truth view.
4. **What the agent sees** — ground truth beside the exact summarizer text the agent
   receives, with a live scan proving no fault labels leak into that text.
5. **Determinism & forking** — same seed reproduces an identical metric hash; a fork starts
   identical to its parent and diverges only when mutated. A preview of the digital twin.

## Run it

From the repository root:

```bash
pip install -r requirements.txt
streamlit run demo/review1_app.py
```

Streamlit opens the app in your browser (default <http://localhost:8501>).

Runs fully offline — no API keys, no network access required.

## Using it during the review

- Set **master seed**, **episode length**, and **fault schedule seed** in the sidebar, then
  click **Run episode**. The entire episode is precomputed and cached up front, so nothing
  is simulated on interaction.
- Scrub the **Selected tick** slider or press **▶ Play** to animate. Every panel updates to
  that tick; scrubbing backwards to revisit a moment is instant and never desyncs.
- Changing any sidebar value and clicking **Run episode** recomputes a fresh cached episode.
