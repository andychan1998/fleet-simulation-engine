"""The line protocol: one JSON command in, one JSON response out."""

from __future__ import annotations

import json
from typing import Iterable, TextIO

from fleetsim.params import CommandError
from fleetsim.simulation import Simulation


def ok(payload: dict) -> dict:
    return {"status": "ok", "payload": payload}


def error(message: str) -> dict:
    return {"status": "error", "payload": {"message": message}}


def encode(response: dict) -> str:
    return json.dumps(response, separators=(",", ":"))


def process_line(sim: Simulation, line: str) -> dict:
    """Run one command line against the simulation and return its response."""
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return error(f"invalid JSON: {exc.msg}")
    if not isinstance(request, dict):
        return error("request must be a JSON object")
    command = request.get("command")
    if not isinstance(command, str):
        return error('request must have a "command" string')
    params = request.get("parameters", {})
    if not isinstance(params, dict):
        return error('"parameters" must be a JSON object')
    try:
        return ok(sim.handle(command, params))
    except CommandError as exc:
        return error(str(exc))


def is_command_line(line: str) -> bool:
    """Blank lines and lines starting with '#' are skipped, so scenario files can have comments."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def run(lines: Iterable[str], out: TextIO) -> None:
    """Process commands in order, writing exactly one response line per command."""
    sim = Simulation()
    for line in lines:
        if not is_command_line(line):
            continue
        try:
            response = process_line(sim, line)
        except Exception as exc:  # a bug in the engine: report it and keep the stream going
            response = error(f"internal error: {type(exc).__name__}: {exc}")
        out.write(encode(response) + "\n")
        out.flush()
