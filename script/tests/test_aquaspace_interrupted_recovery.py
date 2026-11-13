"""An interrupted --recover: every later command knows that a recovery began.

Offline only: fake process/kernel APIs and temporary application bundles.
"""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_update as base
from test_aquaspace_confirm_resume import GuidanceFixture
from test_aquaspace_update import phase_of


def setUpModule(): base.setUpModule()
def tearDownModule(): base.tearDownModule()


# The phases in which the new bundle is in place, so --recover moves the previous one back.
INSTALLED_PHASES = ('replaced', 'launch_attempted', 'child_started')
# Every boundary between two disk changes of --recover, on a volume with and without an
# atomic exchange. 'renamed:<name>' follows each rename of a bundle to <name>.
RECOVER_POINTS = {
    'exchange': ('recovering', 'exchange', 'exchanged', 'renamed:unconfirmed.app', 'recovered'),
    'renames': ('recovering', 'exchange', 'renamed:unconfirmed.app', 'renamed:AquaSpace.app', 'recovered'),
}
NEW_APP_CLAIM = 'new AquaSpace from this update'


class RecoveryFixture(GuidanceFixture):
    def installed_update(self, phase):
        """An update of the running AquaSpace that stopped in this phase, with no manager running."""
        if phase == 'child_started':
            record = self.awaiting()
        else:
            self.system.add(100)
            with base.hard_crash(self.installer, self.system, phase): self.activate()
            record = self.pending()[0]
        self.assertEqual(phase_of(record), phase)
        self.system.running.clear()
        self.system.now += update.LAUNCH_SETTLE_SECONDS
        return record

    @contextlib.contextmanager
    def rename_events(self):
        real = update.replace_directory
        def rename(source, target):
            real(source, target)
            self.system.event('renamed:' + target.name)
        with patch.object(update, 'replace_directory', rename):
            yield

    def installed(self):
        return (self.target / 'Contents/MacOS/AquaSpace').read_text() if self.target.is_dir() else None

    def open_installed_app(self):
        """The user opens the target, whatever it holds now."""
        self.system.now += 60
        return self.system.add(300, birth=int(self.system.time()))

    def assert_names_only_recover(self, text, record):
        named = self.named_commands(text)
        self.assertTrue(named, f'No command is named in: {text}')
        self.assertEqual(set(named), {('recover', record)}, text)
        self.assertNotIn(NEW_APP_CLAIM, text)

    def assert_recovered(self, record):
        self.assertEqual(phase_of(record), 'recovered')
        self.assertEqual(self.installed(), 'old')
        self.assertEqual((record / 'unconfirmed.app/Contents/MacOS/AquaSpace').read_text(), 'new')


class InterruptedRecoverTests(RecoveryFixture):
    COMMANDS = ('install', 'resume', 'confirm', 'recover', 'set_aside')

    def interrupted_recover(self, phase, volume, point):
        record = self.installed_update(phase)
        self.system.exchange_unsupported = volume == 'renames'
        with self.rename_events(), base.hard_crash(self.installer, self.system, point):
            self.installer.recover(record)
        self.system.exchange_unsupported = False
        return record

    def run_command(self, name, record):
        if name == 'install': return self.installer.install()
        return getattr(self.installer, name)(record)

    def test_every_interrupt_of_recover_leaves_a_state_that_only_recover_continues(self):
        for phase in INSTALLED_PHASES:
            for volume, points in RECOVER_POINTS.items():
                for point in points:
                    for running in (False, True):
                        for command in self.COMMANDS:
                            with self.subTest(phase=phase, volume=volume, point=point, running=running, command=command):
                                self.setUp()
                                record = self.interrupted_recover(phase, volume, point)
                                if point == 'recovered':
                                    self.assert_recovered(record)
                                    continue
                                self.assertEqual(phase_of(record), 'recovering')
                                self.assertTrue(self.target.is_dir() or volume == 'renames')
                                if running: self.open_installed_app()
                                self.check_command(command, record, running)

    def check_command(self, command, record, running):
        launches, stops, installed = len(self.system.launches), self.system.events.count('stop'), self.installed()
        try:
            self.run_command(command, record)
        except update.UpdateError as error:
            message = str(error)
        else:
            self.assertEqual(command, 'recover', f'--{command} must refuse during a recovery')
            self.assertFalse(running, 'recover must refuse while a manager runs')
            self.assert_recovered(record)
            message = None
        if message is not None:
            self.assert_names_only_recover(message, record)
            self.assertEqual(self.installed(), installed, 'A refused command changed the installation')
            self.assertEqual(phase_of(record), 'recovering')
            # The user follows the message: quit every manager, then run the named command.
            self.system.running.clear()
            self.run_named(message, 'recover')
            self.assert_recovered(record)
        self.assertEqual(len(self.system.launches), launches)
        self.assertEqual(self.system.events.count('stop'), stops)

    def test_ctrl_c_during_recover_names_only_recover(self):
        record = self.installed_update('replaced')
        def interrupt(system, event):
            if event == 'exchanged': raise KeyboardInterrupt
        self.system.on_event = interrupt
        self.reports.clear()  # Only what --recover reports.
        with self.assertRaises(KeyboardInterrupt): self.installer.recover(record)
        self.system.on_event = None
        self.assertEqual(phase_of(record), 'recovering')
        self.assert_names_only_recover('\n'.join(self.reports), record)

    def test_command_line_ctrl_c_during_recover_exits_130_and_names_recover(self):
        record = self.installed_update('replaced')
        def interrupt(system, event):
            if event == 'exchanged': raise KeyboardInterrupt
        self.system.on_event = interrupt
        err = io.StringIO()
        with patch.object(update, 'NativeSystem', return_value=self.system), \
             patch.object(update, 'USER_STATE_DIRECTORY', self.root / 'state'), \
             patch.object(update, 'LEGACY_LOCK_DIRECTORIES', ()), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = update.main(['--target', str(self.target), '--recover', str(record)])
        self.assertEqual(code, update.EXIT_INTERRUPTED)
        self.assert_names_only_recover(err.getvalue(), record)

    def test_a_refused_recover_changes_nothing_so_resume_still_finishes_forward(self):
        # A recovery that stops at a check, before any file change, has not begun.
        record = self.installed_update('replaced')
        (record / 'previous.app/Contents/MacOS/AquaSpace').write_text('tampered')
        with self.assertRaises(update.UpdateError): self.installer.recover(record)
        self.assertEqual(phase_of(record), 'replaced')
        self.installer.resume(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.installed(), 'new')
        self.assertEqual(len(self.system.launches), 1)


class TargetWithoutTheNewBundleTests(RecoveryFixture):
    """A record whose phase says that the new bundle is in place, while the target holds
    another bundle. The updater before this fix left this state when its --recover was
    interrupted after the exchange."""

    def left_by_an_older_recover(self, phase):
        record = self.installed_update(phase)
        previous = record / 'previous.app'
        swap = record / 'swap.app'
        previous.rename(swap); self.target.rename(previous); swap.rename(self.target)
        self.assertEqual(self.installed(), 'old')
        return record

    def test_no_command_calls_the_previous_app_new_and_only_recover_continues(self):
        for phase in INSTALLED_PHASES:
            for running in (False, True):
                for command in ('install', 'resume', 'confirm', 'recover'):
                    with self.subTest(phase=phase, running=running, command=command):
                        self.setUp()
                        record = self.left_by_an_older_recover(phase)
                        if running: self.open_installed_app()
                        launches = len(self.system.launches)
                        try:
                            self.installer.install() if command == 'install' else getattr(self.installer, command)(record)
                        except update.UpdateError as error:
                            self.assert_names_only_recover(str(error), record)
                            self.assertEqual(phase_of(record), phase)
                            self.system.running.clear()
                            self.run_named(str(error), 'recover')
                        else:
                            self.assertEqual((command, running), ('recover', False))
                        self.assert_recovered(record)
                        self.assertEqual(len(self.system.launches), launches)

    def test_new_app_is_still_named_new_when_the_target_holds_it(self):
        record = self.installed_update('replaced')
        self.open_installed_app()
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn(NEW_APP_CLAIM, str(caught.exception))
        self.run_named(str(caught.exception), 'confirm')
        self.assertEqual(phase_of(record), 'committed')


if __name__ == '__main__': unittest.main()
