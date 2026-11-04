"""Random stress test: many random command sequences, checking rules that must always hold.

Each scenario builds a random world and sends a few hundred random commands,
mostly sensible ones plus some deliberately bad ones. After every command it
checks:

  1. Every command gets exactly one well-formed response.
  2. A rejected command changes nothing.
  3. Only tick changes the committed state; reads change nothing at all.
  4. Cargo is conserved: on docks + on ships + delivered = everything added.
  5. No dock is ever planned past its throughput (recomputed independently here).
  6. Cargo only changes on ships that are docked.
  7. A successful load/unload/swap needs the ship docked (committed and
     planned) and moves exactly the units it names.
  8. Ship states are sane (no overdue arrivals, no empty cargo entries, ...).
  9. History matches: get_state_at(t) is exactly what tick t returned.

Failures report the seed, so a scenario can be replayed on its own:

    FLEETSIM_SEED=1234 python -m unittest tests.test_invariants

More scenarios: FLEETSIM_SCENARIOS=2000 python -m unittest tests.test_invariants
"""

from __future__ import annotations

import json
import os
import random
import unittest
from collections import Counter

from fleetsim.model import DOCKED, TRANSIT
from fleetsim.protocol import process_line
from fleetsim.simulation import Simulation

SCENARIOS = int(os.environ.get("FLEETSIM_SCENARIOS", "100"))
STEPS = 200
FIXED_SEED = os.environ.get("FLEETSIM_SEED")


# ---- random commands ----

def cmd(command: str, **params) -> dict:
    return {"command": command, "parameters": params}


def random_manifest(rng: random.Random, n: int) -> list[dict]:
    entries = []
    for _ in range(rng.randint(0, 3)):
        location = rng.randrange(n)
        destination = rng.choice([d for d in range(n) if d != location])
        entries.append({"location": location, "destination": destination, "quantity": rng.randint(1, 3)})
    return entries


def random_init(rng: random.Random) -> dict:
    n = rng.randint(2, 4)
    return cmd(
        "init",
        travel_times=[[0 if i == j else rng.randint(1, 4) for j in range(n)] for i in range(n)],
        dock_throughput=[rng.randint(1, 3) for _ in range(n)],
        vessels=[{"id": f"v{k}", "location": rng.randrange(n)} for k in range(rng.randint(1, 4))],
        cargo_manifest=random_manifest(rng, n),
    )


def random_bad_command(rng: random.Random, sim: Simulation) -> dict | str:
    n = sim.num_locations
    vessel_id = rng.choice(list(sim.committed.vessels))
    return rng.choice([
        "not json",
        '{"command": 42}',
        cmd("teleport", vessel_id=vessel_id),
        cmd("dock", vessel_id="nope"),
        cmd("set_destination", vessel_id=vessel_id, destination=n),
        cmd("set_destination", vessel_id=vessel_id, destination=True),
        cmd("load", vessel_id=vessel_id, destination=-1),
        cmd("tick", cargo_manifest=[{"location": 0, "destination": 0, "quantity": 1}]),
        cmd("tick", cargo_manifest=[{"location": 0, "destination": 1, "quantity": 0}]),
        cmd("get_state_at", tick=sim.committed.tick + 1),
        cmd("init", travel_times=[[0]], dock_throughput=[1], vessels=[]),
    ])


def random_command(rng: random.Random, sim: Simulation) -> dict | str:
    roll = rng.random()
    if roll < 0.08:
        return random_bad_command(rng, sim)
    if roll < 0.28:
        if rng.random() < 0.3:
            return cmd("tick", cargo_manifest=random_manifest(rng, sim.num_locations))
        return cmd("tick")
    if roll < 0.33:
        return cmd("get_state")
    if roll < 0.38:
        return cmd("get_state_at", tick=rng.randint(0, sim.committed.tick))

    n = sim.num_locations
    vessel_id = rng.choice(list(sim.committed.vessels))
    vessel = sim.planned.vessels[vessel_id]
    here = vessel.location if vessel.location is not None else 0
    on_dock = list(sim.planned.docks.get(here, {})) or [rng.randrange(n)]
    on_ship = list(vessel.cargo) or [rng.randrange(n)]
    return rng.choice([
        cmd("set_destination", vessel_id=vessel_id, destination=rng.randrange(n)),
        cmd("dock", vessel_id=vessel_id),
        cmd("undock", vessel_id=vessel_id),
        cmd("load", vessel_id=vessel_id, destination=rng.choice(on_dock)),
        cmd("load", vessel_id=vessel_id, destination=rng.choice(on_dock)),
        cmd("unload", vessel_id=vessel_id, destination=rng.choice(on_ship)),
        cmd("unload", vessel_id=vessel_id, destination=rng.choice(on_ship)),
        cmd("swap", vessel_id=vessel_id,
            load_destination=rng.choice(on_dock), unload_destination=rng.choice(on_ship)),
    ])


# ---- independent views of the state ----

def snapshot(sim: Simulation) -> tuple:
    return (
        json.dumps(sim.committed.to_payload()),
        json.dumps(sim.planned.to_payload()),
        len(sim.history),
    )


def total_units(world) -> int:
    on_docks = sum(q for _, _, q in world.cargo_entries())
    on_ships = sum(sum(v.cargo.values()) for v in world.vessels.values())
    return on_docks + on_ships + world.delivered


def planned_ops_at(sim: Simulation, location: int) -> int:
    """Net operations planned at a dock, computed with Counter arithmetic
    (a different route from the engine's own transfer_ops)."""
    total = 0
    for vessel_id, before in sim.committed.vessels.items():
        if before.state == DOCKED and before.location == location:
            b = Counter(before.cargo)
            a = Counter(sim.planned.vessels[vessel_id].cargo)
            total += max(sum((a - b).values()), sum((b - a).values()))
    return total


# ---- the scenario ----

class Scenario:
    def __init__(self, test: unittest.TestCase, seed: int):
        self.test = test
        self.seed = seed
        self.rng = random.Random(seed)
        self.sim = Simulation()
        self.recent: list[str] = []
        self.units_added = 0
        self.tick_payloads: dict[int, dict] = {}

    def fail(self, message: str) -> None:
        recent = "\n    ".join(self.recent[-8:])
        self.test.fail(f"seed {self.seed}: {message}\n  last commands:\n    {recent}")

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.fail(message)

    def send(self, command: dict | str) -> dict:
        line = command if isinstance(command, str) else json.dumps(command)
        self.recent.append(line)
        response = process_line(self.sim, line)
        json.dumps(response)  # must be serializable
        self.check(response.get("status") in ("ok", "error"), f"bad status in {response}")
        return response

    def run(self) -> None:
        init = random_init(self.rng)
        response = self.send(init)
        self.check(response["status"] == "ok", f"random init rejected: {response}")
        self.units_added += sum(e["quantity"] for e in init["parameters"]["cargo_manifest"])
        self.tick_payloads[0] = response["payload"]
        self.check_state()

        for _ in range(STEPS):
            self.step(random_command(self.rng, self.sim))

        for tick, payload in self.tick_payloads.items():
            got = self.send(cmd("get_state_at", tick=tick))
            self.check(got == {"status": "ok", "payload": payload}, f"history differs at tick {tick}")

    def step(self, command: dict | str) -> None:
        before = snapshot(self.sim)
        tick_before = self.sim.committed.tick
        name = command.get("command") if isinstance(command, dict) else None
        if name in ("load", "unload", "swap"):
            vessel_id = command["parameters"]["vessel_id"]
            ship_before = self.sim.planned.vessels[vessel_id]
            cargo_before = Counter(ship_before.cargo)
            states_before = (self.sim.committed.vessels[vessel_id].state, ship_before.state)
        response = self.send(command)
        after = snapshot(self.sim)

        if name in ("load", "unload", "swap") and response["status"] == "ok":
            self.check(states_before == (DOCKED, DOCKED), f"{name} accepted for a ship in {states_before}")
            params = command["parameters"]
            expected = Counter(cargo_before)
            if name == "load":
                expected[params["destination"]] += 1
            elif name == "unload":
                expected[params["destination"]] -= 1
            else:
                expected[params["unload_destination"]] -= 1
                expected[params["load_destination"]] += 1
            got = Counter(self.sim.planned.vessels[vessel_id].cargo)
            self.check(+expected == got, f"{name} moved the wrong cargo: expected {dict(+expected)}, got {dict(got)}")

        if response["status"] == "error":
            self.check(after == before, "a rejected command changed the state")
        elif name in ("get_state", "get_state_at"):
            self.check(after == before, f"{name} changed the state")
            if name == "get_state":
                self.check(response["payload"] == self.sim.planned.to_payload(), "get_state isn't the plan")
            else:
                tick = command["parameters"]["tick"]
                self.check(response["payload"] == self.tick_payloads[tick], f"get_state_at({tick}) differs")
        elif name == "tick":
            self.check(self.sim.committed.tick == tick_before + 1, "tick didn't advance by exactly 1")
            self.check(response["payload"] == self.sim.committed.to_payload(), "tick response isn't committed")
            self.check(self.sim.planned.to_payload() == self.sim.committed.to_payload(), "plan not reset")
            manifest = command["parameters"].get("cargo_manifest") or []
            self.units_added += sum(e["quantity"] for e in manifest)
            self.tick_payloads[self.sim.committed.tick] = response["payload"]
        else:
            self.check(after[0] == before[0], f"{name} changed the committed state")
            self.check(response["payload"] == self.sim.planned.to_payload(), "response isn't the plan")

        self.check_state()

    def check_state(self) -> None:
        sim = self.sim
        committed, planned = sim.committed, sim.planned
        self.check(planned.tick == committed.tick, "planned and committed ticks differ")
        self.check(len(sim.history) == committed.tick + 1, "history length doesn't match the tick")

        for world, name in ((committed, "committed"), (planned, "planned")):
            self.check(total_units(world) == self.units_added, f"cargo not conserved in {name} state")
            for loc, dest, qty in world.cargo_entries():
                self.check(qty > 0, f"{name}: dock entry with quantity {qty}")
                self.check(loc != dest, f"{name}: undelivered cargo sitting at its destination")
            for vessel_id, v in world.vessels.items():
                self.check(all(q > 0 for q in v.cargo.values()), f"{name}: {vessel_id} has an empty entry")
                if v.state == TRANSIT:
                    self.check(v.origin != v.destination, f"{name}: {vessel_id} sailing to where it is")
                    self.check(v.arrives_at_tick > world.tick, f"{name}: {vessel_id} overdue")
                else:
                    self.check(0 <= v.location < sim.num_locations, f"{name}: {vessel_id} bad location")

        for location, limit in enumerate(sim.dock_throughput):
            ops = planned_ops_at(sim, location)
            self.check(ops <= limit, f"dock {location} planned {ops} ops, throughput is {limit}")

        self.check(planned.delivered >= committed.delivered, "deliveries went backwards")
        for vessel_id, before in committed.vessels.items():
            if before.state != DOCKED:
                self.check(
                    planned.vessels[vessel_id].cargo == before.cargo,
                    f"{vessel_id} changed cargo without being docked",
                )


class InvariantTests(unittest.TestCase):
    def test_random_scenarios(self):
        seeds = [int(FIXED_SEED)] if FIXED_SEED else range(SCENARIOS)
        for seed in seeds:
            Scenario(self, seed).run()

    def test_scenarios_do_real_work(self):
        """Guard against a generator that only produces rejected commands."""
        counts = Counter()
        for seed in range(30):
            scenario = Scenario(self, seed)
            original_send = scenario.send

            def counting_send(command, original_send=original_send):
                response = original_send(command)
                if isinstance(command, dict) and response["status"] == "ok":
                    counts[command["command"]] += 1
                return response

            scenario.send = counting_send
            scenario.run()
        for name in ("tick", "set_destination", "dock", "undock", "load", "unload", "swap"):
            self.assertGreater(counts[name], 20, f"too few successful {name} commands: {dict(counts)}")
