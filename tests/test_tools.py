"""Smoke tests for the demo: the scenario runs cleanly and the viewer renders it."""

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

    def test_viewer_renders_the_demo(self):
        output = run("tools/view.py", "--no-color", str(DEMO))
        self.assertIn("── tick 6 (committed)", output)
        self.assertIn("(committed, from history)", output)
        self.assertEqual(output.count("✗"), 2)
