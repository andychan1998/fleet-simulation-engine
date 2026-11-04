"""World state: the simulation clock, the ships, and the cargo.

The engine keeps two of these (see simulation.py): `committed`, the world as of
the last tick, and `planned`, the committed world plus every command accepted
since then.

Read-only by design
-------------------
A World, its Vessels and its cargo tables never change once built. A command
builds a new World through a Draft, which copies only what the command touches
(one ship, one port's cargo) and shares everything else with the World it
started from. So states that follow each other, and the whole of history, share
every ship and port that didn't change, and an old state can't be changed by
accident: trying raises an error.

Cargo
-----
Cargo has no identity: a unit is described only by where it needs to go, so
cargo is stored as counts. A ship holds {destination: quantity}; each port's
dock holds its own {destination: quantity}. Python dicts keep insertion order,
which gives the output order "first arrival first" (dock cargo is listed port
by port).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Iterator, Mapping

IDLE = "idle"
TRANSIT = "transit"
DOCKED = "docked"

EMPTY: Mapping = MappingProxyType({})


def _empty() -> Mapping:
    # Used as default_factory: Python 3.11 refuses a read-only mapping as a plain
    # dataclass default, because it can't be hashed there (3.12+ allows it).
    return EMPTY


def read_only(table: dict) -> Mapping:
    """A read-only view of `table`. The caller must not keep changing `table` afterwards."""
    return MappingProxyType(table)


@dataclass(frozen=True)
class Vessel:
    """One ship. Which position fields matter depends on its state:

    idle, docked: `location`
    transit:      `origin`, `destination`, `arrives_at_tick`

    A Vessel never changes: the methods below return a new one.
    """

    state: str
    location: int | None = None
    origin: int | None = None
    destination: int | None = None
    arrives_at_tick: int | None = None
    cargo: Mapping[int, int] = field(default_factory=_empty)  # destination -> quantity

    @classmethod
    def idle_at(cls, location: int) -> Vessel:
        return cls(state=IDLE, location=location)

    # A new position keeps the cargo; every position field is set afresh.

    def idle(self, location: int) -> Vessel:
        return Vessel(IDLE, location=location, cargo=self.cargo)

    def docked(self, location: int) -> Vessel:
        return Vessel(DOCKED, location=location, cargo=self.cargo)

    def in_transit(self, origin: int, destination: int, arrives_at_tick: int) -> Vessel:
        return Vessel(TRANSIT, origin=origin, destination=destination, arrives_at_tick=arrives_at_tick,
                      cargo=self.cargo)

    def with_cargo(self, cargo: dict[int, int]) -> Vessel:
        return replace(self, cargo=read_only(cargo))

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


@dataclass(frozen=True)
class World:
    tick: int
    vessels: Mapping[str, Vessel]  # kept in init order, which is also the output order
    docks: Mapping[int, Mapping[int, int]] = field(default_factory=_empty)  # port -> {destination: quantity}, for ports that have had cargo
    delivered: int = 0  # units unloaded at their destination; not reported, used by tests

    def cargo_entries(self) -> Iterator[tuple[int, int, int]]:
        """(location, destination, quantity) for every unit of dock cargo, in output order."""
        for location in sorted(self.docks):
            for destination, quantity in self.docks[location].items():
                yield location, destination, quantity

    def to_payload(self) -> dict:
        return {
            "tick": self.tick,
            "vessels": {vessel_id: v.to_payload() for vessel_id, v in self.vessels.items()},
            "cargo": [
                {"location": loc, "destination": dest, "quantity": q}
                for loc, dest, q in self.cargo_entries()
            ],
        }


class Draft:
    """A change in progress, built from a World.

    It copies only the references to ships and ports (cheap), and a port's
    cargo table only when that port's cargo is changed. `build()` returns the
    new, read-only World; everything not changed is shared with the original.
    """

    def __init__(self, world: World) -> None:
        self.tick = world.tick
        self.delivered = world.delivered
        self.vessels: dict[str, Vessel] = dict(world.vessels)
        self._docks: dict[int, Mapping[int, int]] = dict(world.docks)
        self._changed_ports: set[int] = set()

    def dock_cargo(self, location: int) -> dict[int, int]:
        """A copy of one port's cargo table that can be changed. Only this port is copied."""
        table = dict(self._docks.get(location, EMPTY))
        self._docks[location] = table
        self._changed_ports.add(location)
        return table

    def build(self) -> World:
        # The new World gets its own copies of the tables this draft can still
        # change, so using the draft again can't reach into the World.
        docks = {
            location: read_only(dict(table)) if location in self._changed_ports else table
            for location, table in self._docks.items()
        }
        return World(self.tick, read_only(dict(self.vessels)), read_only(docks), self.delivered)


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


def transfer_ops(before: Mapping[int, int], after: Mapping[int, int]) -> int:
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
