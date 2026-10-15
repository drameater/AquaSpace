"""Offline lost-executable inventory regressions: no native APIs or app launches."""
import ctypes
import errno
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_update as u


class LostExecutable:
    def __init__(self):
        self.path_error = errno.ENOENT
        self.status = 0x22011111
        self.category = 6
        self.identifier = b'com.microsoft.Word.widgetextension'
        self.team = b'UBF8T346G9'
        self.hash = b'\x42' * 20
        self.calls = []
        self.info_count = self.unique_count = 0
        self.change_birth = self.change_exec = self.change_hash = False
        self.denied_op = None
        self.denied_info = self.short_unique = False
        self.bad_token = False
        self.malformed = False
        self.exec_during_signing = False

    def proc_listpids(self, kind, uid, buffer, size):
        if buffer is not None: ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] = 1234
        return 4

    def proc_pidpath(self, pid, buffer, size):
        ctypes.set_errno(self.path_error)
        return 0

    def proc_pidinfo(self, pid, flavor, arg, buffer, size):
        if self.denied_info:
            ctypes.set_errno(errno.EPERM)
            return 0
        if flavor == 3:
            self.info_count += 1
            value = ctypes.cast(buffer, ctypes.POINTER(u.BsdInfo)).contents
            value.pbi_pid = pid; value.pbi_uid = os.getuid(); value.pbi_status = 2
            value.pbi_start_tvsec = 123 + int(self.change_birth and self.info_count > 1)
            return ctypes.sizeof(u.BsdInfo)
        self.assert_flavor(flavor)
        self.unique_count += 1
        if self.short_unique: return 8
        value = ctypes.cast(buffer, ctypes.POINTER(u.UniqueInfo)).contents
        value.uuid[:] = b'\x11' * 16
        value.uniqueid = 42
        value.idversion = 7 + int(self.change_exec and self.unique_count > 1)
        return ctypes.sizeof(u.UniqueInfo)

    @staticmethod
    def assert_flavor(flavor):
        if flavor != 17: raise AssertionError(flavor)

    def csops_audittoken(self, pid, op, buffer, size, token):
        token_values = ctypes.cast(token, ctypes.POINTER(ctypes.c_uint32 * 8)).contents
        if token_values[5] != pid or token_values[7] != 7: self.bad_token = True
        self.calls.append(op)
        if self.exec_during_signing and len(self.calls) > 2:
            ctypes.set_errno(errno.ESRCH); return -1
        if op == self.denied_op:
            ctypes.set_errno(errno.EPERM); return -1
        if op in (0, 17):
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32))[0] = self.status if op == 0 else self.category
        elif op == 5:
            value = self.hash if not (self.change_hash and self.calls.count(5) > 1) else b'\x43' * 20
            ctypes.memmove(buffer, value, 20)
        else:
            value = self.identifier if op == 11 else self.team
            data = b'\0' * 4 + struct.pack('!I', 2048 if self.malformed else len(value) + 9) + value + b'\0'
            ctypes.memmove(buffer, data, len(data))
        return 0


class LostExecutableInventoryTests(unittest.TestCase):
    def setUp(self):
        self.lib = LostExecutable()
        self.scanner = u.DarwinProcesses(lib=self.lib, codesign=self.lib)
        for name in ('CDLL',):
            p = patch.object(u.ctypes, name, side_effect=AssertionError('No native library loads'))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(u.os, 'kill', side_effect=AssertionError('No signals'))
        p.start(); self.addCleanup(p.stop)
        for name in ('run', 'Popen'):
            p = patch.object(u.subprocess, name, side_effect=AssertionError('No subprocesses'))
            p.start(); self.addCleanup(p.stop)

    def refused(self):
        with self.assertRaises(u.UpdateError): self.scanner.managers()

    def test_lost_widget_is_identified_by_kernel_code_and_exec_generation(self):
        self.assertEqual(self.scanner.managers(), [])
        self.assertEqual(self.lib.calls.count(5), 2)
        self.assertEqual(self.lib.unique_count, 2)
        self.assertFalse(self.lib.bad_token)

    def test_excel_widget_requires_the_same_full_proof(self):
        self.lib.identifier = b'com.microsoft.Excel.widgetextension'
        self.assertEqual(self.scanner.managers(), [])

    def test_permission_denial_is_not_an_enoent_fallback(self):
        self.lib.path_error = errno.EPERM
        self.refused(); self.assertEqual(self.lib.calls, [])

    def test_unknown_or_manager_signing_identity_still_blocks(self):
        for identifier in (b'unknown.widget', b'bobko.aerospace', b'local.aquaspace', b'com.microsoft.Word'):
            with self.subTest(identifier=identifier):
                self.lib.identifier = identifier; self.refused()

    def test_matching_strings_do_not_authorize_adhoc_or_untrusted_code(self):
        for category in (0, 1, 3, 4, 5, 7, 10):
            with self.subTest(category=category):
                self.lib.category = category; self.refused()
        self.lib.category = 6
        for status in (0, 0x22011111 | 2, 0x22011111 | 4, 0x22011111 | 0x10000000,
                       0x22011111 & ~1, 0x22011111 & ~0x10000, 0x22011111 & ~0x10):
            with self.subTest(status=status):
                self.lib.status = status; self.refused()

    def test_wrong_team_and_empty_hash_remain_unknown(self):
        self.lib.team = b'OTHERTEAM'; self.refused()
        self.lib.team = b'UBF8T346G9'; self.lib.hash = b'\0' * 20; self.refused()

    def test_birth_exec_or_code_change_cannot_be_accepted(self):
        for field in ('change_birth', 'change_exec', 'change_hash'):
            with self.subTest(field=field):
                self.lib = LostExecutable()
                self.scanner = u.DarwinProcesses(lib=self.lib, codesign=self.lib)
                setattr(self.lib, field, True); self.refused()

    def test_failed_or_short_identity_reads_fail_closed(self):
        for field in ('denied_info', 'short_unique'):
            with self.subTest(field=field):
                setattr(self.lib, field, True); self.refused(); setattr(self.lib, field, False)
        for op in (0, 5, 11, 14, 17):
            with self.subTest(op=op):
                self.lib.denied_op = op; self.refused()

    def test_malformed_signing_reply_and_exec_during_token_reads_fail_closed(self):
        self.lib.malformed = True; self.refused()
        self.lib.malformed = False; self.lib.exec_during_signing = True; self.refused()

    def test_gone_process_needs_no_fallback(self):
        self.lib.path_error = errno.ESRCH
        self.assertEqual(self.scanner.managers(), [])
        self.assertEqual(self.lib.calls, [])

if __name__ == '__main__': unittest.main()
