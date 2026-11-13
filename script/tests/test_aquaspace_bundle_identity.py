"""A bundle is the recorded one only if its main executable, its seal and its signer match.

Two bundles can share the main executable and differ in other files; only the code
signature binds those files to the executable. Offline only: a fake seal stands in
for codesign, with fake process APIs and temporary application bundles.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import tarfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_update as base
from test_aquaspace_update import FakeSystem, InstallerFixture, bundle, phase_of


def setUpModule(): base.setUpModule()
def tearDownModule(): base.tearDownModule()


SEAL = 'Contents/_CodeSignature/CodeResources'


def seal_digest(app):
    """A digest of every file in the bundle except the seal, as CodeResources seals them."""
    digest = hashlib.sha256()
    for path in sorted(app.rglob('*')):
        relative = str(path.relative_to(app))
        if relative.startswith('Contents/_CodeSignature'): continue
        digest.update(relative.encode() + b'\0')
        if path.is_symlink(): digest.update(b'link:' + os.readlink(path).encode())
        elif path.is_file(): digest.update(b'file:' + path.read_bytes())
    return digest.hexdigest()


def sign(app, requirement=base.LOCAL_IDENTITY):
    """Sign a fake bundle: set its designated requirement, then seal every file."""
    info = app / 'Contents/Info.plist'
    info.write_bytes(plistlib.dumps(dict(plistlib.loads(info.read_bytes()), FakeRequirement=requirement)))
    (app / SEAL).parent.mkdir(exist_ok=True)
    (app / SEAL).write_text(seal_digest(app))
    return app


class SealingSystem(FakeSystem):
    """verify_bundle also fails when a sealed file changed, as codesign --verify does."""
    def verify_bundle(self, path):
        requirement = super().verify_bundle(path)
        try: sealed = (path / SEAL).read_text()
        except OSError: sealed = None
        if sealed != seal_digest(path):
            raise update.UpdateError(f'Simulated invalid signature: a sealed file of {path} changed')
        return requirement


OTHER_IDENTITY = 'identifier "local.aquaspace" and certificate leaf = H"BBBB"'


class SealedFixture(InstallerFixture):
    def setUp(self):
        super().setUp()
        sign(self.source); sign(self.target)
        self.system = SealingSystem(self.target)
        self.installer = update.Installer(self.source, self.target, self.system, timeout=.3)
        self.installer.report = self.reports.append

    def change_cli(self, app, text='changed by hand'):
        """Change a sealed file and keep the main executable."""
        (app / 'Contents/MacOS/aquaspace-cli').write_text(text)

    def crashed_at(self, phase):
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, phase): self.activate()
        self.system.running.clear()
        return self.pending()[0]


class BundleIdentityTests(SealedFixture):
    def test_recover_refuses_a_target_changed_by_hand_with_the_same_main_executable(self):
        record = self.crashed_at('stopped')  # The target still holds the previous bundle.
        self.change_cli(self.target)
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn('does not hold the previous AquaSpace', str(caught.exception))
        self.assertEqual(phase_of(record), 'stopped')

    def test_confirm_refuses_a_target_whose_other_files_changed(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        self.system.status_fault = None
        record = self.pending()[0]
        self.change_cli(self.target)
        with self.assertRaises(update.UpdateError) as caught: self.installer.confirm(record)
        self.assertIn('not the bundle from this update', str(caught.exception))
        self.assertEqual(phase_of(record), 'child_started')

    def test_previous_bundle_signed_by_another_identity_is_not_restored(self):
        record = self.crashed_at('replaced')
        self.assertEqual(json.loads((record / 'journal.json').read_text())['old_requirement'], base.LOCAL_IDENTITY)
        sign(record / 'previous.app', OTHER_IDENTITY)  # Same main executable, valid seal, another signer.
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn('another identity', str(caught.exception))
        self.assertEqual(phase_of(record), 'replaced')
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')

    def test_new_bundle_signed_by_another_identity_is_not_installed_by_resume(self):
        record = self.crashed_at('stopped')
        sign(record / 'new.app', OTHER_IDENTITY)
        with self.assertRaises(update.UpdateError) as caught: self.installer.resume(record)
        self.assertIn('another identity', str(caught.exception))
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertEqual(self.system.launches, [])

    def test_terminal_record_keeps_a_bundle_that_only_shares_the_main_executable(self):
        record = self.installer.install()
        other = shutil.copytree(self.source, record / 'new.app', symlinks=True)
        self.change_cli(other, 'cli-of-another-build')
        self.installer.archive_completed_backups()
        self.assertFalse(other.exists())
        with tarfile.open(record / 'new.app.tar.gz') as archive:
            self.assertEqual(archive.extractfile('new.app/Contents/MacOS/aquaspace-cli').read(), b'cli-of-another-build')

    def test_terminal_record_still_removes_its_own_unused_staged_copy(self):
        record = self.installer.install()
        shutil.copytree(self.source, record / 'new.app', symlinks=True)
        self.installer.archive_completed_backups()
        self.assertFalse((record / 'new.app').exists())
        self.assertFalse((record / 'new.app.tar.gz').exists())


class SameMainExecutableTests(SealedFixture):
    """Records written before 2026-11 name their bundles by content only."""

    def legacy_record(self, target_cli=None):
        """An exchange left the previous bundle in new.app; it has the same main executable as the new one."""
        record = self.target.parent / '.aquaspace-update-legacy'
        record.mkdir()
        shutil.copytree(self.source, record / 'new.app', symlinks=True)
        (record / 'new.app/Contents/MacOS/aquaspace-cli').write_text('cli-previous')
        sign(record / 'new.app')
        shutil.rmtree(self.target)
        shutil.copytree(self.source, self.target, symlinks=True)
        if target_cli: self.change_cli(self.target, target_cli)
        state = dict(schema=1, target=str(self.target), version='new', had_target=True, old=None, phase='replacing',
                     restart=False, main_sha256=update.sha256(self.source / 'Contents/MacOS/AquaSpace'))
        (record / 'journal.json').write_text(json.dumps(state))
        return record

    def test_two_bundles_with_the_same_main_executable_are_not_told_apart_by_a_guess(self):
        for mode in ('recover', 'resume'):
            with self.subTest(mode=mode):
                self.setUp()
                record = self.legacy_record(target_cli='changed by hand')
                def layout():
                    return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                            for top in (record, self.target) for p in top.rglob('*')}
                before = layout()
                with self.assertRaises(update.UpdateError) as caught: getattr(self.installer, mode)(record)
                self.assertIn('same main executable', str(caught.exception))
                self.assertEqual(layout(), before)
                self.assertEqual(phase_of(record), 'replacing')

    def test_two_valid_bundles_with_the_same_main_executable_still_recover(self):
        # Both seals verify, so both bundles hold exactly their signed content.
        record = self.legacy_record()
        self.installer.recover(record)
        self.assertEqual(phase_of(record), 'recovered')


if __name__ == '__main__': unittest.main()
