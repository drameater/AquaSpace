"""The builder's real output functions feed the installer's real verification.

Offline only: fake swift, git, macOS tools and signer; fake process APIs; temporary directories.
"""
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_build as builder_tests
from test_aquaspace_update import FakeSystem, phase_of


class SignedBuildSystem(FakeSystem):
    """Reports the fake signer's requirement for a bundle that the fake signer sealed."""
    def requirement(self, path):
        sealed = (Path(path) / 'Contents/_CodeSignature/CodeResources').is_file()
        return builder_tests.REQUIREMENT if sealed else None


class BuildThenInstallTests(builder_tests.BuilderTest):
    def setUp(self):
        super().setUp()
        # The builder's publish may call renamex_np on these temporary directories; nothing else is native.
        for name in ('subprocess.Popen', 'os.kill'):
            guard = patch(name, side_effect=AssertionError('Live API forbidden in offline tests: ' + name))
            guard.start(); self.addCleanup(guard.stop)
        # The kernel reports canonical paths (/private/var, not /var), so the fake does too.
        self.target = self.root.resolve() / 'Applications/AquaSpace.app'
        self.target.parent.mkdir()
        self.system = SignedBuildSystem(self.target)
        self.installer = update.Installer(self.app, self.target, self.system, timeout=.3)
        self.installer.report = lambda text: None

    def test_first_build_installs_and_matches_its_manifest(self):
        self.main()
        record = self.installer.install()
        self.assertEqual(phase_of(record), 'committed')
        manifest = json.loads((self.output / 'build.json').read_text())
        self.assertEqual(update.sha256(self.target / 'Contents/MacOS/AquaSpace'), manifest['app_sha256'])
        self.assertEqual(update.sha256(self.target / 'Contents/MacOS/aquaspace-cli'), manifest['cli_sha256'])
        self.assertEqual(json.loads((record / 'build.json').read_text()), manifest)
        self.assertEqual(self.system.launches, [])

    def test_rebuild_replaces_the_running_installation_once(self):
        self.main()
        self.installer.install()
        self.tools.dirty = True  # The next build has a different version and binaries.
        self.main()
        self.system.add(100)
        record = self.installer.install(restart=True)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.running.keys(), {200})
        with tarfile.open(record / 'previous.app.tar.gz') as backup:
            self.assertIn('previous.app/Contents/Resources/AppIcon.icns', backup.getnames())

    def test_manifest_that_does_not_match_the_build_is_refused_before_anything_changes(self):
        self.main()
        manifest = json.loads((self.output / 'build.json').read_text())
        manifest['app_sha256'] = '0' * 64
        (self.output / 'build.json').write_text(json.dumps(manifest))
        with self.assertRaises(update.UpdateError) as caught: self.installer.install()
        self.assertIn('does not match its build manifest', str(caught.exception))
        self.assertIn('Build again', str(caught.exception))
        self.assertFalse(self.target.exists())


if __name__ == '__main__': unittest.main()
