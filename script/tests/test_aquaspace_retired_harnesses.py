"""Offline lifecycle regressions: retired entrypoints must have no side effects."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location('retired_' + name.replace('-', '_'),
                                                ROOT / 'script/native-tabs-smoke' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetiredHarnessTests(unittest.TestCase):
    def test_old_runners_refuse_without_any_process_actions_or_file_writes(self):
        # Include import time: no hidden watchdog can be installed on import.
        for name in ['handoff-qa', 'ghostty-live']:
            with self.subTest(name=name), contextlib.ExitStack() as guards:
                for api in ['subprocess.Popen', 'subprocess.run', 'subprocess.check_output',
                            'os.kill', 'ctypes.CDLL', 'pathlib.Path.write_text',
                            'pathlib.Path.mkdir', 'tempfile.mkdtemp']:
                    guards.enter_context(patch(api, side_effect=AssertionError('Forbidden live API: ' + api)))
                errors = guards.enter_context(contextlib.redirect_stderr(io.StringIO()))
                with patch.object(sys, 'argv', [name, '--app', '/Applications/AquaSpace.app']):
                    self.assertEqual(load(name).main(), 2)
                self.assertIn('retired', errors.getvalue())

    def test_preparer_cannot_generate_a_candidate_that_bypasses_admission(self):
        with contextlib.ExitStack() as guards:
            for api in ['subprocess.Popen', 'subprocess.run', 'subprocess.check_output',
                        'os.kill', 'ctypes.CDLL', 'pathlib.Path.write_text',
                        'pathlib.Path.mkdir', 'tempfile.mkdtemp']:
                guards.enter_context(patch(api, side_effect=AssertionError('Forbidden live API: ' + api)))
            errors = guards.enter_context(contextlib.redirect_stderr(io.StringIO()))
            with patch.object(sys, 'argv', ['prepare-ghostty-live', '--handoff-qa']):
                with self.assertRaises(SystemExit) as result:
                    load('prepare-ghostty-live').main()
            self.assertEqual(result.exception.code, 2)
            self.assertIn('retired', errors.getvalue())

    def test_runtime_wires_both_builds_through_admission_and_monitoring(self):
        # Complements behavioral Swift tests: fail if a build-conditional escape
        # hatch or the old release enable/disable lifecycle is reintroduced.
        handoff = (ROOT / 'Sources/AppBundle/WindowManagerHandoff.swift').read_text()
        self.assertIn('admission.prepare(build: isDebug ? .debug : .release', handoff)
        claim = handoff[handoff.index('static func finishStartup()'):handoff.index('static func restoreStartupSession()')]
        self.assertNotIn('isDebug', claim)
        self.assertLess(claim.index('try admission.claim()'), claim.index('writePermission.withLock { $0.claim() }'))
        watch = handoff[handoff.index('static func watchForOriginal()'):handoff.index('static func prepareForStartup()')]
        self.assertNotIn('isDebug', watch)
        self.assertIn('admission.observedPeers(freeze: freezeAXWrites)', watch)
        switch = handoff[handoff.index('static func switchToOriginal('):]
        self.assertNotIn('isDebug', switch)
        observer = (ROOT / 'Sources/AppBundle/GlobalObserver.swift').read_text()
        self.assertIn('WindowManagerProcess.bundleIds.contains', observer)
        self.assertNotIn('!isDebug', observer)
        for path in ['initAppBundle.swift', 'server.swift', 'util/appBundleUtil.swift']:
            self.assertNotIn('toggleReleaseServerIfDebug', (ROOT / 'Sources/AppBundle' / path).read_text())


if __name__ == '__main__':
    unittest.main()
