"""Source-text guards for controller lifecycle wiring; no Swift code is executed.

Swift behavior tests now cover the lifecycle itself (Sources/AppBundleTests/
WindowManagerControllerTest.swift and WindowManagerFailureReportTest.swift, run by
script/test-ownership-offline.py). The two checks here guard properties that no
behavior test can observe: the absence of a startup path that only runs inside the
launched app, and the absence of capabilities in the report type. Matching ignores
all whitespace, so reformatting cannot fail a check.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def compact(text):
    return re.sub(r'\s+', '', text)


def source(path):
    return compact((ROOT / 'Sources/AppBundle' / path).read_text())


class LifecycleSourceGuardTests(unittest.TestCase):
    def assertLacks(self, needle, text):
        self.assertNotIn(compact(needle), text)

    def test_upstream_debug_toggle_of_the_release_server_is_not_restored(self):
        # Upstream's toggleReleaseServerIfDebug lets a debug build disable and later
        # re-enable a running release manager over its socket. The fork removed it;
        # an upstream merge must not bring it back. It runs only in the launched app.
        for path in ['initAppBundle.swift', 'server.swift', 'util/appBundleUtil.swift']:
            self.assertLacks('toggleReleaseServerIfDebug', source(path))

    def test_report_has_no_ui_process_ownership_or_focus_capability(self):
        # WindowManagerFailureReportTest covers what publish() writes and where. A
        # behavior test cannot show that it does nothing else, so this check keeps
        # UI, process, ownership and focus calls out of the report type.
        report = source('model/WindowManagerFailureReport.swift')
        for forbidden in ('NSAlert', 'NSWorkspace', 'Process(', 'claim()', 'kill(', 'nativeFocus'):
            self.assertLacks(forbidden, report)


if __name__ == '__main__':
    unittest.main()
