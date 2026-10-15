"""Actual ctypes adapter, fake native entrypoints; never load or execute a helper."""
import ctypes
import importlib.util
import json
import re
import sys
from types import SimpleNamespace
import unittest

from test_aquaspace_ghostty_focus_restore import SOURCE, offline

with offline():
    spec = importlib.util.spec_from_file_location('ghostty_retained_script_driver', SOURCE/'script_driver.py')
    driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver
    spec.loader.exec_module(driver)


class RetainedDriverTests(unittest.TestCase):
    def fixture(self, *, capability=b'ghostty-topology-retained-source-v5', native_error=False):
        calls, frees, buffers = [], [], []
        source = SimpleNamespace(app=0x100000001, window=0x200000002, pid=100, title='~')
        state = dict(foreground_window=source.window, mutated=False)
        def run(payload, app, window):
            calls.append((json.loads(payload),app.value,window.value))
            # Simulate a same-PID, same-title replacement at the native boundary.
            # The source pointer must remain the OLD window, never the current one.
            if native_error: state['foreground_window'] = 0x300000003
            accepted = window.value == state['foreground_window']
            if accepted: state['mutated'] = True
            result = dict(ok=accepted, result={'activationRequested':True}, error='Source window changed')
            buffer = ctypes.create_string_buffer(json.dumps(result).encode())
            buffers.append(buffer)
            return ctypes.addressof(buffer)
        def free(pointer): frees.append(pointer)
        def version(): return capability
        library = SimpleNamespace(AquaSpaceGhosttyDriverCapabilities=version,
                                  AquaSpaceGhosttyDriverRun=run, AquaSpaceGhosttyDriverFree=free)
        instance = driver.ScriptDriver('/not-loaded.dylib',load_library=lambda _:library)
        return instance, source, state, calls, frees

    def test_exact_full_width_source_references_cross_the_native_call(self):
        with offline():
            d, source, state, calls, frees = self.fixture()
            observed = []
            self.assertEqual(d.run(200,'activate','tab','terminal',source=source,
                                   confirm_source=lambda value:observed.append(value)),
                             {'activationRequested':True})
            self.assertIs(observed[0],source)
            self.assertEqual(calls[0][1:],(source.app,source.window))
            self.assertEqual(d._run.argtypes,[ctypes.c_char_p,ctypes.c_void_p,ctypes.c_void_p])
            self.assertEqual(len(frees),1)

    def test_same_pid_same_title_replacement_never_becomes_a_new_source(self):
        with offline():
            for operation in ('create','add','select','activate'):
                with self.subTest(operation=operation):
                    d, source, state, calls, frees = self.fixture(native_error=True)
                    with self.assertRaisesRegex(driver.DriverError,'Source window changed'):
                        d.run(200,operation,'tab','terminal',source=source,confirm_source=lambda _:None)
                    self.assertEqual(calls[0][2],source.window)
                    self.assertNotEqual(calls[0][2],state['foreground_window'])
                    self.assertFalse(state['mutated'])
                    self.assertEqual(len(frees),1)

    def test_missing_or_metadata_only_source_never_reaches_native_code(self):
        with offline():
            d, source, _, calls, _ = self.fixture()
            for bad in (None,SimpleNamespace(app=None,window=None,pid=100,title='~')):
                with self.subTest(source=bad),self.assertRaises(driver.DriverError):
                    d.run(200,'activate','tab','terminal',source=bad,confirm_source=lambda _:None)
            self.assertEqual(calls,[])

    def test_final_source_confirmation_refuses_before_any_native_mutation(self):
        with offline():
            d, source, _, calls, _ = self.fixture()
            def changed(_): raise RuntimeError('Foreground changed during preparation')
            with self.assertRaisesRegex(RuntimeError,'Foreground changed'):
                d.run(200,'activate','tab','terminal',source=source,confirm_source=changed)
            self.assertEqual(calls,[])

    def test_old_or_unreadable_library_never_falls_back_to_an_executable(self):
        with offline():
            for version in (b'ghostty-topology-foreground-v2',b'old',None):
                with self.subTest(version=version),self.assertRaises(driver.DriverError):
                    self.fixture(capability=version)
            with self.assertRaises(driver.DriverError):
                driver.ScriptDriver('/missing',load_library=lambda _:SimpleNamespace())

    def test_legacy_v3_library_is_refused_before_any_native_operation(self):
        with offline():
            calls = []
            def legacy_capability():
                return b'ghostty-topology-retained-source-v3'
            def native_operation(*args):
                calls.append(args)
                raise AssertionError('Legacy native operations must never run')
            library = SimpleNamespace(AquaSpaceGhosttyDriverCapabilities=legacy_capability,
                                      AquaSpaceGhosttyDriverRun=native_operation,
                                      AquaSpaceGhosttyDriverFree=native_operation)
            loads = []
            def load(path):
                loads.append(path)
                return library
            with self.assertRaisesRegex(driver.DriverError, 'Unsupported diagnostic library'):
                driver.ScriptDriver('/fake/legacy-v3.dylib', load_library=load)
            self.assertEqual(loads, ['/fake/legacy-v3.dylib'])
            self.assertEqual(calls, [])

    def test_legacy_v4_library_cannot_admit_unguarded_termination(self):
        with offline(), self.assertRaisesRegex(driver.DriverError, 'Unsupported diagnostic library'):
            self.fixture(capability=b'ghostty-topology-retained-source-v4')

    def test_native_export_and_adapter_require_the_fresh_reader_capability(self):
        source = (SOURCE/'ScriptDriver.m').read_text()
        exported = re.search(r'const char \*AquaSpaceGhosttyDriverCapabilities\(void\)\s*\{\s*(?://[^\n]*\n\s*)*return "([^"]+)";\s*\}', source)
        self.assertIsNotNone(exported)
        self.assertEqual(exported.group(1), 'ghostty-topology-retained-source-v5')
        self.assertEqual(exported.group(1).encode('ascii'), driver.ScriptDriver.CAPABILITY)

    def test_native_source_is_retained_and_not_recaptured_from_current_focus(self):
        source = (SOURCE/'ScriptDriver.m').read_text()
        initializer = source[source.index('- (instancetype)initWithSnapshot:',source.index('@implementation')):source.index('- (void)check {')]
        self.assertIn('CFRetain(application)',initializer)
        self.assertIn('CFRetain(window)',initializer)
        self.assertNotIn('AXUIElementCopyAttributeValue',initializer)
        self.assertNotIn('AXUIElementCreateApplication',initializer)

    def test_windowless_source_preserves_app_reference_and_requires_no_window(self):
        with offline():
            d, source, state, calls, _ = self.fixture()
            source.window = None; source.title = None; state['foreground_window'] = None
            d.run(200,'create','owned-title',source=source,confirm_source=lambda _:None)
            self.assertEqual(calls[0][1:],(source.app,None))
            self.assertFalse(calls[0][0]['foreground']['hasWindow'])

    def test_invalid_pointer_width_or_missing_final_guard_refuses_native_entry(self):
        with offline():
            for pointer in (True,0,-1,1 << (8*ctypes.sizeof(ctypes.c_void_p))):
                with self.subTest(pointer=pointer):
                    d, source, _, calls, _ = self.fixture(); source.window = pointer
                    with self.assertRaises(driver.DriverError):
                        d.run(200,'select','tab','terminal',source=source,confirm_source=lambda _:None)
                    self.assertEqual(calls,[])
            d, source, _, calls, _ = self.fixture()
            with self.assertRaises(driver.DriverError): d.run(200,'activate',source=source)
            self.assertEqual(calls,[])
            for invalid in (True,0,-1,'100'):
                d, source, _, calls, _ = self.fixture(); source.pid = invalid
                with self.assertRaises(driver.DriverError):
                    d.run(200,'activate',source=source,confirm_source=lambda _:None)
                self.assertEqual(calls,[])

    def test_malformed_native_response_is_freed_once_without_retry(self):
        with offline():
            d, source, _, _, frees = self.fixture()
            buffer = ctypes.create_string_buffer(b'not-json')
            attempts = []
            def bad(*args): attempts.append(args); return ctypes.addressof(buffer)
            d._run = bad
            with self.assertRaises(ValueError):
                d.run(200,'activate','tab','terminal',source=source,confirm_source=lambda _:None)
            self.assertEqual(len(attempts),1)
            self.assertEqual(frees,[ctypes.addressof(buffer)])

    def test_status_and_close_need_no_foreground_grant_or_application_activation(self):
        with offline():
            for op in ('status','close'):
                with self.subTest(op=op):
                    d, _, state, calls, _ = self.fixture(); state['foreground_window'] = None
                    d.run(200,op,'tab','terminal')
                    self.assertEqual(calls[0][1:],(None,None))
                    self.assertNotIn('foreground',calls[0][0])

    def test_termination_passes_the_pinned_identity_without_foreground_activation(self):
        with offline():
            d, _, state, calls, _ = self.fixture(); state['foreground_window'] = None
            d.run(200, 'terminate', expected_birth=(200,123,456,501), protected_pids=[100])
            self.assertEqual(calls[0][0]['process'], {'birth':[200,123,456,501], 'protected':[100]})
            self.assertEqual(calls[0][1:], (None,None))
            self.assertNotIn('foreground', calls[0][0])

    def test_missing_malformed_or_protected_termination_identity_never_reaches_native_code(self):
        cases = [({},()),
                 ({'expected_birth':(200,123,456,501)},()),
                 ({'expected_birth':(201,123,456,501),'protected_pids':[100]},()),
                 ({'expected_birth':(200,123,456,501),'protected_pids':[200]},()),
                 ({'expected_birth':(200,123,456,501),'protected_pids':[True]},()),
                 ({'expected_birth':(200,123,1000000,501),'protected_pids':[100]},()),
                 ({'expected_birth':(200,True,456,501),'protected_pids':[100]},()),
                 ({'expected_birth':(200,123,456,-1),'protected_pids':[100]},()),
                 ({'expected_birth':(200,123,456,501),'protected_pids':[100]},('extra',))]
        with offline():
            for kwargs, arguments in cases:
                with self.subTest(kwargs=kwargs, arguments=arguments):
                    d, _, _, calls, _ = self.fixture()
                    with self.assertRaises(driver.DriverError): d.run(200,'terminate',*arguments,**kwargs)
                    self.assertEqual(calls, [])

    def test_native_termination_refusal_is_freed_without_retry_or_helper_fallback(self):
        with offline():
            d, _, _, calls, frees = self.fixture(native_error=True)
            with self.assertRaises(driver.DriverError):
                d.run(200,'terminate',expected_birth=(200,123,456,501),protected_pids=[100])
            self.assertEqual(len(calls),1)
            self.assertEqual(len(frees),1)

    def test_swift_helper_has_no_pid_only_termination_entrypoint(self):
        source = (SOURCE/'LaunchGhostty.swift').read_text()
        self.assertNotIn('args.first == "terminate"', source)
        self.assertNotIn('.terminate()', source)


if __name__ == '__main__':
    unittest.main()
