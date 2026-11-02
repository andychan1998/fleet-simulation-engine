"""Large-world test: correctness and speed on a big fleet. Runs only on request:

    FLEETSIM_LARGE=1 python -m unittest tests.test_large_world -v

Change the size with these (defaults in brackets):

    FLEETSIM_LARGE_PORTS [100]    FLEETSIM_LARGE_SHIPS [1000]
    FLEETSIM_LARGE_TICKS [20]     FLEETSIM_LARGE_COMMANDS_PER_TICK [200]
    FLEETSIM_SEED [0]             (the random seed, to replay a failure)

It builds a random world of that size and drives it tick by tick: each tick, a
random batch of ships gets commands (mostly sensible, some deliberately bad),
then the tick runs with new cargo arriving. It checks the same kinds of rules
as the stress test, arranged so checking stays affordable at this size:

  - only tick changes the committed state
  - a rejected command leaves the plan untouched; deliberately bad commands
    and commands to ships in transit are always rejected
  - an accepted command changes only its own ship and the dock it sits at,
    keeps cargo conserved, and (load/unload/swap) keeps the dock within throughput
  - after every tick: cargo is conserved, no ship is overdue, no empty cargo
    entries, the response is the new committed state, one history entry per tick
  - at the end: get_state_at for every tick matches what that tick returned

It prints how long the engine took (time spent checking is not included),
broken down by command type, plus the process's peak memory.
"""

from __future__ import annotations

import copy
import json
import os
import random
import sys
import time
import unittest
from collections import defaultdict

from fleetsim.model import DOCKED, IDLE
from fleetsim.protocol import encode, process_line
from fleetsim.simulation import Simulation
from tests.test_invariants import planned_ops_at, total_units

CARGO_COMMANDS = ("load", "unload", "swap")


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def cmd(name: str, **params) -> dict:
    return {"command": name, "parameters": params}


def peak_memory_mb() -> float | None:
    try:
        import resource
    except ImportError:  # not available on Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KB on Linux


class LargeWorld:
    def __init__(self, test: unittest.TestCase, ports: int, ships: int, ticks: int, per_tick: int, seed: int):
        test.assertGreaterEqual(ports, 2, "the large world needs at least 2 ports")
        self.test = test
        self.ports, self.ships, self.ticks, self.per_tick = ports, ships, ticks, per_tick
        self.seed = seed
        self.rng = random.Random(seed)
        self.sim = Simulation()
        self.units_added = 0
        self.tick_payloads: dict[int, dict] = {}
        self.accepted: dict[str, int] = defaultdict(int)
        self.rejected = 0
        self.engine_time: dict[str, float] = defaultdict(float)
        self.engine_count: dict[str, int] = defaultdict(int)
        self.durations: list[float] = []
        self.slowest = (0.0, "")
        self.check_time = 0.0

    # ---- sending, with timing ----

    def send(self, command: dict) -> dict:
        line = json.dumps(command)
        start = time.perf_counter()
        response = process_line(self.sim, line)
        encode(response)  # turning the response into text is part of the cost
        elapsed = time.perf_counter() - start
        name = command["command"]
        self.engine_time[name] += elapsed
        self.engine_count[name] += 1
        self.durations.append(elapsed)
        if elapsed > self.slowest[0]:
            self.slowest = (elapsed, name)
        if response.get("status") == "ok":
            self.accepted[name] += 1
        else:
            self.rejected += 1
        return response

    def fail_if(self, condition: bool, message: str) -> None:
        if condition:
            self.test.fail(f"seed {self.seed}, tick {self.sim.committed.tick}: {message}")

    # ---- random input ----

    def manifest(self, entries: int, max_quantity: int) -> list[dict]:
        out = []
        for _ in range(entries):
            location = self.rng.randrange(self.ports)
            destination = self.rng.randrange(self.ports - 1)
            if destination >= location:
                destination += 1  # never the cargo's own location
            out.append({"location": location, "destination": destination,
                        "quantity": self.rng.randint(1, max_quantity)})
        return out

    def command_for(self, vessel_id: str) -> tuple[dict, bool]:
        """A command for this ship, and whether it must be rejected."""
        rng, sim = self.rng, self.sim
        if rng.random() < 0.03:  # deliberately bad
            return rng.choice([
                cmd("dock", vessel_id="no-such-ship"),
                cmd("set_destination", vessel_id=vessel_id, destination=self.ports),
                cmd("load", vessel_id=vessel_id, destination=-1),
                cmd("get_state_at", tick=sim.committed.tick + 1),
            ]), True
        committed = sim.committed.vessels[vessel_id]
        planned = sim.planned.vessels[vessel_id]
        if committed.state == IDLE:
            if rng.random() < 0.6:
                return cmd("set_destination", vessel_id=vessel_id, destination=rng.randrange(self.ports)), False
            return cmd("dock", vessel_id=vessel_id), False
        if committed.state == DOCKED:
            if planned.state != DOCKED or rng.random() < 0.2:
                return cmd(rng.choice(["undock", "dock"]), vessel_id=vessel_id), False
            here = committed.location
            on_dock = [d for (loc, d) in sim.planned.dock_cargo if loc == here] or [rng.randrange(self.ports)]
            on_ship = list(planned.cargo) or [rng.randrange(self.ports)]
            return rng.choice([
                cmd("load", vessel_id=vessel_id, destination=rng.choice(on_dock)),
                cmd("load", vessel_id=vessel_id, destination=rng.choice(on_dock)),
                cmd("unload", vessel_id=vessel_id, destination=rng.choice(on_ship)),
                cmd("swap", vessel_id=vessel_id, load_destination=rng.choice(on_dock),
                    unload_destination=rng.choice(on_ship)),
            ]), False
        # in transit: nothing is allowed
        return cmd(rng.choice(["dock", "undock", "load", "set_destination"]), vessel_id=vessel_id,
                   destination=rng.randrange(self.ports)), True

    # ---- the run ----

    def run(self) -> None:
        init = cmd(
            "init",
            travel_times=[[0 if i == j else self.rng.randint(1, 5) for j in range(self.ports)]
                          for i in range(self.ports)],
            dock_throughput=[self.rng.randint(1, 5) for _ in range(self.ports)],
            vessels=[{"id": f"v{k}", "location": self.rng.randrange(self.ports)} for k in range(self.ships)],
            cargo_manifest=self.manifest(entries=5 * self.ports, max_quantity=20),
        )
        response = self.send(init)
        self.fail_if(response["status"] != "ok", f"init rejected: {response}")
        self.units_added += sum(e["quantity"] for e in init["parameters"]["cargo_manifest"])
        self.after_tick(response)

        vessel_ids = [f"v{k}" for k in range(self.ships)]
        for _ in range(self.ticks):
            for vessel_id in self.rng.sample(vessel_ids, min(self.per_tick, self.ships)):
                self.step(*self.command_for(vessel_id))
            manifest = self.manifest(entries=max(1, self.ports // 2), max_quantity=5)
            response = self.send(cmd("tick", cargo_manifest=manifest))
            self.fail_if(response["status"] != "ok", f"tick rejected: {response}")
            self.units_added += sum(e["quantity"] for e in manifest)
            self.after_tick(response)

        for tick, payload in self.tick_payloads.items():
            got = self.send(cmd("get_state_at", tick=tick))
            self.fail_if(got != {"status": "ok", "payload": payload}, f"get_state_at {tick} differs from that tick")

    def step(self, command: dict, must_reject: bool) -> None:
        sim = self.sim
        committed_before, planned_before = sim.committed, sim.planned
        vessel_id = command["parameters"].get("vessel_id")
        ship = planned_before.vessels.get(vessel_id)
        location = sim.committed.vessels[vessel_id].location if ship is not None else None

        response = self.send(command)

        start = time.perf_counter()
        name = command["command"]
        self.fail_if(response.get("status") not in ("ok", "error"), f"bad response {response}")
        self.fail_if(sim.committed is not committed_before, f"{name} changed the committed state")
        if response["status"] == "error":
            self.fail_if(sim.planned is not planned_before, f"rejected {name} replaced the plan")
        else:
            self.fail_if(must_reject, f"{command} should have been rejected")
            after = sim.planned
            self.fail_if(total_units(after) != self.units_added, f"{name} lost or created cargo")
            for other_id, other in after.vessels.items():
                if other_id != vessel_id:
                    self.fail_if(other != planned_before.vessels[other_id], f"{name} {vessel_id} changed {other_id}")
            for key in set(after.dock_cargo) | set(planned_before.dock_cargo):
                if key[0] != location:
                    self.fail_if(after.dock_cargo.get(key) != planned_before.dock_cargo.get(key),
                                 f"{name} {vessel_id} changed the dock at {key[0]}")
            if name in CARGO_COMMANDS:
                ops = planned_ops_at(sim, location)
                limit = sim.dock_throughput[location]
                self.fail_if(ops > limit, f"dock {location} planned {ops} operations, throughput is {limit}")
        self.check_time += time.perf_counter() - start

    def after_tick(self, response: dict) -> None:
        start = time.perf_counter()
        world = self.sim.committed
        self.fail_if(response["payload"] != world.to_payload(), "tick response isn't the committed state")
        self.fail_if(len(self.sim.history) != world.tick + 1, "history length doesn't match the tick")
        self.fail_if(total_units(world) != self.units_added, "cargo not conserved")
        self.fail_if(any(q <= 0 for q in world.dock_cargo.values()), "dock entry with quantity 0")
        for vessel_id, vessel in world.vessels.items():
            self.fail_if(any(q <= 0 for q in vessel.cargo.values()), f"{vessel_id} has an empty cargo entry")
            if vessel.arrives_at_tick is not None:
                self.fail_if(vessel.arrives_at_tick <= world.tick, f"{vessel_id} is overdue")
        self.tick_payloads[world.tick] = copy.deepcopy(response["payload"])
        self.check_time += time.perf_counter() - start

    # ---- report ----

    def report(self) -> str:
        total = sum(self.engine_time.values())
        commands = len(self.durations)
        ordered = sorted(self.durations)
        median = ordered[len(ordered) // 2]
        p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
        by_type = ", ".join(
            f"{name} {1000 * self.engine_time[name] / self.engine_count[name]:.1f}"
            for name in sorted(self.engine_time, key=lambda n: -self.engine_count[n])
        )
        accepted = ", ".join(f"{name} {n:,}" for name, n in sorted(self.accepted.items(), key=lambda x: -x[1]))
        memory = peak_memory_mb()
        lines = [
            f"Large world (seed {self.seed}): {self.ports} ports, {self.ships:,} ships, "
            f"{self.ticks} ticks, {self.per_tick} commands per tick",
            f"  commands     {commands:,} sent, {self.rejected:,} rejected",
            f"  accepted     {accepted}",
            f"  engine time  {total:.2f} s, {commands / total:,.0f} commands/s "
            f"(checking took another {self.check_time:.2f} s, not included)",
            f"  per command  median {1000 * median:.2f} ms, 99th percentile {1000 * p99:.2f} ms, "
            f"slowest {1000 * self.slowest[0]:.2f} ms ({self.slowest[1]})",
            f"  average ms   {by_type}",
        ]
        if memory is not None:
            lines.append(f"  peak memory  {memory:,.0f} MB (whole test process)")
        return "\n".join(lines)


@unittest.skipUnless(os.environ.get("FLEETSIM_LARGE"), "set FLEETSIM_LARGE=1 to run the large-world test")
class LargeWorldTests(unittest.TestCase):
    def test_large_world(self):
        world = LargeWorld(
            self,
            ports=env_int("FLEETSIM_LARGE_PORTS", 100),
            ships=env_int("FLEETSIM_LARGE_SHIPS", 1000),
            ticks=env_int("FLEETSIM_LARGE_TICKS", 20),
            per_tick=env_int("FLEETSIM_LARGE_COMMANDS_PER_TICK", 200),
            seed=env_int("FLEETSIM_SEED", 0),
        )
        world.run()
        print("\n" + world.report(), file=sys.stderr)
