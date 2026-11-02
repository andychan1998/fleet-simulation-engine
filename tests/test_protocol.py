"""The protocol's safety net: if the engine hits an unexpected error (a bug),
the caller gets an error response and the stream keeps going."""

import io
import json
import unittest
from unittest import mock

from fleetsim import protocol


class SafetyNetTests(unittest.TestCase):
    def test_internal_error_is_reported_and_the_stream_keeps_going(self):
        real_process_line = protocol.process_line

        def buggy(sim, line):
            if "boom" in line:
                raise RuntimeError("simulated bug")
            return real_process_line(sim, line)

        out = io.StringIO()
        with mock.patch.object(protocol, "process_line", buggy):
            protocol.run(['{"command":"boom"}', '{"command":"get_state"}'], out)

        first, second = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(
            first,
            {"status": "error", "payload": {"message": "internal error: RuntimeError: simulated bug"}},
        )
        # the next command is still answered (here: not initialized yet)
        self.assertEqual(second["status"], "error")
