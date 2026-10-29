"""Transcript tests: a scripted conversation with the engine.

File format (one item per line):

    # comment            ignored
    > {json command}     sent to the engine
    < {json response}    expected response to the command just above (optional)
    <~ {json}            partial match: only the keys given are checked

Blank lines are ignored. Responses are compared as parsed JSON, so spacing and
key order don't matter (list order does). With `<~`, objects are matched key by
key and everything else (lists included) must be equal, e.g.

    <~ {"payload": {"vessels": {"v1": {"state": "docked"}}}}

To add a test, drop a new .txt file anywhere under tests/transcripts/.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path

from fleetsim.protocol import process_line
from fleetsim.simulation import Simulation

TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"

# Keeps this module's frames out of failure tracebacks, so a failing transcript
# shows just the mismatch.
__unittest = True


@dataclass
class Step:
    line_no: int
    command: str
    expected: dict | None = None
    expected_line_no: int | None = None
    partial: bool = False


def parse_transcript(path: Path) -> list[Step]:
    steps: list[Step] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            steps.append(Step(line_no, line[1:].strip()))
        elif line.startswith("<"):
            if not steps or steps[-1].expected is not None:
                raise ValueError(f"{path.name}:{line_no}: '<' line without a command before it")
            partial = line.startswith("<~")
            try:
                steps[-1].expected = json.loads(line[2:] if partial else line[1:])
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_no}: bad JSON in expected response: {exc}") from None
            steps[-1].expected_line_no = line_no
            steps[-1].partial = partial
        else:
            raise ValueError(f"{path.name}:{line_no}: line must start with '>', '<' or '#'")
    return steps


def matches(got, expected, partial: bool) -> bool:
    if not partial:
        return got == expected
    if isinstance(expected, dict):
        return isinstance(got, dict) and all(
            key in got and matches(got[key], value, partial) for key, value in expected.items()
        )
    return got == expected


def check_transcript(test: unittest.TestCase, path: Path) -> None:
    sim = Simulation()
    for step in parse_transcript(path):
        got = process_line(sim, step.command)
        if step.expected is not None and not matches(got, step.expected, step.partial):
            kind = "expected (partial)" if step.partial else "expected"
            test.fail(
                f"\n{path.relative_to(TRANSCRIPT_DIR)}:{step.expected_line_no}\n"
                f"  command:  {step.command}\n"
                f"  {kind}: {json.dumps(step.expected)}\n"
                f"  got:      {json.dumps(got)}"
            )


def all_transcripts() -> list[Path]:
    return sorted(TRANSCRIPT_DIR.rglob("*.txt"))
