"""Source-text guards for controller lifecycle wiring; no Swift code is executed.

These checks read Swift source because the guarded paths end in exit() or need
the running app, so no Swift behavior test covers them yet. Each test names the
behavior that a Swift test must cover before the check can be removed. Matching
ignores all whitespace, so reformatting cannot fail a check.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def compact(text):
    return re.sub(r'\s+', '', text)


def source(path):
    return compact((ROOT / 'Sources/AppBundle' / path).read_text())


def between(text, start, end):
    first = text.index(compact(start))
    return text[first:text.index(compact(end), first)]


class LifecycleSourceGuardTests(unittest.TestCase):
    def assertContains(self, needle, text):
        self.assertIn(compact(needle), text)

    def assertLacks(self, needle, text):
        self.assertNotIn(compact(needle), text)

    def assertBefore(self, first, second, text):
        self.assertLess(text.index(compact(first)), text.index(compact(second)))

    def test_runtime_wires_both_builds_through_admission_and_monitoring(self):
        # Needs a Swift behavior test: startup passes the actual build kind to
        # admission; finishStartup claims admission before it grants window writes;
        # monitoring and the stock handoff have no debug-only path; the launch
        # observer freezes writes for every manager bundle ID in either build.
        handoff = source('WindowManagerHandoff.swift')
        self.assertContains('admission.prepare(build: isDebug ? .debug : .release', handoff)
        claim = between(handoff, 'static func finishStartup()', 'static func restoreStartupSession()')
        self.assertLacks('isDebug', claim)
        self.assertBefore('try admission.claim()', 'writePermission.withLock { $0.claim() }', claim)
        watch = between(handoff, 'static func watchForOriginal()', 'static func prepareForStartup()')
        self.assertLacks('isDebug', watch)
        self.assertContains('admission.observedPeers(freeze: freezeAXWrites)', watch)
        switch = handoff[handoff.index(compact('static func switchToOriginal(')):]
        self.assertLacks('isDebug', switch)
        observer = source('GlobalObserver.swift')
        self.assertContains('WindowManagerProcess.bundleIds.contains', observer)
        self.assertLacks('!isDebug', observer)
        # Upstream's toggleReleaseServerIfDebug lets a debug build disable and later
        # re-enable a running release manager. The fork removed it; an upstream
        # merge must not bring it back.
        for path in ['initAppBundle.swift', 'server.swift', 'util/appBundleUtil.swift']:
            self.assertLacks('toggleReleaseServerIfDebug', source(path))

    def test_report_default_sinks_are_public_and_report_has_no_control_capability(self):
        # The saved file is covered by WindowManagerFailureReportTest
        # (everyRefusalSurvivesInAnOwnerPrivatePlainTextFile). That test injects
        # the log and stderr sinks, so the defaults stay guarded here.
        # Needs a Swift behavior test: the default Console sink logs the reason
        # unredacted, the default stderr sink writes it, and publish() performs no
        # UI, process, ownership or focus action.
        report = source('model/WindowManagerFailureReport.swift')
        self.assertContains('privacy: .public', report)
        self.assertContains('FileHandle.standardError', report)
        for forbidden in ('NSAlert', 'NSWorkspace', 'Process(', 'claim()', 'kill(', 'nativeFocus'):
            self.assertLacks(forbidden, report)

    def test_report_occurs_after_freezing_but_before_any_shutdown_wait(self):
        # Needs a Swift behavior test: on uncertain ownership the controller yields,
        # freezes window writes and pauses management, then publishes the report
        # before its first await, then exits with a failure status.
        handoff = source('WindowManagerHandoff.swift')
        stop = between(handoff, 'private static func stopForUncertainOwnership(', 'static func prepareForStartup()')
        for step in ('admission.yield()', 'freezeAXWrites()', 'pauseWindowManagementForHandoff()'):
            self.assertBefore(step, 'showFailure(', stop)
        self.assertBefore('showFailure(', 'await ', stop)
        self.assertContains('exit(EXIT_FAILURE)', stop)
        failure = between(handoff, 'private static func showFailure(', 'private enum HandoffError:')
        self.assertContains('WindowManagerFailureReport(', failure)
        self.assertContains('.publish()', failure)
        self.assertLacks('NSLog(', failure)

    def test_startup_and_handoff_failures_keep_phase_and_fail_closed_exit_paths(self):
        # Needs a Swift behavior test: a failure in startup preparation, startup
        # claim, the stock handoff, or the final startup session publishes a report
        # with the matching phase and then exits with a failure status.
        handoff = source('WindowManagerHandoff.swift')
        prepare = between(handoff, 'static func prepareForStartup()', 'static func finishStartup()')
        self.assertContains('showFailure(error, phase: .startup)', prepare)
        self.assertContains('exit(EXIT_FAILURE)', prepare)
        finish = between(handoff, 'static func finishStartup()', 'static func restoreStartupSession()')
        self.assertContains('await failStartup(error)', finish)
        self.assertContains('stopForUncertainOwnership(error, phase: .startup)', finish)
        switch = between(handoff, 'static func switchToOriginal(', 'private struct RestorationAnchor:')
        self.assertContains('showFailure(error, phase: .handoff)', switch)
        self.assertContains('exit(EXIT_FAILURE)', switch)
        startup = source('initAppBundle.swift')
        self.assertContains('catch { await WindowManagerHandoff.failStartup(error)', startup)


if __name__ == '__main__':
    unittest.main()
