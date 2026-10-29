"""World state: the simulation clock and the ships.

The engine keeps two of these (see simulation.py): `committed`, the world as of
the last tick, and `planned`, the committed world plus every command accepted
since then.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

IDLE = "idle"
TRANSIT = "transit"
DOCKED = "docked"


@dataclass
class Vessel:
    """One ship. Which fields matter depends on its state:

    idle, docked: `location`
    transit:      `origin`, `destination`, `arrives_at_tick`
    """

    state: str
    location: int | None = None
    origin: int | None = None
    destination: int | None = None
    arrives_at_tick: int | None = None

    @classmethod
    def idle_at(cls, location: int) -> Vessel:
        return cls(state=IDLE, location=location)

    def make_idle(self, location: int) -> None:
        self._set(IDLE, location=location)

    def make_transit(self, origin: int, destination: int, arrives_at_tick: int) -> None:
        self._set(TRANSIT, origin=origin, destination=destination, arrives_at_tick=arrives_at_tick)

    def _set(self, state: str, location=None, origin=None, destination=None, arrives_at_tick=None) -> None:
        self.state = state
        self.location = location
        self.origin = origin
        self.destination = destination
        self.arrives_at_tick = arrives_at_tick

    def to_payload(self) -> dict:
        if self.state == TRANSIT:
            return {
                "state": TRANSIT,
                "from": self.origin,
                "to": self.destination,
                "arrives_at_tick": self.arrives_at_tick,
            }
        return {"state": self.state, "location": self.location}


@dataclass
class World:
    tick: int
    vessels: dict[str, Vessel]  # kept in init order, which is also the output order

    def clone(self) -> World:
        return copy.deepcopy(self)

    def to_payload(self) -> dict:
        return {
            "tick": self.tick,
            "vessels": {vessel_id: v.to_payload() for vessel_id, v in self.vessels.items()},
        }
