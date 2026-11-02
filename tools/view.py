#!/usr/bin/env python3
"""A readable view of a scenario run, for demos.

    python tools/view.py examples/demo.jsonl           # print the whole run
    python tools/view.py --step examples/demo.jsonl    # press Enter between commands
    python tools/view.py --full examples/demo.jsonl    # full state after every command

Runs the scenario through the real engine (python -m fleetsim), then pairs
each command with its response. The engine's output is used exactly as is;
this script only formats it. Comment lines in the scenario are shown as
narration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so fleetsim imports without installing

from fleetsim.protocol import is_command_line  # noqa: E402


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


def run_engine(scenario: Path) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "fleetsim", str(scenario)],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


# ---- formatting ----

def fmt_ship_cargo(items: list[dict]) -> str:
    return ", ".join(f"→{c['destination']} ×{c['quantity']}" for c in items) or "none"


def fmt_position(vessel: dict) -> str:
    if vessel["state"] == "transit":
        return f"{vessel['from']} → {vessel['to']}, arrives tick {vessel['arrives_at_tick']}"
    return f"at {vessel['location']}"


def fmt_vessel(vessel_id: str, vessel: dict, id_width: int) -> str:
    return (
        f"  {vessel_id:<{id_width}}  {vessel['state']:<8} {fmt_position(vessel):<28}"
        f" cargo: {fmt_ship_cargo(vessel['cargo'])}"
    )


def fmt_docks(cargo: list[dict]) -> str:
    by_location: dict[int, list[str]] = {}
    for c in cargo:
        by_location.setdefault(c["location"], []).append(f"→{c['destination']} ×{c['quantity']}")
    if not by_location:
        return "  docks: empty"
    parts = [f"{loc}: {', '.join(items)}" for loc, items in sorted(by_location.items())]
    return "  docks: " + "  |  ".join(parts)


def fmt_state(payload: dict, label: str, style: Style) -> list[str]:
    header = f"── tick {payload['tick']} ({label}) "
    lines = [style.bold(header + "─" * max(0, 64 - len(header)))]
    width = max((len(v) for v in payload["vessels"]), default=0)
    lines += [fmt_vessel(vid, v, width) for vid, v in payload["vessels"].items()]
    lines.append(fmt_docks(payload["cargo"]))
    return lines


def fmt_command(command: dict) -> str:
    name = command.get("command", "?")
    p = command.get("parameters") or {}
    vessel = p.get("vessel_id", "")
    if name == "set_destination":
        return f"set_destination {vessel} → {p.get('destination')}"
    if name in ("load", "unload"):
        return f"{name} {vessel} →{p.get('destination')}"
    if name == "swap":
        return f"swap {vessel}: unload →{p.get('unload_destination')}, load →{p.get('load_destination')}"
    if name in ("dock", "undock"):
        return f"{name} {vessel}"
    if name == "get_state_at":
        return f"get_state_at {p.get('tick')}"
    if name in ("tick", "init") and p.get("cargo_manifest"):
        arrivals = ", ".join(
            f"{e['quantity']} at {e['location']} for {e['destination']}" for e in p["cargo_manifest"]
        )
        return f"{name}  (new cargo: {arrivals})"
    return name


def show(command_line: str, response: dict, full: bool, style: Style) -> list[str]:
    try:
        command = json.loads(command_line)
        if not isinstance(command, dict):
            raise ValueError
    except ValueError:
        command = {"command": command_line.strip()}
    lines = [style.cyan("> " + fmt_command(command))]

    if response["status"] == "error":
        return lines + [style.red("  ✗ " + response["payload"]["message"])]

    payload = response["payload"]
    name = command.get("command")
    if name in ("init", "tick"):
        return lines + fmt_state(payload, "committed", style)
    if name == "get_state_at":
        return lines + fmt_state(payload, "committed, from history", style)
    if name == "get_state" or full:
        return lines + fmt_state(payload, "planned", style)

    # other commands: just the ship they changed, as planned
    vessel_id = (command.get("parameters") or {}).get("vessel_id")
    vessel = payload["vessels"].get(vessel_id)
    if vessel is None:
        return lines + [style.green("  ✓")]
    width = len(vessel_id)
    return lines + [style.green("  ✓") + fmt_vessel(vessel_id, vessel, width)[1:] + style.dim("   (planned)")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path, help="scenario file: one JSON command per line, # for comments")
    parser.add_argument("--step", action="store_true", help="wait for Enter before each command")
    parser.add_argument("--full", action="store_true", help="show the full state after every command")
    parser.add_argument("--no-color", action="store_true", help="plain text output")
    args = parser.parse_args(argv)

    style = Style(sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ)
    try:
        lines = args.scenario.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as exc:
        print(f"view.py: cannot open {args.scenario}: {exc.strerror}", file=sys.stderr)
        return 2
    responses = iter(run_engine(args.scenario))
    step = args.step and sys.stdin.isatty()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            print(style.dim(stripped))
            continue
        if not is_command_line(line):
            print()
            continue
        if step:
            input(style.dim("  [Enter]"))
        print("\n".join(show(line, next(responses), args.full, style)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
