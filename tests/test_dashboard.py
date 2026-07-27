from pathlib import Path

from twinloop.dashboard import viz
from twinloop.dashboard.driver import compare_arms, run_instrumented_episode

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_driver_scripted_episode_structure():
    view = run_instrumented_episode(
        agent_kind="llm",
        gate_enabled=True,
        fidelity=1.0,
        provider_name="scripted",
        seed=0,
        episode_ticks=60,
        interval=10,
        horizon=20,
    )
    assert len(view.nodes) == 17
    assert len(view.services) == 6
    assert len(view.ticks) == 60
    assert view.decisions
    for decision in view.decisions:
        for proposal in decision.proposals:
            assert isinstance(proposal.harmful, bool)
            assert proposal.approved in (True, False)
            assert len(proposal.twin_action_p95) == 20
    assert view.harmful_blocked <= view.harmful_proposals


def test_figures_build():
    view = run_instrumented_episode(
        agent_kind="rule", gate_enabled=False, provider_name="scripted", seed=1, episode_ticks=50
    )
    assert viz.network_figure(view, 25).data
    assert viz.metrics_figure(view, current_tick=25).data
    rows = compare_arms(seed=1, episode_ticks=50)
    assert len(rows) == 6
    assert viz.comparison_figure(rows).data


def test_app_runs_without_exception():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(APP, default_timeout=120)
    app.run()
    assert not app.exception
    app.button[0].click().run()
    assert not app.exception
    assert any("Result" in block.value for block in app.markdown)
