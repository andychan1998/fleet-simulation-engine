"""The simulation engine: one handler per command, plus the tick logic.

State model
-----------
committed  The world as of the last tick. Only `tick` changes it.
planned    committed + every command accepted since the last tick.
           `get_state` reports it; `tick` turns it into the next committed state.

Commands are checked against the committed state (e.g. set_destination needs
the ship to be idle as of the last tick) and their effects go into planned.

A handler that changes anything works on a copy of planned and only replaces
planned once the whole command has succeeded, so a rejected command never
leaves a partial change behind.

History
-------
Every committed state is kept in `history`, indexed by tick. A committed state
is never modified after it is committed (handlers and tick work on copies), so
history can keep the objects themselves without copying them again.
"""

from __future__ import annotations

from fleetsim.model import DOCKED, IDLE, TRANSIT, World, add_units, take_unit, transfer_ops
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
        world = World(tick=0, vessels=vessels)
        _add_manifest(world, manifest)
        self._commit(world)
        return world.to_payload()

    def _tick(self, params: dict) -> dict:
        # Check the manifest first: a bad entry rejects the whole tick.
        manifest = parse_manifest(params.get("cargo_manifest"), self.num_locations)

        world = self.planned.clone()  # 1. pending commands take effect
        world.tick += 1  # 2. advance the clock
        for vessel in world.vessels.values():  # 3. arrivals
            if vessel.state == TRANSIT and vessel.arrives_at_tick <= world.tick:
                vessel.make_idle(vessel.destination)
        _add_manifest(world, manifest)  # 4. new cargo lands on the docks
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
        world = self.planned.clone()
        vessel = world.vessels[vessel_id]
        if destination == here:
            # A trip to where the ship already is: cancel any pending trip or dock.
            vessel.make_idle(here)
        else:
            # Replaces any pending trip or dock. The trip's clock starts now,
            # at the tick the command is issued.
            arrives = world.tick + self.travel_times[here][destination]
            vessel.make_transit(here, destination, arrives)
        self.planned = world
        return world.to_payload()

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

        world = self.planned.clone()
        world.vessels[vessel_id].make_docked(committed.location)
        self.planned = world
        return world.to_payload()

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

        world = self.planned.clone()
        world.vessels[vessel_id].make_idle(committed.location)
        self.planned = world
        return world.to_payload()

    # ---- cargo ----

    def _load(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        destination = check_index(get(params, "destination"), "destination", self.num_locations)
        self._require_docked(vessel_id)

        world = self.planned.clone()
        _move_dock_to_vessel(world, vessel_id, destination)
        return self._apply_cargo_change(world, vessel_id)

    def _unload(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        destination = check_index(get(params, "destination"), "destination", self.num_locations)
        self._require_docked(vessel_id)

        world = self.planned.clone()
        _move_vessel_to_dock(world, vessel_id, destination)
        return self._apply_cargo_change(world, vessel_id)

    def _swap(self, params: dict) -> dict:
        vessel_id = self._vessel_id(params)
        n = self.num_locations
        load_destination = check_index(get(params, "load_destination"), "load_destination", n)
        unload_destination = check_index(get(params, "unload_destination"), "unload_destination", n)
        self._require_docked(vessel_id)

        world = self.planned.clone()
        # Unload first, then load: the swap is valid if both succeed in that order.
        _move_vessel_to_dock(world, vessel_id, unload_destination)
        _move_dock_to_vessel(world, vessel_id, load_destination)
        return self._apply_cargo_change(world, vessel_id)

    def _require_docked(self, vessel_id: str) -> None:
        """Cargo commands need the ship docked as of the last tick and not about to leave."""
        if self.committed.vessels[vessel_id].state != DOCKED:
            raise CommandError(f"vessel {vessel_id} is not docked")
        if self.planned.vessels[vessel_id].state != DOCKED:
            raise CommandError(f"vessel {vessel_id} is undocking")

    def _apply_cargo_change(self, world: World, vessel_id: str) -> dict:
        """Accept a cargo change if the dock can do it before the next tick."""
        location = world.vessels[vessel_id].location
        ops = 0
        for other_id, before in self.committed.vessels.items():
            if before.state == DOCKED and before.location == location:
                ops += transfer_ops(before.cargo, world.vessels[other_id].cargo)
        limit = self.dock_throughput[location]
        if ops > limit:
            raise CommandError(
                f"transfer of {ops} operations exceeds dock throughput of {limit} at location {location}"
            )
        self.planned = world
        return world.to_payload()

    # ---- helpers ----

    def _commit(self, world: World) -> None:
        """Make `world` the new committed state, record it, and start a fresh plan from it."""
        self.committed = world
        self.history.append(world)
        self.planned = world.clone()

    def _vessel_id(self, params: dict) -> str:
        vessel_id = check_str(get(params, "vessel_id"), "vessel_id")
        if vessel_id not in self.committed.vessels:
            raise CommandError(f"unknown vessel {vessel_id}")
        return vessel_id


def _add_manifest(world: World, manifest: list[tuple[int, int, int]]) -> None:
    for location, destination, quantity in manifest:
        add_units(world.dock_cargo, (location, destination), quantity)


def _move_dock_to_vessel(world: World, vessel_id: str, destination: int) -> None:
    vessel = world.vessels[vessel_id]
    here = vessel.location
    if not take_unit(world.dock_cargo, (here, destination)):
        raise CommandError(f"no cargo for destination {destination} at location {here}")
    add_units(vessel.cargo, destination)


def _move_vessel_to_dock(world: World, vessel_id: str, destination: int) -> None:
    vessel = world.vessels[vessel_id]
    here = vessel.location
    if not take_unit(vessel.cargo, destination):
        raise CommandError(f"vessel {vessel_id} has no cargo for destination {destination}")
    if destination == here:
        world.delivered += 1  # delivered: the unit leaves the simulation
    else:
        add_units(world.dock_cargo, (here, destination))
