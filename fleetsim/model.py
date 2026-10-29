"""World state: the simulation clock, the ships, and the cargo.

The engine keeps two of these (see simulation.py): `committed`, the world as of
the last tick, and `planned`, the committed world plus every command accepted
since then.

Cargo has no identity: a unit is described only by where it needs to go, so
cargo is stored as counts. A ship holds {destination: quantity}; the docks hold
{(location, destination): quantity}. Python dicts keep insertion order, which
gives the output order "first arrival first".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

IDLE = "idle"
TRANSIT = "transit"
DOCKED = "docked"


@dataclass
class Vessel:
    """One ship. Which position fields matter depends on its state:

    idle, docked: `location`
    transit:      `origin`, `destination`, `arrives_at_tick`
    """

    state: str
    location: int | None = None
    origin: int | None = None
    destination: int | None = None
    arrives_at_tick: int | None = None
    cargo: dict[int, int] = field(default_factory=dict)  # destination -> quantity

    @classmethod
    def idle_at(cls, location: int) -> Vessel:
        return cls(state=IDLE, location=location)

    # State changes keep the cargo; only the position fields change.

    def make_idle(self, location: int) -> None:
        self._set(IDLE, location=location)

    def make_docked(self, location: int) -> None:
        self._set(DOCKED, location=location)

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
            payload = {
                "state": TRANSIT,
                "from": self.origin,
                "to": self.destination,
                "arrives_at_tick": self.arrives_at_tick,
            }
        else:
            payload = {"state": self.state, "location": self.location}
        payload["cargo"] = [{"destination": d, "quantity": q} for d, q in self.cargo.items()]
        return payload


@dataclass
class World:
    tick: int
    vessels: dict[str, Vessel]  # kept in init order, which is also the output order
    dock_cargo: dict[tuple[int, int], int] = field(default_factory=dict)  # (location, destination) -> quantity
    delivered: int = 0  # units unloaded at their destination; not reported, used by tests

    def clone(self) -> World:
        return copy.deepcopy(self)

    def to_payload(self) -> dict:
        return {
            "tick": self.tick,
            "vessels": {vessel_id: v.to_payload() for vessel_id, v in self.vessels.items()},
            "cargo": [
                {"location": loc, "destination": dest, "quantity": q}
                for (loc, dest), q in self.dock_cargo.items()
            ],
        }


# ---- cargo counts ----

def add_units(counts: dict, key, quantity: int = 1) -> None:
    counts[key] = counts.get(key, 0) + quantity


def take_unit(counts: dict, key) -> bool:
    """Remove one unit. Returns False (and changes nothing) if there is none.
    Entries that reach zero are removed, so the output never shows quantity 0."""
    if counts.get(key, 0) <= 0:
        return False
    counts[key] -= 1
    if counts[key] == 0:
        del counts[key]
    return True


def transfer_ops(before: dict[int, int], after: dict[int, int]) -> int:
    """Fewest load/unload/swap operations that turn one ship's cargo `before` into `after`.

    Only the net change counts, so a load followed by an unload of the same
    cargo costs nothing. A swap does one load and one unload as a single
    operation, so the count is the larger of units loaded and units unloaded.
    """
    loaded = unloaded = 0
    for destination in before.keys() | after.keys():
        change = after.get(destination, 0) - before.get(destination, 0)
        if change > 0:
            loaded += change
        else:
            unloaded -= change
    return max(loaded, unloaded)
