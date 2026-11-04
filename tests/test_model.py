"""States are read-only, and successive states share what didn't change.

This is what keeps history safe (a past state can't be changed by accident)
and cheap (each tick stores only the ships and ports that changed).
"""

import dataclasses
import json
import unittest

from fleetsim.protocol import process_line
from fleetsim.simulation import Simulation


def send(sim: Simulation, name: str, **params) -> dict:
    response = process_line(sim, json.dumps({"command": name, "parameters": params}))
    assert response["status"] == "ok", response
    return response


class ReadOnlyHistoryTests(unittest.TestCase):
    def setUp(self):
        # Tick 1: ship a docks at port 0. Tick 2: a has loaded one unit there;
        # ship b and port 1 are untouched throughout.
        self.sim = Simulation()
        send(self.sim, "init", travel_times=[[0, 2, 2], [2, 0, 2], [2, 2, 0]], dock_throughput=[5, 5, 5],
             vessels=[{"id": "a", "location": 0}, {"id": "b", "location": 1}],
             cargo_manifest=[{"location": 0, "destination": 1, "quantity": 2},
                             {"location": 1, "destination": 2, "quantity": 2}])
        send(self.sim, "dock", vessel_id="a")
        send(self.sim, "tick")
        send(self.sim, "load", vessel_id="a", destination=1)
        send(self.sim, "tick")

    def test_a_past_state_cannot_be_changed(self):
        world = self.sim.history[2]
        ship = world.vessels["a"]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ship.state = "idle"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            world.tick = 99
        with self.assertRaises(TypeError):
            world.vessels["a"] = ship
        with self.assertRaises(TypeError):
            ship.cargo[1] = 99
        with self.assertRaises(TypeError):
            world.docks[0][1] = 99
        with self.assertRaises(TypeError):
            world.docks[2] = {}

    def test_unchanged_ships_and_ports_are_shared(self):
        before, after = self.sim.history[1], self.sim.history[2]
        self.assertIs(after.vessels["b"], before.vessels["b"])
        self.assertIs(after.docks[1], before.docks[1])
        # what the load changed is new
        self.assertIsNot(after.vessels["a"], before.vessels["a"])
        self.assertIsNot(after.docks[0], before.docks[0])

    def test_the_plan_starts_as_the_committed_state(self):
        # nothing pending yet, so nothing has been copied
        self.assertIs(self.sim.planned, self.sim.committed)
        send(self.sim, "set_destination", vessel_id="b", destination=2)
        self.assertIsNot(self.sim.planned, self.sim.committed)
        self.assertIs(self.sim.planned.vessels["a"], self.sim.committed.vessels["a"])
