"""Ship order in responses: ships are listed in init order.

Transcripts compare JSON objects without regard to key order, so this is
checked here on the raw output text instead.
"""

import io
import json
import unittest

from fleetsim.protocol import run


class OutputOrderTests(unittest.TestCase):
    def test_ships_are_listed_in_init_order(self):
        commands = [
            {
                "command": "init",
                "parameters": {
                    "travel_times": [[0, 1], [1, 0]],
                    "dock_throughput": [1, 1],
                    "vessels": [
                        {"id": "zulu", "location": 0},
                        {"id": "alpha", "location": 1},
                        {"id": "v10", "location": 0},
                        {"id": "v2", "location": 0},
                    ],
                },
            },
            {"command": "set_destination", "parameters": {"vessel_id": "v2", "destination": 1}},
            {"command": "tick", "parameters": {}},
        ]
        out = io.StringIO()
        run((json.dumps(c) for c in commands), out)
        for line in out.getvalue().splitlines():
            vessels = json.loads(line)["payload"]["vessels"]
            self.assertEqual(list(vessels), ["zulu", "alpha", "v10", "v2"])
