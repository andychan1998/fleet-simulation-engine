"""Measure the engine's speed and memory use at different world sizes.

    python tools/benchmark.py                 # both, at the standard sizes (one to two minutes)
    python tools/benchmark.py speed           # speed table only
    python tools/benchmark.py speed --ports 50 --ships 1000
    python tools/benchmark.py memory --ports 50 --ships 1000 --ticks 1000

Only the engine is timed: each command goes through the same code the program
uses (parse the JSON line, run the command, encode the response), with no
reading or writing of files. Each measurement runs in its own Python process,
so one doesn't affect the next. Worlds are random but fixed (seeded), so runs
are comparable.

Speed is the typical (median) time for:
  change  an accepted set_destination or dock, which copies the planned state
  read    get_state and get_state_at, which only build the response
  tick    advancing the clock (copies the plan, commits it, starts a fresh plan)

Memory is how much the process grows over many ticks, which is almost all
history: one full snapshot of the world per tick.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fleetsim.protocol import encode, process_line  # noqa: E402
from fleetsim.simulation import Simulation  # noqa: E402

SPEED_SIZES = [(5, 10), (10, 100), (50, 1000), (100, 5000)]
MEMORY_SIZES = [(10, 100, 2000), (50, 1000, 500)]


# ---- measuring, inside a child process ----

def peak_memory_mb() -> float:
    import resource  # not available on Windows
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KB on Linux


class World:
    """A random world of the given size, driven through process_line."""

    def __init__(self, ports: int, ships: int, seed: int = 0):
        self.ports = ports
        self.rng = random.Random(seed)
        self.sim = Simulation()
        self.ids = [f"v{k}" for k in range(ships)]
        self.send({"command": "init", "parameters": {
            "travel_times": [[0 if i == j else self.rng.randint(1, 5) for j in range(ports)] for i in range(ports)],
            "dock_throughput": [5] * ports,
            "vessels": [{"id": vid, "location": self.rng.randrange(ports)} for vid in self.ids],
            "cargo_manifest": self.manifest(5 * ports, 20),
        }})

    def manifest(self, entries: int, max_quantity: int) -> list[dict]:
        out = []
        for _ in range(entries):
            location = self.rng.randrange(self.ports)
            destination = self.rng.randrange(self.ports - 1)
            destination += destination >= location  # never the cargo's own location
            out.append({"location": location, "destination": destination,
                        "quantity": self.rng.randint(1, max_quantity)})
        return out

    def send(self, command: dict) -> float:
        """Run one command; return how long the engine took, in seconds."""
        line = json.dumps(command)
        start = time.perf_counter()
        encode(process_line(self.sim, line))
        return time.perf_counter() - start

    def idle_ships(self, count: int) -> list[str]:
        idle = [vid for vid in self.ids if self.sim.committed.vessels[vid].state == "idle"]
        return self.rng.sample(idle, min(count, len(idle)))

    def move(self, vessel_id: str) -> float:
        """An accepted change for an idle ship: sail somewhere or dock."""
        if self.rng.random() < 0.5:
            command = {"command": "set_destination",
                       "parameters": {"vessel_id": vessel_id, "destination": self.rng.randrange(self.ports)}}
        else:
            command = {"command": "dock", "parameters": {"vessel_id": vessel_id}}
        return self.send(command)

    def tick(self, new_cargo_entries: int) -> float:
        return self.send({"command": "tick", "parameters": {"cargo_manifest": self.manifest(new_cargo_entries, 5)}})


def measure_speed(ports: int, ships: int, rounds: int) -> dict:
    world = World(ports, ships)
    change, read, tick = [], [], []
    for _ in range(rounds):
        for vessel_id in world.idle_ships(50):
            change.append(world.move(vessel_id))
        for _ in range(5):
            read.append(world.send({"command": "get_state", "parameters": {}}))
            past = world.rng.randint(0, world.sim.committed.tick)
            read.append(world.send({"command": "get_state_at", "parameters": {"tick": past}}))
        tick.append(world.tick(max(1, ports // 2)))
    return {"change": statistics.median(change), "read": statistics.median(read), "tick": statistics.median(tick)}


def measure_memory(ports: int, ships: int, ticks: int) -> dict:
    world = World(ports, ships)
    start_mb = peak_memory_mb()
    for _ in range(ticks):
        for vessel_id in world.idle_ships(5):
            world.move(vessel_id)
        world.tick(max(1, ports // 10))
    return {"start_mb": start_mb, "end_mb": peak_memory_mb()}


# ---- running each measurement in its own process, and reporting ----

def child(*args) -> dict:
    result = subprocess.run(
        [sys.executable, __file__, "_child", *map(str, args)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def ms(seconds: float) -> str:
    return f"{1000 * seconds:,.2f} ms"


def report_speed(sizes: list[tuple[int, int]], rounds: int) -> None:
    print("Speed: typical time per command (engine only)")
    print(f"  {'World':<26}{'change':>12}{'read':>12}{'tick':>12}{'changes/s':>12}")
    for ports, ships in sizes:
        r = child("speed", ports, ships, rounds)
        world = f"{ports:,} ports, {ships:,} ships"
        print(f"  {world:<26}{ms(r['change']):>12}{ms(r['read']):>12}{ms(r['tick']):>12}{1 / r['change']:>12,.0f}")
    print("  change: accepted set_destination or dock; read: get_state and get_state_at")


def report_memory(sizes: list[tuple[int, int, int]]) -> None:
    if sys.platform == "win32":
        print("Memory: not measured on Windows (Python can't report a process's memory there without extra packages)")
        return
    print("Memory: growth over many ticks (almost all of it history)")
    for ports, ships, ticks in sizes:
        start = time.perf_counter()
        r = child("memory", ports, ships, ticks)
        grew = r["end_mb"] - r["start_mb"]
        per_tick_kb = 1024 * grew / ticks
        print(f"  {ports:,} ports, {ships:,} ships, {ticks:,} ticks: +{grew:,.0f} MB, "
              f"{per_tick_kb:,.0f} KB per tick ({per_tick_kb / ships:.2f} KB per ship per tick), "
              f"took {time.perf_counter() - start:.0f} s")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_child":  # one measurement, in its own process
        mode, *numbers = argv[1:]
        measure = {"speed": measure_speed, "memory": measure_memory}[mode]
        print(json.dumps(measure(*map(int, numbers))))
        return 0

    parser = argparse.ArgumentParser(description="Measure the engine's speed and memory use.")
    parser.add_argument("what", nargs="?", choices=["speed", "memory"], help="measure only this (default: both)")
    parser.add_argument("--ports", type=int, help="world size to measure (default: the standard sizes)")
    parser.add_argument("--ships", type=int)
    parser.add_argument("--ticks", type=int, default=1000, help="ticks for the memory measurement (default 1000)")
    parser.add_argument("--rounds", type=int, default=10, help="rounds of commands for the speed measurement")
    args = parser.parse_args(argv)
    if (args.ports is None) != (args.ships is None):
        parser.error("give both --ports and --ships, or neither")
    if args.ports is not None and args.ports < 2:
        parser.error("--ports must be at least 2")

    if args.what in (None, "speed"):
        report_speed([(args.ports, args.ships)] if args.ports else SPEED_SIZES, args.rounds)
    if args.what is None:
        print()
    if args.what in (None, "memory"):
        report_memory([(args.ports, args.ships, args.ticks)] if args.ports else MEMORY_SIZES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
