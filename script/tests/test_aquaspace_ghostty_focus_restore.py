"""Offline teardown/focus regressions; every native/process boundary is fake."""
import ast
import contextlib
import io
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'script/native-tabs-smoke/ghostty-topology'


@contextlib.contextmanager
def offline():
    with contextlib.ExitStack() as stack:
        for api in ('ctypes.CDLL', 'subprocess.Popen', 'subprocess.run', 'os.kill',
                    'pathlib.Path.write_text', 'pathlib.Path.mkdir'):
            stack.enter_context(patch(api, side_effect=AssertionError('Forbidden live API: ' + api)))
        yield


class CleanupOrderingTests(unittest.TestCase):
    def fixture(self, *, external=False, drift_after_close=False, focus_ignored=False):
        state = dict(workspace='test-a', window='test-window', foreground='test',
                     commands=[], closes=[], saved={}, focus_checks=0, released=False)
        if external:
            state.update(workspace='new-user-workspace', window='new-user-window', foreground='user')
        baseline = {'11344': ['11344', '1', 'h_tiles', 'false']}
        events, errors = [], []
        manager = SimpleNamespace(identity=(1, 2, 3), record=lambda: {'pid': 1})

        def cli(*args):
            if args == ('list-workspaces', '--focused'):
                return state['workspace']
            if args[0] == 'workspace':
                state['commands'].append(args)
                state['workspace'] = args[1]
                return ''
            if args[:2] == ('focus', '--window-id'):
                state['commands'].append(args)
                if not focus_ignored:
                    state.update(workspace='1', window=args[2], foreground='original')
                return ''
            raise AssertionError('Unexpected CLI call ' + str(args))

        def run(arguments, **kwargs):
            if 'close' in arguments:
                state['closes'].append(state['foreground'])
                # Closing a foreground test app hands focus to another original
                # window. Closing it after restoration is background cleanup.
                if state['foreground'] == 'test' or drift_after_close:
                    state.update(workspace='2', window='11584', foreground='other')
                return '{"closed":true}'
            if 'status' in arguments:
                return '{"groups":[]}'
            if 'terminate' in arguments:
                return 'true'
            raise AssertionError('Unexpected process call ' + str(arguments))

        def restore_before_cleanup(*args):
            if state['foreground'] not in ('test', 'original'):
                raise RuntimeError('External focus; refusing restoration')
            if state['foreground'] == 'test':
                cli('focus', '--window-id', '11344')
            if state['window'] != '11344':
                raise RuntimeError('Focus request did not restore desktop')

        def verify_after_cleanup():
            state['focus_checks'] += 1
            if (state['workspace'], state['window'], state['foreground']) != ('1', '11344', 'original'):
                raise RuntimeError('Desktop focus not restored')
            return True

        controller = SimpleNamespace(
            restore_before_cleanup=restore_before_cleanup,
            verify_after_cleanup=verify_after_cleanup,
            observe=lambda phase: events.append(('focus', phase)),
            report=lambda: {'verified': state['focus_checks'] > 0 and not errors})
        scope = dict(
            pid=200, birth=(200, 1, 2, 501), protected=[100], created={'a': {'tab': 'a', 'terminal': 'a-terminal'}},
            manager=manager, N=SimpleNamespace(run=run, managers=lambda: [manager], processes=SimpleNamespace(_info=lambda pid: None)),
            HERE=Path('/unused'), session={'workspace': '1', 'focusedWindow': '11344'}, baseline=baseline,
            cli=cli, ghost_birth=lambda pid: (200, 1, 2, 501), personal_before=[],
            personal_identity=lambda value: [], driver=lambda *args, **kwargs: {},
            own_rows=lambda: [], preserved=lambda: None, diff=lambda value: {}, rows=lambda: baseline,
            check=lambda condition, note, **data: self.assertTrue(condition, note),
            event=lambda kind, **data: events.append((kind, data)),
            save=lambda name, value: state['saved'].__setitem__(name, value),
            time=SimpleNamespace(monotonic=lambda: 0, sleep=lambda seconds: None),
            json=__import__('json'), errors=errors, success=True, checks=[],
            stop=SimpleNamespace(set=lambda: None), threads=[], samples=[], wsamples=[], OUT=Path('/unused'),
            sys=SimpleNamespace(exit=lambda code: None), A='test-a', B='test-b', prefix='test-',
            record_cleanup_focus=lambda phase: events.append(('focus', phase)),
            focus_controller=controller,
            focus_ax=SimpleNamespace(close=lambda: state.__setitem__('released', True)))
        tree = ast.parse((SOURCE / 'live.py').read_text())
        final = next(node.finalbody for node in reversed(tree.body)
                     if isinstance(node, ast.Try) and node.finalbody)
        return compile(ast.Module(body=final, type_ignores=[]), str(SOURCE / 'live.py'), 'exec'), scope, state

    def execute(self, **options):
        with offline(), contextlib.redirect_stdout(io.StringIO()):
            code, scope, state = self.fixture(**options)
            exec(code, scope)
            return scope, state

    def test_last_foreground_close_handoff_is_prevented_by_restoring_first(self):
        scope, state = self.execute()
        self.assertEqual(state['closes'], ['original'])
        self.assertEqual((state['workspace'], state['window']), ('1', '11344'))
        self.assertTrue(state['saved']['results.json']['success'])
        self.assertGreater(state['focus_checks'], 0)

    def test_new_user_focus_is_not_overridden_and_not_reported_as_pass(self):
        scope, state = self.execute(external=True)
        self.assertEqual(state['commands'], [])
        self.assertEqual(state['closes'], ['user'])
        self.assertFalse(state['saved']['results.json']['success'])
        self.assertTrue(scope['errors'])

    def test_cleanup_focus_drift_invalidates_success_without_another_focus_write(self):
        scope, state = self.execute(drift_after_close=True)
        self.assertFalse(state['saved']['results.json']['success'])
        self.assertEqual(state['commands'], [('focus', '--window-id', '11344')])
        self.assertGreater(state['focus_checks'], 0)

    def test_successful_command_without_focus_change_is_not_reported_as_pass(self):
        scope, state = self.execute(focus_ignored=True)
        self.assertFalse(state['saved']['results.json']['success'])
        self.assertTrue(scope['errors'])


import importlib.util
import sys

with offline():
    spec = importlib.util.spec_from_file_location('ghostty_focus_restoration_policy', SOURCE / 'focus_restore.py')
    policy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = policy
    spec.loader.exec_module(policy)


class Ref:
    def __init__(self, pid, title=None):
        self.pid, self.title, self.alive = pid, title, True


class FocusEnvironment:
    def __init__(self, *, empty=False):
        self.now = 0
        self.original = Ref(100)
        self.original_window = None if empty else Ref(100, 'original')
        self.test, self.test_window = Ref(200), Ref(200, 'AeroSpace-NativeTabs-offline-unique-a')
        self.external, self.external_window = Ref(300), Ref(300, 'new-user-work')
        self.windows = {100: self.original_window, 200: self.test_window, 300: self.external_window}
        self.front = self.original
        self.model = dict(workspace='1', window='' if empty else '11344', pid=None if empty else 100,
                          title=None if empty else 'original', unique=True)
        self.initial_model = dict(self.model)
        self.commands, self.activations, self.log = [], [], []
        self.delay, self.pending, self.ignore_native, self.ignore_command = 0, None, False, False
        self.workspace_windows = ''
        self.guard_failure = None
        self.hook = lambda: None
        self.controller = policy.FocusRestoration(
            ax=self, read_model=self.read_model, cli=self.cli, guard=self.guard,
            log=lambda phase, data: self.log.append(data), clock=lambda: self.now,
            pause=self.pause, timeout=1.5, stable_for=.3)

    def pause(self, seconds):
        self.now += seconds

    def guard(self):
        if self.guard_failure:
            raise policy.FocusError(self.guard_failure)

    def read_model(self):
        self.hook()
        if self.pending is not None and self.now >= self.pending:
            self.front, self.pending = self.original, None
        return dict(self.model)

    def focused_application(self):
        return self.front

    def pid(self, ref):
        if not ref.alive:
            raise policy.FocusError('stale native element')
        return ref.pid

    def optional_element(self, app, attribute):
        self.pid(app)
        return self.windows[app.pid]

    def same(self, left, right):
        return left is right

    def title(self, window):
        self.pid(window)
        return window.title

    def set_frontmost(self, app):
        self.pid(app)
        self.activations.append(app)
        self.front = app

    def cli(self, *args):
        if args == ('list-windows', '--workspace', '1', '--format', '%{window-id}'):
            return self.workspace_windows
        self.commands.append(args)
        if args == ('focus', '--window-id', '11344'):
            if not self.ignore_command:
                self.model = dict(self.initial_model)
                if not self.ignore_native:
                    if self.delay:
                        self.pending = self.now + self.delay
                    else:
                        self.front = self.original
        elif args == ('workspace', '1'):
            self.model = dict(self.initial_model)
        else:
            raise AssertionError('Unexpected CLI command: ' + str(args))
        return ''

    def capture(self):
        self.controller.capture_initial(dict(workspace='1', focusedWindow=self.initial_model['window']))

    def test_focus(self):
        self.front = self.test
        self.model = dict(workspace='test-a', window='test-window', pid=200,
                          title=self.test_window.title, unique=True)

    def external_focus(self):
        self.front = self.external
        self.model = dict(workspace='new-user-workspace', window='new-user-window', pid=300,
                          title='new-user-work', unique=True)

    def restore(self):
        return self.controller.restore_before_cleanup(200, {'test-window'}, {'test-a', 'test-b'},
                                                       'AeroSpace-NativeTabs-offline-unique-')


class FocusPolicyTests(unittest.TestCase):
    def test_focus_switch_during_window_or_title_read_never_authorizes_restore(self):
        for boundary in ('window', 'title'):
            with self.subTest(boundary=boundary), offline():
                f = FocusEnvironment(); f.capture(); f.test_focus()
                adapter = native_menu.NativeAX.__new__(native_menu.NativeAX)
                adapter.frontmost_pid = lambda: f.front.pid
                adapter.application = lambda pid: {100: f.original, 200: f.test, 300: f.external}[pid]
                adapter.pid = f.pid
                f.focused_application = adapter.focused_application
                original = f.optional_element if boundary == 'window' else f.title
                reads = []
                def switch(ref, *args):
                    result = original(ref, *args)
                    if ref is (f.test if boundary == 'window' else f.test_window):
                        reads.append(ref)
                        if len(reads) == 2: f.external_focus()
                    return result
                if boundary == 'window': f.optional_element = switch
                else: f.title = switch
                with self.assertRaises(policy.FocusError): f.restore()
                self.assertEqual(f.commands, [])
                self.assertIs(f.front, f.external)

    def test_empty_restore_checks_foreground_after_late_validation_reads(self):
        for boundary in ('app-window', 'workspace-windows', 'request-log'):
            with self.subTest(boundary=boundary), offline():
                f = FocusEnvironment(empty=True); f.capture(); f.test_focus()
                original_element, original_cli = f.optional_element, f.cli
                def element(app, attribute):
                    result = original_element(app, attribute)
                    if boundary == 'app-window' and app is f.original and f.commands:
                        f.external_focus()
                    return result
                def cli(*args):
                    result = original_cli(*args)
                    if boundary == 'workspace-windows' and args[0] == 'list-windows' and f.commands:
                        f.external_focus()
                    return result
                def log(phase, data):
                    if boundary == 'request-log' and data.get('request', {}).get('kind') == 'activate-empty-application':
                        f.external_focus()
                f.optional_element, f.controller.cli, f.controller.log = element, cli, log
                with self.assertRaises(policy.FocusError): f.restore()
                self.assertEqual(f.activations, [])
                self.assertIs(f.front, f.external)

    def test_focus_request_logging_cannot_hide_a_new_user_focus_change(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus()
            f.controller.log = lambda phase, data: f.external_focus() if phase == 'restore-request' else None
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])
            self.assertIs(f.front, f.external)

    def test_same_application_window_switch_during_snapshot_is_rejected(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus()
            other = Ref(200, 'personal-work')
            original_title = f.title
            reads = []
            def changed(window):
                result = original_title(window)
                if window is f.test_window:
                    reads.append(window)
                    if len(reads) == 4: f.windows[200] = other
                return result
            f.title = changed
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])

    def test_restore_and_post_cleanup_validation_require_same_native_window_and_model(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.restore()
            self.assertEqual(f.commands, [('focus', '--window-id', '11344')])
            self.assertIs(f.front, f.original)
            self.assertFalse(f.controller.report()['verified'])
            self.assertTrue(f.controller.verify_after_cleanup())
            self.assertTrue(f.controller.report()['verified'])

    def test_delayed_native_focus_is_awaited_after_model_already_matches(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.delay = .4
            start = f.now; f.restore()
            self.assertGreaterEqual(f.now - start, .7)
            self.assertTrue(any(v.get('model',{}).get('window') == '11344' and v.get('native_pid') == 200
                                for v in f.log))
            self.assertEqual(len(f.commands), 1)

    def test_model_only_restore_and_ignored_commands_both_fail(self):
        for ignored in ('ignore_native', 'ignore_command'):
            with self.subTest(ignored=ignored), offline():
                f = FocusEnvironment(); f.capture(); f.test_focus(); setattr(f, ignored, True)
                with self.assertRaises(policy.FocusError): f.restore()
                self.assertFalse(f.controller.report()['verified'])
                self.assertEqual(len(f.commands), 1)

    def test_new_user_focus_before_cleanup_is_preserved(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.external_focus()
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, []); self.assertEqual(f.activations, [])
            self.assertIs(f.front, f.external)

    def test_user_focus_change_during_original_window_validation_cancels_the_write(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus()
            original_title = f.title
            def changed(window):
                if window is f.original_window:
                    f.external_focus()
                return original_title(window)
            f.title = changed
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])
            self.assertIs(f.front, f.external)

    def test_native_user_focus_is_preserved_even_when_manager_still_reports_test(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.front = f.external
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, []); self.assertIs(f.front, f.external)

    def test_workspace_name_does_not_authorize_an_unowned_window(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.model['window'] = 'unowned'
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])

    def test_native_test_window_must_have_owned_prefix(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.test_window.title = 'personal-work'
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])

    def test_late_cleanup_handoff_is_failure_and_never_triggers_another_focus_write(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.restore(); f.external_focus()
            with self.assertRaises(policy.FocusError): f.controller.verify_after_cleanup()
            self.assertFalse(f.controller.report()['verified'])
            self.assertEqual(f.commands, [('focus', '--window-id', '11344')])
            self.assertIs(f.front, f.external)

    def test_initially_matching_post_cleanup_read_does_not_hide_delayed_focus_loss(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.restore()
            deadline = f.now + .15
            f.hook = lambda: f.external_focus() if f.now >= deadline else None
            with self.assertRaises(policy.FocusError): f.controller.verify_after_cleanup()
            self.assertEqual(len(f.commands), 1)

    def test_already_restored_focus_needs_no_focus_write(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.restore()
            self.assertEqual(f.commands, [])
            self.assertTrue(f.controller.verify_after_cleanup())

    def test_missing_original_window_or_uncertain_manager_refuses_restoration(self):
        for reason in ('closed-root', 'manager-changed', 'workspace-changed'):
            with self.subTest(reason=reason), offline():
                f = FocusEnvironment(); f.capture(); f.test_focus()
                if reason == 'closed-root': f.original_window.alive = False
                else: f.guard_failure = reason
                with self.assertRaises(policy.FocusError): f.restore()
                self.assertEqual(f.commands, [])

    def test_manager_change_while_native_focus_settles_never_retries(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.test_focus(); f.delay = .4
            deadline = f.now + .1
            def changed():
                if f.now >= deadline: f.guard_failure = 'manager-changed'
            f.hook = changed
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(len(f.commands), 1)

    def test_empty_workspace_restores_stable_original_application_without_window_id(self):
        with offline():
            f = FocusEnvironment(empty=True); f.capture(); f.test_focus(); f.restore()
            self.assertEqual(f.commands, [('workspace', '1')])
            self.assertEqual(f.activations, [f.original])
            self.assertTrue(f.controller.verify_after_cleanup())

    def test_empty_application_gaining_a_window_is_not_activated(self):
        with offline():
            f = FocusEnvironment(empty=True); f.capture(); f.test_focus()
            f.windows[100] = Ref(100, 'new personal window')
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, []); self.assertEqual(f.activations, [])

    def test_other_application_window_in_empty_workspace_prevents_restoration(self):
        with offline():
            f = FocusEnvironment(empty=True); f.capture(); f.test_focus(); f.workspace_windows = '9000'
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, []); self.assertEqual(f.activations, [])

    def test_windowless_focus_with_managed_windows_is_not_an_empty_workspace_baseline(self):
        with offline():
            f = FocusEnvironment(empty=True); f.workspace_windows = '9000'
            with self.assertRaises(policy.FocusError): f.capture()
            self.assertEqual(f.commands, []); self.assertEqual(f.activations, [])

    def test_incoherent_ambiguous_or_fabricated_empty_baselines_are_rejected(self):
        for problem in ('pid', 'title', 'ambiguous', 'empty-native-window'):
            with self.subTest(problem=problem), offline():
                f = FocusEnvironment(empty=problem == 'empty-native-window')
                if problem == 'pid': f.model['pid'] = 999
                elif problem == 'title': f.model['title'] = 'stale model title'
                elif problem == 'ambiguous': f.model['unique'] = False
                else: f.windows[100] = Ref(100, 'real focused window')
                with self.assertRaises(policy.FocusError): f.capture()
                self.assertEqual(f.commands, []); self.assertEqual(f.activations, [])

    def test_capture_stability_restarts_when_native_window_reference_changes(self):
        with offline():
            f = FocusEnvironment(); replacement = Ref(100, 'original')
            f.hook = lambda: f.windows.__setitem__(100, replacement) if f.now >= .2 else None
            f.capture()
            self.assertIs(f.controller.initial.window, replacement)
            self.assertGreaterEqual(f.now, .5)

    def test_different_model_window_does_not_count_as_already_restored(self):
        with offline():
            f = FocusEnvironment(); f.capture(); f.model['window'] = 'another-window'
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])


class ModelFocusReaderTests(unittest.TestCase):
    def cli(self, *, focused='11344\t1\t100\toriginal', all_rows='11344\t100\toriginal', error=None):
        def fake(*args):
            if args[0] == 'list-workspaces': return '1'
            if '--focused' in args:
                if error: raise RuntimeError(error)
                return focused
            return all_rows
        return fake

    def test_exact_no_focused_window_is_supported_but_other_errors_are_not(self):
        with offline():
            self.assertEqual(policy.read_model(self.cli(error='No window is focused'))['window'], '')
            with self.assertRaises(RuntimeError): policy.read_model(self.cli(error='socket failed'))

    def test_duplicate_title_marks_baseline_ambiguous(self):
        with offline():
            self.assertFalse(policy.read_model(self.cli(all_rows='11344\t100\toriginal\n222\t100\toriginal'))['unique'])
            self.assertTrue(policy.read_model(self.cli())['unique'])

    def test_inconsistent_workspace_or_malformed_rows_are_rejected(self):
        for row in ('11344\t2\t100\toriginal', '11344\t1\tnot-pid\toriginal', 'bad'):
            with self.subTest(row=row), offline(), self.assertRaises(policy.FocusError):
                policy.read_model(self.cli(focused=row))


with offline():
    spec = importlib.util.spec_from_file_location('ghostty_native_focus_adapter', SOURCE / 'native_menu.py')
    native_menu = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = native_menu
    spec.loader.exec_module(native_menu)


class NativeFocusAdapterTests(unittest.TestCase):
    def frontmost_backend(self, values):
        b = native_menu.NativeAX.__new__(native_menu.NativeAX)
        readings = iter(values)
        b.frontmost_pid = lambda: next(readings)
        b.application = lambda pid: ('retained-app', pid)
        b.pid = lambda app: app[1]
        return b

    def test_appkit_frontmost_pid_binds_and_rechecks_retained_ax_application(self):
        with offline():
            b = self.frontmost_backend([52214, 52214])
            self.assertEqual(b.focused_application(), ('retained-app', 52214))

    def test_appkit_foreground_churn_or_invalid_pid_cannot_fall_back_to_model(self):
        for values in ([52214, 60264], [0], [-1], [True], ['52214']):
            with self.subTest(values=values), offline():
                with self.assertRaises(native_menu.MenuError):
                    self.frontmost_backend(values).focused_application()

    def test_appkit_read_failure_and_ax_pid_mismatch_remain_errors(self):
        with offline():
            b = self.frontmost_backend([52214, 52214])
            b.pid = lambda app: 60264
            with self.assertRaises(native_menu.MenuError): b.focused_application()
            b = self.frontmost_backend([])
            b.frontmost_pid = lambda: (_ for _ in ()).throw(RuntimeError('AppKit unavailable'))
            with self.assertRaisesRegex(RuntimeError, 'AppKit unavailable'):
                b.focused_application()

    def test_systemwide_focus_read_retains_the_actual_application_element(self):
        with offline():
            b = native_menu.NativeAX.__new__(native_menu.NativeAX)
            b.refs = []
            queries = []
            b.ax = SimpleNamespace(AXUIElementCreateSystemWide=lambda: 1000,
                                   AXUIElementSetMessagingTimeout=lambda ref, seconds: 0)
            b.element = lambda parent, attribute: queries.append((parent, attribute)) or 2000
            self.assertEqual(b.focused_application(), 2000)
            self.assertEqual(queries, [(1000, 'AXFocusedApplication')])
            self.assertEqual(b.refs, [1000])

    def test_only_explicit_no_value_can_mean_no_native_focused_window(self):
        for error in (-25212, -25205, -25204):
            with self.subTest(error=error), offline():
                b = native_menu.NativeAX.__new__(native_menu.NativeAX)
                b.refs = []
                b.cf = SimpleNamespace(CFStringCreateWithCString=lambda *args: 1000)
                b.ax = SimpleNamespace(AXUIElementCopyAttributeValue=lambda *args: error)
                if error == -25212:
                    self.assertIsNone(b.optional_element(2000, 'AXFocusedWindow'))
                else:
                    with self.assertRaises(native_menu.MenuError):
                        b.optional_element(2000, 'AXFocusedWindow')


class FocusEntryWiringTests(unittest.TestCase):
    def test_coherent_focus_is_captured_before_starting_a_disposable_app(self):
        tree = ast.parse((SOURCE / 'live.py').read_text())
        capture = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == 'capture_initial']
        launches = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'run'
                    and any(isinstance(a, ast.Constant) and a.value == 'launch'
                            for arg in n.args for a in ast.walk(arg))]
        self.assertEqual(len(capture), 1)
        self.assertEqual(len(launches), 1)
        self.assertLess(capture[0], launches[0])


if __name__ == '__main__':
    unittest.main()
