"""Offline live-path regressions. No native libraries, apps, CLI, or UI calls."""
import ast
import contextlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'script/native-tabs-smoke/ghostty-topology'


@contextlib.contextmanager
def offline():
    with contextlib.ExitStack() as guards:
        for api in ('ctypes.CDLL', 'subprocess.Popen', 'subprocess.run', 'os.kill'):
            guards.enter_context(patch(api, side_effect=AssertionError('Forbidden live API: ' + api)))
        yield


class WorkspaceCreationTests(unittest.TestCase):
    def fixture(self, *, refuse_switch=False, ignore_switch=False):
        state = {'workspace': 'personal', 'creates': [], 'switches': []}

        def cli(*args):
            if args[0] == 'workspace':
                state['switches'].append(args[1])
                if refuse_switch:
                    raise RuntimeError('workspace switch failed')
                if not ignore_switch:
                    state['workspace'] = args[1]
                return ''
            if args == ('list-workspaces', '--focused'):
                return state['workspace']
            raise AssertionError('Unexpected CLI request: ' + str(args))

        def driver(operation, *args):
            state['creates'].append((operation, state['workspace']))
            return {'tab': 'new-tab', 'terminal': 'new-terminal'}

        # Exercise the runner's actual creation function without importing its
        # live entrypoint. Only these function definitions are evaluated.
        tree = ast.parse((SOURCE / 'live.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('fresh', 'focus_test_workspace')]
        scope = dict(A='test-a', B='test-b', cli=cli, driver=driver,
                     isolated=lambda: None, preserved=lambda: None,
                     focus_guard=lambda phase: None,
                     destinations_guard=lambda: None,
                     activity=SimpleNamespace(workspace_selected=lambda *args: None),
                     focus_controller=SimpleNamespace(revoke=lambda *args: None),
                     save=lambda *args: None, prefix='test-',
                     created={'a': {'tab': 'anchor', 'terminal': 'anchor-terminal'}})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / 'live.py'), 'exec'), scope)
        return scope['fresh'], state

    def test_first_window_created_on_test_workspace(self):
        with offline():
            fresh, state = self.fixture()
            fresh('a')
            self.assertEqual(state['creates'], [('create', 'test-a')])

    def test_destination_window_switches_before_creation(self):
        with offline():
            fresh, state = self.fixture()
            fresh('c', workspace='test-a')
            self.assertEqual(state['creates'], [('create', 'test-a')])

    def test_tab_creation_checks_its_test_workspace_too(self):
        with offline():
            fresh, state = self.fixture()
            fresh('b', 'a', workspace='test-b')
            self.assertEqual(state['creates'], [('add', 'test-b')])

    def test_switch_failure_creates_nothing(self):
        with offline():
            fresh, state = self.fixture(refuse_switch=True)
            with self.assertRaises(RuntimeError):
                fresh('a')
            self.assertEqual(state['creates'], [])

    def test_success_response_without_actual_focus_creates_nothing(self):
        with offline():
            fresh, state = self.fixture(ignore_switch=True)
            with self.assertRaises(RuntimeError):
                fresh('a')
            self.assertEqual(state['creates'], [])

    def test_personal_workspace_rejected_before_switch_or_create(self):
        with offline():
            fresh, state = self.fixture()
            with self.assertRaises(RuntimeError):
                fresh('c', workspace='personal')
            self.assertEqual(state['creates'], [])
            self.assertEqual(state['switches'], [])


class NativeMenuWiringTests(unittest.TestCase):
    def test_runner_uses_stable_native_target_instead_of_system_events(self):
        runner = (SOURCE / 'live.py').read_text()
        self.assertNotIn("['osascript'", runner)
        self.assertIn('run_menu(', runner)
        self.assertNotIn("SOURCE/'native_menu.py'", runner)
        self.assertFalse((SOURCE / 'native-menu.applescript').exists())

# Importing the new menu policy is safe: native libraries are opened only by its
# explicit adapter constructor, never while importing or creating fake elements.
import importlib.util
import io
import copy
from types import SimpleNamespace

with offline():
    spec = importlib.util.spec_from_file_location('ghostty_native_menu_safety', SOURCE / 'native_menu.py')
    menu_policy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = menu_policy
    spec.loader.exec_module(menu_policy)


class Element:
    def __init__(self, pid, title='', role='', **attributes):
        self.pid, self.title, self.role = pid, title, role
        self.attributes = attributes
        self.alive = True


class FakeAX:
    """Process order can change at any callback, but element identity cannot."""
    def __init__(self):
        self.prefix = 'AeroSpace-NativeTabs-offline-unique-'
        self.target = 200
        self.personal = 100
        self.actions = []
        self.hook = lambda stage, element: None
        self.window = Element(self.target, self.prefix + 'a', 'AXWindow')
        self.merge = Element(self.target, 'Merge All Windows', 'AXMenuItem', AXEnabled=True)
        self.detach = Element(self.target, 'Move Tab to New Window', 'AXMenuItem', AXEnabled=True)
        # An untitled separator is legal, but remains bound to the target PID.
        self.separator = Element(self.target, None, 'AXMenuItem')
        self.menu = Element(self.target, role='AXMenu', AXChildren=[self.separator, self.merge, self.detach])
        self.window_menu = Element(self.target, 'Window', 'AXMenuBarItem', AXChildren=[self.menu])
        self.bar = Element(self.target, role='AXMenuBar', AXChildren=[self.window_menu])
        self.app = Element(self.target, role='AXApplication', AXWindows=[self.window],
                           AXFocusedWindow=self.window, AXMenuBar=self.bar, AXFrontmost=True)
        self.personal_app = Element(self.personal, role='AXApplication')
        self.process_order = [Element(50), self.app, self.personal_app]
        self.bindings = []
        self.native={'groups':[{'window':'group-a','selected':'tab-a','members':[{'tab':'tab-a','terminal':'terminal-a','title':self.prefix+'a'}]}]}
        self.expected=copy.deepcopy(self.native)

    def read_membership(self):
        self.hook('membership',self.app)
        return self.native

    def request(self, operation='merge'):
        return menu_policy.Request(operation, self.target, self.personal, self.prefix, self.prefix + 'a')

    def application(self, pid):
        self.bindings.append(pid)
        return next(e for e in self.process_order if e.pid == pid)

    def pid(self, element):
        self.hook('pid', element)
        if not element.alive:
            raise menu_policy.MenuError('stale target element')
        return element.pid

    def element(self, element, attribute):
        self.pid(element)
        self.hook(attribute, element)
        return element.attributes[attribute]

    def elements(self, element, attribute):
        return self.element(element, attribute)

    def title(self, element):
        self.pid(element)
        self.hook('title', element)
        return element.title

    def role(self, element):
        self.pid(element)
        return element.role

    def flag(self, element, attribute):
        self.pid(element)
        self.hook(attribute, element)
        return element.attributes[attribute]

    def same(self, left, right):
        return left is right

    def set_frontmost(self, element):
        self.hook('activate', element)  # churn after the policy's final PID check
        self.pid(element)
        self.actions.append(('activate', element))
        element.attributes['AXFrontmost'] = True

    def press(self, element):
        self.hook('press', element)  # churn after the policy's final PID check
        self.pid(element)
        self.actions.append(('press', element))

    def cancel(self, element):
        self.pid(element)
        self.actions.append(('cancel', element))

    def pause(self):
        pass


class NativeMenuPolicyTests(unittest.TestCase):
    def run_policy(self, backend, operation='merge'):
        with offline():
            return menu_policy.run_menu(backend.request(operation),backend,expected=backend.expected,read_membership=backend.read_membership)

    def test_native_merge_and_detach_press_the_requested_retained_item(self):
        for operation in ('merge', 'detach'):
            with self.subTest(operation=operation):
                b = FakeAX()
                self.run_policy(b, operation)
                action = b.merge if operation == 'merge' else b.detach
                self.assertEqual(b.bindings, [b.target])
                self.assertEqual(b.actions, [('press', b.window_menu),
                                             ('press', action), ('cancel', b.menu)])

    def test_exact_recorded_inactive_ax_roots_are_supported_without_admitting_extra_members(self):
        for expose_inactive in (False,True):
            with self.subTest(expose_inactive=expose_inactive):
                b=FakeAX()
                b.native['groups'][0]['members'].append({'tab':'tab-b','terminal':'terminal-b','title':b.prefix+'b'})
                b.expected=copy.deepcopy(b.native)
                if expose_inactive:b.app.attributes['AXWindows'].append(Element(b.target,b.prefix+'b','AXWindow'))
                self.run_policy(b)
                self.assertIn(('press',b.merge),b.actions)

    def test_duplicate_title_ax_window_never_becomes_an_extra_owned_root(self):
        b=FakeAX()
        b.app.attributes['AXWindows'].append(Element(b.target,b.window.title,'AXWindow'))
        with self.assertRaises(menu_policy.MenuError):self.run_policy(b)
        self.assertEqual(b.actions,[])

    def test_background_target_is_never_activated_to_reclaim_user_focus(self):
        b = FakeAX(); b.app.attributes['AXFrontmost'] = False
        with self.assertRaises(menu_policy.MenuError): self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_user_switch_during_window_validation_refuses_first_menu_press(self):
        b = FakeAX()
        b.hook = lambda stage, element: b.app.attributes.__setitem__('AXFrontmost', False) if stage == 'title' else None
        with self.assertRaises(menu_policy.MenuError): self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_user_switch_after_menu_open_refuses_action_and_only_cancels_owned_menu(self):
        b = FakeAX()
        def switch(stage, element):
            if stage == 'press' and element is b.window_menu:
                b.app.attributes['AXFrontmost'] = False
        b.hook = switch
        with self.assertRaises(menu_policy.MenuError): self.run_policy(b)
        self.assertEqual(b.actions, [('press', b.window_menu), ('cancel', b.menu)])

    def test_process_churn_at_menu_clicks_never_retargets_personal_app(self):
        b = FakeAX()
        stages = []

        def churn(stage, element):
            if stage in ('activate', 'press'):
                # Removing a preceding process places the PERSONAL app at the old
                # target's numeric index. It must not change the mutation target.
                b.process_order = [b.app, b.personal_app]
                stages.append(stage)
        b.hook = churn
        self.run_policy(b)
        self.assertEqual(stages, ['press', 'press'])
        self.assertTrue(all(e.pid == b.target for _, e in b.actions))
        self.assertNotIn(b.personal_app, [e for _, e in b.actions])

    def test_target_exit_during_foreground_validation_does_not_activate_replacement(self):
        b = FakeAX()

        def exit_target(stage, element):
            if stage == 'AXFrontmost':
                b.app.alive = False
                b.process_order = [b.personal_app]
        b.hook = exit_target
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertEqual(b.actions, [])
        self.assertEqual(b.bindings, [b.target])

    def test_target_exit_immediately_before_menu_action_does_not_click_replacement(self):
        b = FakeAX()

        def exit_target(stage, element):
            if stage == 'press' and element is b.merge:
                b.merge.alive = False
                b.process_order = [b.personal_app]
        b.hook = exit_target
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertNotIn(('press', b.merge), b.actions)
        self.assertTrue(all(e.pid == b.target for _, e in b.actions))
        self.assertEqual(b.bindings, [b.target])

    def test_original_process_rejected_before_native_backend_is_constructed(self):
        with offline(), contextlib.redirect_stderr(io.StringIO()):
            factory = unittest.mock.Mock(side_effect=AssertionError('must not bind original PID'))
            code = menu_policy.main(['merge', '100', '100', 'AeroSpace-NativeTabs-unique-offline-',
                                     'AeroSpace-NativeTabs-unique-offline-a'], backend_factory=factory)
            self.assertEqual(code, 2)
            factory.assert_not_called()

    def test_unowned_window_refuses_before_any_menu_mutation(self):
        b = FakeAX()
        b.app.attributes['AXWindows'].append(Element(b.target, 'personal-work', 'AXWindow'))
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_unrecorded_prefixed_background_window_is_not_authorized(self):
        b = FakeAX()
        b.app.attributes['AXWindows'].append(Element(b.target,b.prefix+'initial','AXWindow'))
        with self.assertRaises(menu_policy.MenuError): self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_prefixed_window_arriving_after_menu_open_prevents_merge(self):
        b = FakeAX()
        def arrival(stage, element):
            if stage=='press' and element is b.window_menu:
                b.app.attributes['AXWindows'].append(Element(b.target,b.prefix+'initial','AXWindow'))
        b.hook=arrival
        with self.assertRaises(menu_policy.MenuError): self.run_policy(b)
        self.assertNotIn(('press',b.merge),b.actions)

    def test_hidden_unrecorded_tab_and_changed_terminal_refuse_before_menu_open(self):
        for problem in ('extra-tab','terminal','group','selection','unreadable','duplicate'):
            with self.subTest(problem=problem):
                b=FakeAX();group=b.native['groups'][0]
                if problem=='extra-tab': group['members'].append({'tab':'foreign','terminal':'foreign-uuid','title':b.prefix+'initial'})
                elif problem=='terminal':group['members'][0]['terminal']='replacement-uuid'
                elif problem=='group':group['window']='replacement-group'
                elif problem=='selection':group['selected']='absent'
                elif problem=='unreadable':b.native=None
                else:group['members'].append(copy.deepcopy(group['members'][0]))
                with self.assertRaises(menu_policy.MenuError):self.run_policy(b)
                self.assertEqual(b.actions,[])

    def test_membership_arrival_during_menu_open_refuses_action(self):
        b=FakeAX()
        def arrival(stage,element):
            if stage=='press' and element is b.window_menu:
                b.native['groups'][0]['members'].append({'tab':'foreign','terminal':'foreign-uuid','title':b.prefix+'initial'})
        b.hook=arrival
        with self.assertRaises(menu_policy.MenuError):self.run_policy(b)
        self.assertEqual(b.actions,[('press',b.window_menu),('cancel',b.menu)])

    def test_same_title_ax_root_replacement_is_not_admitted(self):
        b=FakeAX()
        def replace(stage,element):
            if stage=='press' and element is b.window_menu:
                other=Element(b.target,b.prefix+'a','AXWindow')
                b.app.attributes.update(AXWindows=[other],AXFocusedWindow=other)
        b.hook=replace
        with self.assertRaises(menu_policy.MenuError):self.run_policy(b)
        self.assertNotIn(('press',b.merge),b.actions)

    def test_membership_read_failure_cancels_only_retained_menu_without_retry(self):
        b=FakeAX();reads=[]
        def changed(stage,element):
            if stage=='press' and element is b.window_menu:
                def failed():reads.append(True);raise RuntimeError('membership unreadable')
                b.read_membership=failed
        # The callback itself stays retained by run_menu; mutate its read source.
        def reader():return b.read_membership()
        b.hook=changed
        with offline(),self.assertRaisesRegex(RuntimeError,'membership unreadable'):
            menu_policy.run_menu(b.request(),b,expected=b.expected,read_membership=reader)
        self.assertEqual(reads,[True])
        self.assertEqual(b.actions,[('press',b.window_menu),('cancel',b.menu)])

    def test_foreign_pid_element_refuses_before_any_menu_mutation(self):
        b = FakeAX()
        b.window.pid = b.personal
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_selected_title_change_refuses_before_menu_press(self):
        b = FakeAX()

        def change_selection(stage, element):
            if stage == 'AXFrontmost':
                b.window.title = b.prefix + 'another-tab'
        b.hook = change_selection
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertEqual(b.actions, [])

    def test_window_arriving_after_menu_open_refuses_merge(self):
        b = FakeAX()

        def new_window(stage, element):
            if stage == 'press' and element is b.window_menu:
                b.app.attributes['AXWindows'].append(Element(b.target, 'unowned', 'AXWindow'))
        b.hook = new_window
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertNotIn(('press', b.merge), b.actions)
        self.assertEqual(b.actions[-1], ('cancel', b.menu))

    def test_disabled_or_ambiguous_menu_action_is_not_pressed(self):
        for problem in ('disabled', 'ambiguous'):
            with self.subTest(problem=problem):
                b = FakeAX()
                if problem == 'disabled':
                    b.merge.attributes['AXEnabled'] = False
                else:
                    b.menu.attributes['AXChildren'].append(b.merge)
                with self.assertRaises(menu_policy.MenuError):
                    self.run_policy(b)
                self.assertNotIn(('press', b.merge), b.actions)

    def test_ambiguous_action_reply_is_not_retried(self):
        b = FakeAX()
        attempts = []

        def ambiguous(stage, element):
            if stage == 'press' and element is b.merge:
                attempts.append(element)
                raise menu_policy.MenuError('reply lost after possible action')
        b.hook = ambiguous
        with self.assertRaises(menu_policy.MenuError):
            self.run_policy(b)
        self.assertEqual(attempts, [b.merge])
        self.assertEqual(b.actions[-1], ('cancel', b.menu))


class NativeAXAdapterTests(unittest.TestCase):
    def test_native_boundary_uses_retained_refs_directly_despite_inventory_churn(self):
        # Exercise the REAL adapter's bind/set/press/release calls, replacing
        # only the C functions. No framework is loaded and no process is read.
        with offline():
            b = menu_policy.NativeAX.__new__(menu_policy.NativeAX)
            b.refs, b.true = [], 1
            calls = []
            inventory = [50, 200, 100]
            strings = {}

            def string(allocator, value, encoding):
                reference = 1000 + len(strings)
                strings[reference] = value.decode()
                return reference

            def press(reference, action):
                inventory.pop(0) if inventory and inventory[0] == 50 else None
                calls.append(('press', reference, strings[action]))
                return 0

            b.cf = SimpleNamespace(CFStringCreateWithCString=string,
                                   CFRelease=lambda ref: calls.append(('release', ref)))
            b.ax = SimpleNamespace(
                AXUIElementCreateApplication=lambda pid: calls.append(('bind', pid)) or 9000,
                AXUIElementSetMessagingTimeout=lambda ref, seconds: 0,
                AXUIElementSetAttributeValue=lambda ref, attr, value: calls.append(('activate', ref, strings[attr], value)) or 0,
                AXUIElementPerformAction=press)
            application = b.application(200)
            b.set_frontmost(application)
            b.press(9100)  # retained AX menu-item reference, never a process index
            b.cancel(9200)
            b.close()
            self.assertIn(('bind', 200), calls)
            self.assertIn(('activate', 9000, 'AXFrontmost', 1), calls)
            self.assertIn(('press', 9100, 'AXPress'), calls)
            self.assertIn(('press', 9200, 'AXCancel'), calls)
            self.assertEqual(inventory, [200, 100])
            self.assertEqual(b.refs, [])
            self.assertIn(('release', 9000), calls)

    def test_array_elements_are_retained_separately_from_mutable_array(self):
        with offline():
            b = menu_policy.NativeAX.__new__(menu_policy.NativeAX)
            b.refs = []
            retained, released = [], []
            b._attribute = lambda parent, attribute: 10
            b.cf = SimpleNamespace(
                CFGetTypeID=lambda ref: 1 if ref == 10 else 2,
                CFArrayGetTypeID=lambda: 1,
                CFArrayGetCount=lambda array: 2,
                CFArrayGetValueAtIndex=lambda array, i: (20, 30)[i],
                CFRetain=lambda ref: retained.append(ref) or ref,
                CFRelease=lambda ref: released.append(ref))
            b.ax = SimpleNamespace(AXUIElementGetTypeID=lambda: 2)
            self.assertEqual(b.elements(5, 'AXChildren'), [20, 30])
            self.assertEqual(retained, [20, 30])
            b.close()
            self.assertCountEqual(released, retained)

    def test_native_invalid_element_error_stops_without_retry_or_fallback(self):
        with offline():
            b = menu_policy.NativeAX.__new__(menu_policy.NativeAX)
            b.refs = []
            attempts = []
            b.cf = SimpleNamespace(CFStringCreateWithCString=lambda *args: 1000)
            b.ax = SimpleNamespace(AXUIElementPerformAction=lambda ref, action: attempts.append(ref) or -25202)
            with self.assertRaises(menu_policy.MenuError):
                b.press(9000)
            self.assertEqual(attempts, [9000])

    def test_missing_or_unreadable_window_inventory_is_an_error_not_empty(self):
        for error in (-25212, -25204):
            with self.subTest(error=error), offline():
                b = menu_policy.NativeAX.__new__(menu_policy.NativeAX)
                b.refs = []
                b.cf = SimpleNamespace(CFStringCreateWithCString=lambda *args: 1000)
                b.ax = SimpleNamespace(AXUIElementCopyAttributeValue=lambda *args: error)
                with self.assertRaises(menu_policy.MenuError):
                    b.elements(9000, 'AXWindows')


class NativeMenuCliTests(unittest.TestCase):
    def test_help_missing_and_invalid_arguments_do_not_construct_native_backend(self):
        cases = [(['--help'], 0), ([], 2),
                 (['merge','200','100','AeroSpace-NativeTabs-offline-unique-','AeroSpace-NativeTabs-offline-unique-a'],2),
                 (['merge', '-1', '100', 'AeroSpace-NativeTabs-offline-unique-',
                   'AeroSpace-NativeTabs-offline-unique-a'], 2)]
        for arguments, code in cases:
            with self.subTest(arguments=arguments), offline(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                factory = unittest.mock.Mock(side_effect=AssertionError('unexpected native construction'))
                if arguments and arguments[0] == 'merge':
                    self.assertEqual(menu_policy.main(arguments, backend_factory=factory), code)
                else:
                    with self.assertRaises(SystemExit) as exit:
                        menu_policy.main(arguments, backend_factory=factory)
                    self.assertEqual(exit.exception.code, code)
                factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
