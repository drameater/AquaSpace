"""AquaSpace's local updater. Recovery changes files and never launches an app.

The process backend is injected so the entire transaction can be exercised without
loading libproc, signaling processes, invoking a CLI, or accessing /Applications.

Installer._start_replacement is the only launch site. Only an explicit --restart or
--resume reaches it; no error handler, recovery path, or watchdog launches a manager.
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
import shlex
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
# A record in these phases proves that no process was stopped and no file was replaced.
PRE_STOP_PHASES = frozenset(('staging', 'prepared'))
PHASES = TERMINAL_PHASES | PRE_STOP_PHASES | frozenset((
    'stop_requested', 'stopped', 'replacing', 'replaced', 'launch_attempted', 'child_started'))
# In these phases the new bundle is in place, so a running AquaSpace can be confirmed.
CONFIRMABLE_PHASES = frozenset(('replaced', 'launch_attempted', 'child_started'))
RECORD_PREFIX = '.aquaspace-update-'
# A record is created under this name and renamed after its first journal write.
NEW_RECORD_PREFIX = '.aquaspace-new-update-'
KEEP_COMPLETED_RECORDS = 5
READY_TIMEOUT = 30
# Launch Services can start an app some seconds after `open` returns or times out.
LAUNCH_SETTLE_SECONDS = 30
# How long `open` may take to produce a process before the launch outcome is uncertain.
LAUNCH_APPEAR_TIMEOUT = 10
EXIT_AWAITING_CONFIRMATION = 3
EXIT_INTERRUPTED = 130
INSTALLER_SCRIPT = Path(__file__).resolve().with_name('install-aquaspace.py')
# Per-user, so another account cannot pre-create the lock and block updates.
USER_STATE_DIRECTORY = Path.home() / 'Library/Application Support/AquaSpace/Updater'
# Updaters before 2026-11 created and locked their lock file here; hold it too.
LEGACY_LOCK_DIRECTORIES = (Path('/tmp'),)
UNREADABLE_EXECUTABLE = Path('/(unreadable executable)')


class UpdateError(RuntimeError):
    pass


class NotSignaled(UpdateError):
    """The stop was refused before any signal was sent."""


class AwaitingConfirmation(UpdateError):
    """The replacement runs with the expected identity but has not taken control yet."""


class ExchangeUnsupported(OSError):
    """The volume cannot swap two paths atomically."""


class PathUnavailable(UpdateError):
    pass


def canonical(path):
    """Resolve the parent once so every later comparison uses the kernel's spelling.

    The last component stays as given: a bundle that is a symlink must still be seen
    (and refused) as a symlink.
    """
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    return path.parent.resolve() / path.name


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
        canonical_plist = subprocess.run(['/usr/bin/plutil', '-convert', 'binary1', '-o', '-', '-'],
                                         input=raw, capture_output=True, check=True, timeout=2)
        metadata = plistlib.loads(canonical_plist.stdout)
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


def _birth(info):
    return (info.pbi_pid, info.pbi_start_tvsec, info.pbi_start_tvusec, info.pbi_uid)


def _kernel_name(info):
    return os.fsdecode(info.pbi_name or info.pbi_comm)


class DarwinProcesses:
    """Use kernel executable paths, never pgrep/argv or application display names.

    Error policy for one process during a scan:
    - It exits (ESRCH) or is a zombie: skip it.
    - Its path is unreadable: classify it by the kernel's process name. A manager
      whose binary was deleted or replaced on disk still controls windows, so it
      stays visible. If its BSD record is unreadable too, skip it: the kernel
      denies both only for protected processes, and this user's managers are not.
    - It is a manager candidate but its identity cannot be verified, or changes
      while it is inspected: fail the whole scan (the caller refuses to act).
    - An unrelated app's bundle metadata is unreadable: skip it.
    """
    METADATA_CACHE_LIMIT = 512

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
        # Scans run in tight loops; parse each bundle's Info.plist once per file version.
        self._metadata_cache = {}

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
            raise PathUnavailable(f'Cannot inspect process {pid} executable; refusing the update.')
        return Path(os.fsdecode(buf.value))

    def _metadata(self, plist):
        status = os.stat(plist)
        version = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)
        cached = self._metadata_cache.get(plist)
        if cached is not None and cached[0] == version:
            return cached[1]
        metadata = read_bundle_metadata(plist)
        if len(self._metadata_cache) >= self.METADATA_CACHE_LIMIT:
            self._metadata_cache.clear()
        self._metadata_cache[plist] = (version, metadata)
        return metadata

    def _inspect_by_name(self, pid):
        try:
            info = self._info(pid)
        except UpdateError:
            return None
        if info is None: return None
        name = _kernel_name(info)
        if name not in MANAGER_EXECUTABLES: return None
        return Process(pid, (info.pbi_start_tvsec, info.pbi_start_tvusec), info.pbi_uid,
                       UNREADABLE_EXECUTABLE / name, 'unidentified.window-manager')

    def inspect(self, pid):
        # Protected unrelated helpers can deny BSD records on macOS. Classify the
        # executable first; full birth/UID verification is required for managers.
        try: executable = self._executable(pid)
        except PathUnavailable: return self._inspect_by_name(pid)
        if executable is None: return None
        identifier = ''
        if executable.parent.name == 'MacOS' and executable.parent.parent.name == 'Contents':
            try:
                info = self._metadata(executable.parent.parent / 'Info.plist')
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
        if _birth(before) != _birth(after) or executable != verified_executable:
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


RENAME_SWAP = 0x2  # <stdio.h>: renamex_np exchanges both paths in one step.


def exchange_paths(first, second, libc=None):
    """Swap two directories atomically: at no moment is either path absent."""
    if libc is None:
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    libc.renamex_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    if libc.renamex_np(os.fsencode(str(first)), os.fsencode(str(second)), RENAME_SWAP) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
            raise ExchangeUnsupported(code, os.strerror(code), str(second))
        raise OSError(code, os.strerror(code), str(second))
    sync_directory(Path(first).parent)
    if Path(first).parent != Path(second).parent: sync_directory(Path(second).parent)


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


def _owned_directory(path):
    try: info = os.lstat(path)
    except FileNotFoundError: return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


class InstallLock:
    """One flock per user, on a file that is never unlinked.

    The lock file lives in a directory only this user can write. The updater also
    locks the file that updaters before 2026-11 used, so an older updater that is
    still running cannot overlap. Those updaters create that file in /tmp, which
    macOS clears at boot, so this lock creates it too (`legacy`). Locations that no
    old updater created are locked only if the file is already there (`legacy_existing`).

    A legacy file that is not a regular file of this user with one link (for
    example, one that another account planted in /tmp) is skipped with a warning.
    An older updater checks the same properties and refuses to start, so it cannot
    overlap while that file is there. A planted file never blocks this updater.
    """
    def __init__(self, parent, legacy=(), legacy_existing=(), report=None):
        name = f'.aquaspace-install-{os.getuid()}.lock'
        self.path, self.fds = Path(parent) / name, []
        self.legacy = [(Path(p) / name, True) for p in legacy] + [(Path(p) / name, False) for p in legacy_existing]
        self.report = report or (lambda text: print(text, file=sys.stderr, flush=True))
        self.fd = None

    def __enter__(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self.fds.append(self.fd)
            os.set_inheritable(self.fd, False)
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('The persistent install lock has an unexpected owner or file type.')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.fd, 0)
            os.write(self.fd, f'{os.getpid()}\n'.encode())
        except OSError as error:
            self.__exit__()
            raise UpdateError('Another installer is running, or the install lock cannot be acquired.') from error
        primary = os.fstat(self.fd)
        for path, create in self.legacy:
            if not create and not os.path.lexists(path): continue
            # The flags and checks match the older updater's, so it refuses every file skipped here.
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0)
            try:
                fd = os.open(path, flags, 0o600)
            except FileNotFoundError:
                continue  # The directory is absent, so no older updater can lock there either.
            except OSError as error:
                self._skip(path, error.strerror or str(error))
                continue
            self.fds.append(fd)
            os.set_inheritable(fd, False)
            info = os.fstat(fd)
            if info.st_uid != os.getuid():
                self._skip(path, 'it belongs to another account'); continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                self._skip(path, 'it is not a plain file with one link'); continue
            if (info.st_dev, info.st_ino) == (primary.st_dev, primary.st_ino): continue  # Same file: already held.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self.__exit__()
                raise UpdateError(f'An older AquaSpace installer is running (it holds {path}). '
                                  'Wait for it to finish, then try again.') from error
        return self

    def _skip(self, path, reason):
        self.report(f'Note: the old install lock {path} cannot be used ({reason}). An older AquaSpace installer '
                    'cannot run while this file is there, so this installer continues. To let an older '
                    'installer run again, ask an administrator to remove that file.')

    def __exit__(self, *args):
        # Never unlink a flock file: replacing its inode would permit two owners.
        for fd in self.fds: os.close(fd)
        self.fds, self.fd = [], None


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
    CODESIGN = '/usr/bin/codesign'

    def __init__(self):
        self.processes = DarwinProcesses()

    def managers(self): return self.processes.managers()
    def inspect(self, pid): return self.processes.inspect(pid)
    def monotonic(self): return time.monotonic()
    def time(self): return time.time()
    def sleep(self, seconds): time.sleep(seconds)
    def exchange(self, first, second): exchange_paths(first, second)

    @staticmethod
    def run(args, timeout=2):
        result = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise UpdateError(result.stderr.strip() or f'Command failed: {args[0]}')
        # Empty final CLI fields and title whitespace are data, not decoration.
        return result.stdout.rstrip('\r\n')

    def requirement(self, path):
        """The designated requirement macOS binds privacy grants to, or None if unreadable."""
        try:
            return designated_requirement(path)
        except SigningError:
            return None

    def verify_bundle(self, path):
        self.run([self.CODESIGN, '--verify', '--strict', path], timeout=15)
        self.run([self.CODESIGN, '--verify', '--strict', path / 'Contents/MacOS/aquaspace-cli'], timeout=15)
        try:
            requirement = designated_requirement(path)
        except SigningError as error:
            raise UpdateError(str(error)) from error
        if 'cdhash' in requirement or 'certificate' not in requirement:
            raise UpdateError('This build uses an unstable signing identity. Rebuild with AquaSpace persistent signing before installing.')
        return requirement

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
            raise NotSignaled('Process identity changed; refusing to signal it.')
        # SIGKILL bypasses normal quit-time recentering. No SIGSTOP/SIGCONT and no
        # watchdog exist: an interrupted installer cannot leave a dormant controller.
        try: os.kill(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        except PermissionError as error: raise NotSignaled(f'Not allowed to stop process {process.pid}.') from error

    def launch(self, executable, session, log):
        if self.managers():
            raise UpdateError('A window manager appeared before launch; refusing to open another.')
        # Direct Popen inherits the terminal/agent's TCC responsibility. Open the
        # exact bundle through Launch Services so grants belong to AquaSpace.
        arguments = ['/usr/bin/open', '-n', '-g', executable.parents[2],
                     '--stdin', '/dev/null', '--stdout', log, '--stderr', log]
        if session: arguments += ['--args', '--restore-session', session]
        self.run(arguments, timeout=10)
        deadline = self.monotonic() + LAUNCH_APPEAR_TIMEOUT
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
        return json.loads(self.run([cli, 'management-status'], timeout=1))


def _inode(path):
    info = os.lstat(path)
    return [info.st_dev, info.st_ino]


def _remove_tree(path):
    if path.is_symlink(): path.unlink()
    elif path.exists(): shutil.rmtree(path)


class Installer:
    def __init__(self, source, target, system, timeout=5, lock_parent=None,
                 ready_timeout=READY_TIMEOUT, legacy_lock_parents=(), existing_legacy_lock_parents=()):
        self.source, self.target = canonical(source), canonical(target)
        self.system, self.timeout, self.ready_timeout = system, timeout, ready_timeout
        self.lock_parent = Path(lock_parent) if lock_parent is not None else self.target.parent
        self.legacy_lock_parents = tuple(legacy_lock_parents)
        self.existing_legacy_lock_parents = tuple(existing_legacy_lock_parents)
        self.checkpoint = lambda phase: None  # Fault injection seam; never configured by the CLI.
        self.report = lambda text: print(text, file=sys.stderr, flush=True)
        self.summary = ''
        self.warnings = []

    def _lock(self):
        return InstallLock(self.lock_parent, self.legacy_lock_parents, self.existing_legacy_lock_parents,
                           report=self.report)

    # Guidance --------------------------------------------------------------

    def command(self, *arguments):
        return shlex.join(['python3', str(INSTALLER_SCRIPT), '--target', str(self.target), *map(str, arguments)])

    def next_steps(self, transaction, state):
        """Copy-pastable commands for the state this record's journal is in."""
        phase = state.get('phase')
        resume, recover, confirm = (self.command(f'--{mode}', transaction) for mode in ('resume', 'recover', 'confirm'))
        if phase in TERMINAL_PHASES:
            return 'This update record is complete; nothing else is needed.'
        if phase in PRE_STOP_PHASES:
            return ('No process was stopped and no file was replaced. Close this record, then run the update again:\n  '
                    + recover)
        restore = self._restore_hint(transaction, state)
        if not self._restart_requested(state):
            return ('This update was file-only: it starts no app. To finish it (no app is started), run:\n  ' + resume +
                    '\nIt needs every window manager to be closed, except a stock AeroSpace that the update kept running.\n'
                    + restore)
        if phase == 'stop_requested':
            return ('The updater asked the old AquaSpace to stop.\n'
                    'If a window manager is still running, nothing was replaced. Close this record with:\n  ' + recover +
                    '\nIf no window manager is running, finish the update and start the new AquaSpace once:\n  ' + resume +
                    '\n' + restore)
        if phase == 'child_started':
            child = (state.get('child') or {}).get('pid')
            return (f'The new AquaSpace was started (pid {child}). Its log: {transaction / "app.log"}\n'
                    'If it is running, allow it in System Settings > Privacy & Security > Accessibility '
                    'if macOS asks, then confirm the update (this stops nothing):\n  ' + confirm +
                    '\nIf no window manager is running, start the new AquaSpace once more:\n  ' + resume +
                    '\n' + restore)
        if phase == 'launch_attempted':
            return ('The new AquaSpace may or may not have started.\n'
                    'If it is running, confirm the update (this stops nothing):\n  ' + confirm +
                    f'\nIf no window manager is running, wait {LAUNCH_SETTLE_SECONDS} seconds, then start it once:\n  '
                    + resume + '\n' + restore)
        started = ('\nIf you started the new AquaSpace yourself after this, confirm the update (this stops nothing):\n  '
                   + confirm if phase == 'replaced' else '')
        return ('The new AquaSpace was not started. When no window manager is running, finish the update '
                'and start the new AquaSpace once:\n  ' + resume + started + '\n' + restore)

    def _restore_hint(self, transaction, state):
        recover = self.command('--recover', transaction)
        if state.get('had_target'):
            return ('To put the previous version back instead (files only; no app is started), run:\n  ' + recover +
                    '\nthen start AquaSpace yourself:\n  ' + shlex.join(['open', str(self.target)]))
        return 'To close this record and leave the new files installed (no app is started), run:\n  ' + recover

    @staticmethod
    def _restart_requested(state):
        """Whether the original command asked to start the new AquaSpace.

        Records written before this field: a launch phase or a stopped manager proves
        a restart. Nothing else proves one, so then nothing is started.
        """
        if isinstance(state.get('restart'), bool): return state['restart']
        return state.get('phase') in ('launch_attempted', 'child_started') or bool(state.get('old'))

    # Records ---------------------------------------------------------------

    def journal(self, transaction):
        if transaction.is_symlink() or transaction.parent != self.target.parent or not transaction.name.startswith(RECORD_PREFIX):
            raise UpdateError('Recovery record is outside this installation directory.')
        try:
            data = json.loads((transaction / 'journal.json').read_text())
            if data.get('schema') != 1 or data.get('target') != str(self.target) or data.get('phase') not in PHASES:
                raise ValueError('Invalid journal identity or phase')
            return data
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise UpdateError(f'Unreadable update record; no process actions taken: {transaction}') from error

    @staticmethod
    def _abandoned(record):
        """A record directory without a journal was left before its first journal write."""
        journal = record / 'journal.json'
        return (not record.is_symlink() and record.is_dir()
                and not journal.exists() and not journal.is_symlink())

    def _records(self):
        return [(record, self.journal(record)) for record in sorted(self.target.parent.glob(RECORD_PREFIX + '*'))
                if not self._abandoned(record)]

    def pending_transactions(self):
        return [record for record, state in self._records() if state['phase'] not in TERMINAL_PHASES]

    def _begin(self, state):
        name = uuid.uuid4().hex
        draft = self.target.parent / (NEW_RECORD_PREFIX + name)
        draft.mkdir(mode=0o700)
        state['phase'] = 'staging'
        atomic_json(draft / 'journal.json', state)
        transaction = self.target.parent / (RECORD_PREFIX + name)
        draft.rename(transaction)
        sync_directory(self.target.parent)
        self.checkpoint('staging')
        return transaction

    def _close_aborted(self, transaction, state):
        """Close a record whose phase proves that nothing was stopped or replaced."""
        self._phase(transaction, state, 'aborted')
        # An unused staging copy is never evidence of an installed app; do not archive it.
        _remove_tree(transaction / 'new.app')

    def _tidy_records(self):
        parent = self.target.parent
        for draft in parent.glob(NEW_RECORD_PREFIX + '*'):
            if _owned_directory(draft) and set(os.listdir(draft)) <= {'journal.json', 'journal.json.tmp'}:
                shutil.rmtree(draft)
        terminal = []
        for record in sorted(parent.glob(RECORD_PREFIX + '*')):
            if self._abandoned(record):
                if _owned_directory(record) and set(os.listdir(record)) <= {'journal.json.tmp'}:
                    shutil.rmtree(record)
                continue
            state = self.journal(record)
            if state['phase'] in PRE_STOP_PHASES:
                # This updater holds the lock, so the installer that wrote it has died.
                self._close_aborted(record, state)
            if state['phase'] not in TERMINAL_PHASES: continue
            terminal.append(record)
            staged = record / 'new.app'
            if self._holds_other_bundle(staged, state):
                # An interrupted exchange can leave the previous bundle here. Keep it.
                archive_bundle(staged)
            else:
                _remove_tree(staged)
            for name in ('previous.app', 'unconfirmed.app'):
                bundle = record / name
                if bundle.exists():
                    if bundle.is_symlink():
                        raise UpdateError('A backup bundle must not be a symlink.')
                    # Removal may have been interrupted after the durable archive was published.
                    if not bundle.with_name(bundle.name + '.tar.gz').is_file():
                        bundle_metadata(bundle)
                    archive_bundle(bundle)
        terminal.sort(key=lambda record: ((record / 'journal.json').stat().st_mtime_ns, record.name))
        for record in terminal[:-KEEP_COMPLETED_RECORDS]:
            if _owned_directory(record): shutil.rmtree(record)

    @staticmethod
    def _holds_other_bundle(staged, state):
        """True if new.app is a complete bundle that is not this update's staged copy."""
        executable = staged / 'Contents/MacOS/AquaSpace'
        if staged.is_symlink() or not executable.is_file() or not state.get('main_sha256'):
            return False
        try:
            bundle_metadata(staged)
            return sha256(executable) != state['main_sha256']
        except (UpdateError, OSError):
            return False

    def archive_completed_backups(self):
        with self._lock():
            self._tidy_records()

    def _expect(self, expected):
        actual = self.system.managers()
        if sorted(p.identity for p in actual) != sorted(p.identity for p in expected):
            raise UpdateError('Window-manager inventory changed or is uncertain; refusing to continue.')

    def _phase(self, transaction, state, phase, **fields):
        state.update(fields, phase=phase)
        atomic_json(transaction / 'journal.json', state)
        self.checkpoint(phase)

    # Bundles ---------------------------------------------------------------

    def _locate(self, transaction, state):
        """Return the paths of the update's (new, old) bundles, or None where absent."""
        if 'new_bundle' not in state:
            return self._identify_by_content(transaction, state)
        # Renames and exchanges keep a directory's inode, so this finds each bundle
        # after any interruption.
        found = {}
        for path in (self.target, transaction / 'new.app', transaction / 'previous.app', transaction / 'unconfirmed.app'):
            try: key = _inode(path)
            except FileNotFoundError: continue
            for role in ('new', 'old'):
                if state.get(role + '_bundle') == key: found[role] = path
        return found.get('new'), found.get('old')

    def _identify_by_content(self, transaction, state):
        """Name the bundles of a record without inodes by their main executable's hash.

        Updaters before 2026-11 wrote no inodes. A path is not proof: an exchange by an
        interrupted later updater can leave the previous bundle in new.app. The order
        below matches the old updater's renames, and decides only when both copies
        hold the same build.
        """
        candidates = (transaction / 'new.app', transaction / 'unconfirmed.app',
                      self.target, transaction / 'previous.app')
        new, old = [], []
        for path in candidates:
            if path.is_symlink() or not path.exists(): continue
            try: digest = sha256(path / 'Contents/MacOS/AquaSpace')
            except OSError as error:
                raise UpdateError(f'Cannot read the bundle {path}; no files were changed.') from error
            (new if digest == state.get('main_sha256') else old).append(path)
        if len(new) > 2 or len(old) > 1 or (len(new) == 2 and old):
            raise UpdateError(f'The update record {transaction} holds more bundles than one update can leave. '
                              'The updater cannot tell which one is the previous AquaSpace; no files were changed.')
        if len(new) == 2:
            return new[0], (new[1] if state.get('had_target') else None)
        return (new[0] if new else None), (old[0] if old else None)

    def _record_bundles(self, transaction, state):
        """Before the first file change, save each bundle's inode and the previous bundle's hash.

        After this write, every later step finds the bundles by inode, so no crash
        can make the updater mistake one bundle for the other.
        """
        needs_inodes = 'new_bundle' not in state
        needs_hash = bool(state.get('had_target')) and not state.get('old_sha256')
        if not (needs_inodes or needs_hash): return
        new, old = self._locate(transaction, state)
        fields = {}
        if needs_inodes:
            fields.update(new_bundle=_inode(new) if new else None, old_bundle=_inode(old) if old else None)
        if needs_hash and old is not None:
            fields['old_sha256'] = sha256(old / 'Contents/MacOS/AquaSpace')
        if fields:
            state.update(fields)
            atomic_json(transaction / 'journal.json', state)
            self.checkpoint('upgraded')

    def _check_new(self, path, state):
        bundle_metadata(path)
        if sha256(path / 'Contents/MacOS/AquaSpace') != state.get('main_sha256'):
            raise UpdateError(f'The bundle from this update changed on disk; refusing to use it: {path}')
        self.system.verify_bundle(path)

    def _swap_into_target(self, incoming, outgoing):
        """Move `incoming` to the target and the target's bundle to `outgoing`."""
        try:
            self.system.exchange(incoming, self.target)
        except ExchangeUnsupported:
            # Without an atomic exchange the target is briefly absent; --resume finishes it.
            replace_directory(self.target, outgoing)
            replace_directory(incoming, self.target)
            return
        replace_directory(incoming, outgoing)  # It now holds the bundle that left the target.

    def _publish(self, transaction, state, retained=()):
        """Install the staged bundle. Each step may be repeated after an interruption."""
        if state['phase'] in ('prepared', 'stop_requested'):
            self._expect(retained)
            self._phase(transaction, state, 'stopped')
        if state['phase'] == 'stopped':
            self._expect(retained)
            self._phase(transaction, state, 'replacing')
        if state['phase'] != 'replacing':
            return
        previous = transaction / 'previous.app'
        new, old = self._locate(transaction, state)
        target_present = self.target.exists() or self.target.is_symlink()
        if new == self.target:
            if old is not None and old != previous: replace_directory(old, previous)
        elif new is None:
            raise UpdateError('The staged AquaSpace bundle is missing from the update record.')
        elif old == self.target:
            self._swap_into_target(new, previous)
        elif target_present:
            raise UpdateError('The installation changed during this update; refusing to replace it.')
        else:
            replace_directory(new, self.target)
        # A process that starts after this moment runs the new files.
        self._phase(transaction, state, 'replaced', replaced_at=self.system.time())

    def _restore_previous(self, transaction, state):
        unconfirmed = transaction / 'unconfirmed.app'
        new, old = self._locate(transaction, state)
        target_present = self.target.exists() or self.target.is_symlink()
        if old is None:
            if state.get('had_target'):
                raise UpdateError('The previous AquaSpace bundle is missing from this record; no files were changed.')
            return  # A first installation has no old bundle; leave its files available.
        if old == self.target:
            if new is not None and new != transaction / 'new.app' and new != unconfirmed:
                replace_directory(new, unconfirmed)
            return
        bundle_metadata(old)
        if sha256(old / 'Contents/MacOS/AquaSpace') != state.get('old_sha256'):
            raise UpdateError(f'The previous AquaSpace bundle in this record changed on disk: {old}\n'
                              'No files were changed.')
        self.system.verify_bundle(old)
        if not target_present:
            replace_directory(old, self.target)
        elif new == self.target:
            self._swap_into_target(old, unconfirmed)
        else:
            raise UpdateError('The installation changed after this update; refusing to overwrite it.')

    def _check_restored(self, transaction, state):
        """Prove by content that the target holds the previous bundle before reporting it."""
        if not state.get('had_target'): return
        executable = self.target / 'Contents/MacOS/AquaSpace'
        try:
            restored = (not self.target.is_symlink() and executable.is_file()
                        and sha256(executable) == state.get('old_sha256'))
        except OSError:
            restored = False
        if not restored:
            raise UpdateError(f'{self.target} does not hold the previous AquaSpace from this update. '
                              f'The record stays open: {transaction}')

    # Processes -------------------------------------------------------------

    @staticmethod
    def _same_process(process, record):
        return (record is not None and process.pid == record.get('pid')
                and list(process.started) == record.get('started') and process.uid == record.get('uid')
                and str(process.executable) == record.get('executable'))

    def _old_still_running(self, state):
        """In stop_requested, a living old manager proves the stop never happened."""
        old = state.get('old')
        if state['phase'] != 'stop_requested' or not old: return False
        current = self.system.inspect(old['pid'])
        return current is not None and self._same_process(current, old)

    def _journal_time(self, transaction):
        return (transaction / 'journal.json').stat().st_mtime

    def _launch_time(self, transaction, state):
        """When the last launch was attempted. Records before 2026-11 have no such
        field; their journal was last written at that attempt or later."""
        value = state.get('launch_attempted_at')
        return value if isinstance(value, (int, float)) else self._journal_time(transaction)

    def _installed_since(self, transaction, state):
        """A time after the new bundle reached the target; a process started later runs its files."""
        for key in ('replaced_at', 'launch_attempted_at'):
            if isinstance(state.get(key), (int, float)): return state[key]
        return self._journal_time(transaction)

    def _runs_installed_update(self, process, transaction, state):
        """True for the recorded child, or for any instance of the target that started
        after the new bundle was in place (for example, after a restart of the Mac)."""
        if (process.bundle_id != 'local.aquaspace' or process.uid != os.getuid()
                or process.executable != self.target / 'Contents/MacOS/AquaSpace'):
            return False
        if state.get('child') and self._same_process(process, state['child']):
            return True
        if state['phase'] not in CONFIRMABLE_PHASES: return False
        # The kernel's start time and time.time() read the same clock.
        return process.started[0] + process.started[1] / 1e6 >= self._installed_since(transaction, state)

    def _allowed_managers(self, transaction, state, mode, action):
        """Return the running managers that the original command allowed; refuse any other.

        A restart allows none at this point. A file-only update allows the stock
        AeroSpace that it kept running, with an unchanged identity, and nothing else.
        """
        managers = self.system.managers()
        retained = [] if self._restart_requested(state) else state.get('retained') or []
        kept = [m for m in managers if any(self._same_process(m, r) and m.bundle_id == r.get('bundle_id')
                                           for r in retained)]
        others = [m for m in managers if m not in kept]
        if not others: return kept
        if len(others) == 1 and self._runs_installed_update(others[0], transaction, state):
            raise UpdateError(f'The new AquaSpace from this update is running (pid {others[0].pid}). '
                              'To keep it, confirm the update (this stops nothing):\n  '
                              + self.command('--confirm', transaction)
                              + f'\nTo {action} instead, quit AquaSpace first, then run:\n  '
                              + self.command(f'--{mode}', transaction))
        names = ', '.join(f'{m.executable.name} (pid {m.pid})' for m in others)
        raise UpdateError(f'A window manager is running: {names}. Quit it first, then run this command '
                          f'again to {action}:\n  ' + self.command(f'--{mode}', transaction))

    def _start_replacement(self, transaction, state):
        """The only launch site. Callers: install --restart and the explicit --resume."""
        self._expect([])
        session = transaction / 'session.json'
        self._phase(transaction, state, 'launch_attempted', child=None,
                    launch_attempted_at=self.system.time(), launches=state.get('launches', 0) + 1)
        child = self.system.launch(self.target / 'Contents/MacOS/AquaSpace',
                                   session if session.is_file() else None, transaction / 'app.log')
        self._phase(transaction, state, 'child_started', child=child.record())
        self._await_ready(transaction, child)

    def _await_ready(self, transaction, child):
        """AquaSpace answers only after it has Accessibility permission. Until then a
        live child with the expected identity is waiting, not failed."""
        cli = self.target / 'Contents/MacOS/aquaspace-cli'
        start = self.system.monotonic()
        announced = False
        while True:
            self._expect([child])
            try: status = self.system.status(child, cli)
            except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired): status = None
            if valid_status(status, child):
                self._expect([child])
                return
            elapsed = self.system.monotonic() - start
            if elapsed >= self.ready_timeout:
                raise AwaitingConfirmation(
                    f'The new AquaSpace is running (pid {child.pid}) but did not take control of windows within '
                    f'{self.ready_timeout:g} s.\nThis is expected on a first install or after a signing identity '
                    'change: AquaSpace waits for Accessibility permission before it starts.\n'
                    'Allow AquaSpace in System Settings > Privacy & Security > Accessibility, then confirm the '
                    'update (this stops nothing):\n  ' + self.command('--confirm', transaction) +
                    f'\nUpdate record: {transaction}\nNo other app was launched.')
            if not announced and elapsed >= min(3, self.ready_timeout / 2):
                self.report(f'Waiting up to {self.ready_timeout:g} s for the new AquaSpace to take control. '
                            'If macOS asks for Accessibility permission, allow it now.')
                announced = True
            self.system.sleep(.1)

    def _commit(self, transaction, state):
        self._phase(transaction, state, 'committed')
        previous = transaction / 'previous.app'
        if previous.exists():
            try:
                archive_bundle(previous)
            except (OSError, tarfile.TarError) as error:
                self.warnings.append(f'The update is complete, but its backup was not archived ({error}). '
                                     'To archive it later, run:\n  ' + self.command('--archive-backups'))

    def _failure(self, transaction, state, error, nothing_stopped):
        """Explain the state and the next commands. Never launches or stops anything."""
        if isinstance(error, AwaitingConfirmation):
            return error
        if nothing_stopped:
            self._close_aborted(transaction, state)
            detail = f'No process was stopped and no file was replaced. The update record is closed: {transaction}'
        else:
            detail = (f'Update record: {transaction}\nNo recovery app was launched.\n'
                      + self.next_steps(transaction, state))
        if not isinstance(error, Exception):
            self.report(detail)  # KeyboardInterrupt and similar keep their own type.
            return error
        failure = UpdateError(f'{error}\n{detail}')
        failure.__cause__ = error
        return failure

    def _check_identity(self, requirement, retry):
        installed = self.system.requirement(self.target)
        if installed == requirement:
            return False
        if retry is None:
            self.report('The new build is signed with a different identity than the installed AquaSpace. '
                        'macOS will ask for Accessibility permission again.')
            return True
        raise UpdateError(
            'The new build is signed with a different identity than the installed AquaSpace.\n'
            f'Installed: {installed or "no readable signature"}\nNew: {requirement}\n'
            'macOS ties Accessibility permission to this identity. After the change, AquaSpace has no '
            'permission until you allow it again in System Settings > Privacy & Security > Accessibility; '
            'then confirm the update with --confirm. Nothing was stopped or changed.\n'
            'If this is not intended, rebuild with the usual signing identity '
            '(python3 script/aquaspace_signing.py shows it).\n'
            'To accept the change, run:\n  ' + retry)

    # Entry points ----------------------------------------------------------

    def install(self, restart=False, allow_running_stock=False, allow_identity_change=False):
        if restart and allow_running_stock:
            raise UpdateError('Installing beside running stock AeroSpace must be file-only.')
        with self._lock():
            self._tidy_records()
            pending = self.pending_transactions()
            if pending:
                raise UpdateError(f'An unfinished update blocks new installs: {pending[0]}\n'
                                  + self.next_steps(pending[0], self.journal(pending[0])))
            metadata = bundle_metadata(self.source)
            if self.target == self.source or self.target in self.source.parents or self.source in self.target.parents:
                raise UpdateError('Build and installation paths must be separate.')
            requirement = self.system.verify_bundle(self.source)
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
            identity_changed = False
            if had_target:
                retry = None if allow_identity_change else shlex.join(
                    ['python3', str(INSTALLER_SCRIPT), '--source', str(self.source), '--target', str(self.target),
                     *(['--restart'] if restart else []), *(['--allow-running-stock'] if allow_running_stock else []),
                     '--allow-identity-change'])
                identity_changed = self._check_identity(requirement, retry)
            state = dict(schema=1, target=str(self.target), version=metadata.get('CFBundleShortVersionString'),
                         had_target=had_target, old=old.record() if old else None, requirement=requirement,
                         identity_changed=identity_changed, old_bundle=_inode(self.target) if had_target else None,
                         old_sha256=sha256(self.target / 'Contents/MacOS/AquaSpace') if had_target else None,
                         restart=restart, retained=[p.record() for p in retained])
            transaction = self._begin(state)
            signaled = False
            try:
                staged = transaction / 'new.app'
                shutil.copytree(self.source, staged, symlinks=True)
                if self.system.verify_bundle(staged) != requirement:
                    raise UpdateError('The staged copy is signed differently from its source.')
                bundle_metadata(staged)
                main_hash = sha256(staged / 'Contents/MacOS/AquaSpace')
                cli_hash = sha256(staged / 'Contents/MacOS/aquaspace-cli')
                manifest_path = self.source.parent / 'build.json'
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text())
                    if (manifest.get('app_sha256') != main_hash or manifest.get('version') != state['version'] or
                            ('cli_sha256' in manifest and manifest['cli_sha256'] != cli_hash) or
                            ('designated_requirement' in manifest and manifest['designated_requirement'] != requirement)):
                        raise UpdateError('The staged app/CLI does not match its build manifest.')
                    atomic_json(transaction / 'build.json', manifest)
                self._phase(transaction, state, 'prepared', main_sha256=main_hash, cli_sha256=cli_hash,
                            new_bundle=_inode(staged))
                if old:
                    snapshot = self.system.capture_session(old, self.target / 'Contents/MacOS/aquaspace-cli')
                    atomic_json(transaction / 'session.json', snapshot)
                    self._expect([old])
                    self._phase(transaction, state, 'stop_requested')
                    signaled = True  # Assume a signal unless stop() proves that it sent none.
                    try:
                        self.system.stop(old)
                    except NotSignaled:
                        signaled = False
                        raise
                    deadline = self.system.monotonic() + self.timeout
                    while True:
                        current = self.system.inspect(old.pid)
                        if current is None or current.identity != old.identity: break
                        if self.system.monotonic() >= deadline:
                            raise UpdateError('The old AquaSpace did not exit. No replacement was launched.')
                        self.system.sleep(.02)
                self._publish(transaction, state, retained)
                if restart:
                    self._start_replacement(transaction, state)
                self._commit(transaction, state)
                self.summary = ('Installed AquaSpace' + (' and verified sole ownership.' if restart else '; no app launched.')
                                + f'\nUpdate record and backup: {transaction}')
                return transaction
            except BaseException as error:
                nothing_stopped = (state.get('phase') in PRE_STOP_PHASES
                                   or (state.get('phase') == 'stop_requested' and not signaled))
                raise self._failure(transaction, state, error, nothing_stopped)

    def resume(self, transaction):
        """Finish an interrupted update: complete the swap, then launch once."""
        transaction = canonical(transaction)
        with self._lock():
            state = self.journal(transaction)
            phase = state['phase']
            if phase in TERMINAL_PHASES:
                raise UpdateError('This update is already complete; there is nothing to resume.')
            if phase in PRE_STOP_PHASES or self._old_still_running(state):
                self._close_aborted(transaction, state)
                self.summary = ('No process was stopped and no file was replaced, so there was nothing to resume.\n'
                                f'The record is closed: {transaction}\nRun the update again.')
                return transaction
            restart = self._restart_requested(state)
            retained = self._allowed_managers(transaction, state, 'resume',
                                              'start the new AquaSpace' if restart else 'finish the update')
            if phase == 'launch_attempted':
                waited = self.system.time() - self._launch_time(transaction, state)
                if waited < LAUNCH_SETTLE_SECONDS:
                    raise UpdateError(f'The last start attempt was {max(waited, 0):.0f} s ago, and macOS may still start it. '
                                      f'Wait {LAUNCH_SETTLE_SECONDS - waited:.0f} s, then run this command again:\n  '
                                      + self.command('--resume', transaction))
            try:
                self._record_bundles(transaction, state)
                new, _ = self._locate(transaction, state)
                if new is None:
                    raise UpdateError('The bundle from this update is missing; it cannot be resumed.')
                self._check_new(new, state)
                if phase not in ('launch_attempted', 'child_started'):
                    self._publish(transaction, state, retained)
                if self._locate(transaction, state)[0] != self.target:
                    raise UpdateError('The installed AquaSpace is not the bundle from this update.')
                if restart:
                    self._start_replacement(transaction, state)
                self._commit(transaction, state)
            except BaseException as error:
                raise self._failure(transaction, state, error, nothing_stopped=False)
            if restart:
                self.summary = 'Finished the interrupted update; the new AquaSpace controls windows.'
            else:
                self.summary = 'Finished the interrupted file-only update; no app was started, as the original command asked.'
                if not retained:
                    self.summary += '\nTo start AquaSpace:\n  ' + shlex.join(['open', str(self.target)])
            self.summary += f'\nUpdate record: {transaction}'
            return transaction

    def confirm(self, transaction):
        """Commit an update whose replacement is running. Never stops anything."""
        transaction = canonical(transaction)
        with self._lock():
            state = self.journal(transaction)
            phase = state['phase']
            if phase in TERMINAL_PHASES:
                raise UpdateError('This update is already complete; there is nothing to confirm.')
            if phase not in CONFIRMABLE_PHASES:
                raise UpdateError('This update has not finished replacing the files, so there is nothing to confirm.\n'
                                  + self.next_steps(transaction, state))
            new, _ = self._locate(transaction, state)
            if new != self.target or sha256(self.target / 'Contents/MacOS/AquaSpace') != state.get('main_sha256'):
                raise UpdateError('The installed AquaSpace is not the bundle from this update; refusing to confirm.')
            managers = self.system.managers()
            if len(managers) != 1 or not self._runs_installed_update(managers[0], transaction, state):
                raise UpdateError(self._confirm_refusal(transaction, state, managers))
            child = managers[0]
            try:
                if not (state.get('child') and self._same_process(child, state['child'])):
                    # Its start time proves that it runs the new files; readiness checks its identity again.
                    self._phase(transaction, state, 'child_started', child=child.record(), adopted=True)
                self._await_ready(transaction, child)
                self._commit(transaction, state)
            except BaseException as error:
                raise self._failure(transaction, state, error, nothing_stopped=False)
            self.summary = f'Confirmed the running AquaSpace; the update is complete.\nUpdate record: {transaction}'
            return transaction

    def _confirm_refusal(self, transaction, state, managers):
        """Explain why nothing can be confirmed, and name a command that can proceed."""
        resume = self.command('--resume', transaction)
        restart = self._restart_requested(state)
        finish = ('start the new AquaSpace once' if restart else 'finish the update (no app is started)')
        retained = [] if restart else state.get('retained') or []
        others = [m for m in managers if not any(self._same_process(m, r) for r in retained)]
        if not others:
            reason = 'No window manager from this update is running.'
            step = f'To {finish}, run:\n  {resume}'
        else:
            installed = self.target / 'Contents/MacOS/AquaSpace'
            if len(others) == 1 and others[0].executable == installed:
                reason = (f'The running AquaSpace (pid {others[0].pid}) started before this update installed its '
                          'files, so it can still run the previous version.')
            else:
                reason = 'Another window manager is running: ' + ', '.join(
                    f'{m.executable} (pid {m.pid})' for m in others) + '.'
            step = f'Quit it, then {finish} with:\n  {resume}'
        return f'{reason} Nothing was confirmed.\n{step}\n' + self._restore_hint(transaction, state)

    def recover(self, transaction):
        """Restore files only. Never starts or stops a process."""
        transaction = canonical(transaction)
        with self._lock():
            state = self.journal(transaction)
            if state['phase'] in TERMINAL_PHASES:
                raise UpdateError('This update is already complete; it is not a pending recovery.')
            if state['phase'] in PRE_STOP_PHASES or self._old_still_running(state):
                # Nothing was stopped or replaced, so a running manager is not affected.
                self._close_aborted(transaction, state)
                self.summary = f'Closed the update record; no process or installed file was changed.\nRecord: {transaction}'
                return transaction
            self._allowed_managers(transaction, state, 'recover', 'restore the previous files')
            self._record_bundles(transaction, state)
            self._restore_previous(transaction, state)
            self._check_restored(transaction, state)
            self._phase(transaction, state, 'recovered')
            if state.get('had_target'):
                self.summary = ('Restored the previous AquaSpace files; no app was started. To start AquaSpace:\n  '
                                + shlex.join(['open', str(self.target)]))
            elif self.target.exists():
                self.summary = 'Closed the update record; the new files stay installed. No app was started.'
            else:
                self.summary = ('Closed the update record. No AquaSpace is installed, as before this update. '
                                'No app was started.')
            self.summary += f'\nRecord: {transaction}'
            return transaction


EXIT_CODES = f'''exit status:
  0    success
  1    stopped; the message says what state remains and which command comes next
  2    invalid arguments
  {EXIT_AWAITING_CONFIRMATION}    the new AquaSpace is running but has not taken control yet; run the --confirm command shown
  {EXIT_INTERRUPTED}  interrupted; the message says which command comes next'''


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='Install a staged AquaSpace build. Recovery never launches an application.',
                                     epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, default=root / '.debug/aquaspace/release/AquaSpace.app')
    parser.add_argument('--target', type=Path, default=Path('/Applications/AquaSpace.app'))
    parser.add_argument('--restart', action='store_true', help='Save the sole installed AquaSpace session, stop it, wait for exit, then launch the replacement once')
    parser.add_argument('--resume', type=Path, metavar='UPDATE_RECORD', help='Finish an interrupted update with no window manager running: complete the file swap, then launch the new AquaSpace once')
    parser.add_argument('--confirm', type=Path, metavar='UPDATE_RECORD', help='Verify that the AquaSpace started by an update controls windows, then complete the update; never stops or launches an app')
    parser.add_argument('--recover', type=Path, metavar='UPDATE_RECORD', help='Restore files from an unfinished update; never launches an app. Needs all managers stopped unless the record proves nothing was stopped')
    parser.add_argument('--archive-backups', action='store_true', help=f'Archive bundles from completed updates and keep only the newest {KEEP_COMPLETED_RECORDS} completed records; never starts or stops apps')
    parser.add_argument('--allow-running-stock', action='store_true', help='File-only repair of inactive AquaSpace while the sole stock AeroSpace remains running')
    parser.add_argument('--allow-identity-change', action='store_true', help='Install a build signed with a different identity; macOS then asks for Accessibility permission again')
    parser.add_argument('--ready-timeout', type=float, metavar='SECONDS', help=f'How long to wait for the new AquaSpace to take control (default {READY_TIMEOUT})')
    args = parser.parse_args(argv)
    modes = [args.restart, args.resume, args.confirm, args.recover, args.archive_backups]
    if sum(bool(x) for x in modes) > 1:
        parser.error('--restart, --resume, --confirm, --recover and --archive-backups cannot be combined')
    if args.allow_running_stock and any(modes):
        parser.error('--allow-running-stock is only available for file-only installation')
    if args.allow_identity_change and (args.resume or args.confirm or args.recover or args.archive_backups):
        parser.error('--allow-identity-change is only available when installing')
    if args.ready_timeout is not None:
        if not (args.restart or args.resume or args.confirm):
            parser.error('--ready-timeout needs --restart, --resume or --confirm')
        if not 0 < args.ready_timeout <= 3600:
            parser.error('--ready-timeout must be between 0 and 3600 seconds')
    try:
        # One lock for all of this user's updates, with any --target directory or spelling.
        installer = Installer(args.source, args.target, NativeSystem(), lock_parent=USER_STATE_DIRECTORY,
                              ready_timeout=args.ready_timeout or READY_TIMEOUT,
                              legacy_lock_parents=LEGACY_LOCK_DIRECTORIES,
                              existing_legacy_lock_parents=(canonical(args.target).parent,))
        if args.archive_backups:
            installer.archive_completed_backups()
            installer.summary = 'Completed update backups archived and old records pruned; no app launched or stopped.'
        elif args.resume: installer.resume(args.resume)
        elif args.confirm: installer.confirm(args.confirm)
        elif args.recover: installer.recover(args.recover)
        else: installer.install(restart=args.restart, allow_running_stock=args.allow_running_stock,
                                allow_identity_change=args.allow_identity_change)
        print(installer.summary)
        for warning in installer.warnings: print(f'Warning: {warning}', file=sys.stderr)
        return 0
    except AwaitingConfirmation as error:
        print(f'AquaSpace update is waiting for confirmation: {error}', file=sys.stderr)
        return EXIT_AWAITING_CONFIRMATION
    except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        print(f'AquaSpace update stopped: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('AquaSpace update interrupted.', file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == '__main__': sys.exit(main())
