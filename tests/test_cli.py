"""End-to-end: run `python -m fleetsim` as a real process, the way a user would."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.transcript import TRANSCRIPT_DIR, parse_transcript

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_cli(stdin_text: str, *args: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "fleetsim", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout.splitlines()


class CliTests(unittest.TestCase):
    def test_spec_example_through_stdin(self):
        steps = parse_transcript(TRANSCRIPT_DIR / "spec" / "base_example.txt")
        stdin_text = "".join(step.command + "\n" for step in steps)
        output = run_cli(stdin_text)
        self.assertEqual(len(output), len(steps), "expected exactly one output line per command")
        for step, line in zip(steps, output):
            if step.expected is not None:
                self.assertEqual(json.loads(line), step.expected, step.command)

    def test_blank_and_comment_lines_produce_no_output(self):
        output = run_cli('\n# a comment\n   \n{"command":"get_state"}\n')
        self.assertEqual(len(output), 1)
        self.assertEqual(json.loads(output[0])["status"], "error")

    def test_bad_json_is_an_error_and_the_stream_keeps_going(self):
        output = run_cli('not json\n{"command":"get_state"}\n')
        self.assertEqual(len(output), 2)
        self.assertTrue(json.loads(output[0])["payload"]["message"].startswith("invalid JSON"))

    def test_reads_commands_from_a_file_argument(self):
        path = TRANSCRIPT_DIR.parent / "data" / "two_commands.jsonl"
        output = run_cli("", str(path))
        self.assertEqual(len(output), 2)
        self.assertEqual(json.loads(output[1])["payload"]["tick"], 1)
