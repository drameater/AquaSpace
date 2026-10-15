"""Offline guard: ordinary invocations of the live harness must be inert."""
import contextlib
import ctypes
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'script/native-tabs-smoke/ghostty-topology/live.py'

class GhosttyTopologyOptInTest(unittest.TestCase):
    def invoke(self, arguments, expected_code):
        # Block real native loading, subprocesses, signals and fixture writes.
        # A regression reaching any of these fails offline, before any live action.
        with contextlib.ExitStack() as stack:
            for target in ('ctypes.CDLL', 'subprocess.Popen', 'os.kill',
                           'pathlib.Path.mkdir', 'pathlib.Path.write_text'):
                stack.enter_context(patch(target, side_effect=AssertionError('live side effect')))
            stack.enter_context(patch.object(sys, 'argv', [str(HARNESS), *arguments]))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as exit:
                runpy.run_path(str(HARNESS), run_name='__main__')
            self.assertEqual(exit.exception.code, expected_code)

    def test_no_opt_in_is_inert(self):
        self.invoke([], 2)

    def test_help_is_inert(self):
        self.invoke(['--help'], 0)

    def test_explicit_baseline_is_required(self):
        self.invoke(['--run-live'], 2)

    def test_malformed_baseline_is_inert(self):
        for value in ('21423', '21423:', 'not-an-id:2'):
            with self.subTest(value=value):
                self.invoke(['--run-live', '--expect-existing-window', value], 2)

if __name__ == '__main__':
    unittest.main()
