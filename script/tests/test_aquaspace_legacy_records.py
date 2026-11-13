"""Records written by updaters before 2026-11: no bundle inodes, no launch time.

Offline only: fake process/kernel APIs and temporary application bundles.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_update as base
from test_aquaspace_update import Crash, InstallerFixture, bundle, phase_of


def setUpModule(): base.setUpModule()
def tearDownModule(): base.tearDownModule()


# Where the old updater left each bundle at each phase. It renamed the target to
# previous.app, then new.app to the target; it never exchanged two paths.
LEGACY_LAYOUTS = {
    'before-renames': ('stopped', {'target': 'old', 'new.app': 'new'}),
    'between-renames': ('replacing', {'previous.app': 'old', 'new.app': 'new'}),
    'after-renames': ('replaced', {'previous.app': 'old', 'target': 'new'}),
    # The 2026-11 updater before this fix: an exchange, then a crash before the
    # rename to previous.app. new.app holds the OLD bundle.
    'exchanged-by-new-updater': ('replacing', {'new.app': 'old', 'target': 'new'}),
}

# Every boundary between two disk changes in --resume and --recover.
RESUME_CRASH_POINTS = ['upgraded', 'stopped', 'replacing', 'exchange', 'exchanged', 'replaced',
                       'launch_attempted', 'launched', 'child_started']
RECOVER_CRASH_POINTS = ['upgraded', 'exchange', 'exchanged', 'recovered']


class LegacyFixture(InstallerFixture):
    def legacy_record(self, layout, old_running=False):
        """Write a record exactly as the pre-2026-11 updater did, in the given layout."""
        phase, places = LEGACY_LAYOUTS[layout]
        record = self.target.parent / '.aquaspace-update-legacy'
        record.mkdir()
        shutil.rmtree(self.target)
        source = {'old': self.root / 'old-copy.app', 'new': self.source}
        bundle(source['old'], 'old')
        for name, content in places.items():
            path = self.target if name == 'target' else record / name
            shutil.copytree(source[content], path, symlinks=True)
        shutil.rmtree(source['old'])
        state = dict(schema=1, target=str(self.target), version='new', had_target=True,
                     old=dict(pid=100, started=[1, 100], uid=os.getuid(),
                              executable=str(self.target / 'Contents/MacOS/AquaSpace'), bundle_id='local.aquaspace'),
                     phase=phase, main_sha256=update.sha256(self.source / 'Contents/MacOS/AquaSpace'),
                     cli_sha256=update.sha256(self.source / 'Contents/MacOS/aquaspace-cli'))
        (record / 'journal.json').write_text(json.dumps(state))
        update.atomic_json(record / 'session.json', {'windows': [], 'workspaces': [], 'workspace': 'w',
                                                     'focusedWindow': ''})
        return record

    def copies(self, content):
        """Every place, including archives, that holds a bundle with this main executable."""
        found = []
        for path in self.root.rglob('AquaSpace'):
            if path.parent.name == 'MacOS' and path.is_file() and path.read_text() == content:
                found.append(path.parents[2])
        for archive in self.root.rglob('*.tar.gz'):
            with tarfile.open(archive) as stream:
                for member in stream.getmembers():
                    if member.name.endswith('Contents/MacOS/AquaSpace') and member.isfile():
                        if stream.extractfile(member).read() == content.encode():
                            found.append(archive)
        return found

    def assert_both_bundles_kept(self):
        self.assertTrue(self.copies('old'), 'The previous bundle was lost')
        self.assertTrue(self.copies('new'), 'The new bundle was lost')

    def installed(self):
        return (self.target / 'Contents/MacOS/AquaSpace').read_text() if self.target.is_dir() else None

    def crash(self, point, call):
        """Run call with a hard crash at point; return False if the point was never reached."""
        try:
            with base.hard_crash(self.installer, self.system, point): call()
        except AssertionError as error:
            if 'never reached' in str(error): return False
            raise
        return True


class LegacyBundleIdentityTests(LegacyFixture):
    def test_recover_after_an_exchange_on_a_legacy_record_restores_the_previous_bundle(self):
        # The reviewer's sequence: the old bundle sits in new.app after an exchange.
        record = self.legacy_record('exchanged-by-new-updater')
        self.installer.recover(record)
        self.assertEqual(self.installed(), 'old')
        self.assertEqual(phase_of(record), 'recovered')
        self.installer.archive_completed_backups()
        self.assert_both_bundles_kept()
        self.assertEqual(self.installed(), 'old')

    def test_resume_after_an_exchange_on_a_legacy_record_keeps_the_previous_bundle(self):
        record = self.legacy_record('exchanged-by-new-updater')
        self.installer.resume(record)
        self.assertEqual(self.installed(), 'new')
        self.assertEqual(phase_of(record), 'committed')
        self.installer.archive_completed_backups()
        with tarfile.open(record / 'previous.app.tar.gz') as backup:
            self.assertEqual(backup.extractfile('previous.app/Contents/MacOS/AquaSpace').read(), b'old')

    def test_every_resume_crash_on_a_legacy_record_keeps_both_bundles_and_recovers(self):
        for layout in LEGACY_LAYOUTS:
            for point in RESUME_CRASH_POINTS:
                for then in ('recover', 'resume'):
                    with self.subTest(layout=layout, point=point, then=then):
                        self.setUp()
                        record = self.legacy_record(layout)
                        if not self.crash(point, lambda: self.installer.resume(record)): continue
                        self.assert_both_bundles_kept()
                        self.system.running.clear()
                        self.system.now += update.LAUNCH_SETTLE_SECONDS
                        if phase_of(record) in update.TERMINAL_PHASES: continue
                        getattr(self.installer, then)(record)
                        self.assertEqual(self.installed(), 'old' if then == 'recover' else 'new')
                        self.installer.archive_completed_backups()
                        self.assert_both_bundles_kept()
                        self.assertLessEqual(len(self.system.launches), 2)
                        self.assertLessEqual(self.system.maximum_controllers, 1)

    def test_every_recover_crash_on_a_legacy_record_keeps_both_bundles_and_finishes(self):
        for layout in LEGACY_LAYOUTS:
            for point in RECOVER_CRASH_POINTS:
                with self.subTest(layout=layout, point=point):
                    self.setUp()
                    record = self.legacy_record(layout)
                    if not self.crash(point, lambda: self.installer.recover(record)): continue
                    self.assert_both_bundles_kept()
                    if phase_of(record) not in update.TERMINAL_PHASES:
                        self.installer.recover(record)
                    self.assertEqual(phase_of(record), 'recovered')
                    self.assertEqual(self.installed(), 'old')
                    self.installer.archive_completed_backups()
                    self.assert_both_bundles_kept()
                    self.assertNotIn('launch', self.system.events)

    def test_recover_never_reports_success_unless_the_target_holds_the_previous_bundle(self):
        # A record whose previous bundle was changed on disk after it was saved.
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, 'replaced'): self.activate()
        self.system.running.clear()
        record = self.pending()[0]
        (record / 'previous.app/Contents/MacOS/AquaSpace').write_text('tampered')
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn('previous', str(caught.exception))
        self.assertEqual(phase_of(record), 'replaced')
        self.assertEqual(self.installed(), 'new')

    def test_recover_refuses_success_when_the_target_was_changed_in_place(self):
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, 'stopped'): self.activate()
        self.system.running.clear()
        record = self.pending()[0]
        (self.target / 'Contents/MacOS/AquaSpace').write_text('changed by hand')
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn('does not hold the previous AquaSpace', str(caught.exception))
        self.assertEqual(phase_of(record), 'stopped')

    def test_recover_of_an_unfinished_first_install_says_that_nothing_is_installed(self):
        shutil.rmtree(self.target)
        with base.hard_crash(self.installer, self.system, 'replacing'): self.installer.install()
        record = self.pending()[0]
        self.installer.recover(record)
        self.assertFalse(self.target.exists())
        self.assertNotIn('stay installed', self.installer.summary)
        self.assertIn('No AquaSpace is installed', self.installer.summary)

    def test_terminal_record_never_deletes_a_bundle_other_than_its_staged_copy(self):
        # A legacy record that an earlier updater closed while new.app held the previous bundle.
        record = self.legacy_record('exchanged-by-new-updater')
        state = json.loads((record / 'journal.json').read_text())
        state['phase'] = 'recovered'
        (record / 'journal.json').write_text(json.dumps(state))
        self.installer.archive_completed_backups()
        self.assertTrue(self.copies('old'), 'The only copy of the previous bundle was deleted')


if __name__ == '__main__': unittest.main()
