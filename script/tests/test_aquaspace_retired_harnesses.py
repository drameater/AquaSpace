"""Offline lifecycle regression: runtime admission has no debug escape hatch."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RetiredHarnessTests(unittest.TestCase):
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
