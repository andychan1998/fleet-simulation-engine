"""Runs every transcript under tests/transcripts/ as its own test.

Test names follow the file path, e.g. tests/transcripts/spec/base_example.txt
becomes test_spec__base_example, so you can run a subset with:

    python -m unittest -k spec
"""

import unittest

from tests.transcript import TRANSCRIPT_DIR, all_transcripts, check_transcript


class TranscriptTests(unittest.TestCase):
    pass


def _make_test(path):
    def test(self):
        check_transcript(self, path)

    test.__doc__ = str(path.relative_to(TRANSCRIPT_DIR))
    return test


for _path in all_transcripts():
    _name = _path.relative_to(TRANSCRIPT_DIR).with_suffix("").as_posix()
    _name = "test_" + _name.replace("/", "__").replace("-", "_")
    setattr(TranscriptTests, _name, _make_test(_path))
