"""Offline only: fake process/kernel APIs and temporary application bundles."""
import contextlib
import ctypes
import errno
import fcntl
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import signal
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_update as update

# An accidental use of a real backend must fail this suite, never touch the desktop.
_offline_guards = []
def setUpModule():
    for name in ['subprocess.Popen', 'subprocess.run', 'os.kill', 'ctypes.CDLL']:
        guard = patch(name, side_effect=AssertionError('Live API forbidden in offline tests: ' + name))
        guard.start(); _offline_guards.append(guard)

def tearDownModule():
    for guard in reversed(_offline_guards): guard.stop()


LOCAL_IDENTITY = 'identifier "local.aquaspace" and certificate leaf = H"AAAA"'


def bundle(path, version, requirement=LOCAL_IDENTITY):
    (path / 'Contents/MacOS').mkdir(parents=True)
    (path / 'Contents/MacOS/AquaSpace').write_text(version)
    (path / 'Contents/MacOS/aquaspace-cli').write_text('cli-' + version)
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.aquaspace', 'CFBundleExecutable': 'AquaSpace',
        'CFBundleShortVersionString': version, 'FakeRequirement': requirement}))
    return path


class Crash(BaseException):
    """Simulated power loss: the installer ends at once and writes nothing more."""


@contextlib.contextmanager
def hard_crash(installer, system, at):
    """End the installer at a journal phase or fake-system event, as a power loss would.

    After the crash no handler can change the disk: journal writes, renames and
    removals raise, so the record keeps exactly the state of the crash.
    """
    crashed = []
    def trigger(name):
        if name == at and not crashed:
            crashed.append(name); raise Crash(name)
    def frozen(real):
        def call(*args, **kwargs):
            if crashed: raise Crash('the installer is gone')
            return real(*args, **kwargs)
        return call
    installer.checkpoint = trigger
    system.on_crash_point = trigger
    try:
        with patch.object(update, 'atomic_json', frozen(update.atomic_json)), \
             patch.object(update, 'replace_directory', frozen(update.replace_directory)), \
             patch.object(update, '_remove_tree', frozen(update._remove_tree)):
            yield crashed
    except Crash:
        pass
    finally:
        installer.checkpoint = lambda phase: None
        system.on_crash_point = None
    assert crashed, 'The simulated crash point was never reached: ' + at


class FakeSystem:
    """No native APIs, signals, subprocesses or real application directories."""
    def __init__(self, target):
        self.target = target
        self.now = 0
        self.running = {}
        self.events = []
        self.launches = []
        self.maximum_controllers = 0
        self.status_fault = None
        self.scan_fault = None
        self.stop_never_exits = False
        self.stop_delay = 0
        self.stop_deadline = None
        self.spawn_raises_after_start = False
        self.on_event = None
        self.on_crash_point = None
        self.exchange_unsupported = False
        self.unsigned = set()
        self.launch_sessions = []
        self.launched_at = None

    def add(self, pid, kind='local.aquaspace', path=None, birth=1, micro=None):
        process = update.Process(pid, (birth, pid if micro is None else micro), os.getuid(),
                                 path or self.target / 'Contents/MacOS/AquaSpace', kind)
        self.running[pid] = process
        self.maximum_controllers = max(self.maximum_controllers, len(self.running))
        return process

    def managers(self):
        self.event('scan')
        if self.scan_fault: return self.scan_fault(self)
        return list(self.running.values())

    def event(self, name):
        self.events.append(name)
        if self.on_event: self.on_event(self, name)
        if self.on_crash_point: self.on_crash_point(name)

    def inspect(self, pid):
        return self.running.get(pid)

    def requirement(self, path):
        try: return plistlib.loads((path / 'Contents/Info.plist').read_bytes())['FakeRequirement']
        except (OSError, KeyError): return None

    def verify_bundle(self, path):
        update.bundle_metadata(path)
        self.event('verify')
        if path.name in self.unsigned: raise update.UpdateError('Simulated invalid signature: ' + str(path))
        return self.requirement(path)

    def exchange(self, first, second):
        self.event('exchange')
        if self.exchange_unsupported: raise update.ExchangeUnsupported(errno.ENOTSUP, 'Simulated HFS+ volume')
        swap = first.with_name(first.name + '.swap')
        first.rename(swap); second.rename(first); swap.rename(second)
        self.event('exchanged')

    def capture_session(self, old, cli):
        self.event('capture')
        return {'windows': [['42', 'work', 'floating', 'false']],
                'workspaces': [['work', 'h_tiles']], 'workspace': 'work', 'focusedWindow': '42'}

    def stop(self, process):
        self.event('stop')
        current = self.inspect(process.pid)
        if current is None or current.identity != process.identity:
            raise update.NotSignaled('Process identity changed; refusing to signal it')
        if self.stop_never_exits: return
        if self.stop_delay:
            self.stop_deadline = self.now + self.stop_delay
        else:
            self.running.pop(process.pid)
            self.event('old-exited')

    def launch(self, executable, session, log):
        self.event('launch')
        self.launch_sessions.append(session)
        self.now += .001  # The kernel records the start after `open` was called.
        self.launched_at = self.now
        started = self.time()
        child = self.add(200, birth=int(started), micro=round(started % 1 * 1e6))
        self.launches.append(child)
        self.event('launched')
        if self.spawn_raises_after_start: raise OSError('Spawn outcome uncertain')
        return child

    def status(self, child, cli):
        self.event('status')
        if self.status_fault: return self.status_fault(self, child)
        return {'pid': child.pid, 'phase': 'controlling', 'controlsWindows': True,
                'readOnly': False, 'originalPids': []}

    def monotonic(self): return self.now
    def time(self): return 1_700_000_000 + self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.stop_deadline is not None and self.now >= self.stop_deadline:
            self.running.pop(100, None); self.stop_deadline = None
            self.event('old-exited')


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aquaspace-offline-')
        self.addCleanup(self.tmp.cleanup)
        # F_FULLFSYNC flushes the drive's cache at every journal write, which makes
        # hundreds of transactions slow. DurableWriteTests cover the real flush.
        fast = patch.object(update, 'full_sync', side_effect=os.fsync)
        fast.start(); self.addCleanup(fast.stop)
        # The kernel reports canonical paths (/private/var, not /var), so the fake does too.
        self.root = Path(self.tmp.name).resolve()
        self.source = bundle(self.root / 'build/AquaSpace.app', 'new')
        self.target = bundle(self.root / 'Applications/AquaSpace.app', 'old')
        self.system = FakeSystem(self.target)
        self.installer = update.Installer(self.source, self.target, self.system, timeout=.3)
        self.reports = []
        self.installer.report = self.reports.append

    def activate(self): return self.installer.install(restart=True)

    def pending(self): return self.installer.pending_transactions()

    def assert_single_launch(self):
        self.assertLessEqual(self.system.maximum_controllers, 1)
        self.assertLessEqual(len(self.system.launches), 1)


class InstallerTests(InstallerFixture):
    def test_success_waits_for_old_exit_before_launch_and_restores_metadata(self):
        self.system.add(100)
        self.system.stop_delay = .1
        transaction = self.activate()
        self.assertLess(self.system.events.index('old-exited'), self.system.events.index('launch'))
        self.assertEqual(self.system.running.keys(), {200})
        self.assertEqual(json.loads((transaction / 'session.json').read_text())['workspace'], 'work')
        self.assertEqual(json.loads((transaction / 'journal.json').read_text())['phase'], 'committed')
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertFalse((transaction / 'previous.app').exists())
        with tarfile.open(transaction / 'previous.app.tar.gz') as backup:
            self.assertEqual(backup.extractfile('previous.app/Contents/MacOS/AquaSpace').read(), b'old')
        self.assert_single_launch()

    def test_default_install_refuses_active_manager_without_signaling_it(self):
        self.system.add(100)
        with self.assertRaises(update.UpdateError): self.installer.install()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')

    def test_install_only_never_launches_an_app(self):
        self.installer.install()
        self.assertNotIn('launch', self.system.events)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')

    def test_completed_backup_cleanup_preserves_symlinks_and_skips_pending_recovery(self):
        transaction = self.installer.install()
        legacy = bundle(transaction / 'previous.app', 'legacy')
        (legacy / 'Contents/Helpers').mkdir()
        (legacy / 'Contents/Helpers/aerospace').symlink_to('../MacOS/aquaspace-cli')
        (transaction / 'previous.app.tar.gz').unlink()
        pending = self.target.parent / '.aquaspace-update-pending'
        pending.mkdir()
        update.atomic_json(pending / 'journal.json', dict(schema=1, target=str(self.target), phase='replaced'))
        bundle(pending / 'previous.app', 'pending')
        self.installer.archive_completed_backups()
        self.assertFalse(legacy.exists())
        with tarfile.open(transaction / 'previous.app.tar.gz') as archive:
            alias = archive.getmember('previous.app/Contents/Helpers/aerospace')
            self.assertTrue(alias.issym())
            self.assertEqual(alias.linkname, '../MacOS/aquaspace-cli')
        self.assertTrue((pending / 'previous.app').is_dir())
        self.assertNotIn('launch', self.system.events)

    def test_archive_retry_keeps_complete_backup_after_interrupted_removal(self):
        transaction = self.installer.install()
        backup = bundle(transaction / 'previous.app', 'original')
        (transaction / 'previous.app.tar.gz').unlink()
        with patch.object(update.shutil, 'rmtree', side_effect=OSError('interrupted removal')):
            with self.assertRaises(OSError): self.installer.archive_completed_backups()
        (backup / 'Contents/MacOS/AquaSpace').unlink()
        self.installer.archive_completed_backups()
        with tarfile.open(transaction / 'previous.app.tar.gz') as archive:
            self.assertEqual(archive.extractfile('previous.app/Contents/MacOS/AquaSpace').read(), b'original')

    def test_partial_aborted_staging_copy_does_not_block_next_install_and_is_not_archived(self):
        # An aborted staging copy never ran, so it is not evidence worth 4 MB per record.
        transaction = self.target.parent / '.aquaspace-update-aborted'
        (transaction / 'new.app/Contents').mkdir(parents=True)
        update.atomic_json(transaction / 'journal.json', dict(schema=1, target=str(self.target), phase='aborted'))
        self.installer.install()
        self.assertFalse((transaction / 'new.app.tar.gz').exists())
        self.assertFalse((transaction / 'new.app').exists())

    def test_restart_without_active_manager_launches_once(self):
        self.activate()
        self.assertEqual(len(self.system.launches), 1)
        self.assert_single_launch()

    def test_stock_aerospace_is_never_stopped_or_launched(self):
        self.system.add(100, 'bobko.aerospace', self.root / 'AeroSpace.app/Contents/MacOS/AeroSpace')
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)

    def test_explicit_file_only_repair_retains_stock_and_never_launches(self):
        stock = self.system.add(100, 'bobko.aerospace', Path('/Applications/AeroSpace.app/Contents/MacOS/AeroSpace'))
        self.installer.install(allow_running_stock=True)
        self.assertEqual(self.system.running, {100: stock})
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)
        self.assertNotIn('capture', self.system.events)

    def test_stock_exit_or_peer_arrival_aborts_file_only_repair(self):
        self.system.add(100, 'bobko.aerospace', Path('/Applications/AeroSpace.app/Contents/MacOS/AeroSpace'))
        def change(system, event):
            if event == 'verify' and system.events.count('verify') == 2: system.running.clear()
        self.system.on_event = change
        with self.assertRaises(update.UpdateError): self.installer.install(allow_running_stock=True)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)

    def test_other_aquaspace_copy_or_debug_build_is_not_stopped(self):
        for identifier in ['local.aquaspace', 'local.aquaspace.debug']:
            with self.subTest(identifier=identifier):
                self.system.running.clear()
                self.system.add(100, identifier, self.root / 'Some Other Copy.app/Contents/MacOS/AquaSpace')
                with self.assertRaises(update.UpdateError): self.activate()
                self.assertNotIn('stop', self.system.events)

    def test_multiple_existing_managers_fail_before_modifying_install(self):
        self.system.add(100); self.system.add(101)
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)

    def test_pid_reuse_between_capture_and_stop_does_not_signal_replacement(self):
        self.system.add(100)
        def change(system, event):
            if event == 'capture': system.add(100, birth=99)
        self.system.on_event = change
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)
        self.assertEqual(self.system.running[100].started[0], 99)

    def test_stop_timeout_never_publishes_or_launches_new_app(self):
        self.system.add(100); self.system.stop_never_exits = True
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {100})
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertNotIn('launch', self.system.events)
        self.assert_single_launch()

    def test_peer_arriving_after_stop_prevents_launch(self):
        self.system.add(100)
        def peer(system, event):
            if event == 'old-exited': system.add(300, 'bobko.aerospace')
        self.system.on_event = peer
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('launch', self.system.events)
        self.assert_single_launch()

    def test_false_process_absence_after_launch_never_starts_rollback(self):
        self.system.add(100)
        self.system.scan_fault = lambda system: [] if system.launches else list(system.running.values())
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {200})
        self.assert_single_launch()

    def test_wrong_or_unready_status_never_starts_rollback(self):
        for result in [{'pid': 999, 'controlsWindows': True},
                       {'pid': 200, 'controlsWindows': False, 'phase': 'preparing'}, None]:
            with self.subTest(result=result):
                self.setUp()
                self.system.add(100)
                self.system.status_fault = lambda system, child: result
                with self.assertRaises(update.UpdateError): self.activate()
                self.assertEqual(self.system.running.keys(), {200})
                self.assert_single_launch()

    def test_uncertain_spawn_outcome_never_launches_or_restores_another_app(self):
        self.system.add(100); self.system.spawn_raises_after_start = True
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {200})
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assert_single_launch()

    def test_retry_after_interrupted_spawn_requires_explicit_recovery(self):
        self.system.add(100); self.system.spawn_raises_after_start = True
        with self.assertRaises(update.UpdateError): self.activate()
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(len(self.system.launches), 1)

    def test_file_recovery_refuses_any_running_manager(self):
        self.system.add(100); self.system.spawn_raises_after_start = True
        with self.assertRaises(update.UpdateError): self.activate()
        transaction = self.pending()[0]
        with self.assertRaises(update.UpdateError): self.installer.recover(transaction)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assert_single_launch()

    def test_recovery_restores_files_without_ever_launching_a_manager(self):
        self.system.add(100); self.system.spawn_raises_after_start = True
        with self.assertRaises(update.UpdateError): self.activate()
        self.system.running.clear()
        transaction = self.pending()[0]
        self.installer.recover(transaction)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertEqual(len(self.system.launches), 1)
        self.assertFalse(self.pending())

    def test_corrupt_journal_blocks_update_without_process_actions(self):
        tx = self.target.parent / '.aquaspace-update-broken'; tx.mkdir()
        (tx / 'journal.json').write_text('{broken')
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)

    def test_second_installer_cannot_enter_same_transaction_lock(self):
        with update.InstallLock(self.target.parent):
            with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)

    def test_publish_failure_leaves_no_new_controller_and_recovery_is_file_only(self):
        self.system.add(100)
        real_replace = update.replace_directory
        def fail(source, target):
            if source.name == 'new.app': raise OSError('Simulated failed second rename')
            return real_replace(source, target)
        with patch.object(update, 'replace_directory', side_effect=fail):
            with self.assertRaises(update.UpdateError): self.activate()
        self.assertFalse(self.system.running)
        self.assertNotIn('launch', self.system.events)
        self.installer.recover(self.pending()[0])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertNotIn('launch', self.system.events)

    def test_checkpoint_crashes_cannot_relaunch_or_rollback_a_running_child(self):
        # Exercise interruption at every persisted boundary, including the window
        # between spawn and recording the child identity. No automatic fallback.
        for checkpoint in ['prepared', 'stop_requested', 'stopped', 'replacing',
                           'replaced', 'launch_attempted', 'child_started', 'committed']:
            with self.subTest(checkpoint=checkpoint):
                self.setUp(); self.system.add(100)
                def crash(phase):
                    if phase == checkpoint: raise KeyboardInterrupt('Simulated installer death')
                self.installer.checkpoint = crash
                with self.assertRaises(KeyboardInterrupt): self.activate()
                self.assert_single_launch()
                self.assertEqual(len(self.system.launches), int(checkpoint in ['child_started', 'committed']))

    def test_manifest_mismatch_aborts_before_stopping_the_running_app(self):
        self.system.add(100)
        (self.source.parent / 'build.json').write_text(json.dumps({
            'version': 'new', 'app_sha256': update.sha256(self.source / 'Contents/MacOS/AquaSpace'),
            'cli_sha256': '0' * 64}))
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertNotIn('launch', self.system.events)
        self.assertFalse(self.pending())

    def test_staging_signature_failure_keeps_old_running_and_allows_retry(self):
        self.system.add(100)
        def fail(system, event):
            if event == 'verify' and system.events.count('verify') == 2:
                raise update.UpdateError('Simulated bad signature')
        self.system.on_event = fail
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertFalse(self.pending())
        self.system.on_event = None
        self.activate()
        self.assert_single_launch()

    def test_global_lock_serializes_different_destination_directories(self):
        shared = self.root / 'shared-lock'; shared.mkdir()
        self.installer.lock_parent = shared
        other_target = bundle(self.root / 'Other Applications/AquaSpace.app', 'old')
        other_system = FakeSystem(other_target)
        other = update.Installer(self.source, other_target, other_system, lock_parent=shared)
        with update.InstallLock(shared):
            with self.assertRaises(update.UpdateError): self.activate()
            with self.assertRaises(update.UpdateError): other.install(restart=True)
        self.assertNotIn('launch', other_system.events)
        lock = update.InstallLock(shared).path
        inode = lock.stat().st_ino
        with update.InstallLock(shared): pass
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertNotIn('launch', self.system.events)

    def test_recovery_after_every_interrupted_filesystem_boundary_never_launches(self):
        # A power loss runs no handler, so the record stays in the crashed phase.
        for checkpoint in ['staging', 'prepared', 'stop_requested', 'stopped',
                           'replacing', 'exchanged', 'replaced', 'launch_attempted', 'child_started']:
            with self.subTest(checkpoint=checkpoint):
                self.setUp(); self.system.add(100)
                with hard_crash(self.installer, self.system, checkpoint): self.activate()
                # Simulate the user closing any running manager before --recover.
                self.system.running.clear()
                launches = len(self.system.launches)
                self.installer.recover(self.pending()[0])
                self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
                self.assertEqual(len(self.system.launches), launches)
                self.assert_single_launch()

    def test_readiness_permission_failure_leaves_one_child_and_never_runs_recovery(self):
        self.system.add(100)
        def denied(system, child): raise PermissionError('Simulated missing permission')
        self.system.status_fault = denied
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {200})
        self.assert_single_launch()

    def test_peer_starting_during_file_publish_is_not_joined_by_another_launch(self):
        self.system.add(100)
        def peer(phase):
            if phase == 'replaced': self.system.add(300, 'bobko.aerospace')
        self.installer.checkpoint = peer
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('launch', self.system.events)
        self.assert_single_launch()

    def test_current_cli_capture_failure_does_not_stop_the_existing_manager(self):
        self.system.add(100)
        def failed(system, event):
            if event == 'capture': raise update.UpdateError('Ownership did not match')
        self.system.on_event = failed
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {100})
        self.assertNotIn('stop', self.system.events)
        self.assertFalse(self.pending())

    def test_repeated_successful_updates_never_overlap_controllers(self):
        self.system.add(100)
        for _ in range(3):
            transaction = self.activate()
            self.assertEqual(json.loads((transaction / 'journal.json').read_text())['phase'], 'committed')
            self.assertEqual(len(self.system.running), 1)
        self.assertEqual(len(self.system.launches), 3)
        self.assertEqual(self.system.maximum_controllers, 1)
        self.assertFalse(self.pending())

    def test_process_scan_error_after_spawn_never_launches_recovery(self):
        self.system.add(100)
        def uncertain(system):
            if system.launches: raise update.UpdateError('Process inventory unavailable')
            return list(system.running.values())
        self.system.scan_fault = uncertain
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertEqual(self.system.running.keys(), {200})
        self.assert_single_launch()

def ready(child):
    return {'pid': child.pid, 'phase': 'controlling', 'controlsWindows': True,
            'readOnly': False, 'originalPids': []}


def phase_of(record):
    return json.loads((record / 'journal.json').read_text())['phase']


class InterruptedUpdateTests(InstallerFixture):
    """H1: after the stop, every failure names its next command and --resume finishes."""
    def lines_with(self, text, flag):
        return [shlex.split(line) for line in text.splitlines() if flag in line]

    def assert_guides(self, text, record, *modes):
        for mode in modes:
            commands = self.lines_with(text, mode)
            self.assertTrue(commands, f'{mode} command missing from: {text}')
            argv = commands[0]
            self.assertEqual(argv[:2], ['python3', str(update.INSTALLER_SCRIPT)])
            self.assertEqual(argv[argv.index('--target') + 1], str(self.target))
            self.assertEqual(argv[argv.index(mode) + 1], str(record))

    def test_failure_after_stop_names_resume_and_recover_and_resume_finishes_once(self):
        self.system.add(100)
        def fail(system, event):
            if event == 'exchange': raise OSError('Simulated rename failure')
        self.system.on_event = fail
        with self.assertRaises(update.UpdateError) as caught: self.activate()
        record = self.pending()[0]
        self.assert_guides(str(caught.exception), record, '--resume', '--recover')
        self.assertFalse(self.system.running)
        self.assertNotIn('launch', self.system.events)
        self.system.on_event = None
        self.installer.resume(record)
        self.assertEqual(self.system.running.keys(), {200})
        self.assertEqual(self.system.launch_sessions, [record / 'session.json'])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertEqual(phase_of(record), 'committed')
        with tarfile.open(record / 'previous.app.tar.gz') as backup:
            self.assertEqual(backup.extractfile('previous.app/Contents/MacOS/AquaSpace').read(), b'old')
        self.assert_single_launch()

    def test_resume_after_power_loss_at_each_post_stop_boundary_launches_once(self):
        for point in ['stopped', 'replacing', 'exchanged', 'replaced', 'launch_attempted']:
            with self.subTest(point=point):
                self.setUp(); self.system.add(100)
                with hard_crash(self.installer, self.system, point): self.activate()
                self.assertFalse(self.system.running)
                self.assertTrue(self.target.is_dir(), 'The target path must never be absent')
                self.system.now += update.LAUNCH_SETTLE_SECONDS
                record = self.pending()[0]
                self.installer.resume(record)
                self.assertEqual(len(self.system.launches), 1)
                self.assertEqual(self.system.launch_sessions, [record / 'session.json'])
                self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
                self.assertEqual(phase_of(record), 'committed')
                self.assertFalse(self.pending())
                self.assert_single_launch()

    def test_resume_refuses_while_any_manager_runs(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'replaced'): self.activate()
        self.system.add(300, 'bobko.aerospace', self.root / 'AeroSpace.app/Contents/MacOS/AeroSpace')
        with self.assertRaises(update.UpdateError): self.installer.resume(self.pending()[0])
        self.assertNotIn('launch', self.system.events)

    def test_resume_waits_after_an_uncertain_launch_before_trying_again(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'launch_attempted'): self.activate()
        record = self.pending()[0]
        with self.assertRaises(update.UpdateError) as caught: self.installer.resume(record)
        self.assert_guides(str(caught.exception), record, '--resume')
        self.assertNotIn('launch', self.system.events)
        self.system.now += update.LAUNCH_SETTLE_SECONDS
        self.installer.resume(record)
        self.assertEqual(len(self.system.launches), 1)

    def test_resume_after_the_child_died_starts_it_once_more(self):
        self.system.add(100)
        def died(system, child):
            system.running.pop(child.pid, None); return None
        self.system.status_fault = died
        with self.assertRaises(update.UpdateError) as caught: self.activate()
        record = self.pending()[0]
        self.assert_guides(str(caught.exception), record, '--resume', '--confirm', '--recover')
        self.system.status_fault = None
        self.installer.resume(record)
        self.assertEqual(len(self.system.launches), 2)
        self.assertEqual(phase_of(record), 'committed')
        self.assertLessEqual(self.system.maximum_controllers, 1)

    def test_target_is_never_absent_while_the_bundle_is_swapped(self):
        self.system.add(100)
        present = []
        real = update.replace_directory
        def rename(source, target):
            real(source, target); present.append(self.target.is_dir())
        self.system.on_event = lambda system, event: present.append(self.target.is_dir()) if event == 'exchanged' else None
        with patch.object(update, 'replace_directory', side_effect=rename): self.activate()
        self.assertGreaterEqual(len(present), 2)
        self.assertTrue(all(present))

    def test_volume_without_exchange_still_installs_and_a_crash_between_renames_resumes(self):
        self.system.exchange_unsupported = True
        self.activate()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.setUp(); self.system.exchange_unsupported = True; self.system.add(100)
        real = update.replace_directory
        def first_rename_only(source, target):
            real(source, target)
            if source == self.target: raise Crash('power loss between the two renames')
        with patch.object(update, 'replace_directory', side_effect=first_rename_only):
            with self.assertRaises(Crash): self.activate()
        self.assertFalse(self.target.exists())
        self.installer.resume(self.pending()[0])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertEqual(len(self.system.launches), 1)

    def test_recover_after_crash_between_exchange_and_rename_restores_old(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'exchanged'): self.activate()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.installer.recover(self.pending()[0])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.assertNotIn('launch', self.system.events)

    def test_recover_interrupted_after_its_exchange_completes_on_retry(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'replaced'): self.activate()
        record = self.pending()[0]
        with hard_crash(self.installer, self.system, 'exchanged'): self.installer.recover(record)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')
        self.installer.recover(record)
        self.assertEqual(phase_of(record), 'recovered')
        self.assertEqual((record / 'unconfirmed.app/Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')

    def test_interrupt_after_stop_reports_next_commands_and_keeps_the_record(self):
        self.system.add(100)
        def interrupt(phase):
            if phase == 'replaced': raise KeyboardInterrupt
        self.installer.checkpoint = interrupt
        with self.assertRaises(KeyboardInterrupt): self.activate()
        record = self.pending()[0]
        self.assert_guides('\n'.join(self.reports), record, '--resume', '--recover')

    def test_unfinished_update_block_names_the_next_command(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'replaced'): self.activate()
        record = self.pending()[0]
        with self.assertRaises(update.UpdateError) as caught: self.activate()
        self.assert_guides(str(caught.exception), record, '--resume', '--recover')


class ReadinessTests(InstallerFixture):
    """H2: a live replacement that waits for permission is not a failure."""

    def test_unready_replacement_is_left_running_and_confirm_commits_without_stopping(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation) as caught: self.activate()
        record = self.pending()[0]
        self.assertIn(self.installer.command('--confirm', record), str(caught.exception))
        self.assertIn('Accessibility', str(caught.exception))
        self.assertEqual(self.system.running.keys(), {200})
        self.assertEqual(phase_of(record), 'child_started')
        # The next update is refused, and the refusal names the same command.
        with self.assertRaises(update.UpdateError) as blocked: self.activate()
        self.assertIn(self.installer.command('--confirm', record), str(blocked.exception))
        stops = self.system.events.count('stop')
        self.system.status_fault = None
        self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.system.events.count('stop'), stops)
        self.assertEqual(self.system.running.keys(), {200})
        self.assert_single_launch()

    def test_first_install_without_permission_has_no_dead_end(self):
        shutil.rmtree(self.target)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        record = self.pending()[0]
        with self.assertRaises(update.AwaitingConfirmation): self.installer.confirm(record)
        self.system.status_fault = None
        self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertFalse(self.pending())
        self.assertTrue(any('Accessibility' in text for text in self.reports))

    def test_ready_timeout_bounds_the_wait(self):
        # The replacement takes control 12 s after its launch.
        for timeout, completes in ((5, False), (20, True)):
            with self.subTest(timeout=timeout):
                self.setUp(); self.system.add(100)
                self.installer.ready_timeout = timeout
                self.system.status_fault = lambda system, child: ready(child) if system.now - system.launched_at >= 12 else None
                if completes:
                    self.assertEqual(phase_of(self.activate()), 'committed')
                else:
                    with self.assertRaises(update.AwaitingConfirmation): self.activate()
                self.assertEqual(self.system.running.keys(), {200})

    def test_confirm_refuses_a_manager_this_update_did_not_start(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        record = self.pending()[0]
        self.system.running.clear()
        self.system.add(201)  # Same bundle, different process.
        self.system.status_fault = None
        with self.assertRaises(update.UpdateError): self.installer.confirm(record)
        self.system.running.clear()
        with self.assertRaises(update.UpdateError) as caught: self.installer.confirm(record)
        self.assertIn('--resume', str(caught.exception))
        self.assertEqual(phase_of(record), 'child_started')

    def test_child_exiting_during_confirm_names_the_next_commands(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        record = self.pending()[0]
        def exits(system, child):
            system.running.pop(child.pid, None); return None
        self.system.status_fault = exits
        with self.assertRaises(update.UpdateError) as caught: self.installer.confirm(record)
        self.assertIn(self.installer.command('--resume', record), str(caught.exception))
        self.assertEqual(phase_of(record), 'child_started')

    def test_confirm_adopts_child_started_after_the_recorded_launch_intent(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'launched'): self.activate()
        record = self.pending()[0]
        self.assertEqual(phase_of(record), 'launch_attempted')
        with self.assertRaises(update.UpdateError) as caught: self.installer.resume(record)
        self.assertIn('--confirm', str(caught.exception))
        self.installer.confirm(record)
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(len(self.system.launches), 1)

    def test_confirm_never_adopts_a_manager_older_than_the_launch(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'launch_attempted'): self.activate()
        self.system.add(200, birth=1)  # Started long before the launch intent.
        with self.assertRaises(update.UpdateError): self.installer.confirm(self.pending()[0])

    def test_recover_with_the_new_child_running_points_to_confirm(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        with self.assertRaises(update.AwaitingConfirmation): self.activate()
        record = self.pending()[0]
        with self.assertRaises(update.UpdateError) as caught: self.installer.recover(record)
        self.assertIn(self.installer.command('--confirm', record), str(caught.exception))
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')


class AbortTests(InstallerFixture):
    """M1/M2: records that prove nothing was stopped never require quitting a manager."""

    def test_interrupt_during_staging_copy_aborts_cleanly(self):
        self.system.add(100)
        real = shutil.copytree
        def interrupted(*args, **kwargs):
            real(*args, **kwargs); raise KeyboardInterrupt
        with patch.object(update.shutil, 'copytree', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.activate()
        self.assertFalse(self.pending())
        record, = self.target.parent.glob('.aquaspace-update-*')
        self.assertEqual(phase_of(record), 'aborted')
        self.assertFalse((record / 'new.app').exists())
        self.assertEqual(self.system.running.keys(), {100})
        self.activate()  # The manager kept running; the retry needs nothing else.
        self.assert_single_launch()

    def test_stop_refused_before_any_signal_closes_the_record(self):
        self.system.add(100)
        def replaced(system, event):
            if event == 'stop': system.add(100, birth=99)
        self.system.on_event = replaced
        with self.assertRaises(update.UpdateError) as caught: self.activate()
        self.assertIn('No process was stopped', str(caught.exception))
        self.assertFalse(self.pending())
        self.assertEqual(self.system.running[100].started[0], 99)
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')

    def test_dead_record_that_proves_nothing_was_stopped_closes_with_manager_running(self):
        for point in ['staging', 'prepared', 'stop_requested']:
            for close in ('recover', 'resume'):
                with self.subTest(point=point, close=close):
                    self.setUp(); old = self.system.add(100)
                    with hard_crash(self.installer, self.system, point): self.activate()
                    getattr(self.installer, close)(self.pending()[0])
                    self.assertFalse(self.pending())
                    self.assertEqual(self.system.running, {100: old})
                    self.assertNotIn('stop', self.system.events)
                    self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')

    def test_install_closes_a_dead_pre_stop_record_by_itself(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'prepared'): self.activate()
        self.assertTrue(self.pending())
        self.activate()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')

    def test_failed_first_journal_write_leaves_no_blocking_record(self):
        with patch.object(update, 'atomic_json', side_effect=OSError('Simulated full disk')):
            with self.assertRaises(OSError): self.installer.install()
        self.assertFalse(list(self.target.parent.glob('.aquaspace-update-*')))
        self.installer.install()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')

    def test_records_abandoned_before_their_first_journal_never_block(self):
        parent = self.target.parent
        draft = parent / '.aquaspace-new-update-1'; draft.mkdir()
        update.atomic_json(draft / 'journal.json', dict(schema=1, target=str(self.target), phase='staging'))
        legacy_empty = parent / '.aquaspace-update-2'; legacy_empty.mkdir()
        legacy_partial = parent / '.aquaspace-update-3'; legacy_partial.mkdir()
        (legacy_partial / 'journal.json.tmp').write_text('{')
        unknown = parent / '.aquaspace-update-4'; unknown.mkdir()
        (unknown / 'notes.txt').write_text('kept')
        self.installer.install()
        for path in (draft, legacy_empty, legacy_partial):
            self.assertFalse(path.exists())
        self.assertEqual((unknown / 'notes.txt').read_text(), 'kept')


class RecordPruningTests(InstallerFixture):
    """M3: completed records are bounded; pending records are never removed."""

    def committed(self, name, age):
        record = self.target.parent / ('.aquaspace-update-' + name)
        record.mkdir()
        update.atomic_json(record / 'journal.json', dict(schema=1, target=str(self.target), phase='committed'))
        (record / 'previous.app.tar.gz').write_bytes(b'x' * 10)
        os.utime(record / 'journal.json', (1000 + age, 1000 + age))
        return record

    def test_only_the_newest_completed_records_are_kept(self):
        records = [self.committed(f'{i:02}', age=i) for i in range(8)]
        pending = self.target.parent / '.aquaspace-update-pending'
        pending.mkdir()
        update.atomic_json(pending / 'journal.json', dict(schema=1, target=str(self.target), phase='replaced'))
        os.utime(pending / 'journal.json', (1, 1))
        self.installer.archive_completed_backups()
        kept = sorted(p for p in self.target.parent.glob('.aquaspace-update-*') if p != pending)
        self.assertEqual(kept, records[-update.KEEP_COMPLETED_RECORDS:])
        self.assertTrue((pending / 'journal.json').is_file())

    def test_install_prunes_before_adding_its_own_record(self):
        for i in range(update.KEEP_COMPLETED_RECORDS + 3): self.committed(f'{i:02}', age=i)
        self.installer.install()
        self.assertEqual(len(list(self.target.parent.glob('.aquaspace-update-*'))), update.KEEP_COMPLETED_RECORDS + 1)


class IdentityChangeTests(InstallerFixture):
    """M7: a different signing identity loses the Accessibility grant; ask first."""

    def test_identity_change_is_refused_before_anything_stops(self):
        shutil.rmtree(self.source)
        bundle(self.source, 'new', 'identifier "local.aquaspace" and certificate leaf = H"BBBB"')
        self.system.add(100)
        with self.assertRaises(update.UpdateError) as caught: self.activate()
        message = str(caught.exception)
        self.assertIn('Accessibility', message)
        retry = [shlex.split(line) for line in message.splitlines() if '--allow-identity-change' in line][0]
        self.assertIn('--restart', retry)
        self.assertEqual(retry[retry.index('--source') + 1], str(self.source))
        self.assertNotIn('stop', self.system.events)
        self.assertFalse(list(self.target.parent.glob('.aquaspace-update-*')))
        record = self.installer.install(restart=True, allow_identity_change=True)
        self.assertTrue(json.loads((record / 'journal.json').read_text())['identity_changed'])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')

    def test_unreadable_installed_signature_counts_as_a_change(self):
        info = self.target / 'Contents/Info.plist'
        data = plistlib.loads(info.read_bytes()); del data['FakeRequirement']
        info.write_bytes(plistlib.dumps(data))
        with self.assertRaises(update.UpdateError): self.installer.install()
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'old')

    def test_manifest_requirement_must_match_the_staged_signature(self):
        self.system.add(100)
        (self.source.parent / 'build.json').write_text(json.dumps({
            'version': 'new', 'app_sha256': update.sha256(self.source / 'Contents/MacOS/AquaSpace'),
            'designated_requirement': 'identifier "local.aquaspace" and certificate leaf = H"CCCC"'}))
        with self.assertRaises(update.UpdateError): self.activate()
        self.assertNotIn('stop', self.system.events)
        self.assertFalse(self.pending())


class SmallerDefectTests(InstallerFixture):
    def test_archive_failure_after_commit_reports_success_with_a_warning(self):
        self.system.add(100)
        with patch.object(update, 'archive_bundle', side_effect=OSError('Simulated disk error')):
            record = self.activate()
        self.assertEqual(phase_of(record), 'committed')
        self.assertTrue((record / 'previous.app').is_dir())
        self.assertIn('--archive-backups', self.installer.warnings[0])
        self.installer.archive_completed_backups()
        self.assertTrue((record / 'previous.app.tar.gz').is_file())

    def test_other_spelling_of_target_is_the_same_installation(self):
        link = self.root / 'Linked Applications'
        link.symlink_to(self.target.parent)
        self.installer = update.Installer(self.source, link / '.' / 'AquaSpace.app', self.system, timeout=.3)
        self.installer.report = self.reports.append
        self.system.add(100)
        record = self.activate()
        self.assertEqual(phase_of(record), 'committed')
        self.assertEqual(self.installer.target, self.target)

    def test_recover_refuses_a_previous_bundle_with_an_invalid_signature(self):
        self.system.add(100)
        with hard_crash(self.installer, self.system, 'replaced'): self.activate()
        self.system.unsigned.add('previous.app')
        with self.assertRaises(update.UpdateError): self.installer.recover(self.pending()[0])
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')


class DurableWriteTests(unittest.TestCase):
    """Pre-existing issue b: on macOS, fsync can leave data in the drive's cache."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def synced(self, fcntl_effect=None):
        flushed, fsynced = [], []
        real_fcntl = fcntl.fcntl
        def record_fcntl(fd, command, *args):
            if command == getattr(fcntl, 'F_FULLFSYNC', None):
                flushed.append(os.fstat(fd).st_ino)
                if fcntl_effect: raise fcntl_effect
                return 0
            return real_fcntl(fd, command, *args)
        def record_fsync(fd): fsynced.append(os.fstat(fd).st_ino)
        with patch.object(update.fcntl, 'fcntl', side_effect=record_fcntl), \
             patch.object(update.os, 'fsync', side_effect=record_fsync):
            update.atomic_json(self.root / 'journal.json', {'phase': 'staging'})
        return flushed, fsynced

    @unittest.skipUnless(hasattr(fcntl, 'F_FULLFSYNC'), 'F_FULLFSYNC is a macOS fcntl')
    def test_journal_and_its_directory_are_flushed_to_the_drive(self):
        flushed, _ = self.synced()
        journal = (self.root / 'journal.json').stat().st_ino
        self.assertIn(journal, flushed)
        self.assertIn(self.root.stat().st_ino, flushed)
        self.assertEqual(json.loads((self.root / 'journal.json').read_text()), {'phase': 'staging'})

    def test_file_system_without_full_flush_falls_back_to_fsync(self):
        flushed, fsynced = self.synced(OSError(errno.ENOTSUP, 'not supported'))
        self.assertIn((self.root / 'journal.json').stat().st_ino, fsynced)
        self.assertIn(self.root.stat().st_ino, fsynced)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.name = f'.aquaspace-install-{os.getuid()}.lock'

    def test_user_lock_directory_is_created_private(self):
        directory = self.root / 'Application Support/AquaSpace/Updater'
        with update.InstallLock(directory): pass
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertTrue((directory / self.name).is_file())

    def test_legacy_lock_held_by_an_older_updater_blocks(self):
        legacy = self.root / 'legacy'; legacy.mkdir()
        with (legacy / self.name).open('w') as older:
            fcntl.flock(older, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(update.UpdateError, 'older'):
                with update.InstallLock(self.root / 'user', legacy=[legacy]): pass
        with update.InstallLock(self.root / 'user', legacy=[legacy]): pass

    @staticmethod
    def older_updater_lock(directory):
        """The lock of the pre-2026-11 updater: it creates the file, then locks it."""
        path = directory / f'.aquaspace-install-{os.getuid()}.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('unexpected owner or file type')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd); raise
        return fd

    def test_absent_legacy_lock_is_created_so_an_older_updater_cannot_overlap(self):
        # The requirement changed: macOS clears /tmp at boot, so the file is often
        # absent, and an older updater creates it. Create and hold it as that updater does.
        legacy = self.root / 'legacy'; legacy.mkdir()
        with update.InstallLock(self.root / 'user', legacy=[legacy]):
            path = legacy / self.name
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(OSError): self.older_updater_lock(legacy)
        os.close(self.older_updater_lock(legacy))  # Released afterwards; never unlinked.
        self.assertTrue((legacy / self.name).is_file())

    def test_unusable_legacy_lock_is_skipped_with_a_warning_and_never_followed(self):
        # An older updater refuses to start on each of these files, so it cannot overlap.
        linked, directory, hardlinked = self.root / 'linked', self.root / 'directory', self.root / 'hardlinked'
        for path in (linked, directory, hardlinked): path.mkdir()
        (linked / self.name).symlink_to(self.root / 'elsewhere')
        (directory / self.name).mkdir()
        (self.root / 'original').write_text('')
        os.link(self.root / 'original', hardlinked / self.name)
        for legacy in (linked, directory, hardlinked):
            with self.subTest(legacy=legacy.name):
                reports = []
                with update.InstallLock(self.root / 'user', legacy=[legacy], report=reports.append): pass
                self.assertTrue(reports and str(legacy / self.name) in reports[0])
                with self.assertRaises(OSError): os.close(self.older_updater_lock(legacy))
        self.assertFalse((self.root / 'elsewhere').exists())

    def test_legacy_lock_planted_by_another_account_does_not_block_and_is_reported(self):
        legacy = self.root / 'legacy'; legacy.mkdir()
        planted = legacy / self.name
        planted.write_text('')
        planted_inode = planted.stat().st_ino
        real_fstat = os.fstat
        def other_owner(fd):
            info = real_fstat(fd)
            if info.st_ino != planted_inode: return info
            values = list(info); values[stat.ST_UID] = os.getuid() + 1
            return os.stat_result(values)
        reports = []
        with patch.object(update.os, 'fstat', side_effect=other_owner):
            with update.InstallLock(self.root / 'user', legacy=[legacy], report=reports.append): pass
        self.assertEqual(len(reports), 1)
        self.assertIn('another account', reports[0])
        self.assertIn('older AquaSpace installer cannot run', reports[0])

    def test_legacy_lock_only_where_it_exists_is_never_created(self):
        existing_only = self.root / 'applications'; existing_only.mkdir()
        with update.InstallLock(self.root / 'user', legacy_existing=[existing_only]): pass
        self.assertFalse((existing_only / self.name).exists())
        (existing_only / self.name).write_text('')
        with update.InstallLock(self.root / 'user', legacy_existing=[existing_only]):
            with self.assertRaises(OSError): self.older_updater_lock(existing_only)

    def test_legacy_location_equal_to_the_user_lock_is_not_locked_twice(self):
        with update.InstallLock(self.root, legacy=[self.root]): pass


class CommandLineTests(InstallerFixture):
    def main(self, *args):
        err = io.StringIO()
        with patch.object(update, 'NativeSystem', return_value=self.system), \
             patch.object(update, 'USER_STATE_DIRECTORY', self.root / 'state'), \
             patch.object(update, 'LEGACY_LOCK_DIRECTORIES', ()), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = update.main(['--source', str(self.source), '--target', str(self.target), *map(str, args)])
        return code, err.getvalue()

    def test_waiting_replacement_exits_3_and_confirm_exits_0(self):
        self.system.add(100)
        self.system.status_fault = lambda system, child: None
        code, err = self.main('--restart', '--ready-timeout', '2')
        self.assertEqual(code, update.EXIT_AWAITING_CONFIRMATION)
        record = self.pending()[0]
        confirm = [shlex.split(line) for line in err.splitlines() if '--confirm' in line][0]
        self.assertEqual(confirm[confirm.index('--confirm') + 1], str(record))
        self.system.status_fault = None
        self.assertEqual(self.main('--confirm', record)[0], 0)
        self.assertEqual(phase_of(record), 'committed')

    def test_failure_after_stop_exits_1_with_commands_and_resume_exits_0(self):
        self.system.add(100)
        self.system.on_event = lambda system, event: (_ for _ in ()).throw(OSError('Simulated')) if event == 'exchange' else None
        code, err = self.main('--restart')
        self.assertEqual(code, 1)
        record = self.pending()[0]
        self.assertIn(f'--resume {shlex.quote(str(record))}', err)
        self.system.on_event = None
        self.assertEqual(self.main('--resume', record)[0], 0)
        self.assertEqual(len(self.system.launches), 1)

    def test_identity_change_retry_reproduces_every_option_of_the_invocation(self):
        shutil.rmtree(self.source)
        bundle(self.source, 'new', 'identifier "local.aquaspace" and certificate leaf = H"BBBB"')
        self.system.add(100)
        # The replacement takes control 100 s after its launch: only the longer timeout waits for it.
        self.system.status_fault = lambda system, child: ready(child) if system.now - system.launched_at >= 100 else None
        code, err = self.main('--restart', '--ready-timeout', '120')
        self.assertEqual(code, 1)
        self.assertNotIn('stop', self.system.events)
        retry = [shlex.split(line) for line in err.splitlines() if '--allow-identity-change' in line][0]
        self.assertEqual(retry[:2], ['python3', str(update.INSTALLER_SCRIPT)])
        code, _ = self.main(*retry[2:])
        self.assertEqual(code, 0)
        self.assertEqual(phase_of(self.target.parent.glob('.aquaspace-update-*').__next__()), 'committed')

    def test_conflicting_modes_are_usage_errors(self):
        for args in (['--restart', '--resume', 'x'], ['--confirm', 'x', '--recover', 'y'],
                     ['--recover', 'x', '--allow-identity-change'], ['--ready-timeout', '5'],
                     ['--restart', '--ready-timeout', '0']):
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                self.main(*args)
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.system.events, [])


class FakeLibProc:
    def __init__(self, executable):
        self.path = str(executable).encode()
        self.arg_list = ['--restore-session', '/directory with spaces/session.json']
        self.path_error = None
        self.info_error = None
        self.birth = 123
        self.change_birth = False
        self.info_reads = 0
        self.name = b''

    def proc_listpids(self, kind, uid, buffer, size):
        if buffer is None: return 4
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] = 1234
        return 4

    def proc_pidinfo(self, pid, flavor, argument, buffer, size):
        if self.info_error:
            ctypes.set_errno(self.info_error); return 0
        self.info_reads += 1
        info = ctypes.cast(buffer, ctypes.POINTER(update.BsdInfo)).contents
        info.pbi_pid = pid; info.pbi_uid = os.getuid(); info.pbi_status = 2
        info.pbi_name = self.name
        info.pbi_start_tvsec = self.birth + int(self.change_birth and self.info_reads > 1)
        info.pbi_start_tvusec = 456
        return ctypes.sizeof(update.BsdInfo)

    def proc_pidpath(self, pid, buffer, size):
        if self.path_error:
            ctypes.set_errno(self.path_error); return 0
        ctypes.memmove(buffer, self.path + b'\0', len(self.path)+1)
        return len(self.path)


class ProcessInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app = bundle(Path(self.tmp.name) / 'A folder with spaces/AquaSpace.app', 'new')
        self.lib = FakeLibProc(self.app / 'Contents/MacOS/AquaSpace')
        self.inspector = update.DarwinProcesses(lib=self.lib)

    def test_kernel_executable_identity_ignores_all_startup_arguments(self):
        for args in [[], ['--restore-session', '/tmp/a'], ['--config-path', 'a path', '--read-only']]:
            self.lib.arg_list = args
            result = self.inspector.managers()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].executable, self.app / 'Contents/MacOS/AquaSpace')
            self.assertEqual(result[0].identity, (1234, 123, 456, os.getuid()))

    def macos_xml(self):
        info = self.app / 'Contents/Info.plist'
        metadata = plistlib.loads(info.read_bytes())
        raw = info.read_bytes().replace(b'</dict>', b'<key>NSHumanReadableCopyright</key><string>\x03Apple</string></dict>')
        info.write_bytes(raw)
        return raw, metadata

    def test_macos_xml_conversion_preserves_manager_identity_and_exact_input(self):
        raw, metadata = self.macos_xml()
        with patch.object(update.subprocess, 'run', return_value=SimpleNamespace(
                stdout=plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY))) as convert:
            managers = self.inspector.managers()
        self.assertEqual(len(managers), 1)
        self.assertEqual(managers[0].bundle_id, 'local.aquaspace')
        self.assertEqual(convert.call_args.args[0], ['/usr/bin/plutil', '-convert', 'binary1', '-o', '-', '-'])
        self.assertEqual(convert.call_args.kwargs['input'], raw)
        self.assertTrue(convert.call_args.kwargs['check'])
        self.assertEqual(convert.call_args.kwargs['timeout'], 2)

    def test_failed_macos_xml_conversion_remains_a_contextual_admission_failure(self):
        self.macos_xml()
        with patch.object(update.subprocess, 'run', side_effect=update.subprocess.CalledProcessError(1, 'plutil')):
            with self.assertRaisesRegex(update.UpdateError, '1234'):
                self.inspector.managers()

    def test_native_conversion_cannot_admit_ambiguous_or_malformed_identity(self):
        self.macos_xml()
        for converted in [[], {'CFBundleIdentifier': ['local.aquaspace'], 'CFBundleExecutable': 'AquaSpace'},
                          {'CFBundleIdentifier': 'local.aquaspace', 'CFBundleExecutable': ['AquaSpace']}]:
            with self.subTest(converted=converted):
                with patch.object(update.subprocess, 'run', return_value=SimpleNamespace(
                        stdout=plistlib.dumps(converted, fmt=plistlib.FMT_BINARY))):
                    with self.assertRaises(update.UpdateError):
                        self.inspector.managers()

    def test_unreadable_executable_path_of_unrelated_process_is_skipped_not_a_blocker(self):
        # Regression: a Homebrew upgrade deleted a running opencode binary (ENOENT)
        # and the installer refused every update while that process lived. The
        # kernel's process name now classifies it, so one BSD record read is allowed.
        self.lib.name = b'opencode'
        for code in (errno.ENOENT, errno.EPERM):
            with self.subTest(code=code):
                self.lib.path_error = code
                self.assertEqual(self.inspector.managers(), [])

    def test_manager_with_deleted_executable_stays_visible_by_kernel_name(self):
        # An update can delete a manager's binary while it runs; it still controls windows.
        for name in (b'AquaSpace', b'AeroSpace'):
            with self.subTest(name=name):
                self.lib.path_error = errno.ENOENT
                self.lib.name = name
                managers = self.inspector.managers()
                self.assertEqual(len(managers), 1)
                self.assertEqual(managers[0].bundle_id, 'unidentified.window-manager')
                self.assertEqual(managers[0].identity, (1234, 123, 456, os.getuid()))

    def test_process_with_neither_path_nor_bsd_record_is_skipped(self):
        # Only protected processes deny both; this user's managers never do.
        self.lib.path_error = errno.EPERM
        self.lib.info_error = errno.EPERM
        self.assertEqual(self.inspector.managers(), [])

    def test_bundle_metadata_is_parsed_once_per_file_version(self):
        raw, metadata = self.macos_xml()
        reply = SimpleNamespace(stdout=plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY))
        with patch.object(update.subprocess, 'run', return_value=reply) as convert:
            for _ in range(3):
                self.assertEqual(self.inspector.managers()[0].bundle_id, 'local.aquaspace')
        self.assertEqual(convert.call_count, 1)
        # A replaced Info.plist (new inode and size) is read again, so identity changes are seen.
        info = self.app / 'Contents/Info.plist'
        replacement = info.with_name('Info.new')
        replacement.write_bytes(plistlib.dumps(dict(metadata, CFBundleIdentifier='bobko.aerospace')))
        replacement.replace(info)
        self.assertEqual(self.inspector.managers()[0].bundle_id, 'bobko.aerospace')

    def test_unrelated_helper_and_embedded_cli_need_no_bsd_identity_permission(self):
        for path in [Path(self.tmp.name) / 'system/protected-helper',
                     self.app / 'Contents/MacOS/aquaspace-cli']:
            with self.subTest(path=path):
                self.lib.path = str(path).encode()
                self.lib.info_error = errno.EPERM
                self.assertEqual(self.inspector.managers(), [])
                self.assertEqual(self.lib.info_reads, 0)

    def test_manager_with_denied_bsd_identity_still_blocks_update(self):
        self.lib.info_error = errno.EPERM
        with self.assertRaises(update.UpdateError): self.inspector.managers()

    def test_process_exiting_during_scan_is_not_a_phantom_manager(self):
        self.lib.path_error = errno.ESRCH
        self.assertEqual(self.inspector.managers(), [])

    def test_pid_reuse_during_executable_lookup_fails_closed(self):
        self.lib.change_birth = True
        with self.assertRaises(update.UpdateError): self.inspector.managers()

    def test_relocated_app_is_identified_by_bundle_metadata(self):
        info = self.app / 'Contents/Info.plist'
        data = plistlib.loads(info.read_bytes()); data['CFBundleIdentifier'] = 'bobko.aerospace'
        info.write_bytes(plistlib.dumps(data))
        self.assertEqual(self.inspector.managers()[0].bundle_id, 'bobko.aerospace')

    def test_unbundled_debug_manager_blocks_updates_instead_of_being_missed(self):
        for name in ('AeroSpaceApp', 'AquaSpaceApp'):
            self.lib.path = str(Path(self.tmp.name) / 'build/debug' / name).encode()
            processes = self.inspector.managers()
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0].bundle_id, 'unidentified.window-manager')

    def test_embedded_cli_is_not_mistaken_for_a_controlling_manager(self):
        self.lib.path = str(self.app / 'Contents/MacOS/aquaspace-cli').encode()
        self.assertEqual(self.inspector.managers(), [])


class NativeBackendTests(unittest.TestCase):
    def setUp(self):
        ProcessInspectionTests.setUp(self)
        self.native = object.__new__(update.NativeSystem)
        self.native.processes = self.inspector

    def test_cli_output_preserves_empty_fields_and_title_whitespace(self):
        for stdout in ('123\t1\t456\t\n', '123\t1\t456\t a title \n', '\tleading\t\r\n'):
            with self.subTest(stdout=stdout):
                reply = unittest.mock.Mock(returncode=0, stdout=stdout, stderr='')
                with patch('aquaspace_update.subprocess.run', return_value=reply):
                    self.assertEqual(self.native.run(['fake-cli']), stdout.rstrip('\r\n'))

    def test_native_launch_uses_launch_services_with_restore_session_arguments(self):
        session = Path(self.tmp.name) / 'a folder/session.json'
        child = self.inspector.inspect(1234)
        with patch.object(self.native, 'run') as launch, patch.object(self.native, 'managers', side_effect=[[], [child]]):
            process = self.native.launch(self.app / 'Contents/MacOS/AquaSpace', session,
                                         Path(self.tmp.name) / 'app.log')
        arguments = [str(a) for a in launch.call_args.args[0]]
        self.assertEqual(arguments[:4], ['/usr/bin/open', '-n', '-g', str(self.app)])
        self.assertEqual(arguments[-3:], ['--args', '--restore-session', str(session)])
        self.assertEqual(process.pid, 1234)

    def test_native_uncertain_launch_never_launches_fallback(self):
        with patch.object(self.native, 'run', side_effect=update.UpdateError('uncertain')) as launch:
            with patch.object(self.native, 'managers', return_value=[]):
                with self.assertRaises(update.UpdateError):
                    self.native.launch(self.app / 'Contents/MacOS/AquaSpace', None,
                                       Path(self.tmp.name) / 'app.log')
        self.assertEqual(launch.call_count, 1)

    def test_native_launch_refuses_existing_manager_before_open(self):
        with patch.object(self.native, 'run') as launch, patch.object(self.native, 'managers', return_value=[self.inspector.inspect(1234)]):
            with self.assertRaises(update.UpdateError):
                self.native.launch(self.app / 'Contents/MacOS/AquaSpace', None, Path(self.tmp.name) / 'app.log')
        launch.assert_not_called()

    def test_native_launch_rejects_wrong_bundle_and_times_out_without_retrying_open(self):
        wrong = update.Process(999, (1, 1), os.getuid(), Path('/other/AquaSpace'), 'local.aquaspace')
        for inventory in ([wrong], []):
            with self.subTest(inventory=inventory), patch.object(self.native, 'run') as launch:
                with patch.object(self.native, 'managers', side_effect=[[], inventory]), patch.object(self.native, 'monotonic', side_effect=[0, update.LAUNCH_APPEAR_TIMEOUT + 1]):
                    with self.assertRaises(update.UpdateError):
                        self.native.launch(self.app / 'Contents/MacOS/AquaSpace', None, Path(self.tmp.name) / 'app.log')
                self.assertEqual(launch.call_count, 1)

    def test_bundle_verification_uses_the_system_codesign(self):
        requirement = 'identifier "local.aquaspace" and certificate leaf = H"AAAA"'
        with patch.object(self.native, 'run') as tool, \
             patch.object(update, 'designated_requirement', return_value=requirement):
            self.assertEqual(self.native.verify_bundle(self.app), requirement)
        self.assertEqual({call.args[0][0] for call in tool.call_args_list}, {'/usr/bin/codesign'})

    def test_native_stop_rejects_reused_pid_before_sending_any_signal(self):
        old = update.Process(1234, (1, 2), os.getuid(), self.app / 'Contents/MacOS/AquaSpace', 'local.aquaspace')
        with patch('aquaspace_update.os.kill') as signaler:
            with self.assertRaises(update.UpdateError): self.native.stop(old)
        signaler.assert_not_called()

    def test_native_stop_uses_no_pause_or_resume_signals(self):
        old = self.inspector.inspect(1234)
        with patch('aquaspace_update.os.kill') as signaler: self.native.stop(old)
        signaler.assert_called_once_with(1234, signal.SIGKILL)

    def test_status_rejects_a_different_start_identity(self):
        old = self.inspector.inspect(1234)
        self.lib.birth = 999
        with patch.object(self.native, 'run') as cli:
            with self.assertRaises(update.UpdateError): self.native.status(old, 'fake-cli')
        cli.assert_not_called()

    def test_session_capture_checks_the_same_fork_pid_on_both_sides(self):
        old = self.inspector.inspect(1234)
        status = dict(pid=1234, phase='controlling', controlsWindows=True, readOnly=False, originalPids=[])
        cli_path = self.app / 'Contents/MacOS/aquaspace-cli'
        def query(args):
            self.assertEqual(args[0], cli_path)
            if args[1:] == ['management-status']: return json.dumps(status)
            if args[1:3] == ['list-windows', '--all']: return '42\twork\tfloating\tfalse'
            if args[1:3] == ['list-workspaces', '--all']: return 'work\th_tiles'
            return 'work' if args[1] == 'list-workspaces' else '42'
        with patch.object(self.native, 'run', side_effect=query) as cli:
            session = self.native.capture_session(old, cli_path)
        self.assertEqual(session['workspace'], 'work')
        self.assertEqual(sum(call.args[0][1:] == ['management-status'] for call in cli.call_args_list), 2)

    def test_session_capture_allows_empty_workspace_without_hiding_other_errors(self):
        old = self.inspector.inspect(1234)
        status = dict(pid=1234, phase='controlling', controlsWindows=True, readOnly=False, originalPids=[])
        def query(args):
            if args[1:] == ['management-status']: return json.dumps(status)
            if args[1:] == ['list-workspaces', '--focused']: return 'empty-test-workspace'
            if args[1:3] == ['list-windows', '--focused']: raise update.UpdateError('No window is focused')
            return ''
        with patch.object(self.native, 'run', side_effect=query):
            result = self.native.capture_session(old, 'fake-cli')
        self.assertEqual(result['focusedWindow'], '')
        self.assertEqual(result['workspace'], 'empty-test-workspace')
        with patch.object(self.native, 'run', side_effect=update.UpdateError('Uncertain ownership')):
            with self.assertRaises(update.UpdateError): self.native.capture_session(old, 'fake-cli')


class CommandHelpTests(unittest.TestCase):
    def test_help_does_not_initialize_a_native_backend(self):
        with patch.object(update, 'NativeSystem', side_effect=AssertionError('No native backend for help')):
            with patch('sys.stdout'):
                with self.assertRaises(SystemExit) as error: update.main(['--help'])
        self.assertEqual(error.exception.code, 0)


if __name__ == '__main__': unittest.main()
