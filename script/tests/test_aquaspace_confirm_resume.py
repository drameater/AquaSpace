"""--confirm, --resume and --recover after the state changed outside the updater.

Offline only: fake process/kernel APIs and temporary application bundles.
"""
import json
from pathlib import Path
import shlex
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aquaspace_update as update
import test_aquaspace_update as base
from test_aquaspace_update import InstallerFixture, phase_of


def setUpModule(): base.setUpModule()
def tearDownModule(): base.tearDownModule()


STOCK = Path('/Applications/AeroSpace.app/Contents/MacOS/AeroSpace')


class GuidanceFixture(InstallerFixture):
    def named_commands(self, text):
        """Each installer command in a message, as (mode, record)."""
        commands = []
        for line in text.splitlines():
            if str(update.INSTALLER_SCRIPT) not in line: continue
            argv = shlex.split(line.strip())
            self.assertEqual(argv[argv.index('--target') + 1], str(self.target))
            for mode in ('--resume', '--confirm', '--recover'):
                if mode in argv: commands.append((mode[2:], Path(argv[argv.index(mode) + 1])))
        return commands

    def run_named(self, text, mode):
        """Run the command of this mode that the message names; fail if it names none."""
        named = [record for name, record in self.named_commands(text) if name == mode]
        self.assertTrue(named, f'--{mode} is not named in: {text}')
        return getattr(self.installer, mode)(named[0])

    def awaiting(self):
        """An update whose child runs but waits for Accessibility permission."""
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        self.system.status_fault = None
        return self.pending()[0]


class ConfirmAdoptionTests(GuidanceFixture):
    """Finding 3: --confirm adopts the installed replacement when it runs as another instance."""

    def test_confirm_adopts_a_later_instance_of_the_installed_replacement(self):
        # For example, the user restarted the Mac and a login item opened AquaSpace.
        record = self.awaiting()
        self.system.running.pop(200)
        self.system.now += 100
        self.system.add(300, birth=int(self.system.time()))
        self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.running.keys(), {300})
        self.assertEqual(len(self.system.launches), 1)
        self.assertEqual(self.system.events.count('stop'), 1)

    def test_confirm_adopts_an_instance_started_after_files_were_replaced_but_before_any_launch(self):
        self.system.add(100)
        with base.hard_crash(self.installer, self.system, 'replaced'): self.activate()
        record = self.pending()[0]
        self.system.now += 5
        self.system.add(300, birth=int(self.system.time()))
        self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.launches, [])

    def test_instance_that_started_before_the_files_were_replaced_is_never_adopted(self):
        record = self.awaiting()
        self.system.running.pop(200)
        self.system.add(300, birth=1)  # It may still run the previous executable.
        with self.assertRaises(update.UpdateError) as caught: self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'child_started')
        self.assertIn('Quit', str(caught.exception))
        self.system.running.clear()  # The user follows the instruction.
        self.system.now += update.LAUNCH_SETTLE_SECONDS
        self.run_named(str(caught.exception), 'resume')
        self.assertEqual(phase_of(record), 'committed')
        self.assertLessEqual(self.system.maximum_controllers, 1)

    def test_adopted_instance_must_report_control_with_its_own_pid(self):
        record = self.awaiting()
        self.system.running.pop(200)
        self.system.now += 100
        self.system.add(300, birth=int(self.system.time()))
        self.system.status_fault = lambda system, child: base.ready(update.Process(999, (1, 1), 0, Path('/x'), 'x'))
        self.installer.ready_timeout = 1
        with self.assertRaises(update.AwaitingConfirmation): self.installer.confirm(record)
        self.assertNotEqual(phase_of(record), 'committed')

    def test_adopted_instance_must_run_the_files_from_this_update(self):
        record = self.awaiting()
        self.system.running.pop(200)
        self.system.now += 100
        self.system.add(300, birth=int(self.system.time()))
        (self.target / 'Contents/MacOS/AquaSpace').write_text('replaced by someone else')
        with self.assertRaises(update.UpdateError): self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'child_started')

    def test_every_confirm_refusal_names_a_command_that_proceeds(self):
        cases = {
            'no manager': lambda system: None,
            'stock AeroSpace': lambda system: system.add(300, 'bobko.aerospace', STOCK),
            'another AquaSpace copy': lambda system: system.add(300, path=self.root / 'Copy.app/Contents/MacOS/AquaSpace'),
            'an AquaSpace older than the update': lambda system: system.add(300, birth=1),
        }
        for name, arrange in cases.items():
            with self.subTest(case=name):
                self.setUp()
                record = self.awaiting()
                self.system.running.pop(200)
                arrange(self.system)
                with self.assertRaises(update.UpdateError) as caught: self.installer.confirm(record)
                self.system.running.clear()  # Every refusal asks to quit the manager first.
                self.system.now += update.LAUNCH_SETTLE_SECONDS
                self.run_named(str(caught.exception), 'resume')
                self.assertEqual(phase_of(record), 'committed')
                self.assertLessEqual(self.system.maximum_controllers, 1)


class FileOnlyRecordTests(GuidanceFixture):
    """Finding 4: an interrupted file-only update finishes with the rules of its original command."""

    def interrupted_file_only(self, point='replacing'):
        stock = self.system.add(100, 'bobko.aerospace', STOCK)
        with base.hard_crash(self.installer, self.system, point): self.installer.install(allow_running_stock=True)
        return stock, self.pending()[0]

    def test_journal_records_what_the_command_asked_for(self):
        stock, record = self.interrupted_file_only()
        state = json.loads((record / 'journal.json').read_text())
        self.assertIs(state['restart'], False)
        self.assertEqual(state['retained'], [stock.record()])

    def test_resume_finishes_beside_the_same_stock_process_and_launches_nothing(self):
        for point in ('stopped', 'replacing', 'exchanged', 'replaced'):
            with self.subTest(point=point):
                self.setUp()
                stock, record = self.interrupted_file_only(point)
                self.installer.resume(record)
                self.assertEqual(phase_of(record), 'committed')
                self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
                self.assertEqual(self.system.running, {100: stock})
                self.assertNotIn('launch', self.system.events)
                self.assertNotIn('stop', self.system.events)

    def test_resume_after_stock_quit_still_launches_nothing(self):
        _, record = self.interrupted_file_only()
        self.system.running.clear()
        self.installer.resume(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.launches, [])

    def test_recover_restores_files_beside_the_same_stock_process(self):
        stock, record = self.interrupted_file_only('replaced')
        self.installer.recover(record)
        self.assertEqual(phase_of(record), 'recovered')
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertEqual(self.system.running, {100: stock})
        self.assertNotIn('launch', self.system.events)

    def test_a_different_manager_still_blocks_a_file_only_resume(self):
        for name, arrange in {
                'stock restarted with a new identity': lambda s: s.add(100, 'bobko.aerospace', STOCK, birth=50),
                'AquaSpace started': lambda s: s.add(300),
        }.items():
            with self.subTest(case=name):
                self.setUp()
                _, record = self.interrupted_file_only()
                arrange(self.system)
                for mode in ('resume', 'recover'):
                    with self.assertRaises(update.UpdateError):
                        getattr(self.installer, mode)(record)
                self.assertEqual(phase_of(record), 'replacing')
                self.assertNotIn('launch', self.system.events)

    def test_blocked_install_names_a_finish_that_starts_nothing(self):
        _, record = self.interrupted_file_only()
        with self.assertRaises(update.UpdateError) as caught: self.installer.install(allow_running_stock=True)
        message = str(caught.exception)
        self.assertNotIn('start the new AquaSpace', message)
        self.run_named(message, 'resume')
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.launches, [])

    def test_plain_install_record_never_launches_on_resume(self):
        # No manager runs and no restart was asked for.
        with base.hard_crash(self.installer, self.system, 'replacing'): self.installer.install()
        record = self.pending()[0]
        self.installer.resume(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.launches, [])
        self.assertIn('open', self.installer.summary)


if __name__ == '__main__': unittest.main()
