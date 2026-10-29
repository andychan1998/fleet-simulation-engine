"""Reading and checking command parameters.

Every check raises CommandError with a message that is sent back to the caller.
`label` is how the value is named in that message, e.g. "destination" or
"vessels[1].location".
"""

from __future__ import annotations

from fleetsim.model import Vessel


class CommandError(Exception):
    """The command was rejected. The message becomes the error response."""


# ---- single values ----

def get(obj: dict, name: str, label: str | None = None):
    """Return obj[name], or raise if it is missing."""
    if name not in obj:
        if label is None:
            raise CommandError(f'missing parameter "{name}"')
        raise CommandError(f'{label} is missing "{name}"')
    return obj[name]


def is_int(value) -> bool:
    # JSON true/false arrive as Python bool, which is a subclass of int.
    return isinstance(value, int) and not isinstance(value, bool)


def check_int(value, label: str) -> int:
    if not is_int(value):
        raise CommandError(f"{label} must be an integer")
    return value


def check_positive_int(value, label: str) -> int:
    if not is_int(value) or value <= 0:
        raise CommandError(f"{label} must be a positive integer")
    return value


def check_index(value, label: str, size: int) -> int:
    """A location index: an integer from 0 to size - 1."""
    check_int(value, label)
    if not 0 <= value < size:
        raise CommandError(f"{label} {value} is out of range (0 to {size - 1})")
    return value


def check_str(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CommandError(f"{label} must be a non-empty string")
    return value


def check_list(value, label: str) -> list:
    if not isinstance(value, list):
        raise CommandError(f"{label} must be a list")
    return value


def check_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise CommandError(f"{label} must be an object")
    return value


# ---- init parameters ----

def parse_travel_times(value) -> list[list[int]]:
    """N x N matrix: 0 on the diagonal, positive integers everywhere else."""
    rows = check_list(value, "travel_times")
    if not rows:
        raise CommandError("travel_times must not be empty")
    n = len(rows)
    for i, row in enumerate(rows):
        check_list(row, f"travel_times[{i}]")
        if len(row) != n:
            raise CommandError(
                f"travel_times must be square: row {i} has {len(row)} entries, expected {n}"
            )
        for j, ticks in enumerate(row):
            label = f"travel_times[{i}][{j}]"
            if i == j:
                if not is_int(ticks) or ticks != 0:
                    raise CommandError(f"{label} must be 0")
            else:
                check_positive_int(ticks, label)
    return [list(row) for row in rows]


def parse_vessels(value, num_locations: int) -> dict[str, Vessel]:
    """List of {id, location}. Every ship starts idle. Dict order = input order."""
    items = check_list(value, "vessels")
    vessels: dict[str, Vessel] = {}
    for k, item in enumerate(items):
        label = f"vessels[{k}]"
        check_object(item, label)
        vessel_id = check_str(get(item, "id", label), f"{label}.id")
        if vessel_id in vessels:
            raise CommandError(f'duplicate vessel id "{vessel_id}"')
        location = check_index(get(item, "location", label), f"{label}.location", num_locations)
        vessels[vessel_id] = Vessel.idle_at(location)
    return vessels
