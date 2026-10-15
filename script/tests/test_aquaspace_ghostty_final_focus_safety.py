"""Offline regressions for exact restoration and final native-menu focus grants."""
import ast
import copy
import unittest

from test_aquaspace_ghostty_focus_restore import FocusEnvironment, Ref, SOURCE, offline, policy
from test_aquaspace_ghostty_topology_safety import FakeAX, Element, menu_policy


class RestorationOwnershipTests(unittest.TestCase):
    def fixture(self):
        f = FocusEnvironment()
        f.capture()
        f.test_focus()
        f.model['window'] = '2000'
        title = f.test_window.title
        state = dict(
            native={'groups': [{'window': 'group-a', 'selected': 'tab-a', 'members': [
                {'tab': 'tab-a', 'terminal': 'term-a', 'title': title}]}]},
            rows=[['2000', 'test-a', '200', title]], read_error=None)

        def driver(*args):
            self.assertEqual(args, ('status',))
            if state['read_error'] is not None:
                raise state['read_error']
            return copy.deepcopy(state['native'])

        def cli(*args):
            if args[0] != 'list-windows':
                raise AssertionError('Unexpected live-path query: ' + str(args))
            rows = state['rows']
            if '--workspace' in args:
                workspace = args[args.index('--workspace') + 1]
                rows = [r for r in rows if r[1] == workspace]
            return '\n'.join('\t'.join(r) for r in rows)

        scope = dict(driver=driver, cli=cli, created={'a': {'tab': 'tab-a', 'terminal': 'term-a', 'title': title}},
                     focus_controller=f.controller, focus_restore=policy,
                     canonical_membership=menu_policy.canonical_membership,
                     isolated=f.guard, pid=200, prefix='AeroSpace-NativeTabs-offline-unique-',
                     baseline={'11344': ['11344', '1', 'h_tiles', 'false']}, A='test-a', B='test-b')
        tree = ast.parse((SOURCE / 'live.py').read_text())
        helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name in ('native', 'own_rows', 'require_restoration_source')]
        exec(compile(ast.Module(body=helpers, type_ignores=[]), str(SOURCE / 'live.py'), 'exec'), scope)
        # Evaluate the actual teardown call, so a prefix-only grant at the call
        # site cannot hide behind a correctly implemented standalone validator.
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'restore_before_cleanup')
        restore = lambda: eval(compile(ast.Expression(call), str(SOURCE / 'live.py'), 'eval'), scope)
        return f, scope, state, restore

    def unrecorded_focus(self, f, scope, state):
        title = scope['prefix'] + 'initial'
        state['native']['groups'].append({'window': 'unknown-group', 'selected': 'unknown-tab', 'members': [
            {'tab': 'unknown-tab', 'terminal': 'unknown-terminal', 'title': title}]})
        state['rows'].append(['2001', 'test-a', '200', title])
        f.windows[200] = Ref(200, title)
        f.model.update(window='2001', title=title)

    def test_rejected_native_ownership_cannot_authorize_prefix_only_restoration(self):
        with offline():
            f, scope, state, restore = self.fixture()
            self.unrecorded_focus(f, scope, state)
            with self.assertRaises(RuntimeError):
                scope['native']()
            with self.assertRaises(policy.FocusError):
                restore()
            self.assertIsNotNone(f.controller.revoked)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.activations, [])
            self.assertEqual(f.model['window'], '2001')

    def test_fresh_restoration_grant_rejects_unrecorded_foreground_without_prior_poll(self):
        with offline():
            f, scope, state, restore = self.fixture()
            self.unrecorded_focus(f, scope, state)
            with self.assertRaises((policy.FocusError, RuntimeError)):
                restore()
            self.assertEqual(f.commands, [])
            self.assertEqual(f.activations, [])
            self.assertIsNotNone(f.controller.revoked)

    def test_native_read_or_schema_failure_revokes_even_after_return_to_recorded_focus(self):
        for problem in ('read', 'nil', 'malformed', 'duplicate', 'title', 'terminal'):
            with self.subTest(problem=problem), offline():
                f, scope, state, restore = self.fixture()
                original = copy.deepcopy(state['native'])
                if problem == 'read':
                    state['read_error'] = RuntimeError('Unreadable native membership')
                elif problem == 'nil':
                    state['native'] = None
                elif problem == 'malformed':
                    state['native'] = {'groups': 'unreadable'}
                elif problem == 'duplicate':
                    state['native']['groups'].append(copy.deepcopy(state['native']['groups'][0]))
                elif problem == 'title':
                    state['native']['groups'][0]['members'][0]['title'] = scope['prefix'] + 'other'
                else:
                    state['native']['groups'][0]['members'][0]['terminal'] = 'replacement'
                with self.assertRaises(RuntimeError):
                    scope['native']()
                state.update(native=original, read_error=None)
                with self.assertRaises(policy.FocusError):
                    restore()
                self.assertEqual(f.commands, [])
                self.assertIsNotNone(f.controller.revoked)

    def test_late_unrecorded_arrival_during_target_validation_prevents_focus_write(self):
        with offline():
            f, scope, state, restore = self.fixture()
            def arrival(phase, data):
                if phase == 'restore-request':
                    self.unrecorded_focus(f, scope, state)
            f.controller.log = arrival
            with self.assertRaises((policy.FocusError, RuntimeError)):
                restore()
            self.assertEqual(f.commands, [])
            self.assertEqual(f.activations, [])
            self.assertIsNotNone(f.controller.revoked)

    def test_valid_exact_source_still_restores_once_and_verifies(self):
        with offline():
            f, _, _, restore = self.fixture()
            restore()
            self.assertEqual(f.commands, [('focus', '--window-id', '11344')])
            self.assertIsNone(f.controller.revoked)
            self.assertTrue(f.controller.verify_after_cleanup())

    def test_prefix_or_truthy_grant_cannot_replace_fresh_exact_validator(self):
        for validator in ({'test-window'}, None, lambda source: {'owned': True}, lambda source: False):
            with self.subTest(validator=validator), offline():
                f = FocusEnvironment()
                f.capture()
                f.test_focus()
                with self.assertRaises(policy.FocusError):
                    f.controller.restore_before_cleanup(200, validator, {'test-a', 'test-b'},
                                                        'AeroSpace-NativeTabs-offline-unique-')
                self.assertEqual(f.commands, [])
                self.assertEqual(f.activations, [])
                self.assertIsNotNone(f.controller.revoked)

    def test_restoration_rejects_incoherent_or_ambiguous_model_source(self):
        for problem in ('ambiguous', 'model-title', 'duplicate-row', 'missing-row', 'baseline-id', 'manager'):
            with self.subTest(problem=problem), offline():
                f, scope, state, restore = self.fixture()
                if problem == 'ambiguous':
                    f.model['unique'] = False
                elif problem == 'model-title':
                    f.model['title'] = scope['prefix'] + 'another'
                elif problem == 'duplicate-row':
                    state['rows'].append(list(state['rows'][0]))
                elif problem == 'missing-row':
                    state['rows'] = []
                elif problem == 'baseline-id':
                    scope['baseline']['2000'] = ['2000', 'personal', 'floating', 'false']
                else:
                    f.guard_failure = 'Manager identity/count changed'
                with self.assertRaises((policy.FocusError, RuntimeError)):
                    restore()
                self.assertEqual(f.commands, [])
                self.assertEqual(f.activations, [])
                self.assertIsNotNone(f.controller.revoked)

    def test_source_changes_during_fresh_membership_validation_cannot_inherit_grant(self):
        for problem in ('external', 'same-title-root'):
            with self.subTest(problem=problem), offline():
                f, scope, _, restore = self.fixture()
                original_cli = scope['cli']
                def changed(*args):
                    result = original_cli(*args)
                    if problem == 'external':
                        f.external_focus()
                    else:
                        f.windows[200] = Ref(200, f.test_window.title)
                    return result
                scope['cli'] = changed
                with self.assertRaises(policy.FocusError):
                    restore()
                self.assertEqual(f.commands, [])
                self.assertEqual(f.activations, [])
                self.assertIsNotNone(f.controller.revoked)

    def test_later_owned_group_or_same_title_root_cannot_inherit_restoration_source(self):
        for problem in ('other-group', 'same-title-root'):
            with self.subTest(problem=problem), offline():
                f, scope, state, restore = self.fixture()
                other = Ref(200, scope['prefix'] + 'b' if problem == 'other-group' else f.test_window.title)
                if problem == 'other-group':
                    scope['created']['b'] = {'tab': 'tab-b', 'terminal': 'term-b', 'title': other.title}
                    state['native']['groups'].append({'window': 'group-b', 'selected': 'tab-b', 'members': [
                        {'tab': 'tab-b', 'terminal': 'term-b', 'title': other.title}]})
                    state['rows'].append(['2001', 'test-a', '200', other.title])
                def changed(phase, data):
                    if phase == 'restore-request':
                        f.windows[200] = other
                        f.model.update(window='2001' if problem == 'other-group' else '2000', title=other.title)
                f.controller.log = changed
                with self.assertRaises(policy.FocusError):
                    restore()
                self.assertEqual(f.commands, [])
                self.assertEqual(f.activations, [])
                self.assertIsNotNone(f.controller.revoked)

    def test_empty_workspace_transition_retains_only_the_exact_granted_native_source(self):
        for problem in ('unknown-prefix', 'same-title-root', 'changed-title'):
            with self.subTest(problem=problem), offline():
                f = FocusEnvironment(empty=True)
                f.capture()
                f.test_focus()
                original_cli = f.cli
                def changed(*args):
                    result = original_cli(*args)
                    if args == ('workspace', '1'):
                        if problem == 'changed-title':
                            f.test_window.title = 'AeroSpace-NativeTabs-offline-unique-initial'
                        else:
                            f.windows[200] = Ref(200, f.recorded_title if problem == 'same-title-root'
                                                 else 'AeroSpace-NativeTabs-offline-unique-initial')
                    return result
                f.controller.cli = changed
                with self.assertRaises(policy.FocusError):
                    f.restore()
                self.assertEqual(f.commands, [('workspace', '1')])
                self.assertEqual(f.activations, [])
                self.assertIsNotNone(f.controller.revoked)


class FinalMenuFocusTests(unittest.TestCase):
    def fixture(self, problem):
        b = FakeAX()
        f = FocusEnvironment()
        f.capture()
        f.test, f.test_window, f.front = b.app, b.window, b.app
        f.windows[200] = b.window
        f.model = dict(workspace='test-a', window='2000', pid=200, title=b.window.title, unique=True)
        rows = [['2000', 'test-a', '200', b.window.title]]
        other = Element(200, b.prefix + 'b', 'AXWindow')
        if problem == 'other-group':
            b.app.attributes['AXWindows'].append(other)
            b.native['groups'].append({'window': 'group-b', 'selected': 'tab-b', 'members': [
                {'tab': 'tab-b', 'terminal': 'terminal-b', 'title': other.title}]})
            rows.append(['2001', 'test-a', '200', other.title])
        else:
            other.title = b.window.title
        b.expected = copy.deepcopy(b.native)
        gate = policy.ExecutionFocus(f.controller, test_pid=lambda: 200, owned_rows=lambda: rows,
                                     workspaces={'test-a', 'test-b'}, prefix=b.prefix)
        state = dict(discovered=False, reads=0)
        def changed(stage, element):
            if stage == 'AXChildren' and element is b.menu:
                state['discovered'] = True
            if stage == 'membership' and state['discovered']:
                state['reads'] += 1
                if state['reads'] == 2:
                    if problem != 'other-group':
                        b.app.attributes['AXWindows'] = [other]
                    b.app.attributes['AXFocusedWindow'] = other
                    f.windows[200] = other
                    f.model.update(window='2001' if problem == 'other-group' else '2000', title=other.title)
        b.hook = changed
        def read():
            current = b.read_membership()
            gate.check('native-menu-membership')  # The real runner's callback gate.
            return current
        return b, f, read

    def test_final_membership_callback_cannot_replace_requested_window(self):
        for problem in ('other-group', 'same-title-root'):
            for operation in ('merge', 'detach'):
                with self.subTest(problem=problem, operation=operation), offline():
                    b, f, read = self.fixture(problem)
                    with self.assertRaises(menu_policy.MenuError):
                        menu_policy.run_menu(b.request(operation), b, expected=b.expected, read_membership=read)
                    self.assertEqual(b.actions, [])
                    self.assertEqual(f.commands, [])
                    self.assertEqual(f.activations, [])
                    self.assertEqual(b.bindings, [b.target])

    def test_runner_latches_final_menu_selection_failure_and_closes_local_backend(self):
        for problem in ('other-group', 'same-title-root'):
            with self.subTest(problem=problem), offline():
                b, f, _ = self.fixture(problem)
                closed = []
                b.close = lambda: closed.append(True)
                rows = [['2000', 'test-a', '200', b.window.title]]
                if problem == 'other-group':
                    rows.append(['2001', 'test-a', '200', b.prefix + 'b'])
                gate = policy.ExecutionFocus(f.controller, test_pid=lambda: 200, owned_rows=lambda: rows,
                                             workspaces={'test-a', 'test-b'}, prefix=b.prefix)
                node = next(n for n in ast.parse((SOURCE / 'live.py').read_text()).body
                            if isinstance(n, ast.FunctionDef) and n.name == 'menu')
                scope = dict(select=lambda label: None, native=b.read_membership,
                             created={'a': {'tab': 'tab-a', 'terminal': 'terminal-a'}},
                             pid=200, protected=[100], prefix=b.prefix, isolated=f.guard,
                             destinations_guard=f.guard, focus_guard=gate.check, focus_controller=f.controller,
                             NativeAX=lambda: b, MenuRequest=menu_policy.Request, run_menu=menu_policy.run_menu,
                             event=lambda *args, **kwargs: None, preserved=f.guard)
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE / 'live.py'), 'exec'), scope)
                with self.assertRaises(menu_policy.MenuError):
                    scope['menu']('merge', 'a')
                self.assertEqual(closed, [True])
                self.assertEqual(b.actions, [])
                self.assertIsNotNone(f.controller.revoked)
                with self.assertRaises(policy.FocusError):
                    f.restore()
                self.assertEqual(f.commands, [])


if __name__ == '__main__':
    unittest.main()
