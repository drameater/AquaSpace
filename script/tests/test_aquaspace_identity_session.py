"""Identity-session transport tests: no native APIs, subprocesses or signals."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_update as update


def owner():
    return dict(pid=9, uid=os.getuid(), startedSeconds=100, startedMicroseconds=2,
                executable='/fixture/app', uniqueId=7, execVersion=1, imageUUID=[1] * 16)


def session():
    return dict(windows=[['42', 'saved', 'floating', 'true']],
                workspaces=[['saved', 'h_tiles']], workspace='current', focusedWindow='', owners={'42': owner()})


class IdentitySessionTests(unittest.TestCase):
    def setUp(self):
        self.native = object.__new__(update.NativeSystem)
        self.process = update.Process(1234, (100, 2), os.getuid(), Path('/fixture/AquaSpace'), 'local.aquaspace')
        self.status = dict(pid=1234, controlsWindows=True, readOnly=False, phase='controlling', originalPids=[], sessionSchema=2)
        self.payload = session()
        self.final_status = None
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('aquaspace_update.ctypes.CDLL', side_effect=AssertionError('No native library')).start()
        patch('aquaspace_update.subprocess.run', side_effect=AssertionError('No subprocess')).start()
        patch('aquaspace_update.os.kill', side_effect=AssertionError('No signal')).start()
        patch.object(self.native, 'run', side_effect=self.query).start()

    def query(self, args):
        self.calls.append(args[1:])
        if args[1:] == ['management-status']:
            count = self.calls.count(['management-status'])
            return json.dumps(self.final_status if count > 1 and self.final_status is not None else self.status)
        if args[1:] == ['handoff-session']: return json.dumps(self.payload)
        raise AssertionError('No legacy fallback after schema advertisement: ' + repr(args))

    def capture(self): return self.native.capture_session(self.process, Path('/fixture/cli'))

    def test_new_export_preserves_pending_window_identities(self):
        result = self.capture()
        self.assertEqual(result, self.payload)
        self.assertEqual(self.calls, [['management-status'], ['handoff-session'], ['management-status']])

    def test_final_controller_or_schema_change_refuses(self):
        for field, value in [('pid', 4321), ('controlsWindows', False), ('sessionSchema', 3), ('sessionSchema', 2.0)]:
            self.calls = []; self.final_status = dict(self.status, **{field: value})
            with self.subTest(field=field), self.assertRaises(update.UpdateError): self.capture()

    def test_unknown_or_boolean_schema_is_not_downgraded(self):
        for value in (1, 3, True, '2'):
            self.calls = []; self.status['sessionSchema'] = value
            with self.subTest(schema=value), self.assertRaises(update.UpdateError): self.capture()
            self.assertEqual(self.calls, [['management-status']])

    def test_missing_or_malformed_identity_payload_has_no_legacy_fallback(self):
        for payload in ({}, None, dict(session(), owners=None), dict(session(), windows='42')):
            self.calls = []; self.payload = payload
            with self.subTest(payload=payload), self.assertRaises(update.UpdateError): self.capture()
            self.assertNotIn(['list-windows', '--all'], self.calls)

    def test_foreign_uid_reused_pid_and_invalid_exec_identity_refuse(self):
        for key, value in [('uid', os.getuid()+1), ('pid', 0), ('pid', True), ('execVersion', 0),
                           ('uniqueId', 0), ('uniqueId', 2**64), ('startedSeconds', 2**64), ('execVersion', 2**31), ('imageUUID', [0]*16), ('imageUUID', [1]*15),
                           ('imageUUID', [True]*16), ('executable', 'relative'), ('startedMicroseconds', 1000000)]:
            self.payload = session(); self.payload['owners']['42'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(update.UpdateError): self.capture()

    def test_duplicate_malformed_and_unrecorded_windows_refuse(self):
        for change in ('duplicate', 'leading-zero', 'unknown-owner', 'bad-fullscreen'):
            self.payload = session()
            if change == 'duplicate': self.payload['windows'] *= 2
            if change == 'leading-zero': self.payload['windows'][0][0] = '042'
            if change == 'unknown-owner': self.payload['owners']['99'] = owner()
            if change == 'bad-fullscreen': self.payload['windows'][0][3] = 'yes'
            with self.subTest(change=change), self.assertRaises(update.UpdateError): self.capture()

    def test_uncaptured_window_owner_is_not_invented(self):
        self.payload['owners'] = {}
        self.assertEqual(self.capture()['owners'], {})

    def test_export_command_failure_is_not_downgraded(self):
        def failed(args):
            if args[1:] == ['management-status']: return json.dumps(self.status)
            raise update.UpdateError('Cannot capture handoff session')
        with patch.object(self.native, 'run', side_effect=failed):
            with self.assertRaises(update.UpdateError): self.capture()

    def test_legacy_release_retains_immediate_compatibility_without_owners(self):
        legacy = copy.deepcopy(self.status); legacy.pop('sessionSchema')
        def query(args):
            if args[1:] == ['management-status']: return json.dumps(legacy)
            if args[1:3] == ['list-windows', '--all']: return '42\tsaved\tfloating\tfalse'
            if args[1:3] == ['list-workspaces', '--all']: return 'saved\th_tiles'
            if args[1:] == ['list-workspaces', '--focused']: return 'saved'
            if args[1:3] == ['list-windows', '--focused']: return '42'
            raise AssertionError(args)
        with patch.object(self.native, 'run', side_effect=query): result = self.capture()
        self.assertNotIn('owners', result)
        self.assertEqual(result['windows'][0][1], 'saved')
