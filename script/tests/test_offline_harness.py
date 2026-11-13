"""Checks for the pure logic of the offline Swift runners; no Swift tool or subprocess runs."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
import offline_harness as harness  # noqa: E402

spec = importlib.util.spec_from_file_location('test_offline', SCRIPT / 'test-offline.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def no_subprocess(*args, **kwargs):
    raise AssertionError(f'unexpected subprocess: {args}')


class NoSubprocessTest(unittest.TestCase):
    def setUp(self):
        patcher = patch('subprocess.run', side_effect=no_subprocess)
        patcher.start()
        self.addCleanup(patcher.stop)


class ToolchainTest(NoSubprocessTest):
    def test_package_identity_follows_the_checkout_directory_name(self):
        # SwiftPM derives a path dependency's identity from the directory name, lowercased.
        self.assertEqual(harness.path_package_identity('/Users/me/Developer/AquaSpace'), 'aquaspace')
        self.assertEqual(harness.path_package_identity('/w/t3-521add7d-38d06e3039427424'), 't3-521add7d-38d06e3039427424')
        self.assertEqual(harness.path_package_identity('/src/aerospace'), 'aerospace')
        self.assertEqual(harness.path_package_identity('/src/Mirror.git'), 'mirror')

    def test_testing_plugin_comes_from_the_active_toolchain(self):
        info = {'paths': {'runtimeResourcePath': '/Toolchain/usr/lib/swift'}}
        plugin = harness.testing_plugin_path(info, exists=lambda path: True)
        self.assertEqual(plugin, Path('/Toolchain/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib'))

    def test_missing_testing_plugin_is_one_clear_error(self):
        info = {'paths': {'runtimeResourcePath': '/Toolchain/usr/lib/swift'}}
        with self.assertRaises(harness.HarnessError) as raised:
            harness.testing_plugin_path(info, exists=lambda path: False)
        self.assertIn('/Toolchain/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib', str(raised.exception))
        self.assertIn('Swift Testing', str(raised.exception))
        with self.assertRaises(harness.HarnessError):
            harness.testing_plugin_path({'paths': {}})

    def test_swift_falls_back_to_xcrun_and_reports_absence(self):
        found = types.SimpleNamespace(stdout='/Library/Developer/CommandLineTools/usr/bin/swift\n')
        with patch.object(harness.shutil, 'which', return_value=None), \
                patch.object(harness.subprocess, 'run', return_value=found) as run:
            self.assertEqual(harness.find_swift(), Path('/Library/Developer/CommandLineTools/usr/bin/swift'))
        self.assertEqual(run.call_args.args[0], ['xcrun', '--find', 'swift'])
        with patch.object(harness.shutil, 'which', return_value=None), \
                patch.object(harness.subprocess, 'run', side_effect=OSError):
            with self.assertRaises(harness.HarnessError):
                harness.find_swift()


SAMPLE = '''import AppKit
import Common

/// The doc comment belongs to the struct.
@MainActor
struct Session: Codable {
    let name = "a { brace in a string"
    // A } brace in a comment.
    func label() -> String { "\\(name.map { $0 }) }" }
}

/* A block comment that mentions func helper( stays out. */
@MainActor
func helper(
    _ value: Int
) -> Int {
    let initialization = value
    print("{")
    return value
}

private func tail() {}
'''


class SwiftSourceTest(NoSubprocessTest):
    def setUp(self):
        super().setUp()
        self.source = harness.SwiftSource('Sources/Sample.swift', SAMPLE)

    def test_declaration_includes_its_attributes_and_ignores_braces_in_strings_and_comments(self):
        session = self.source.decl('struct Session:')
        self.assertTrue(session.startswith('/// The doc comment belongs to the struct.\n@MainActor\nstruct Session'))
        self.assertTrue(session.endswith('"\\(name.map { $0 }) }" }\n}\n'))

    def test_signature_matches_wrapped_or_reindented_code_but_not_comments(self):
        # The block comment mentions "func helper(" too, but only code matches. A comment
        # directly above a declaration belongs to it.
        helper = self.source.decl('func helper( _ value: Int )')
        self.assertTrue(helper.startswith('/* A block comment that mentions func helper( stays out. */\n@MainActor\nfunc helper('))
        self.assertTrue(helper.endswith('    return value\n}\n'))

    def test_ranges_between_declarations(self):
        self.assertTrue(self.source.between('func helper(').endswith('private func tail() {}\n'))
        self.assertTrue(self.source.between('struct Session:', 'func helper(').endswith('}) }" }\n}\n\n'))
        self.assertTrue(self.source.before('struct Session:').endswith('import Common\n\n'))
        self.assertNotIn('func helper', self.source.without('func helper('))
        self.assertIn('private func tail', self.source.without('func helper('))
        self.assertEqual(self.source.statements('let initialization', 'return value'),
                         '    let initialization = value\n    print("{")\n')
        self.assertEqual(self.source.body_until('func helper(', 'print('), '\n    let initialization = value\n')

    def test_missing_or_repeated_declaration_names_the_file_and_the_signature(self):
        for signature, count in (('func renamed(', 0), ('func', 3)):
            with self.subTest(signature=signature), self.assertRaises(harness.HarnessError) as raised:
                self.source.decl(signature)
            message = str(raised.exception)
            self.assertIn('Sources/Sample.swift', message)
            self.assertIn(f'"{signature}"', message)
            self.assertIn(f'found {count}', message)
        with self.assertRaises(harness.HarnessError) as raised:
            self.source.body_until('func tail(', 'return value')
        self.assertIn('"return value"', str(raised.exception))

    def test_imports_are_retargeted(self):
        text = harness.retarget_imports(SAMPLE, drop=('AppKit', 'Common'), add=('Foundation',))
        self.assertTrue(text.startswith('import Foundation\n\n/// The doc comment'))


class PackageTest(NoSubprocessTest):
    def test_unchanged_files_are_kept_and_stale_sources_are_pruned(self):
        with tempfile.TemporaryDirectory(prefix='offline-harness-') as directory:
            first = harness.OfflinePackage(directory)
            kept = first.write('Sources/Lib/Kept.swift', 'let a = 1\n')
            first.write('Sources/Old/Stale.swift', 'let b = 2\n')
            os.utime(kept, (1, 1))
            second = harness.OfflinePackage(directory)
            second.write('Sources/Lib/Kept.swift', 'let a = 1\n')
            second.write('Package.swift', '// manifest\n')
            second.prune()
            self.assertEqual(kept.stat().st_mtime, 1)
            self.assertFalse((Path(directory) / 'Sources/Old').exists())
            self.assertTrue((Path(directory) / 'Package.swift').exists())


class EntryPointTest(NoSubprocessTest):
    def test_test_counts_come_from_swift_testing_or_unittest_summaries(self):
        self.assertEqual(harness.tests_run('✔ Test run with 39 tests in 5 suites passed after 0.006 seconds.'), 39)
        self.assertEqual(harness.tests_run('✘ Test run with 1 test in 1 suite failed after 0.1 seconds with 1 issue.'), 1)
        self.assertEqual(harness.tests_run('...\nRan 321 tests in 1.478s\n\nOK\n'), 321)
        self.assertIsNone(harness.tests_run('error: Build failed'))

    def test_swift_runners_share_the_scratch_root_and_sdk(self):
        args = types.SimpleNamespace(scratch_root=Path('/scratch'), jobs=2, sdk=Path('/SDK'))
        command = entry.command('window-discovery', args)
        self.assertEqual(command[1], str(SCRIPT / 'test-window-discovery-offline.py'))
        self.assertEqual(command[2:], ['--scratch-root', '/scratch', '--jobs', '2', '--sdk', '/SDK'])
        self.assertEqual(entry.command('python', args)[-3:], ['discover', '-s', str(SCRIPT / 'tests')])

    def test_any_failed_suite_fails_the_run_and_appears_in_the_table(self):
        def fake_run(suite, args, logs):
            failed = suite == 'hidden-handoff'
            return dict(suite=suite, status=1 if failed else 0, tests=None if failed else 3,
                        seconds=1.0, log=logs / f'{suite}.log', text='error: Build failed\n' if failed else '')
        with tempfile.TemporaryDirectory(prefix='offline-entry-') as directory, \
                patch.object(entry, 'run', side_effect=fake_run), \
                patch.object(sys, 'argv', ['test-offline.py', '--scratch-root', directory]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entry.main(), 1)
        text = output.getvalue()
        self.assertRegex(text, r'hidden-handoff\s+-\s+FAIL \(1\)')
        self.assertRegex(text, r'ownership\s+3\s+pass')
        self.assertIn('1 of 6 suites failed: hidden-handoff', text)


if __name__ == '__main__':
    unittest.main()
