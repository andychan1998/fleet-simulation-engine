"""End-to-end: run `python -m fleetsim` as a real process, the way a user would."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fleetsim.__main__ import main

from tests.transcript import TRANSCRIPT_DIR, parse_transcript

REPO_ROOT = Path(__file__).resolve().parent.parent

# Output buffering matters to some tests, so they run the program with
# Python's normal buffering even if PYTHONUNBUFFERED is set in this shell.
NORMAL_ENV = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}


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


def run_cli_bytes(stdin_bytes: bytes, **env: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "fleetsim"],
        input=stdin_bytes,
        capture_output=True,
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        check=True,
    )
    return result.stdout.decode("utf-8").splitlines()


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

    def test_closing_the_output_early_is_not_an_error(self):
        # Like `python -m fleetsim big.jsonl | head -1`. The output must be
        # bigger than the pipe's buffer, or the engine finishes before we close.
        big = Path(tempfile.mkdtemp()) / "big.jsonl"
        big.write_text(
            '{"command":"init","parameters":{"travel_times":[[0,1],[1,0]],"dock_throughput":[1,1],'
            '"vessels":[{"id":"v1","location":0}]}}\n' + '{"command":"get_state"}\n' * 5000
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "fleetsim", str(big)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=REPO_ROOT, env=NORMAL_ENV,
        )
        proc.stdout.readline()
        proc.stdout.close()
        _, stderr = proc.communicate(timeout=30)
        self.assertEqual(stderr, "", "should exit quietly")
        self.assertEqual(proc.returncode, 1)

    def test_answers_each_command_immediately(self):
        # Interactive use: each reply must arrive while the input is still open,
        # not only when the program exits.
        proc = subprocess.Popen(
            [sys.executable, "-m", "fleetsim"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=REPO_ROOT, env=NORMAL_ENV,
        )
        try:
            proc.stdin.write('{"command":"get_state"}\n')
            proc.stdin.flush()
            replies = []
            reader = threading.Thread(target=lambda: replies.append(proc.stdout.readline()), daemon=True)
            reader.start()
            reader.join(timeout=5)
            self.assertTrue(replies and replies[0], "no reply while the input was still open")
            self.assertEqual(json.loads(replies[0])["status"], "error")
        finally:
            proc.kill()
            proc.communicate()

    def test_windows_line_endings(self):
        crlf = (
            '{"command":"init","parameters":{"travel_times":[[0,1],[1,0]],"dock_throughput":[1,1],'
            '"vessels":[{"id":"v1","location":0}]}}\r\n{"command":"tick","parameters":{}}\r\n'
        )
        path = Path(tempfile.mkdtemp()) / "crlf.jsonl"
        path.write_bytes(crlf.encode())
        for output in (run_cli("", str(path)), run_cli(crlf)):
            responses = [json.loads(line) for line in output]
            self.assertEqual([r["status"] for r in responses], ["ok", "ok"])
            self.assertEqual(responses[1]["payload"]["tick"], 1)

    def test_byte_order_mark_is_ignored(self):
        # Some Windows editors start a UTF-8 file with an invisible byte-order mark.
        text = (
            '\ufeff{"command":"init","parameters":{"travel_times":[[0,1],[1,0]],"dock_throughput":[1,1],'
            '"vessels":[]}}\n{"command":"tick"}\n'
        )
        path = Path(tempfile.mkdtemp()) / "bom.jsonl"
        path.write_bytes(text.encode("utf-8"))
        for output in (run_cli("", str(path)), run_cli_bytes(text.encode("utf-8"))):
            self.assertEqual([json.loads(line)["status"] for line in output], ["ok", "ok"])

    def test_invalid_bytes_get_an_error_line_not_a_crash(self):
        data = b'\xff\xfe not text\n{"command":"get_state"}\n'
        path = Path(tempfile.mkdtemp()) / "bad.jsonl"
        path.write_bytes(data)
        # PYTHONIOENCODING forces strict decoding, as on a Mac's default settings
        for output in (run_cli("", str(path)), run_cli_bytes(data, PYTHONIOENCODING="utf-8:strict")):
            self.assertEqual(len(output), 2)
            first = json.loads(output[0])["payload"]["message"]
            self.assertTrue(first.startswith("invalid JSON"), first)

    def test_python_version_check(self):
        def import_as(version):
            code = f"import sys; sys.version_info = {version!r}; import fleetsim; print('imported')"
            return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)

        old = import_as((3, 8, 18, "final", 0))
        self.assertEqual(old.returncode, 1)
        self.assertEqual(old.stderr, "fleetsim requires Python 3.9 or newer\n")

        oldest_supported = import_as((3, 9, 0, "final", 0))
        self.assertEqual(oldest_supported.stdout, "imported\n", oldest_supported.stderr)

    def test_too_many_arguments_prints_usage(self):
        result = subprocess.run(
            [sys.executable, "-m", "fleetsim", "a.jsonl", "b.jsonl"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "usage: python -m fleetsim [COMMANDS_FILE]\n")
        self.assertEqual(result.stdout, "")

    def test_missing_file_is_a_clear_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "fleetsim", "no-such-file.jsonl"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot open no-such-file.jsonl", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_reads_commands_from_a_file_argument(self):
        path = TRANSCRIPT_DIR.parent / "data" / "two_commands.jsonl"
        output = run_cli("", str(path))
        self.assertEqual(len(output), 2)
        self.assertEqual(json.loads(output[1])["payload"]["tick"], 1)


class MainFunctionTests(unittest.TestCase):
    """main() called directly, as the entry point does, to check its return codes."""

    def test_success_returns_0(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(TRANSCRIPT_DIR.parent / "data" / "two_commands.jsonl")])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.getvalue().splitlines()), 2)

    def test_too_many_arguments_returns_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["a.jsonl", "b.jsonl"]), 2)
        self.assertIn("usage:", err.getvalue())

    def test_works_with_a_stdin_that_cannot_be_reconfigured(self):
        # A program that calls main() may swap in its own stdin, such as an
        # in-memory one with no reconfigure(). main() should skip that step and
        # still run the commands.
        commands = (TRANSCRIPT_DIR.parent / "data" / "two_commands.jsonl").read_text(encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(commands)), contextlib.redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertEqual(len(out.getvalue().splitlines()), 2)

    def test_missing_file_returns_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["no-such-file.jsonl"]), 2)
        self.assertIn("cannot open", err.getvalue())
