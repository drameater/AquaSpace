"""Offline only: fake process/kernel APIs and temporary application bundles."""
import ctypes
import errno
import json
import os
from pathlib import Path
import plistlib
import signal
import sys
import tempfile
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


def bundle(path, version):
    (path / 'Contents/MacOS').mkdir(parents=True)
    (path / 'Contents/MacOS/AquaSpace').write_text(version)
    (path / 'Contents/MacOS/aquaspace-cli').write_text('cli-' + version)
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.aquaspace', 'CFBundleExecutable': 'AquaSpace',
        'CFBundleShortVersionString': version}))
    return path


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

    def add(self, pid, kind='local.aquaspace', path=None, birth=1):
        process = update.Process(pid, (birth, pid), os.getuid(),
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

    def inspect(self, pid):
        return self.running.get(pid)

    def verify_bundle(self, path):
        update.bundle_metadata(path)
        self.event('verify')

    def capture_session(self, old, cli):
        self.event('capture')
        return {'windows': [['42', 'work', 'floating', 'false']],
                'workspaces': [['work', 'h_tiles']], 'workspace': 'work', 'focusedWindow': '42'}

    def stop(self, process):
        self.event('stop')
        current = self.inspect(process.pid)
        if current is None or current.identity != process.identity:
            raise update.UpdateError('Process identity changed; refusing to signal it')
        if self.stop_never_exits: return
        if self.stop_delay:
            self.stop_deadline = self.now + self.stop_delay
        else:
            self.running.pop(process.pid)
            self.event('old-exited')

    def launch(self, executable, session, log):
        self.event('launch')
        child = self.add(200)
        self.launches.append(child)
        if self.spawn_raises_after_start: raise OSError('Spawn outcome uncertain')
        return child

    def status(self, child, cli):
        self.event('status')
        if self.status_fault: return self.status_fault(self, child)
        return {'pid': child.pid, 'phase': 'controlling', 'controlsWindows': True,
                'readOnly': False, 'originalPids': []}

    def monotonic(self): return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.stop_deadline is not None and self.now >= self.stop_deadline:
            self.running.pop(100, None); self.stop_deadline = None
            self.event('old-exited')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aquaspace-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = bundle(self.root / 'build/AquaSpace.app', 'new')
        self.target = bundle(self.root / 'Applications/AquaSpace.app', 'old')
        self.system = FakeSystem(self.target)
        self.installer = update.Installer(self.source, self.target, self.system, timeout=.3)

    def activate(self): return self.installer.install(restart=True)

    def pending(self): return self.installer.pending_transactions()

    def assert_single_launch(self):
        self.assertLessEqual(self.system.maximum_controllers, 1)
        self.assertLessEqual(len(self.system.launches), 1)

    def test_success_waits_for_old_exit_before_launch_and_restores_metadata(self):
        self.system.add(100)
        self.system.stop_delay = .1
        transaction = self.activate()
        self.assertLess(self.system.events.index('old-exited'), self.system.events.index('launch'))
        self.assertEqual(self.system.running.keys(), {200})
        self.assertEqual(json.loads((transaction / 'session.json').read_text())['workspace'], 'work')
        self.assertEqual(json.loads((transaction / 'journal.json').read_text())['phase'], 'committed')
        self.assertEqual((self.target / 'Contents/MacOS/AquaSpace').read_text(), 'new')
        self.assertEqual((transaction / 'previous.app/Contents/MacOS/AquaSpace').read_text(), 'old')
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

    def test_restart_without_active_manager_launches_once(self):
        self.activate()
        self.assertEqual(len(self.system.launches), 1)
        self.assert_single_launch()

    def test_stock_aerospace_is_never_stopped_or_launched(self):
        self.system.add(100, 'bobko.aerospace', self.root / 'AeroSpace.app/Contents/MacOS/AeroSpace')
        with self.assertRaises(update.UpdateError): self.activate()
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
        for checkpoint in ['staging', 'prepared', 'stop_requested', 'stopped',
                           'replacing', 'replaced', 'launch_attempted', 'child_started']:
            with self.subTest(checkpoint=checkpoint):
                self.setUp(); self.system.add(100)
                def crash(phase):
                    if phase == checkpoint: raise KeyboardInterrupt('Simulated abrupt exit')
                self.installer.checkpoint = crash
                with self.assertRaises(KeyboardInterrupt): self.activate()
                self.installer.checkpoint = lambda phase: None
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


class FakeLibProc:
    def __init__(self, executable):
        self.path = str(executable).encode()
        self.arg_list = ['--restore-session', '/directory with spaces/session.json']
        self.path_error = None
        self.birth = 123
        self.change_birth = False
        self.info_reads = 0

    def proc_listpids(self, kind, uid, buffer, size):
        if buffer is None: return 4
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] = 1234
        return 4

    def proc_pidinfo(self, pid, flavor, argument, buffer, size):
        self.info_reads += 1
        info = ctypes.cast(buffer, ctypes.POINTER(update.BsdInfo)).contents
        info.pbi_pid = pid; info.pbi_uid = os.getuid(); info.pbi_status = 2
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

    def test_inaccessible_process_information_fails_closed(self):
        self.lib.path_error = errno.EPERM
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
        self.lib.path = str(Path(self.tmp.name) / 'build/debug/AeroSpaceApp').encode()
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
        self.native.children = {}

    def test_native_launch_retains_child_with_restore_session_arguments(self):
        session = Path(self.tmp.name) / 'a folder/session.json'
        child = unittest.mock.Mock(pid=1234)
        child.poll.return_value = None
        with patch('aquaspace_update.subprocess.Popen', return_value=child) as launch:
            process = self.native.launch(self.app / 'Contents/MacOS/AquaSpace', session,
                                         Path(self.tmp.name) / 'app.log')
        self.assertIs(self.native.children[1234], child)
        self.assertEqual(launch.call_args.args[0],
                         [str(self.app / 'Contents/MacOS/AquaSpace'), '--restore-session', str(session)])
        self.assertEqual(process.pid, 1234)
        self.assertTrue(launch.call_args.kwargs['close_fds'])

    def test_native_uncertain_launch_keeps_handle_without_launching_fallback(self):
        child = unittest.mock.Mock(pid=1234)
        with patch('aquaspace_update.subprocess.Popen', return_value=child) as launch:
            with patch.object(self.native, 'inspect', return_value=None):
                with self.assertRaises(update.UpdateError):
                    self.native.launch(self.app / 'Contents/MacOS/AquaSpace', None,
                                       Path(self.tmp.name) / 'app.log')
        self.assertEqual(launch.call_count, 1)
        self.assertIs(self.native.children[1234], child)

    def test_native_stop_rejects_reused_pid_before_sending_any_signal(self):
        old = update.Process(1234, (1, 2), os.getuid(), self.app / 'Contents/MacOS/AquaSpace', 'local.aquaspace')
        with patch('aquaspace_update.os.kill') as signaler:
            with self.assertRaises(update.UpdateError): self.native.stop(old)
        signaler.assert_not_called()

    def test_native_stop_uses_no_pause_or_resume_signals(self):
        old = self.inspector.inspect(1234)
        with patch('aquaspace_update.os.kill') as signaler: self.native.stop(old)
        signaler.assert_called_once_with(1234, signal.SIGKILL)

    def test_status_uses_child_handle_and_rejects_a_different_start_identity(self):
        old = self.inspector.inspect(1234)
        child = unittest.mock.Mock(); child.poll.return_value = None
        self.native.children[1234] = child
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


class CommandHelpTests(unittest.TestCase):
    def test_help_does_not_initialize_a_native_backend(self):
        with patch.object(update, 'NativeSystem', side_effect=AssertionError('No native backend for help')):
            with patch('sys.stdout'):
                with self.assertRaises(SystemExit) as error: update.main(['--help'])
        self.assertEqual(error.exception.code, 0)


if __name__ == '__main__': unittest.main()
