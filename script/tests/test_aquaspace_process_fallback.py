"""Offline lost-executable inventory regressions: no native APIs or app launches."""
import ctypes
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_update as u


class LostExecutable:
    """One live process whose binary was deleted, e.g. by a Homebrew upgrade."""
    def __init__(self):
        self.path_error = errno.ENOENT
        self.info_reads = 0

    def proc_listpids(self, kind, uid, buffer, size):
        if buffer is not None: ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] = 1234
        return 4

    def proc_pidpath(self, pid, buffer, size):
        ctypes.set_errno(self.path_error)
        return 0

    def proc_pidinfo(self, pid, flavor, arg, buffer, size):
        self.info_reads += 1
        raise AssertionError('A process that cannot be classified needs no identity read')


class LostExecutableInventoryTests(unittest.TestCase):
    def setUp(self):
        self.lib = LostExecutable()
        self.scanner = u.DarwinProcesses(lib=self.lib)
        p = patch.object(u.ctypes, 'CDLL', side_effect=AssertionError('No native library loads'))
        p.start(); self.addCleanup(p.stop)
        p = patch.object(u.os, 'kill', side_effect=AssertionError('No signals'))
        p.start(); self.addCleanup(p.stop)
        for name in ('run', 'Popen'):
            p = patch.object(u.subprocess, name, side_effect=AssertionError('No subprocesses'))
            p.start(); self.addCleanup(p.stop)

    def test_deleted_executable_is_skipped_without_blocking_the_update(self):
        self.assertEqual(self.scanner.managers(), [])
        self.assertEqual(self.lib.info_reads, 0)

    def test_gone_process_is_skipped(self):
        self.lib.path_error = errno.ESRCH
        self.assertEqual(self.scanner.managers(), [])


if __name__ == '__main__': unittest.main()
