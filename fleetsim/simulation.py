"""The simulation engine: one handler per command, plus the tick logic.

State model
-----------
committed  The world as of the last tick. Only `tick` changes it.
planned    committed + every command accepted since the last tick.
           `get_state` reports it; `tick` turns it into the next committed state.

Commands are checked against the committed state (e.g. set_destination needs
the ship to be idle as of the last tick) and their effects go into planned.

A handler that changes anything works on a Draft of planned (model.py) and
only replaces planned once the whole command has succeeded, so a rejected
command never leaves a partial change behind. A Draft copies only what the
command touches; everything else is shared with the state it started from.

History
-------
Every committed state is kept in `history`, indexed by tick. States are
read-only (model.py), so history keeps the objects themselves, and successive
entries share every ship and port that didn't change.
"""

from __future__ import annotations

from fleetsim.model import DOCKED, IDLE, TRANSIT, Draft, World, add_units, read_only, take_unit, transfer_ops
from fleetsim.params import (
    CommandError,
    check_index,
    check_int,
    check_str,
    get,
    parse_dock_throughput,
    parse_manifest,
    parse_travel_times,
    parse_vessels,
)


class Simulation:
    def __init__(self) -> None:
        self.travel_times: list[list[int]] = []
        self.dock_throughput: list[int] = []
        self.committed: World | None = None
        self.planned: World | None = None
        self.history: list[World] = []  # history[t] is the committed state at tick t
        self._docked_at: dict[int, list[str]] = {}  # location -> ships docked there as of the last tick
        self._handlers = {
            "init": self._init,
            "tick": self._tick,
            "set_destination": self._set_destination,
            "get_state": self._get_state,
            "get_state_at": self._get_state_at,
            "dock": self._dock,
            "undock": self._undock,
            "load": self._load,
            "unload": self._unload,
            "swap": self._swap,
        }

    @property
    def num_locations(self) -> int:
        return len(self.travel_times)

    def handle(self, command: str, params: dict) -> dict:
        """Run one command. Returns the response payload or raises CommandError."""
        handler = self._handlers.get(command)
        if handler is None:
            raise CommandError(f"unknown command: {command}")
        if command != "init" and self.committed is None:
            raise CommandError("simulation is not initialized: send init first")
        return handler(params)

    # ---- clock ----

    def _init(self, params: dict) -> dict:
        if self.committed is not None:
            raise CommandError("simulation is already initialized")
        # Parse everything before changing anything, so a failed init can be retried.
        travel_times = parse_travel_times(get(params, "travel_times"))
        n = len(travel_times)
        dock_throughput = parse_dock_throughput(get(params, "dock_throughput"), n)
        vessels = parse_vessels(get(params, "vessels"), n)
        manifest = parse_manifest(params.get("cargo_manifest"), n)

        self.travel_times = travel_times
        self.dock_throughput = dock_throughput
        draft = Draft(World(tick=0, vessels=read_only(vessels)))
        _add_manifest(draft, manifest)
        world = draft.build()
        self._commit(world)
        return world.to_payload()

    def _tick(self, params: dict) -> dict:
        # Check the manifest first: a bad entry rejects the whole tick.
        manifest = parse_manifest(params.get("cargo_manifest"), self.num_locations)

        draft = Draft(self.planned)  # 1. pending commands take effect
        draft.tick += 1  # 2. advance the clock
        for vessel_id, vessel in self.planned.vessels.items():  # 3. arrivals
            if vessel.state == TRANSIT and vessel.arrives_at_tick <= draft.tick:
                draft.vessels[vessel_id] = vessel.idle(vessel.destination)
        _add_manifest(draft, manifest)  # 4. new cargo lands on the docks
        world = draft.build()
        self._commit(world)  # 5. save to history, 6. start a fresh plan
        return world.to_payload()

    def _get_state(self, params: dict) -> dict:
        return self.planned.to_payload()

    def _get_state_at(self, params: dict) -> dict:
        tick = check_int(get(params, "tick"), "tick")
        current = self.committed.tick
        if tick < 0:
            raise CommandError(f"tick {tick} is out of range (must be 0 or more)")
        if tick > current:
            raise CommandError(f"tick {tick} is in the future (current tick: {current})")
        return self.history[tick].to_payload()

    # ---- movement ----

    def _set_destination(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        destination = check_index(get(params, "destination"), "destination", self.num_locations)
        current = self.committed.vessels[vessel_id]
        if current.state != IDLE:
            raise CommandError(f"vessel {vessel_id} is not idle")

        here = current.location
        draft = Draft(self.planned)
        vessel = draft.vessels[vessel_id]
        if destination == here:
            # A trip to where the ship already is: cancel any pending trip or dock.
            draft.vessels[vessel_id] = vessel.idle(here)
        else:
            # Replaces any pending trip or dock. The trip's clock starts now,
            # at the tick the command is issued.
            arrives = draft.tick + self.travel_times[here][destination]
            draft.vessels[vessel_id] = vessel.in_transit(here, destination, arrives)
        self.planned = draft.build()
        return self.planned.to_payload()

    def _dock(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        committed = self.committed.vessels[vessel_id]
        planned = self.planned.vessels[vessel_id]
        if committed.state == IDLE:
            pass  # dock here; replaces a pending trip; a repeated dock changes nothing
        elif committed.state == DOCKED and planned.state == IDLE:
            pass  # cancels a pending undock; cargo commands already sent stay in place
        elif committed.state == DOCKED:
            raise CommandError(f"vessel {vessel_id} is already docked")
        else:
            raise CommandError(f"vessel {vessel_id} is not idle")

        draft = Draft(self.planned)
        draft.vessels[vessel_id] = planned.docked(committed.location)
        self.planned = draft.build()
        return self.planned.to_payload()

    def _undock(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        committed = self.committed.vessels[vessel_id]
        planned = self.planned.vessels[vessel_id]
        if committed.state == DOCKED:
            pass  # leaves at the next tick; cargo commands already sent still happen
        elif committed.state == IDLE and planned.state == DOCKED:
            pass  # cancels a pending dock
        else:
            raise CommandError(f"vessel {vessel_id} is not docked")

        draft = Draft(self.planned)
        draft.vessels[vessel_id] = planned.idle(committed.location)
        self.planned = draft.build()
        return self.planned.to_payload()

    # ---- cargo ----

    def _load(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        destination = check_index(get(params, "destination"), "destination", self.num_locations)
        self._require_docked(vessel_id)

        draft = Draft(self.planned)
        _move_dock_to_vessel(draft, vessel_id, destination)
        return self._apply_cargo_change(draft, vessel_id)

    def _unload(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        destination = check_index(get(params, "destination"), "destination", self.num_locations)
        self._require_docked(vessel_id)

        draft = Draft(self.planned)
        _move_vessel_to_dock(draft, vessel_id, destination)
        return self._apply_cargo_change(draft, vessel_id)

    def _swap(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        n = self.num_locations
        load_destination = check_index(get(params, "load_destination"), "load_destination", n)
        unload_destination = check_index(get(params, "unload_destination"), "unload_destination", n)
        self._require_docked(vessel_id)

        if load_destination == unload_destination:
            # Swapping a destination for itself always changes nothing, whatever cargo
            # the ship or dock has (not even the order the ship's cargo is listed in).
            return self.planned.to_payload()

        draft = Draft(self.planned)
        # Both halves must succeed. With different destinations, their order doesn't matter.
        _move_vessel_to_dock(draft, vessel_id, unload_destination)
        _move_dock_to_vessel(draft, vessel_id, load_destination)
        return self._apply_cargo_change(draft, vessel_id)

    def _require_docked(self, vessel_id: str) -> None:
        """Cargo commands need the ship docked as of the last tick and not about to leave."""
        if self.committed.vessels[vessel_id].state != DOCKED:
            raise CommandError(f"vessel {vessel_id} is not docked")
        if self.planned.vessels[vessel_id].state != DOCKED:
            raise CommandError(f"vessel {vessel_id} is undocking")

    def _apply_cargo_change(self, draft: Draft, vessel_id: str) -> dict:
        """Accept a cargo change if the dock can do it before the next tick."""
        location = draft.vessels[vessel_id].location
        ops = 0
        for other_id in self._docked_at.get(location, ()):
            ops += transfer_ops(self.committed.vessels[other_id].cargo, draft.vessels[other_id].cargo)
        limit = self.dock_throughput[location]
        if ops > limit:
            raise CommandError(
                f"transfer of {ops} operations exceeds dock throughput of {limit} at location {location}"
            )
        self.planned = draft.build()
        return self.planned.to_payload()

    # ---- helpers ----

    def _commit(self, world: World) -> None:
        """Make `world` the new committed state, record it, and start a fresh plan from it."""
        self.committed = world
        self.history.append(world)
        self.planned = world  # read-only, so the plan can start as the same object
        self._docked_at = {}
        for vessel_id, vessel in world.vessels.items():
            if vessel.state == DOCKED:
                self._docked_at.setdefault(vessel.location, []).append(vessel_id)

    def _vessel_id(self, params: dict) -> str:
        vessel_id = check_str(get(params, "vessel_id"), "vessel_id")
        if vessel_id not in self.committed.vessels:
            raise CommandError(f"unknown vessel {vessel_id}")
        return vessel_id


def _add_manifest(draft: Draft, manifest: list[tuple[int, int, int]]) -> None:
    for location, destination, quantity in manifest:
        add_units(draft.dock_cargo(location), destination, quantity)


def _move_dock_to_vessel(draft: Draft, vessel_id: str, destination: int) -> None:
    vessel = draft.vessels[vessel_id]
    here = vessel.location
    if not take_unit(draft.dock_cargo(here), destination):
        raise CommandError(f"no cargo for destination {destination} at location {here}")
    cargo = dict(vessel.cargo)
    add_units(cargo, destination)
    draft.vessels[vessel_id] = vessel.with_cargo(cargo)


def _move_vessel_to_dock(draft: Draft, vessel_id: str, destination: int) -> None:
    vessel = draft.vessels[vessel_id]
    here = vessel.location
    cargo = dict(vessel.cargo)
    if not take_unit(cargo, destination):
        raise CommandError(f"vessel {vessel_id} has no cargo for destination {destination}")
    draft.vessels[vessel_id] = vessel.with_cargo(cargo)
    if destination == here:
        draft.delivered += 1  # delivered: the unit leaves the simulation
    else:
        add_units(draft.dock_cargo(here), destination)
