"""Lifecycle wiring checks; no app, native reader or process API is executed."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FailureReportingTests(unittest.TestCase):
    def test_report_retains_public_console_and_stderr_fallbacks_without_live_actions(self):
        report = (ROOT / 'Sources/AppBundle/model/WindowManagerFailureReport.swift').read_text()
        self.assertIn('privacy: .public', report)
        self.assertIn('FileHandle.standardError', report)
        self.assertIn('last-failure.txt', report)
        for forbidden in ('NSAlert', 'NSWorkspace', 'Process(', 'claim()', 'kill(', 'nativeFocus'):
            self.assertNotIn(forbidden, report)

    def test_report_occurs_after_freezing_but_before_any_shutdown_wait(self):
        source = (ROOT / 'Sources/AppBundle/WindowManagerHandoff.swift').read_text()
        stop = source[source.index('private static func stopForUncertainOwnership('):
                      source.index('static func prepareForStartup()')]
        self.assertLess(stop.index('admission.yield()'), stop.index('showFailure('))
        self.assertLess(stop.index('freezeAXWrites()'), stop.index('showFailure('))
        self.assertLess(stop.index('pauseWindowManagementForHandoff()'), stop.index('showFailure('))
        self.assertLess(stop.index('showFailure('), stop.index('await '))
        self.assertIn('exit(EXIT_FAILURE)', stop)
        failure = source[source.index('private static func showFailure('):source.index('private enum HandoffError:')]
        self.assertIn('WindowManagerFailureReport(', failure)
        self.assertIn('.publish()', failure)
        self.assertNotIn('NSLog(', failure)

    def test_startup_and_handoff_failures_keep_phase_and_fail_closed_exit_paths(self):
        source = (ROOT / 'Sources/AppBundle/WindowManagerHandoff.swift').read_text()
        prepare = source[source.index('static func prepareForStartup()'):source.index('static func finishStartup()')]
        self.assertIn('showFailure(error, phase: .startup)', prepare)
        self.assertIn('exit(EXIT_FAILURE)', prepare)
        finish = source[source.index('static func finishStartup()'):source.index('static func restoreStartupSession()')]
        self.assertIn('await failStartup(error)', finish)
        self.assertIn('stopForUncertainOwnership(error, phase: .startup)', finish)
        handoff = source[source.index('static func switchToOriginal('):source.index('private struct RestorationAnchor:')]
        self.assertIn('showFailure(error, phase: .handoff)', handoff)
        self.assertIn('exit(EXIT_FAILURE)', handoff)
        startup = (ROOT / 'Sources/AppBundle/initAppBundle.swift').read_text()
        self.assertIn('catch {\n            await WindowManagerHandoff.failStartup(error)', startup)


if __name__ == '__main__':
    unittest.main()
