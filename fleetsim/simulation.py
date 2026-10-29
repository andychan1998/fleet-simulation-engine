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
"""

from __future__ import annotations

from fleetsim.model import IDLE, TRANSIT, World
from fleetsim.params import (
    CommandError,
    check_index,
    check_str,
    get,
    parse_travel_times,
    parse_vessels,
)


class Simulation:
    def __init__(self) -> None:
        self.travel_times: list[list[int]] = []
        self.committed: World | None = None
        self.planned: World | None = None
        self._handlers = {
            "init": self._init,
            "tick": self._tick,
            "set_destination": self._set_destination,
            "get_state": self._get_state,
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

    # ---- commands ----

    def _init(self, params: dict) -> dict:
        if self.committed is not None:
            raise CommandError("simulation is already initialized")
        # Parse everything before changing anything, so a failed init can be retried.
        travel_times = parse_travel_times(get(params, "travel_times"))
        vessels = parse_vessels(get(params, "vessels"), len(travel_times))

        self.travel_times = travel_times
        self._commit(World(tick=0, vessels=vessels))
        return self.committed.to_payload()

    def _tick(self, params: dict) -> dict:
        world = self.planned.clone()  # 1. pending commands take effect
        world.tick += 1  # 2. advance the clock
        for vessel in world.vessels.values():  # 3. arrivals
            if vessel.state == TRANSIT and vessel.arrives_at_tick <= world.tick:
                vessel.make_idle(vessel.destination)
        self._commit(world)
        return world.to_payload()

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
            # A trip to where the ship already is: cancel whatever was pending.
            vessel.make_idle(here)
        else:
            # The trip's clock starts now, at the tick the command is issued.
            arrives = world.tick + self.travel_times[here][destination]
            vessel.make_transit(here, destination, arrives)
        self.planned = world
        return world.to_payload()

    def _get_state(self, params: dict) -> dict:
        return self.planned.to_payload()

    # ---- helpers ----

    def _commit(self, world: World) -> None:
        """Make `world` the new committed state and start a fresh plan from it."""
        self.committed = world
        self.planned = world.clone()

    def _vessel_id(self, params: dict) -> str:
        vessel_id = check_str(get(params, "vessel_id"), "vessel_id")
        if vessel_id not in self.committed.vessels:
            raise CommandError(f"unknown vessel {vessel_id}")
        return vessel_id
