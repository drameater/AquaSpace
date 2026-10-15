"""Execution focus safety using the real policy/runner functions and fake APIs."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_aquaspace_ghostty_focus_restore import FocusEnvironment, Ref, SOURCE, offline, policy


class ExecutionFocusTests(unittest.TestCase):
    def fixture(self):
        f = FocusEnvironment(); f.capture()
        rows = [['test-window', 'test-a', '200', f.test_window.title]]
        gate = policy.ExecutionFocus(f.controller, test_pid=lambda: 200,
                                     owned_rows=lambda: rows, workspaces={'test-a', 'test-b'},
                                     prefix='AeroSpace-NativeTabs-offline-unique-')
        return f, gate, rows

    def test_only_captured_initial_focus_authorizes_first_workspace_transition(self):
        with offline():
            f, gate, _ = self.fixture()
            source = gate.check('before-workspace')
            gate.workspace_selected('test-a', source)
            f.model = dict(workspace='test-a', window='', pid=None, title=None, unique=True)
            gate.check('before-create')
            f.test_focus(); gate.check('after-create')
            f.external_focus()
            with self.assertRaises(policy.FocusError): gate.check('next-action')
            self.assertEqual(f.commands, [])

    def test_initial_native_carry_cannot_authorize_an_unowned_model_window(self):
        with offline():
            f, gate, _ = self.fixture(); source = gate.check('initial')
            gate.workspace_selected('test-a', source)
            f.model.update(workspace='test-a', window='personal-new-window')
            with self.assertRaises(policy.FocusError): gate.check('before-create')

    def test_initial_ambiguity_or_model_identity_changes_refuse_first_action(self):
        for change in ({'unique':False}, {'pid':300}, {'title':'changed-model-title'}):
            with self.subTest(change=change), offline():
                f, gate, _ = self.fixture(); f.model.update(change)
                with self.assertRaises(policy.FocusError): gate.check('initial-transition')
                self.assertIsNotNone(f.controller.revoked)

    def test_loss_is_latched_even_if_user_returns_to_a_test_window(self):
        with offline():
            f, gate, _ = self.fixture(); f.test_focus(); gate.check('running')
            f.external_focus()
            with self.assertRaises(policy.FocusError): gate.check('poll')
            f.test_focus()
            with self.assertRaises(policy.FocusError): gate.check('later')
            with self.assertRaises(policy.FocusError): f.restore()
            self.assertEqual(f.commands, [])

    def test_returning_to_original_focus_mid_suite_is_not_new_permission(self):
        with offline():
            f, gate, _ = self.fixture(); f.test_focus(); gate.check('running')
            f.front, f.model = f.original, dict(f.initial_model)
            with self.assertRaises(policy.FocusError): gate.check('next-select')

    def test_wrong_workspace_pid_title_ambiguity_or_native_owner_refuses(self):
        for problem in ('workspace', 'model-pid', 'title', 'ambiguous', 'native-owner', 'foreign-id'):
            with self.subTest(problem=problem), offline():
                f, gate, _ = self.fixture(); f.test_focus()
                if problem == 'workspace': f.model['workspace'] = 'personal'
                elif problem == 'model-pid': f.model['pid'] = 300
                elif problem == 'title': f.test_window.title = 'personal-work'
                elif problem == 'ambiguous': f.model['unique'] = False
                elif problem == 'native-owner': f.front = f.external
                else: f.model['window'] = 'unowned'
                with self.assertRaises(policy.FocusError): gate.check(problem)

    def test_owner_or_original_window_guard_failure_is_not_bypassed(self):
        with offline():
            f, gate, _ = self.fixture(); f.test_focus(); f.guard_failure = 'original-window-changed'
            with self.assertRaises(policy.FocusError): gate.check('action')
            self.assertIsNotNone(f.controller.revoked)

    def test_test_to_empty_workspace_carries_only_the_unchanged_native_source(self):
        with offline():
            f, gate, rows = self.fixture(); f.test_focus(); source = gate.check('before-workspace')
            gate.workspace_selected('test-b', source)
            f.model = dict(workspace='test-b', window='', pid=None, title=None, unique=True)
            gate.check('before-new-window')
            f.external_focus()
            with self.assertRaises(policy.FocusError): gate.check('before-create')

    def test_foreground_change_during_gate_logging_revokes_execution(self):
        with offline():
            f, gate, _ = self.fixture(); f.test_focus()
            f.controller.log = lambda phase, data: f.external_focus()
            with self.assertRaises(policy.FocusError): gate.check('before-select')
            self.assertIsNotNone(f.controller.revoked)


class RunnerFocusTests(unittest.TestCase):
    def fixture(self):
        f = FocusEnvironment(); f.capture(); f.test_focus()
        rows = [['test-window', 'test-a', '200', f.test_window.title]]
        gate = policy.ExecutionFocus(f.controller, test_pid=lambda: 200, owned_rows=lambda: rows,
                                     workspaces={'test-a', 'test-b'}, prefix='AeroSpace-NativeTabs-offline-unique-')
        calls = []
        def cli(*args):
            calls.append(('cli', args))
            if args == ('list-workspaces', '--focused'): return f.model['workspace']
            if args[0] == 'workspace':
                f.model.update(workspace=args[1], window='', pid=None, title=None)
            return ''
        def run(args, **kwargs):
            calls.append(('helper', args))
            return json.dumps({'tab':'new', 'terminal':'terminal'})
        def isolated(): f.guard()
        tree = ast.parse((SOURCE / 'live.py').read_text())
        names = {'focus_guard', 'focus_test_workspace', 'fresh', 'select', 'move', 'menu', 'driver', 'settle', 'activate_initial'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = dict(activity=gate, isolated=isolated, preserved=f.guard, focus_controller=f.controller,
                     native=lambda: {'groups':[{'selected':'tab-a'}]}, pid=200, protected=[100],
                     created={'a':{'tab':'tab-a','terminal':'term-a'}}, own_rows=lambda: rows,
                     prefix='AeroSpace-NativeTabs-offline-unique-', A='test-a', B='test-b',
                     CLI=Path('/fake/cli'), HERE=Path('/fake/helper'), SOURCE=SOURCE,
                     sys=SimpleNamespace(executable='/fake/python'), N=SimpleNamespace(run=run),
                     cli=cli, json=json, focus_restore=policy, time=SimpleNamespace(sleep=f.pause, monotonic=lambda:f.now),
                     save=lambda *args:None, event=lambda *args, **kwargs:None)
        exec(compile(ast.Module(body=nodes,type_ignores=[]), str(SOURCE / 'live.py'), 'exec'), scope)
        return f, gate, scope, calls

    def test_every_runner_action_refuses_external_user_focus_before_mutation(self):
        actions = [('select',('a',)), ('move',('a','test-b')), ('fresh',('b',)),
                   ('menu',('merge','a')), ('driver',('create','test-title')),
                   ('driver',('add','test-title','tab-a','term-a')), ('driver',('select','tab-a','term-a'))]
        for name, args in actions:
            with self.subTest(action=name, args=args), offline():
                f, _, scope, calls = self.fixture(); f.external_focus()
                with self.assertRaises(policy.FocusError): scope[name](*args)
                self.assertEqual(calls, [])

    def test_late_user_focus_after_native_target_validation_refuses_move(self):
        with offline():
            f, _, scope, calls = self.fixture()
            scope['native'] = lambda: f.external_focus()
            with self.assertRaises(policy.FocusError): scope['move']('a','test-b')
            self.assertEqual(calls, [])

    def test_selection_rechecks_focus_after_helper_before_cli_activation(self):
        with offline():
            f, _, scope, calls = self.fixture()
            def run(args, **kwargs):
                calls.append(('helper', args)); f.external_focus()
                return '{}'
            scope['N'].run = run
            with self.assertRaises(policy.FocusError): scope['select']('a')
            self.assertEqual([kind for kind, _ in calls], ['helper'])

    def test_workspace_switch_rechecks_focus_before_creating_a_window(self):
        with offline():
            f, _, scope, calls = self.fixture(); original = scope['cli']
            def cli(*args):
                result = original(*args)
                if args[0] == 'workspace': f.front = f.external
                return result
            scope['cli'] = cli
            with self.assertRaises(policy.FocusError): scope['fresh']('b')
            self.assertFalse(any(kind == 'helper' for kind, _ in calls))

    def test_manager_or_window_guard_still_precedes_focus_mutation(self):
        with offline():
            f, _, scope, calls = self.fixture(); f.guard_failure = 'manager-changed'
            with self.assertRaises(policy.FocusError): scope['select']('a')
            self.assertEqual(calls, [])

    def test_authorized_selection_and_move_still_work_with_exact_target(self):
        with offline():
            f, _, scope, calls = self.fixture()
            scope['select']('a'); scope['move']('a','test-b')
            helper = calls[0][1]
            self.assertEqual(helper[1:5], [200,'select','tab-a','term-a'])
            self.assertEqual(helper[-2], '--foreground')
            self.assertEqual(json.loads(helper[-1]), {'pid':200,'title':f.test_window.title,'hasWindow':True})
            self.assertIn(('cli', ('focus','--window-id','test-window')), calls)
            self.assertIn(('cli', ('move-node-to-workspace','--window-id','test-window','--focus-follows-window','test-b')), calls)

    def test_poll_detects_user_focus_and_latches_before_next_selection(self):
        with offline():
            f, _, scope, calls = self.fixture()
            scope['native'] = lambda: f.external_focus()
            with self.assertRaises(policy.FocusError): scope['settle']({},'poll')
            f.test_focus()
            with self.assertRaises(policy.FocusError): scope['select']('a')
            self.assertEqual(calls, [])

    def test_uncertain_action_reply_revokes_cleanup_focus_permission(self):
        for action, arguments in [('select',('a',)), ('move',('a','test-b')), ('fresh',('b',))]:
            with self.subTest(action=action), offline():
                f, _, scope, _ = self.fixture()
                def fail(*args): raise RuntimeError('reply unavailable')
                scope['cli'] = fail
                with self.assertRaises(RuntimeError): scope[action](*arguments)
                self.assertIsNotNone(f.controller.revoked)
                with self.assertRaises(policy.FocusError): f.restore()

    def test_initial_activation_is_exactly_once_and_guarded_by_unchanged_source(self):
        with offline():
            f, gate, scope, calls = self.fixture()
            gate.workspace_selected('test-a', f.controller.initial); f.front = f.original
            def run(args, **kwargs):
                calls.append(('helper',args))
                if args[2] == 'activate': f.front = f.test
                return '{}'
            scope['N'].run = run
            scope['activate_initial']('a'); scope['activate_initial']('a')
            mutations = [args for kind,args in calls if kind == 'helper']
            self.assertEqual(len(mutations),1)
            self.assertEqual(mutations[0][1:5],[200,'activate','tab-a','term-a'])
            self.assertEqual(json.loads(mutations[0][-1])['pid'],100)
            self.assertIsNone(gate.carry)

    def test_initial_activation_refuses_user_focus_change_without_any_activation(self):
        with offline():
            f, gate, scope, calls = self.fixture()
            gate.workspace_selected('test-a', f.controller.initial); f.front = f.original
            scope['native'] = lambda: f.external_focus()
            with self.assertRaises(policy.FocusError): scope['activate_initial']('a')
            self.assertEqual(calls,[])

    def test_initial_activation_without_native_transition_is_not_retried(self):
        with offline():
            f, gate, scope, calls = self.fixture()
            gate.workspace_selected('test-a', f.controller.initial); f.front = f.original
            with self.assertRaises(policy.FocusError): scope['activate_initial']('a')
            self.assertEqual(len(calls),1)
            self.assertIsNotNone(f.controller.revoked)


class HelperAdmissionTests(unittest.TestCase):
    def test_old_or_unreadable_helper_cannot_reach_live_entrypoint(self):
        tree = ast.parse((SOURCE / 'live.py').read_text())
        admission = next(n for n in tree.body if isinstance(n,ast.If)
                         and '--capabilities' in ast.unparse(n.test))
        capture = next(n.lineno for n in tree.body if isinstance(n,ast.Assign)
                       and 'capture_session' in ast.unparse(n))
        self.assertLess(admission.lineno, capture)
        code = compile(ast.Module(body=[admission],type_ignores=[]),'<helper admission>','exec')
        for reply in ('old-helper', 'ghostty-topology-foreground-v1', 'ghostty-topology-foreground-v2', None):
            with self.subTest(reply=reply), offline():
                def run(args, **kwargs):
                    self.assertEqual(args,[Path('/fake/script-driver'),'--capabilities'])
                    if reply is None: raise RuntimeError('helper failed')
                    return reply
                scope = {'N':SimpleNamespace(run=run),'HERE':Path('/fake')}
                if reply == 'ghostty-topology-foreground-v2': exec(code,scope)
                else:
                    with self.assertRaises(RuntimeError): exec(code,scope)

    def test_helper_mutation_sites_require_grant_and_only_explicit_activation_activates_app(self):
        source = (SOURCE / 'ScriptDriver.m').read_text()
        main = source[source.index('int main('):]
        self.assertIn('Foreground grant required', main)
        self.assertLess(main.index('initWithSnapshot:'),main.index('SBApplication applicationWithProcessIdentifier:'))
        self.assertIn('[foreground check];\n        id tab =', main)
        self.assertIn('[foreground check];\n            [(GhosttyObject *)targetTab selectTab]', main)
        self.assertEqual(main.count('activateWithOptions:'),1)
        self.assertIn('[foreground check];\n            if (![native activateWithOptions:', main)
        self.assertNotIn('targetWindow activateWindow', main)


if __name__ == '__main__':
    unittest.main()
