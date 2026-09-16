"""Small reproducible sensitivity audit. Only temporary copies are mutated.

Run with the repository's Python environment. No additional packages or network.
An assertion failure kills a mutant; import/collection errors do not count.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("missing_latency_zero", "src/twinloop/sim/engine.py",
     "metrics.service_p95[sid] = None", "metrics.service_p95[sid] = 0.0", 1,
     "tests/test_sim_boundaries.py::test_no_completions_are_missing_not_zero_latency"),
    ("migration_route_omitted", "src/twinloop/sim/engine.py",
     "self.state.routes[service.id] = path", "pass  # mutant: leave old route", 1,
     "tests/test_recovery_validity.py::test_exact_migration_probe_and_live_placement_tools"),
    ("overlap_restores_health_early", "src/twinloop/faults/injector.py",
     "        compose_faults(state)",
     '        compose_faults(state)\n        for event in self.schedule.events:\n'
     '            if event.type == "node_crash" and tick == event.start_tick + event.duration:\n'
     '                state.nodes[event.target].status = "healthy"', 1,
     "tests/test_recovery_validity.py::test_exact_overlapping_crashes_union"),
    ("action_losses_omitted", "src/twinloop/sim/service.py",
     "self.pending_request_losses += lost", "self.pending_request_losses += 0", 1,
     "tests/test_sim_boundaries.py::test_pending_losses_snapshot_restore_count_exactly_once"),
    ("downtime_one_tick_short", "src/twinloop/actions/executor.py",
     "remaining=downtime,", "remaining=max(1, downtime - 1),", 2,
     "tests/test_recovery_validity.py::test_exact_processing_downtime"),
    ("tool_exhaustion_mislabeled_hold", "src/twinloop/agent/llm_agent.py",
     '                    outcome = "tool_budget_exhausted"',
     '                    action = NoOp()\n                    outcome = "deliberate_no_op"', 1,
     "tests/test_llm_finalization.py::test_matched_budget_outcomes"),
]


def digest_tree():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "tests") for p in (ROOT / folder).rglob("*.py")}


def run_tests(directory, names, label):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(directory / "src")
    xml = directory / f"{label}.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-W", "error", *names,
         "--tb=short", f"--junitxml={xml}"], cwd=directory, env=env,
        capture_output=True, text=True, timeout=90,
    )
    failed, errors = [], []
    if xml.exists():
        root = ET.parse(xml).getroot()
        for case in root.iter("testcase"):
            if case.find("failure") is not None:
                failure = case.find("failure")
                failed.append({"test": case.attrib["name"], "message": failure.attrib.get("message", "")})
            if case.find("error") is not None:
                errors.append(case.attrib["name"])
    return result.returncode, failed, errors, result.stdout + result.stderr


def main():
    before = digest_tree()
    results = []
    with tempfile.TemporaryDirectory(prefix="twinloop-mutation-audit-") as temp:
        workspace = Path(temp)
        for folder in ("src", "tests"):
            shutil.copytree(ROOT / folder, workspace / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "pyproject.toml", workspace / "pyproject.toml")
        selected = [case[-1] for case in CASES]
        code, _, errors, output = run_tests(workspace, selected, "control")
        if code != 0 or errors:
            raise RuntimeError(f"Unmodified control failed:\n{output}")
        for name, relative, original, replacement, count, test in CASES:
            target = workspace / relative
            source = target.read_text(encoding="utf-8")
            if source.count(original) != count:
                raise RuntimeError(f"Mutation anchor changed: {name}")
            try:
                target.write_text(source.replace(original, replacement), encoding="utf-8")
                # Avoid same-size edits reusing Python's timestamp-based bytecode.
                for cache in workspace.rglob("__pycache__"):
                    if not cache.resolve().is_relative_to(workspace.resolve()):
                        raise RuntimeError("cache path escaped temporary workspace")
                    shutil.rmtree(cache)
                code, failed, errors, output = run_tests(workspace, [test], name)
                killed = code == 1 and bool(failed) and not errors and all(
                    "assert" in failure["message"].lower() for failure in failed)
                results.append({"mutation": name, "status": "killed" if killed else "survived_or_invalid",
                                "exit_code": code, "failures": failed, "errors": errors})
                print(f"{name}: {results[-1]['status']}", flush=True)
                if not killed:
                    print(output, flush=True)
            finally:
                target.write_text(source, encoding="utf-8")
    unchanged = before == digest_tree()
    report = {"control": "passed", "real_source_and_tests_unchanged": unchanged,
              "mutations": results}
    destination = ROOT / "docs/research/test_mutation_results.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not unchanged or any(result["status"] != "killed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
