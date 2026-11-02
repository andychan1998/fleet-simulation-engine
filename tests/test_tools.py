"""Smoke tests for the tools: the mutation test's setup, the demo and its viewer, and the benchmark."""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "examples" / "demo.jsonl"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, encoding="utf-8",
        cwd=REPO_ROOT, env=ENV, check=True,
    )
    return result.stdout


def load_mutation_tool():
    path = REPO_ROOT / "tools" / "mutation_test.py"
    spec = importlib.util.spec_from_file_location("mutation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MutationToolTests(unittest.TestCase):
    def test_changes_are_valid_python_that_differ_from_the_original(self):
        # Running the tool takes minutes, so this only checks its setup, on a
        # sample: each change still compiles and really changes the code.
        tool = load_mutation_tool()
        changes = [c for f in tool.ENGINE_FILES for c in tool.find_changes(REPO_ROOT, f)]
        self.assertGreater(len(changes), 300)
        for change in changes[::10]:
            source = tool.apply_change(REPO_ROOT, change)
            compile(source, change["file"], "exec")
            original = ast.unparse(ast.parse((REPO_ROOT / change["file"]).read_text(encoding="utf-8")))
            self.assertNotEqual(source, original, change)


class DemoTests(unittest.TestCase):
    def test_demo_has_only_its_two_intended_rejections(self):
        responses = [json.loads(line) for line in run("-m", "fleetsim", str(DEMO)).splitlines()]
        errors = [r["payload"]["message"] for r in responses if r["status"] == "error"]
        self.assertEqual(
            errors,
            [
                "vessel bravo is not idle",
                "transfer of 3 operations exceeds dock throughput of 2 at location 0",
            ],
        )

    def test_viewer_reports_a_missing_file_clearly(self):
        result = subprocess.run(
            [sys.executable, "tools/view.py", "no-such-file.jsonl"],
            capture_output=True, text=True, cwd=REPO_ROOT, env=ENV,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot open no-such-file.jsonl", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_viewer_renders_the_demo(self):
        output = run("tools/view.py", "--no-color", str(DEMO))
        self.assertIn("── tick 6 (committed)", output)
        self.assertIn("(committed, from history)", output)
        self.assertEqual(output.count("✗"), 2)


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_runs_on_a_tiny_world(self):
        # The real measurements take a minute or two; this only checks the tool works.
        output = run("tools/benchmark.py", "speed", "--ports", "3", "--ships", "4", "--rounds", "2")
        self.assertIn("3 ports, 4 ships", output)
        self.assertIn("changes/s", output)
        if sys.platform != "win32":
            output = run("tools/benchmark.py", "memory", "--ports", "3", "--ships", "4", "--ticks", "5")
            self.assertIn("3 ports, 4 ships, 5 ticks: +", output)
