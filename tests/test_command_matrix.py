"""Every movement and cargo command, in every ship situation: one table, checked cell by cell.

A situation is the ship's state as of the last tick plus whatever is already
pending. For each (situation, command) the table gives the expected result:

  ok(ship)       accepted; the ship as planned right after the command. The
                 next tick must commit exactly that.
  err(message)   rejected with exactly this message, and nothing changes.

The table is written from the design decisions, not from the code, so it also
serves as a reference for what each command does in each situation.
"""

import json
import unittest

from fleetsim.protocol import process_line
from fleetsim.simulation import Simulation


def cmd(name, **params):
    return {"command": name, "parameters": params}


TICK = cmd("tick")
DOCK = cmd("dock", vessel_id="v1")
UNDOCK = cmd("undock", vessel_id="v1")


def set_destination(port):
    return cmd("set_destination", vessel_id="v1", destination=port)


def load(dest):
    return cmd("load", vessel_id="v1", destination=dest)


def unload(dest):
    return cmd("unload", vessel_id="v1", destination=dest)


def swap(load_dest, unload_dest):
    return cmd("swap", vessel_id="v1", load_destination=load_dest, unload_destination=unload_dest)


# Three ports, 2 ticks apart. Dock throughput is high so it never interferes
# here (tests/transcripts/ covers throughput). Port 0 starts with 5 units for
# port 1 and 5 for port 2.
INIT = cmd(
    "init",
    travel_times=[[0, 2, 2], [2, 0, 2], [2, 2, 0]],
    dock_throughput=[9, 9, 9],
    vessels=[{"id": "v1", "location": 0}],
    cargo_manifest=[
        {"location": 0, "destination": 1, "quantity": 5},
        {"location": 0, "destination": 2, "quantity": 5},
    ],
)

# In every situation the ship is at port 0 carrying one unit for port 1 and
# one for port 2, as of tick 2 (tick 3 for "in transit").
TO_IDLE = [DOCK, TICK, load(1), load(2), UNDOCK, TICK]
TO_DOCKED = [DOCK, TICK, load(1), load(2), TICK]

SITUATIONS = {
    "idle": TO_IDLE,
    "idle, trip pending": TO_IDLE + [set_destination(2)],
    "idle, dock pending": TO_IDLE + [DOCK],
    "docked": TO_DOCKED,
    "docked, undock pending": TO_DOCKED + [UNDOCK],
    "docked, load pending": TO_DOCKED + [load(1)],
    "docked, load + undock pending": TO_DOCKED + [load(1), UNDOCK],
    "in transit": TO_IDLE + [set_destination(1), TICK],  # 0 -> 1, arrives at tick 4
}

COMMANDS = {
    "set_destination here": set_destination(0),
    "set_destination other": set_destination(1),
    "dock": DOCK,
    "undock": UNDOCK,
    "load": load(1),
    "unload": unload(2),
    "swap": swap(1, 2),  # unload the port-2 unit, load a port-1 unit
}


def idle(port, cargo):
    return {"state": "idle", "location": port, "cargo": cargo}


def docked(port, cargo):
    return {"state": "docked", "location": port, "cargo": cargo}


def transit(origin, to, arrives, cargo):
    return {"state": "transit", "from": origin, "to": to, "arrives_at_tick": arrives, "cargo": cargo}


def ok(ship):
    return ("ok", ship)


def err(message):
    return ("error", message)


NOT_IDLE = err("vessel v1 is not idle")
NOT_DOCKED = err("vessel v1 is not docked")
UNDOCKING = err("vessel v1 is undocking")
ALREADY_DOCKED = err("vessel v1 is already docked")

C0 = {1: 1, 2: 1}  # the ship's starting cargo: destination -> quantity
C0_PLUS_LOAD = {1: 2, 2: 1}  # after the pending load in the "load pending" situations

EXPECTED = {
    "idle": {
        "set_destination here": ok(idle(0, C0)),
        "set_destination other": ok(transit(0, 1, 4, C0)),
        "dock": ok(docked(0, C0)),
        "undock": NOT_DOCKED,
        "load": NOT_DOCKED,
        "unload": NOT_DOCKED,
        "swap": NOT_DOCKED,
    },
    "idle, trip pending": {
        "set_destination here": ok(idle(0, C0)),  # cancels the trip
        "set_destination other": ok(transit(0, 1, 4, C0)),  # replaces the trip
        "dock": ok(docked(0, C0)),  # replaces the trip
        "undock": NOT_DOCKED,
        "load": NOT_DOCKED,
        "unload": NOT_DOCKED,
        "swap": NOT_DOCKED,
    },
    "idle, dock pending": {
        "set_destination here": ok(idle(0, C0)),  # cancels the dock
        "set_destination other": ok(transit(0, 1, 4, C0)),  # replaces the dock
        "dock": ok(docked(0, C0)),  # repeat: no change
        "undock": ok(idle(0, C0)),  # cancels the dock
        "load": NOT_DOCKED,  # the dock isn't committed yet
        "unload": NOT_DOCKED,
        "swap": NOT_DOCKED,
    },
    "docked": {
        "set_destination here": NOT_IDLE,
        "set_destination other": NOT_IDLE,
        "dock": ALREADY_DOCKED,
        "undock": ok(idle(0, C0)),
        "load": ok(docked(0, {1: 2, 2: 1})),
        "unload": ok(docked(0, {1: 1})),
        "swap": ok(docked(0, {1: 2})),
    },
    "docked, undock pending": {
        "set_destination here": NOT_IDLE,
        "set_destination other": NOT_IDLE,
        "dock": ok(docked(0, C0)),  # cancels the undock
        "undock": ok(idle(0, C0)),  # repeat: no change
        "load": UNDOCKING,
        "unload": UNDOCKING,
        "swap": UNDOCKING,
    },
    "docked, load pending": {
        "set_destination here": NOT_IDLE,
        "set_destination other": NOT_IDLE,
        "dock": ALREADY_DOCKED,
        "undock": ok(idle(0, C0_PLUS_LOAD)),  # the pending load still happens
        "load": ok(docked(0, {1: 3, 2: 1})),
        "unload": ok(docked(0, {1: 2})),
        "swap": ok(docked(0, {1: 3})),
    },
    "docked, load + undock pending": {
        "set_destination here": NOT_IDLE,
        "set_destination other": NOT_IDLE,
        "dock": ok(docked(0, C0_PLUS_LOAD)),  # cancels the undock; the load stays
        "undock": ok(idle(0, C0_PLUS_LOAD)),  # repeat: no change
        "load": UNDOCKING,
        "unload": UNDOCKING,
        "swap": UNDOCKING,
    },
    "in transit": {
        "set_destination here": NOT_IDLE,
        "set_destination other": NOT_IDLE,
        "dock": NOT_IDLE,
        "undock": NOT_DOCKED,
        "load": NOT_DOCKED,
        "unload": NOT_DOCKED,
        "swap": NOT_DOCKED,
    },
}


def send(sim, command):
    return process_line(sim, json.dumps(command))


def ship(payload):
    """v1 from a state payload, with cargo as {destination: quantity}."""
    vessel = dict(payload["vessels"]["v1"])
    vessel["cargo"] = {c["destination"]: c["quantity"] for c in vessel["cargo"]}
    return vessel


def total_units(world):
    on_ship = sum(sum(v.cargo.values()) for v in world.vessels.values())
    return on_ship + sum(world.dock_cargo.values()) + world.delivered


class CommandMatrixTests(unittest.TestCase):
    def test_table_covers_every_situation_and_command(self):
        self.assertEqual(set(EXPECTED), set(SITUATIONS))
        for situation, row in EXPECTED.items():
            self.assertEqual(set(row), set(COMMANDS), situation)

    def test_every_command_in_every_situation(self):
        for situation, setup in SITUATIONS.items():
            for name, command in COMMANDS.items():
                with self.subTest(situation=situation, command=name):
                    self.check(setup, command, EXPECTED[situation][name])

    def check(self, setup, command, expected):
        sim = Simulation()
        for step in [INIT, *setup]:
            response = send(sim, step)
            self.assertEqual(response["status"], "ok", f"setup step failed: {step} -> {response}")
        committed_before = sim.committed.to_payload()
        planned_before = sim.planned.to_payload()
        units_before = total_units(sim.planned)

        response = send(sim, command)
        kind, value = expected

        if kind == "error":
            self.assertEqual(response, {"status": "error", "payload": {"message": value}})
            self.assertEqual(sim.planned.to_payload(), planned_before, "a rejected command changed the plan")
            self.assertEqual(sim.committed.to_payload(), committed_before)
            return

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(ship(response["payload"]), value)
        self.assertEqual(response["payload"], sim.planned.to_payload(), "response isn't the plan")
        self.assertEqual(sim.committed.to_payload(), committed_before, "only tick may change the committed state")
        self.assertEqual(total_units(sim.planned), units_before, "cargo was created or lost")

        after = send(sim, TICK)
        self.assertEqual(ship(after["payload"]), value, "the tick didn't commit exactly the plan")
