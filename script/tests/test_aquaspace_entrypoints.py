"""Exercise the shell wrapper using a fake Python executable: no builds/apps."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aquaspace-wrapper-offline-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.record = self.root / 'arguments'
        shim = self.root / 'python3'
        shim.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$AQUASPACE_OFFLINE_RECORD"\n')
        shim.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.root) + ':/usr/bin:/bin',
                        AQUASPACE_OFFLINE_RECORD=str(self.record))
        self.wrapper = Path(__file__).resolve().parents[2] / 'install-from-sources.sh'

    def invoke(self, *args):
        result = subprocess.run(['/bin/bash', str(self.wrapper), *args],
                                env=self.env, capture_output=True, text=True)
        return result, self.record.read_text().splitlines() if self.record.exists() else None

    def test_default_build_and_update_forwards_an_empty_argument_array(self):
        result, args = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/build-aquaspace.py', '--install-and-restart'])

    def test_existing_build_uses_only_the_new_installer(self):
        result, args = self.invoke('--dont-rebuild')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/install-aquaspace.py', '--restart'])

    def test_sdk_path_with_spaces_is_one_argument(self):
        result, args = self.invoke('--sdk', 'a folder with spaces/SDK')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/build-aquaspace.py', '--install-and-restart',
                                '--sdk', 'a folder with spaces/SDK'])

    def test_installer_options_pass_through_with_an_existing_build(self):
        result, args = self.invoke('--dont-rebuild', '--allow-identity-change', '--ready-timeout', '120')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/install-aquaspace.py', '--restart',
                                '--allow-identity-change', '--ready-timeout', '120'])

    def test_installer_options_with_a_rebuild_go_to_the_builder(self):
        result, args = self.invoke('--sdk', 'SDK', '--ready-timeout', '120', '--jobs', '1', '--allow-identity-change')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/build-aquaspace.py', '--install-and-restart', '--sdk', 'SDK', '--jobs', '1',
                                '--ready-timeout', '120', '--allow-identity-change'])

    def test_ready_timeout_without_a_value_fails_without_invoking_anything(self):
        for options in (['--ready-timeout'], ['--dont-rebuild', '--ready-timeout']):
            with self.subTest(options=options):
                result, args = self.invoke(*options)
                self.assertEqual(result.returncode, 2)
                self.assertIsNone(args)

    def test_build_options_with_dont_rebuild_fail_without_invoking_anything(self):
        result, args = self.invoke('--dont-rebuild', '--sdk', 'unused')
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(args)

    def test_release_entrypoint_builds_without_install_or_restart(self):
        self.wrapper = self.wrapper.with_name('build-release.sh')
        result, args = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/build-aquaspace.py'])

    def test_release_version_and_sdk_with_spaces_are_forwarded(self):
        self.wrapper = self.wrapper.with_name('build-release.sh')
        result, args = self.invoke('--build-version', 'local-test', '--sdk', 'folder with spaces/SDK')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['script/build-aquaspace.py', '--version', 'local-test', '--sdk', 'folder with spaces/SDK'])


class InstallerLiveHarnessOptInTests(unittest.TestCase):
    """script/installer-smoke/live.py does nothing at all without --run-live."""

    def test_harness_stops_before_any_action_without_its_opt_in_flag(self):
        spec = importlib.util.spec_from_file_location('installer_live', ROOT / 'script/installer-smoke/live.py')
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)  # Defines functions only.
        forbidden = AssertionError('The live harness acted without --run-live')
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(live, 'ROOT', Path(temporary)), \
                patch.object(live.update, 'NativeSystem', side_effect=forbidden), \
                patch.object(live.update, 'DarwinProcesses', side_effect=forbidden), \
                patch.object(live.update, 'Installer', side_effect=forbidden), \
                patch.object(live.update, 'InstallLock', side_effect=forbidden), \
                patch.object(live.subprocess, 'run', side_effect=forbidden), \
                patch.object(live.subprocess, 'Popen', side_effect=forbidden):
            for argv in ([], ['--run'], ['--run-live=yes']):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        live.main(argv)
                    self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(os.listdir(temporary), [])


if __name__ == '__main__': unittest.main()
