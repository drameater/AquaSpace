"""Destination guards run entirely against the actual runner and fake boundaries."""
import ast
import unittest

import test_aquaspace_ghostty_execution_focus as execution
from test_aquaspace_ghostty_focus_restore import SOURCE, offline, policy


class DestinationSafetyTests(unittest.TestCase):
    def fixture(self, foreign_workspace='test-b'):
        f, gate, scope, calls = execution.RunnerFocusTests().fixture()
        mutations = []
        owned = ['2000','test-a','200',f.test_window.title]
        foreign = ['personal-window',foreign_workspace,'300','personal-work']
        rows = [owned, foreign] if foreign_workspace else [owned]
        scope['native'] = lambda: {'groups':[{'selected':'tab-a','members':[
            {'tab':'tab-a','terminal':'term-a','title':f.test_window.title}]}]}
        scope['baseline'] = {}
        def cli(*args):
            if args[0] == 'list-windows':
                workspace = args[args.index('--workspace')+1]
                return '\n'.join('\t'.join(row) for row in rows if row[1] == workspace)
            if args == ('list-workspaces','--focused'): return f.model['workspace']
            mutations.append(args)
            if args[0] == 'workspace':
                f.model.update(workspace=args[1],window='',pid=None,title=None)
                if any(row[1] == args[1] and row[2] != '200' for row in rows):
                    f.external_focus(); f.model['workspace'] = args[1]
            return ''
        scope['cli'] = cli
        # Include the production guard when present, without importing live.py.
        tree = ast.parse((SOURCE/'live.py').read_text())
        nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == 'destinations_guard']
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE/'live.py'),'exec'),scope)
        return f, gate, scope, calls, mutations, rows

    def test_foreign_destination_refuses_before_workspace_switch(self):
        with offline():
            f, _, scope, _, mutations, _ = self.fixture()
            with self.assertRaises((policy.FocusError,RuntimeError)):
                scope['focus_test_workspace']('test-b')
            self.assertEqual(mutations,[])
            self.assertIs(f.front,f.test)

    def test_foreign_destination_refuses_before_focus_following_move(self):
        with offline():
            f, _, scope, _, mutations, _ = self.fixture()
            with self.assertRaises((policy.FocusError,RuntimeError)):
                scope['move']('a','test-b')
            self.assertEqual(mutations,[])

    def test_new_foreign_window_after_switch_refuses_before_creation(self):
        with offline():
            f, _, scope, calls, mutations, rows = self.fixture(None)
            scope['focus_test_workspace']('test-a')
            mutations.clear(); calls.clear()
            rows.append(['new-personal','test-a','300','new-user-work'])
            with self.assertRaises((policy.FocusError,RuntimeError)):
                scope['driver']('create','AeroSpace-NativeTabs-offline-unique-b')
            self.assertEqual(mutations,[])
            self.assertEqual(calls,[])

    def test_empty_or_exact_owned_destinations_remain_allowed(self):
        with offline():
            _, _, scope, _, mutations, _ = self.fixture(None)
            scope['focus_test_workspace']('test-b')
            self.assertEqual(mutations,[('workspace','test-b')])

    def test_malformed_destination_inventory_stops_before_mutation(self):
        with offline():
            _, _, scope, _, mutations, rows = self.fixture(None)
            rows.append(['bad','test-b','300'])
            with self.assertRaises((policy.FocusError,RuntimeError)):
                scope['focus_test_workspace']('test-b')
            self.assertEqual(mutations,[])

    def test_same_pid_or_prefix_does_not_authorize_an_unrecorded_window(self):
        for title in ('personal-work','AeroSpace-NativeTabs-offline-unique-unrecorded'):
            with self.subTest(title=title),offline():
                _, _, scope, _, mutations, rows = self.fixture(None)
                rows.append(['unrecorded','test-b','200',title])
                with self.assertRaises((policy.FocusError,RuntimeError)):
                    scope['focus_test_workspace']('test-b')
                self.assertEqual(mutations,[])

    def test_original_id_or_missing_owned_row_cannot_look_like_empty_destination(self):
        for changed in ('baseline-id','missing-row'):
            with self.subTest(changed=changed),offline():
                _, _, scope, _, mutations, rows = self.fixture(None)
                if changed == 'baseline-id': scope['baseline'] = {'2000':['2000','personal','floating','false']}
                else: rows.clear()
                with self.assertRaises((policy.FocusError,RuntimeError)):
                    scope['focus_test_workspace']('test-b')
                self.assertEqual(mutations,[])

    def test_membership_errors_or_duplicate_native_titles_refuse_before_switch(self):
        for changed in ('unrecorded-native','duplicate-native-title'):
            with self.subTest(changed=changed),offline():
                f, _, scope, _, mutations, _ = self.fixture(None)
                title = f.test_window.title if changed == 'duplicate-native-title' else 'AeroSpace-NativeTabs-offline-unique-other'
                scope['native'] = lambda: {'groups':[
                    {'selected':'tab-a','members':[{'tab':'tab-a','terminal':'term-a','title':f.test_window.title}]},
                    {'selected':'foreign','members':[{'tab':'foreign','terminal':'foreign-terminal','title':title}]}]}
                with self.assertRaises((policy.FocusError,RuntimeError)):
                    scope['focus_test_workspace']('test-b')
                self.assertEqual(mutations,[])


if __name__ == '__main__':
    unittest.main()
