"""AquaSpace's local updater. Recovery changes files and never launches an app.

The process backend is injected so the entire transaction can be exercised without
loading libproc, signaling processes, invoking a CLI, or accessing /Applications.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
import uuid
from xml.parsers.expat import ExpatError
from aquaspace_signing import designated_requirement, SigningError

MANAGER_IDS = frozenset(('local.aquaspace', 'local.aquaspace.debug',
                         'bobko.aerospace', 'bobko.aerospace.debug', 'unidentified.window-manager'))
MANAGER_EXECUTABLES = frozenset(('AquaSpace', 'AquaSpaceApp', 'AeroSpace', 'AeroSpaceApp'))
TERMINAL_PHASES = frozenset(('committed', 'recovered', 'aborted'))
PHASES = TERMINAL_PHASES | frozenset(('staging', 'prepared', 'stop_requested', 'stopped',
                                     'replacing', 'replaced', 'launch_attempted', 'child_started'))


class UpdateError(RuntimeError):
    pass


def read_bundle_metadata(path):
    """Use macOS's plist semantics for XML accepted by Foundation, not Expat.

    macOS 27 ships an Apple extension with a control character in an unrelated
    copyright field. Canonicalize the exact captured bytes, never sanitize or
    guess identity fields. Conversion errors retain fail-closed admission.
    """
    raw = path.read_bytes()
    try:
        metadata = plistlib.loads(raw)
    except ExpatError:
        canonical = subprocess.run(['/usr/bin/plutil', '-convert', 'binary1', '-o', '-', '-'],
                                   input=raw, capture_output=True, check=True, timeout=2)
        metadata = plistlib.loads(canonical.stdout)
    if not isinstance(metadata, dict):
        raise ValueError('Bundle metadata must be a dictionary.')
    for key in ('CFBundleIdentifier', 'CFBundleExecutable'):
        if key in metadata and not isinstance(metadata[key], str):
            raise ValueError('Bundle identity fields must be strings.')
    return metadata


@dataclass(frozen=True)
class Process:
    pid: int
    started: tuple[int, int]
    uid: int
    executable: Path
    bundle_id: str

    @property
    def identity(self):
        # Paths can change when a bundle is renamed; PID and kernel birth time do not.
        return (self.pid, *self.started, self.uid)

    def record(self):
        return dict(pid=self.pid, started=list(self.started), uid=self.uid,
                    executable=str(self.executable), bundle_id=self.bundle_id)


class BsdInfo(ctypes.Structure):
    # Public Darwin struct proc_bsdinfo (sys/proc_info.h), including its padding.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'pbi_flags', 'pbi_status', 'pbi_xstatus', 'pbi_pid', 'pbi_ppid', 'pbi_uid',
        'pbi_gid', 'pbi_ruid', 'pbi_rgid', 'pbi_svuid', 'pbi_svgid', 'rfu_1')]
    _fields_ += [('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'pbi_nfiles', 'pbi_pgid', 'pbi_pjobc', 'e_tdev', 'e_tpgid')]
    _fields_ += [('pbi_nice', ctypes.c_int32), ('pbi_start_tvsec', ctypes.c_uint64),
                ('pbi_start_tvusec', ctypes.c_uint64)]


class DarwinProcesses:
    """Use kernel executable paths, never pgrep/argv or application display names."""
    def __init__(self, lib=None):
        if lib is None:
            if sys.platform != 'darwin':
                raise UpdateError('Installing requires macOS; offline tests are portable.')
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
            lib.proc_listpids.restype = ctypes.c_int
            lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            lib.proc_pidinfo.restype = ctypes.c_int
            lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_pidpath.restype = ctypes.c_int
        self.lib = lib

    def _info(self, pid):
        info = BsdInfo()
        ctypes.set_errno(0)
        count = self.lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if count != ctypes.sizeof(info):
            if ctypes.get_errno() == errno.ESRCH: return None
            raise UpdateError(f'Cannot establish process {pid} identity; refusing the update.')
        if info.pbi_status == 5: return None  # SZOMB: an exited process cannot control windows.
        if info.pbi_pid != pid:
            raise UpdateError('Process identity changed during inspection.')
        return info

    def _executable(self, pid):
        buf = ctypes.create_string_buffer(4096)  # PROC_PIDPATHINFO_MAXSIZE
        ctypes.set_errno(0)
        count = self.lib.proc_pidpath(pid, buf, len(buf))
        if count <= 0:
            code = ctypes.get_errno()
            if code == errno.ESRCH: return None
            raise UpdateError(f'Cannot inspect process {pid} executable; refusing the update.')
        return Path(os.fsdecode(buf.value))

    def inspect(self, pid):
        # Protected unrelated helpers can deny BSD records on macOS. Classify the
        # executable first; full birth/UID verification is required for managers.
        # An unreadable path (for example, a binary deleted by an upgrade while the
        # process runs) cannot identify a manager, so that process is skipped.
        try: executable = self._executable(pid)
        except UpdateError: return None
        if executable is None: return None
        identifier = ''
        if executable.parent.name == 'MacOS' and executable.parent.parent.name == 'Contents':
            try:
                info = read_bundle_metadata(executable.parent.parent / 'Info.plist')
                # Embedded CLIs/helpers belong to the bundle but are not its manager.
                if info.get('CFBundleExecutable') == executable.name:
                    identifier = info.get('CFBundleIdentifier', '')
            except (OSError, ValueError, plistlib.InvalidFileException, ExpatError, subprocess.SubprocessError) as error:
                # Only a manager-named executable needs its bundle identity; skip other apps.
                if executable.name not in MANAGER_EXECUTABLES: return None
                raise UpdateError(f'Cannot classify application process {pid} ({executable}): {error}') from error
        if identifier not in MANAGER_IDS and executable.name in MANAGER_EXECUTABLES:
            # SwiftPM/debug executables need not live inside a bundle. Treat an
            # unclassified known manager as a blocker, never as an absent peer.
            identifier = 'unidentified.window-manager'
        if identifier not in MANAGER_IDS: return None
        before = self._info(pid)
        if before is None: return None
        verified_executable = self._executable(pid)
        if verified_executable is None: return None
        after = self._info(pid)
        if after is None: return None
        birth = lambda info: (info.pbi_pid, info.pbi_start_tvsec, info.pbi_start_tvusec, info.pbi_uid)
        if birth(before) != birth(after) or executable != verified_executable:
            raise UpdateError('Process identity changed during inspection; refusing the update.')
        return Process(pid, (after.pbi_start_tvsec, after.pbi_start_tvusec),
                       after.pbi_uid, executable, identifier)

    def managers(self):
        # proc_listpids returns byte counts. Reserve headroom and refuse truncation.
        ctypes.set_errno(0)
        needed = self.lib.proc_listpids(4, os.getuid(), None, 0)  # PROC_UID_ONLY
        if needed <= 0: raise UpdateError('Cannot enumerate user processes.')
        for _ in range(3):
            buffer = (ctypes.c_int * (needed // ctypes.sizeof(ctypes.c_int) + 64))()
            count = self.lib.proc_listpids(4, os.getuid(), buffer, ctypes.sizeof(buffer))
            if count <= 0 or count % ctypes.sizeof(ctypes.c_int):
                raise UpdateError('Incomplete process inventory; refusing the update.')
            if count < ctypes.sizeof(buffer): break
            needed = ctypes.sizeof(buffer) * 2
        else:
            raise UpdateError('Process inventory kept changing; refusing the update.')
        result = []
        for pid in sorted(set(buffer[:count // ctypes.sizeof(ctypes.c_int)])):
            if pid <= 0: continue
            process = self.inspect(pid)
            if process and process.uid == os.getuid() and process.bundle_id in MANAGER_IDS:
                result.append(process)
        return result


def bundle_metadata(app):
    if app.is_symlink(): raise UpdateError('An application bundle must not be a symlink.')
    try:
        info = read_bundle_metadata(app / 'Contents/Info.plist')
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError, subprocess.SubprocessError) as error:
        raise UpdateError(f'Invalid AquaSpace bundle: {app}') from error
    if info.get('CFBundleIdentifier') != 'local.aquaspace' or info.get('CFBundleExecutable') != 'AquaSpace':
        raise UpdateError(f'Refusing to replace an application other than AquaSpace: {app}')
    for name in ['AquaSpace', 'aquaspace-cli']:
        path = app / 'Contents/MacOS' / name
        if path.is_symlink() or not path.is_file():
            raise UpdateError(f'Missing regular executable: {path}')
    return info


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def replace_directory(source, target):
    # Both live on the target volume; recovery handles interruption between renames.
    source.rename(target)
    sync_directory(target.parent)
    if source.parent != target.parent: sync_directory(source.parent)


def archive_bundle(bundle):
    """Keep recovery evidence without leaving another discoverable application.

    Finish and sync the archive before removing the bundle. A retry after an
    interrupted removal reuses the complete archive, never a partial directory.
    Only terminal transactions may call this; pending recovery stays unchanged.
    """
    archive = bundle.with_name(bundle.name + '.tar.gz')
    if not archive.exists():
        temporary = archive.with_name(archive.name + '.tmp')
        with tarfile.open(temporary, 'w:gz') as stream:
            stream.add(bundle, arcname=bundle.name)
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
        temporary.replace(archive)
        sync_directory(bundle.parent)
    shutil.rmtree(bundle)
    sync_directory(bundle.parent)


class InstallLock:
    def __init__(self, parent):
        self.path, self.fd = parent / f'.aquaspace-install-{os.getuid()}.lock', None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.set_inheritable(self.fd, False)
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('The persistent install lock has an unexpected owner or file type.')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.fd, 0)
            os.write(self.fd, f'{os.getpid()}\n'.encode())
        except OSError as error:
            if self.fd is not None: os.close(self.fd); self.fd = None
            raise UpdateError('Another installer is running, or the install lock cannot be acquired.') from error
        return self

    def __exit__(self, *args):
        # Never unlink a flock file: replacing its inode would permit two owners.
        if self.fd is not None: os.close(self.fd); self.fd = None


def valid_status(value, process):
    return (isinstance(value, dict) and value.get('pid') == process.pid and
            value.get('controlsWindows') is True and value.get('readOnly') is False and
            value.get('phase') == 'controlling' and value.get('originalPids') == [])


def validate_identity_session(session, uid):
    if (not isinstance(session, dict) or not isinstance(session.get('windows'), list)
            or not isinstance(session.get('workspaces'), list) or not isinstance(session.get('owners'), dict)
            or not isinstance(session.get('workspace'), str) or not isinstance(session.get('focusedWindow'), str)):
        raise UpdateError('Invalid identity session; leaving the manager alone.')
    ids = set()
    for row in session['windows']:
        if (not isinstance(row, list) or len(row) != 4 or any(not isinstance(x, str) for x in row)
                or not row[0].isascii() or not row[0].isdigit() or not 0 < int(row[0]) <= 0xffffffff
                or str(int(row[0])) != row[0] or row[0] in ids or not row[1] or row[3] not in ('true', 'false')):
            raise UpdateError('Invalid identity window rows; leaving the manager alone.')
        ids.add(row[0])
    if any(not isinstance(row, list) or len(row) != 2 or any(not isinstance(x, str) or not x for x in row)
           for row in session['workspaces']):
        raise UpdateError('Invalid identity workspace rows; leaving the manager alone.')
    for window_id, owner in session['owners'].items():
        if window_id not in ids or not isinstance(owner, dict):
            raise UpdateError('Identity owner is not a recorded window.')
        numbers = ('pid', 'uid', 'startedSeconds', 'startedMicroseconds', 'uniqueId', 'execVersion')
        if (any(type(owner.get(k)) is not int for k in numbers) or not 0 < owner['pid'] <= 0x7fffffff
                or owner['uid'] != uid or not 0 < owner['startedSeconds'] <= 0xffffffffffffffff
                or not 0 <= owner['startedMicroseconds'] < 1000000 or not 0 < owner['uniqueId'] <= 0xffffffffffffffff
                or not 0 < owner['execVersion'] <= 0x7fffffff
                or not isinstance(owner.get('executable'), str) or not owner['executable'].startswith('/')
                or not isinstance(owner.get('imageUUID'), list) or len(owner['imageUUID']) != 16
                or any(type(x) is not int or not 0 <= x <= 255 for x in owner['imageUUID']) or not any(owner['imageUUID'])):
            raise UpdateError('Invalid application identity; leaving the manager alone.')


class NativeSystem:
    def __init__(self):
        self.processes = DarwinProcesses()

    def managers(self): return self.processes.managers()
    def inspect(self, pid): return self.processes.inspect(pid)
    def monotonic(self): return time.monotonic()
    def sleep(self, seconds): time.sleep(seconds)

    @staticmethod
    def run(args, timeout=2):
        result = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise UpdateError(result.stderr.strip() or f'Command failed: {args[0]}')
        # Empty final CLI fields and title whitespace are data, not decoration.
        return result.stdout.rstrip('\r\n')

    def verify_bundle(self, path):
        self.run(['codesign', '--verify', '--strict', path], timeout=15)
        self.run(['codesign', '--verify', '--strict', path / 'Contents/MacOS/aquaspace-cli'], timeout=15)
        try:
            requirement = designated_requirement(path)
        except SigningError as error:
            raise UpdateError(str(error)) from error
        if 'cdhash' in requirement or 'certificate' not in requirement:
            raise UpdateError('This build uses an unstable signing identity. Rebuild with AquaSpace persistent signing before installing.')

    def capture_session(self, old, cli):
        def query(*args): return self.run([cli, *args])
        initial_status = json.loads(query('management-status'))
        if not valid_status(initial_status, old):
            raise UpdateError('The installed CLI does not own the running AquaSpace process.')
        schema = initial_status.get('sessionSchema')
        if schema is not None:
            if type(schema) is not int or schema != 2:
                raise UpdateError('Unsupported handoff identity schema; leaving the manager alone.')
            session = json.loads(query('handoff-session'))
            validate_identity_session(session, os.getuid())
            final_status = json.loads(query('management-status'))
            if (not valid_status(final_status, old) or type(final_status.get('sessionSchema')) is not int
                    or final_status.get('sessionSchema') != schema):
                raise UpdateError('Window-manager ownership changed while saving the identity session.')
            return session
        # Old releases have no authenticated per-window identity export. Keep
        # immediate startup compatibility; they cannot authorize delayed moves.
        windows = [line.split('\t') for line in query('list-windows', '--all', '--format',
            '%{window-id}\t%{workspace}\t%{window-layout}\t%{window-is-fullscreen}').splitlines()]
        workspaces = [line.split('\t') for line in query('list-workspaces', '--all', '--format',
            '%{workspace}\t%{workspace-root-container-layout}').splitlines()]
        workspace = query('list-workspaces', '--focused')
        try:
            focused = query('list-windows', '--focused', '--format', '%{window-id}')
        except UpdateError as error:
            if str(error) != 'No window is focused': raise
            focused = ''  # An empty workspace is valid session metadata.
        if (any(len(row) != 4 or not row[0].isdigit() or not row[1] or row[3] not in ('true', 'false') for row in windows)
                or any(len(row) != 2 or not row[0] for row in workspaces)):
            raise UpdateError('Invalid workspace metadata; leaving the current manager alone.')
        if not valid_status(json.loads(query('management-status')), old):
            raise UpdateError('Window-manager ownership changed while saving the session.')
        return dict(windows=windows, workspaces=workspaces, workspace=workspace, focusedWindow=focused)

    def stop(self, process):
        current = self.inspect(process.pid)
        if current is None: return
        if current.identity != process.identity or current.executable != process.executable or current.uid != os.getuid():
            raise UpdateError('Process identity changed; refusing to signal it.')
        # SIGKILL bypasses normal quit-time recentering. No SIGSTOP/SIGCONT and no
        # watchdog exist: an interrupted installer cannot leave a dormant controller.
        try: os.kill(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass

    def launch(self, executable, session, log):
        if self.managers():
            raise UpdateError('A window manager appeared before launch; refusing to open another.')
        # Direct Popen inherits the terminal/agent's TCC responsibility. Open the
        # exact bundle through Launch Services so grants belong to AquaSpace.
        arguments = ['/usr/bin/open', '-n', '-g', executable.parents[2],
                     '--stdin', '/dev/null', '--stdout', log, '--stderr', log]
        if session: arguments += ['--args', '--restore-session', session]
        self.run(arguments, timeout=10)
        deadline = self.monotonic() + 5
        while True:
            processes = self.managers()
            if processes:
                if (len(processes) == 1 and processes[0].executable == executable
                        and processes[0].bundle_id == 'local.aquaspace'):
                    return processes[0]
                raise UpdateError('Unexpected manager after launch; no recovery application will be started.')
            if self.monotonic() >= deadline:
                raise UpdateError('Launch outcome is uncertain; no recovery application will be started.')
            self.sleep(.05)

    def status(self, child, cli):
        current = self.inspect(child.pid)
        if (current is None or current.identity != child.identity
                or current.executable != child.executable or current.bundle_id != child.bundle_id):
            raise UpdateError('The replacement AquaSpace exited during startup.')
        return json.loads(self.run([cli, 'management-status'], timeout=.5))


class Installer:
    def __init__(self, source, target, system, timeout=5, lock_parent=None):
        self.source, self.target = Path(source).absolute(), Path(target).absolute()
        self.system, self.timeout = system, timeout
        self.lock_parent = Path(lock_parent) if lock_parent is not None else self.target.parent
        self.checkpoint = lambda phase: None  # Fault injection seam; never configured by the CLI.

    def journal(self, transaction):
        if transaction.is_symlink() or transaction.parent != self.target.parent or not transaction.name.startswith('.aquaspace-update-'):
            raise UpdateError('Recovery record is outside this installation directory.')
        try:
            data = json.loads((transaction / 'journal.json').read_text())
            if data.get('schema') != 1 or data.get('target') != str(self.target) or data.get('phase') not in PHASES:
                raise ValueError('Invalid journal identity or phase')
            return data
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise UpdateError(f'Unreadable update record; no process actions taken: {transaction}') from error

    def pending_transactions(self):
        return [p for p in sorted(self.target.parent.glob('.aquaspace-update-*'))
                if self.journal(p)['phase'] not in TERMINAL_PHASES]

    def _archive_completed_backups(self):
        for transaction in sorted(self.target.parent.glob('.aquaspace-update-*')):
            if self.journal(transaction)['phase'] in TERMINAL_PHASES:
                for name in ('previous.app', 'unconfirmed.app', 'new.app'):
                    bundle = transaction / name
                    if bundle.exists():
                        if bundle.is_symlink():
                            raise UpdateError('A backup bundle must not be a symlink.')
                        # Removal may have been interrupted after the durable
                        # archive was published. An aborted staging copy may
                        # also be incomplete and is evidence, not an install.
                        if name != 'new.app' and not bundle.with_name(bundle.name + '.tar.gz').is_file():
                            bundle_metadata(bundle)
                        archive_bundle(bundle)

    def archive_completed_backups(self):
        with InstallLock(self.lock_parent):
            self._archive_completed_backups()

    def _expect(self, expected):
        actual = self.system.managers()
        if sorted(p.identity for p in actual) != sorted(p.identity for p in expected):
            raise UpdateError('Window-manager inventory changed or is uncertain; refusing to continue.')

    def _phase(self, transaction, state, phase, **fields):
        state.update(fields, phase=phase)
        atomic_json(transaction / 'journal.json', state)
        self.checkpoint(phase)

    def install(self, restart=False, allow_running_stock=False):
        if restart and allow_running_stock:
            raise UpdateError('Installing beside running stock AeroSpace must be file-only.')
        with InstallLock(self.lock_parent):
            pending = self.pending_transactions()
            if pending:
                raise UpdateError(f'An unfinished update blocks new launches. Review or recover: {pending[0]}')
            self._archive_completed_backups()
            metadata = bundle_metadata(self.source)
            if self.target == self.source or self.target in self.source.parents or self.source in self.target.parents:
                raise UpdateError('Build and installation paths must be separate.')
            self.system.verify_bundle(self.source)
            existing = self.system.managers()
            old = existing[0] if len(existing) == 1 else None
            retained = existing if (allow_running_stock and old is not None and old.bundle_id == 'bobko.aerospace'
                                    and old.executable == Path('/Applications/AeroSpace.app/Contents/MacOS/AeroSpace')) else []
            if retained: old = None
            if existing and not retained and (not restart or old is None or old.bundle_id != 'local.aquaspace' or
                             old.executable != self.target / 'Contents/MacOS/AquaSpace'):
                raise UpdateError('Close other window managers first. --restart can replace only the sole installed AquaSpace.')
            had_target = self.target.exists() or self.target.is_symlink()
            if had_target: bundle_metadata(self.target)
            if old and not had_target: raise UpdateError('The running installation disappeared.')
            transaction = self.target.parent / ('.aquaspace-update-' + uuid.uuid4().hex)
            transaction.mkdir(mode=0o700)
            state = dict(schema=1, target=str(self.target), version=metadata.get('CFBundleShortVersionString'),
                         had_target=had_target, old=old.record() if old else None)
            try:
                self._phase(transaction, state, 'staging')
                staged = transaction / 'new.app'
                shutil.copytree(self.source, staged, symlinks=True)
                self.system.verify_bundle(staged)
                bundle_metadata(staged)
                main_hash = sha256(staged / 'Contents/MacOS/AquaSpace')
                cli_hash = sha256(staged / 'Contents/MacOS/aquaspace-cli')
                manifest_path = self.source.parent / 'build.json'
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text())
                    if (manifest.get('app_sha256') != main_hash or manifest.get('version') != state['version'] or
                            ('cli_sha256' in manifest and manifest['cli_sha256'] != cli_hash)):
                        raise UpdateError('The staged app/CLI does not match its build manifest.')
                    atomic_json(transaction / 'build.json', manifest)
                self._phase(transaction, state, 'prepared', main_sha256=main_hash, cli_sha256=cli_hash)
                session = None
                if old:
                    snapshot = self.system.capture_session(old, self.target / 'Contents/MacOS/aquaspace-cli')
                    session = transaction / 'session.json'; atomic_json(session, snapshot)
                    self._expect([old])
                    self._phase(transaction, state, 'stop_requested')
                    self.system.stop(old)
                    deadline = self.system.monotonic() + self.timeout
                    while True:
                        current = self.system.inspect(old.pid)
                        if current is None or current.identity != old.identity: break
                        if self.system.monotonic() >= deadline:
                            raise UpdateError('The old AquaSpace did not exit. No replacement was launched.')
                        self.system.sleep(.02)
                self._expect(retained)
                self._phase(transaction, state, 'stopped')
                self._expect(retained)
                self._phase(transaction, state, 'replacing')
                if had_target: replace_directory(self.target, transaction / 'previous.app')
                replace_directory(staged, self.target)
                self._phase(transaction, state, 'replaced')
                if restart:
                    self._expect([])
                    # Persist intent before spawn. Even a crash before recording its PID
                    # blocks a retry; neither recovery nor a watchdog can launch a peer.
                    self._phase(transaction, state, 'launch_attempted')
                    child = self.system.launch(self.target / 'Contents/MacOS/AquaSpace', session, transaction / 'app.log')
                    self._phase(transaction, state, 'child_started', child=child.record())
                    deadline = self.system.monotonic() + self.timeout
                    while True:
                        self._expect([child])
                        try: status = self.system.status(child, self.target / 'Contents/MacOS/aquaspace-cli')
                        except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired): status = None
                        if valid_status(status, child): break
                        if self.system.monotonic() >= deadline:
                            raise UpdateError('Replacement ownership could not be verified. It was left alone; no rollback was launched.')
                        self.system.sleep(.05)
                    self._expect([child])
                self._phase(transaction, state, 'committed')
                if had_target: archive_bundle(transaction / 'previous.app')
                return transaction
            except Exception as error:
                # Prepared files are unused by any app. After a stop/spawn attempt,
                # retain the journal and leave all processes alone, even on false
                # negatives, timeouts, or an uncertain Launch Services outcome.
                if state.get('phase') in ('staging', 'prepared'):
                    self._phase(transaction, state, 'aborted')
                raise UpdateError(f'{error}\nUpdate record: {transaction}\nNo recovery app was launched.') from error

    def recover(self, transaction):
        transaction = Path(transaction).absolute()
        with InstallLock(self.lock_parent):
            state = self.journal(transaction)
            if state['phase'] in TERMINAL_PHASES:
                raise UpdateError('This update is already complete; it is not a pending recovery.')
            self._expect([])
            previous = transaction / 'previous.app'
            if previous.exists():
                bundle_metadata(previous)
                if self.target.exists():
                    bundle_metadata(self.target)
                    if sha256(self.target / 'Contents/MacOS/AquaSpace') != state.get('main_sha256'):
                        raise UpdateError('The installation changed after this update; refusing to overwrite it.')
                    replace_directory(self.target, transaction / 'unconfirmed.app')
                self._expect([])
                replace_directory(previous, self.target)
            # A first installation has no old bundle to restore; leave its files
            # available. Recovery never starts/stops/resumes any process.
            self._phase(transaction, state, 'recovered')
            return transaction


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='Install a staged AquaSpace build. Recovery never launches an application.')
    parser.add_argument('--source', type=Path, default=root / '.debug/aquaspace/release/AquaSpace.app')
    parser.add_argument('--target', type=Path, default=Path('/Applications/AquaSpace.app'))
    parser.add_argument('--restart', action='store_true', help='Save the sole installed AquaSpace session, stop it, wait for exit, then launch the replacement once')
    parser.add_argument('--recover', type=Path, metavar='UPDATE_DIRECTORY', help='Restore files from an unfinished update with all managers stopped; never launches an app')
    parser.add_argument('--archive-backups', action='store_true', help='Archive bundles from completed updates without starting or stopping apps')
    parser.add_argument('--allow-running-stock', action='store_true', help='File-only repair of inactive AquaSpace while the sole stock AeroSpace remains running')
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.restart, args.recover, args.archive_backups)) > 1:
        parser.error('--restart, --recover and --archive-backups cannot be combined')
    if args.allow_running_stock and (args.restart or args.recover or args.archive_backups):
        parser.error('--allow-running-stock is only available for file-only installation')
    try:
        # All CLI invocations for this user share one lock, even with different
        # --target directories or spellings. Tests inject a temporary lock root.
        installer = Installer(args.source, args.target, NativeSystem(), lock_parent=Path('/tmp'))
        if args.archive_backups:
            installer.archive_completed_backups()
            print('Completed update backups archived; no app launched or stopped.')
            return 0
        transaction = installer.recover(args.recover) if args.recover else installer.install(restart=args.restart, allow_running_stock=args.allow_running_stock)
        print(('Recovered files; no app launched.' if args.recover else 'Installed AquaSpace' + (' and verified sole ownership.' if args.restart else '; no app launched.')) + f'\nUpdate record and backup: {transaction}')
        return 0
    except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f'AquaSpace update stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
