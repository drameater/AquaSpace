"""Records that the updater cannot use must not block every install with no way out.

Offline only: fake process/kernel APIs and temporary application bundles.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_update as base
from test_aquaspace_update import InstallerFixture, bundle, phase_of


def setUpModule(): base.setUpModule()
def tearDownModule(): base.tearDownModule()


class BlockingRecordFixture(InstallerFixture):
    def age(self, path, seconds=update.LAUNCH_SETTLE_SECONDS + 5):
        """Make every entry of a record older than the fake clock's present by seconds."""
        moment = self.system.time() - seconds
        entries = [path] + (list(path.iterdir()) if path.is_dir() and not path.is_symlink() else [])
        for entry in entries:
            os.utime(entry, (moment, moment), follow_symlinks=False)
        return path

    def corrupt_record(self, name='.aquaspace-update-broken', with_bundle=False):
        record = self.target.parent / name
        record.mkdir()
        (record / 'journal.json').write_text('{broken')
        if with_bundle: bundle(record / 'previous.app', 'precious')
        return self.age(record)

    def contents(self, path):
        if path.is_symlink(): return {'.': ('link', os.readlink(path))}
        if path.is_file(): return {'.': ('file', path.read_text())}
        return {str(p.relative_to(path)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in path.rglob('*')}

    def set_aside_command(self, message):
        lines = [shlex.split(line.strip()) for line in message.splitlines() if '--set-aside' in line]
        self.assertTrue(lines, f'--set-aside is not named in: {message}')
        argv = lines[0]
        return Path(argv[argv.index('--set-aside') + 1])


class SetAsideTests(BlockingRecordFixture):
    def test_unreadable_records_block_installs_and_name_a_way_out_that_deletes_nothing(self):
        cases = {
            'corrupt journal': lambda: self.corrupt_record(with_bundle=True),
            'symlinked journal': self.symlinked_journal,
            'record that is a file': self.file_record,
            'record that is a symlink': self.symlinked_record,
        }
        for name, make in cases.items():
            with self.subTest(case=name):
                self.setUp()
                record = make()
                before = self.contents(record)
                self.system.add(100)
                with self.assertRaises(update.UpdateError) as caught: self.activate()
                self.assertNotIn('stop', self.system.events)
                named = self.set_aside_command(str(caught.exception))
                self.assertEqual(named, record)
                self.installer.set_aside(named)
                self.assertFalse(os.path.lexists(record))
                aside, = [p for p in self.target.parent.iterdir() if p.name.startswith('.aquaspace-set-aside-')]
                self.assertEqual(self.contents(aside), before)
                self.assertIn(str(aside), self.installer.summary)
                self.activate()
                self.assertEqual(phase_of(self.pending_or_last()), 'committed')
                self.assertEqual(self.contents(aside), before)

    def pending_or_last(self):
        return sorted(self.target.parent.glob('.aquaspace-update-*'), key=lambda p: (p / 'journal.json').stat().st_mtime)[-1]

    def symlinked_journal(self):
        record = self.target.parent / '.aquaspace-update-linked-journal'
        record.mkdir()
        elsewhere = self.root / 'elsewhere.json'
        elsewhere.write_text(json.dumps(dict(schema=1, target=str(self.target), phase='committed')))
        (record / 'journal.json').symlink_to(elsewhere)
        return self.age(record)

    def file_record(self):
        record = self.target.parent / '.aquaspace-update-file'
        record.write_text('not a record')
        return self.age(record)

    def symlinked_record(self):
        elsewhere = self.root / 'elsewhere-record'
        elsewhere.mkdir()
        (elsewhere / 'journal.json').write_text('{}')
        record = self.target.parent / '.aquaspace-update-symlinked'
        record.symlink_to(elsewhere)
        return self.age(record)

    def test_readable_record_is_never_set_aside(self):
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, 'replaced'): self.activate()
        record = self.age(self.pending()[0])
        with self.assertRaises(update.UpdateError) as caught: self.installer.set_aside(record)
        self.assertIn('--recover', str(caught.exception))
        self.assertTrue((record / 'journal.json').is_file())

    def test_recently_changed_record_is_not_set_aside(self):
        record = self.corrupt_record()
        self.age(record, seconds=1)
        with self.assertRaises(update.UpdateError) as caught: self.installer.set_aside(record)
        self.assertIn('Wait', str(caught.exception))
        self.assertTrue(record.is_dir())

    def test_record_that_may_hold_the_only_bundle_is_not_set_aside_while_the_target_is_damaged(self):
        record = self.corrupt_record(with_bundle=True)
        shutil.rmtree(self.target)
        with self.assertRaises(update.UpdateError) as caught: self.installer.set_aside(record)
        self.assertIn('previous.app', str(caught.exception))
        self.assertTrue((record / 'previous.app').is_dir())

    def test_record_without_a_bundle_is_set_aside_even_without_a_target(self):
        record = self.corrupt_record()
        shutil.rmtree(self.target)
        self.installer.set_aside(record)
        self.assertFalse(record.exists())

    def test_resume_of_an_unreadable_record_names_the_set_aside_command(self):
        record = self.corrupt_record()
        for mode in ('resume', 'confirm', 'recover'):
            with self.subTest(mode=mode):
                with self.assertRaises(update.UpdateError) as caught: getattr(self.installer, mode)(record)
                self.assertEqual(self.set_aside_command(str(caught.exception)), record)

    def test_set_aside_from_the_command_line(self):
        record = self.corrupt_record()
        err = io.StringIO()
        with patch.object(update, 'NativeSystem', return_value=self.system), \
             patch.object(update, 'USER_STATE_DIRECTORY', self.root / 'state'), \
             patch.object(update, 'LEGACY_LOCK_DIRECTORIES', ()), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = update.main(['--target', str(self.target), '--set-aside', str(record)])
        self.assertEqual(code, 0, err.getvalue())
        self.assertFalse(record.exists())
        self.assertEqual(self.system.events, [])


class NewerRecordTests(BlockingRecordFixture):
    """A record that a newer updater wrote is not damaged: no command hides or changes it."""

    def newer_record(self, **journal):
        """A pending record whose new.app holds the previous AquaSpace, as an interrupted exchange leaves it."""
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, 'exchanged'): self.activate()
        record = self.pending()[0]
        self.assertEqual((record / 'new.app/Contents/MacOS/AquaSpace').read_text(), 'old')
        state = json.loads((record / 'journal.json').read_text())
        state.update(journal)
        (record / 'journal.json').write_text(json.dumps(state))
        self.system.running.clear()
        self.system.events.clear()
        return self.age(record)

    CASES = {'newer schema': dict(schema=2), 'unknown phase': dict(phase='swapping'),
             'newer schema and unknown fields': dict(schema=7, phase='verifying', target=['moved'])}

    def test_every_command_refuses_a_newer_record_and_never_suggests_setting_it_aside(self):
        commands = {
            'install': lambda record: self.installer.install(),
            'install --restart': lambda record: self.installer.install(restart=True),
            '--resume': lambda record: self.installer.resume(record),
            '--confirm': lambda record: self.installer.confirm(record),
            '--recover': lambda record: self.installer.recover(record),
            '--set-aside': lambda record: self.installer.set_aside(record),
        }
        for case, journal in self.CASES.items():
            for name, command in commands.items():
                with self.subTest(case=case, command=name):
                    self.setUp()
                    record = self.newer_record(**journal)
                    before, installed = self.contents(record), self.contents(self.target)
                    with self.assertRaises(update.UpdateError) as caught: command(record)
                    message = str(caught.exception)
                    self.assertIn('newer AquaSpace updater', message)
                    self.assertIn('the updater version that wrote it', message)
                    if name != '--set-aside':
                        self.assertNotIn('--set-aside', message)
                    self.assertEqual(self.contents(record), before)
                    self.assertEqual(self.contents(self.target), installed)
                    self.assertNotIn('stop', self.system.events)
                    self.assertNotIn('launch', self.system.events)

    def test_tidying_leaves_a_newer_record_alone(self):
        record = self.newer_record(schema=2)
        before = self.contents(record)
        self.installer.archive_completed_backups()
        self.assertEqual(self.contents(record), before)

    def test_set_aside_of_a_newer_record_needs_the_explicit_flag_and_names_its_bundles(self):
        record = self.newer_record(schema=2)
        aside = self.installer.set_aside(record, allow_newer=True)
        self.assertFalse(os.path.lexists(record))
        self.assertEqual((aside / 'new.app/Contents/MacOS/AquaSpace').read_text(), 'old')
        summary = self.installer.summary
        self.assertIn('new.app', summary)
        self.assertIn('previous version', summary)
        self.assertIn(f'stay in {aside}', summary)

    def test_command_line_flag_reaches_set_aside_and_is_refused_elsewhere(self):
        record = self.newer_record(schema=2)
        def main(*args):
            err = io.StringIO()
            with patch.object(update, 'NativeSystem', return_value=self.system), \
                 patch.object(update, 'USER_STATE_DIRECTORY', self.root / 'state'), \
                 patch.object(update, 'LEGACY_LOCK_DIRECTORIES', ()), \
                 contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                try: return update.main(['--target', str(self.target), *map(str, args)]), err.getvalue()
                except SystemExit as exit: return exit.code, err.getvalue()
        self.assertEqual(main('--set-aside', record)[0], 1)
        self.assertTrue(record.is_dir())
        self.assertEqual(main('--resume', record, '--allow-newer-record')[0], 2)
        self.assertEqual(main('--set-aside', record, '--allow-newer-record'), (0, ''))
        self.assertFalse(os.path.lexists(record))

    def test_damaged_journal_is_still_called_unreadable(self):
        for journal in (dict(schema=0), dict(schema='2'), dict(schema=True), dict(phase=3)):
            with self.subTest(journal=journal):
                self.setUp()
                record = self.newer_record(**journal)
                with self.assertRaises(update.UpdateError) as caught: self.installer.install()
                self.assertNotIn('newer', str(caught.exception))
                self.assertEqual(self.set_aside_command(str(caught.exception)), record)


class OtherTargetTests(BlockingRecordFixture):
    """A record for another --target in the same directory belongs to that installation."""

    def other_installer(self):
        other_target = bundle(self.target.parent / 'AquaSpace Dev.app', 'other-old')
        other = update.Installer(self.source, other_target, base.FakeSystem(other_target), timeout=.3)
        other.report = lambda text: None
        return other

    def test_complete_record_of_another_target_does_not_block(self):
        other = self.other_installer()
        record = other.install()
        self.installer.install()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertEqual(phase_of(record), 'committed')

    def test_pending_record_of_another_target_names_commands_for_that_target(self):
        other = self.other_installer()
        with base.hard_crash(other, other.system, 'replaced'): other.install()
        record, = other.pending_transactions()
        with self.assertRaises(update.UpdateError) as caught: self.installer.install()
        commands = [shlex.split(line.strip()) for line in str(caught.exception).splitlines()
                    if str(update.INSTALLER_SCRIPT) in line]
        self.assertTrue(commands)
        for argv in commands:
            self.assertEqual(argv[argv.index('--target') + 1], str(other.target))
        other.recover(record)
        self.installer.install()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')


if __name__ == '__main__': unittest.main()
