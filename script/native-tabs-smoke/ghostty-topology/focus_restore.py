"""Desktop focus transaction for the Ghostty harness. Import is entirely inert."""
from __future__ import annotations

from dataclasses import dataclass
import time


class FocusError(RuntimeError):
    pass


def require_owned_destinations(inventories, owned_rows, native, test_pid, prefix, baseline_ids):
    """Reject foreign occupants using exact model rows AND native selected tabs."""
    selected = []
    for group in native['groups']:
        matches = [m for m in group['members'] if m['tab'] == group['selected']]
        if len(matches) != 1:
            raise FocusError('Native destination selection is ambiguous')
        selected.append(matches[0]['title'])
    if len(set(selected)) != len(selected):
        raise FocusError('Duplicate native destination titles')
    owned = {}
    for row in owned_rows:
        if (len(row) != 4 or not row[0].isdigit() or row[0] in owned or row[0] in baseline_ids
                or row[1] not in inventories or row[2] != str(test_pid)
                or row[3] not in selected or not row[3].startswith(prefix)):
            raise FocusError('Unverified disposable destination row')
        owned[row[0]] = row
    if sorted(row[3] for row in owned.values()) != sorted(selected):
        raise FocusError('Native and model destination membership disagree')
    for workspace, text in inventories.items():
        seen = set()
        for line in text.splitlines():
            row = line.split('\t', 3)
            if (len(row) != 4 or row[0] in seen or row[1] != workspace
                    or owned.get(row[0]) != row):
                raise FocusError('Foreign or unreadable destination occupant on ' + workspace)
            seen.add(row[0])
        if seen != {key for key, row in owned.items() if row[1] == workspace}:
            raise FocusError('Destination inventory changed during validation on ' + workspace)


def read_model(cli):
    workspace = cli('list-workspaces', '--focused')
    try:
        line = cli('list-windows', '--focused', '--format',
                   '%{window-id}\t%{workspace}\t%{app-pid}\t%{window-title}')
    except RuntimeError as error:
        if str(error) != 'No window is focused':
            raise
        return dict(workspace=workspace, window='', pid=None, title=None, unique=True)
    row = line.split('\t', 3)
    if (len(row) != 4 or not row[0].isdigit() or row[1] != workspace
            or not row[2].isdigit() or int(row[2]) <= 0):
        raise FocusError('Inconsistent manager focus snapshot')
    candidates = cli('list-windows', '--all', '--format',
                     '%{window-id}\t%{app-pid}\t%{window-title}')
    matches = []
    for candidate in candidates.splitlines():
        fields = candidate.split('\t', 2)
        if len(fields) != 3:
            raise FocusError('Invalid focus candidate metadata')
        if fields[1:] == row[2:]:
            matches.append(fields[0])
    return dict(workspace=workspace, window=row[0], pid=int(row[2]),
                title=row[3], unique=matches == [row[0]])


@dataclass
class Snapshot:
    model: dict
    app: object
    window: object
    pid: int
    title: str | None


class FocusRestoration:
    def __init__(self, *, ax, read_model, cli, guard, log,
                 clock=time.monotonic, pause=time.sleep, timeout=5.0, stable_for=.5):
        self.ax, self.read_model, self.cli = ax, read_model, cli
        self.guard, self.log, self.clock, self.pause = guard, log, clock, pause
        self.timeout, self.stable_for = timeout, stable_for
        self.initial = None
        self.status, self.error, self.verified = 'not-captured', None, False
        self.observations = []
        self.requests = []
        self.revoked = None

    def revoke(self, reason):
        # Once desktop ownership is lost, returning to a test window cannot
        # silently authorize more focus writes, including during cleanup.
        if self.revoked is None:
            self.revoked = str(reason)

    def confirm_native(self, snapshot):
        # Bracket the potentially blocking window/title reads with a final
        # foreground read. A background app can still report its focused window.
        title_matches = (snapshot.window is None
                         or self.ax.title(snapshot.window) == snapshot.title)
        window = self.ax.optional_element(snapshot.app, 'AXFocusedWindow')
        if snapshot.window is None:
            same_window = window is None
        else:
            same_window = (window is not None and self.ax.pid(window) == snapshot.pid
                           and self.ax.same(window, snapshot.window) and title_matches)
        app = self.ax.focused_application()
        if not same_window or self.ax.pid(app) != snapshot.pid or not self.ax.same(app, snapshot.app):
            raise FocusError('Native foreground changed during window validation')

    def _request(self, kind, **target):
        data = dict(kind=kind, at_monotonic=self.clock(), **target)
        self.requests.append(data)
        self.log('restore-request', dict(phase='restore-request', request=data))

    def _native_matches(self, current, initial):
        return (current.pid == initial.pid and self.ax.same(current.app, initial.app)
                and ((current.window is None and initial.window is None)
                     or (current.window is not None and initial.window is not None
                         and self.ax.same(current.window, initial.window))))

    def _matches(self, current, initial):
        return (current.model['workspace'] == initial.model['workspace']
                and current.model['window'] == initial.model['window']
                and self._native_matches(current, initial))

    def observe(self, phase):
        # Read-only: cleanup can still record focus after restoration is refused.
        model = self.read_model()
        app = self.ax.focused_application()
        pid = self.ax.pid(app)
        window = self.ax.optional_element(app, 'AXFocusedWindow')
        if pid <= 0 or (window is not None and self.ax.pid(window) != pid):
            raise FocusError('Native focus has an invalid process identity')
        current = Snapshot(model, app, window, pid,
                           self.ax.title(window) if window is not None else None)
        data = dict(phase=phase, at_monotonic=self.clock(), model=dict(model),
                    native_pid=pid, native_title=current.title,
                    native_has_window=window is not None,
                    matches_initial=self._matches(current, self.initial) if self.initial else None)
        self.observations.append(data)
        self.log(phase, data)
        self.confirm_native(current)
        return current

    def _settle(self, phase, predicate):
        deadline, since, consecutive = self.clock() + self.timeout, None, 0
        while self.clock() < deadline:
            self.guard()
            current = self.observe(phase)
            if predicate(current):
                since = self.clock() if since is None else since
                consecutive += 1
                if consecutive >= 3 and self.clock() - since >= self.stable_for:
                    return current
            else:
                since, consecutive = None, 0
            self.pause(.1)
        raise FocusError('Desktop focus did not settle during ' + phase)

    def capture_initial(self, session):
        candidate = None

        def coherent(current):
            nonlocal candidate
            m = current.model
            valid = m['workspace'] == session['workspace'] and m['window'] == session['focusedWindow']
            if m['window']:
                valid = (valid and current.window is not None and m['unique']
                         and m['pid'] == current.pid and m['title'] == current.title)
            else:
                valid = valid and current.window is None
            if not valid:
                candidate = None
                return False
            if candidate is None or not self._matches(current, candidate):
                had_candidate = candidate is not None
                candidate = current
                if had_candidate:
                    return False
            return self._matches(current, candidate)

        try:
            if self.revoked is not None:
                raise FocusError('Execution foreground ownership revoked: ' + self.revoked)
            self.guard()
            if not session['focusedWindow'] and self.cli(
                    'list-windows', '--workspace', session['workspace'], '--format', '%{window-id}'):
                raise FocusError('Windowless focus is not a confirmed empty workspace')
            self.initial = self._settle('baseline', coherent)
            self.status = 'captured'
        except Exception as error:
            self.status, self.error = 'capture-failed', str(error)
            raise

    def restore_before_cleanup(self, test_pid, owned_windows, test_workspaces, prefix):
        self.verified = False
        try:
            if self.revoked is not None:
                raise FocusError('Execution foreground ownership revoked: ' + self.revoked)
            self.guard()
            if self.initial is None:
                raise FocusError('No verified original desktop focus')
            current = self.observe('before-restore')
            def is_test(value):
                return (test_pid is not None and value.model['workspace'] in test_workspaces
                        and value.model['window'] in owned_windows
                        and value.model['pid'] == test_pid and value.pid == test_pid
                        and value.window is not None and value.title is not None
                        and value.title.startswith(prefix))

            def may_write():
                # Target validation can take time. Recheck foreground ownership
                # afterwards, immediately before requesting original focus.
                self.guard()
                latest = self.observe('before-focus-write')
                if self._matches(latest, self.initial):
                    return False
                if not is_test(latest):
                    raise FocusError('Focus changed during target validation; refusing restoration')
                return True

            if not self._matches(current, self.initial):
                # A workspace name alone is not evidence that we still own focus.
                if not is_test(current):
                    raise FocusError('Focus changed outside the test; refusing to override it')
                self.guard()
                if self.ax.pid(self.initial.app) != self.initial.pid:
                    raise FocusError('Original application identity changed')
                if self.initial.window is not None:
                    # Force a real read of the retained window before addressing
                    # its model ID; stale roots cannot authorize restoration.
                    self.ax.title(self.initial.window)
                    # This command also selects the target workspace. One focus
                    # write avoids an intermediate MRU window/workspace hop.
                    self._request('focus-window', window=self.initial.model['window'],
                                  workspace=self.initial.model['workspace'], native_pid=self.initial.pid)
                    if may_write():
                        self.cli('focus', '--window-id', self.initial.model['window'])
                else:
                    if self.ax.optional_element(self.initial.app, 'AXFocusedWindow') is not None:
                        raise FocusError('Originally empty application gained a window; refusing activation')
                    if self.cli('list-windows', '--workspace', self.initial.model['workspace'],
                                '--format', '%{window-id}'):
                        raise FocusError('Originally empty workspace gained a window; refusing restoration')
                    self._request('focus-empty-workspace', workspace=self.initial.model['workspace'])
                    if may_write():
                        self.cli('workspace', self.initial.model['workspace'])
                        transition = self.observe('empty-workspace-selected')
                        native_is_test = (transition.pid == test_pid and transition.title is not None
                                          and transition.title.startswith(prefix))
                        if not (native_is_test or self._native_matches(transition, self.initial)):
                            raise FocusError('Native focus changed while restoring empty workspace')
                        self.guard()
                        if self.ax.optional_element(self.initial.app, 'AXFocusedWindow') is not None:
                            raise FocusError('Original application gained a window during restoration')
                        if self.cli('list-windows', '--workspace', self.initial.model['workspace'],
                                    '--format', '%{window-id}'):
                            raise FocusError('Empty workspace gained a window during restoration')
                        self._request('activate-empty-application', native_pid=self.initial.pid)
                        self.guard()
                        latest = self.observe('before-empty-activation')
                        if (latest.model['workspace'] != self.initial.model['workspace']
                                or latest.model['window']
                                or not self._native_matches(latest, transition)):
                            raise FocusError('Foreground changed during empty-workspace validation')
                        self.ax.set_frontmost(self.initial.app)
            self._settle('restore-before-cleanup', lambda value: self._matches(value, self.initial))
            self.status = 'restored-before-cleanup'
        except Exception as error:
            self.status, self.error = 'restore-failed', str(error)
            raise

    def verify_after_cleanup(self):
        try:
            if self.status != 'restored-before-cleanup':
                raise FocusError('Desktop focus was not restored before cleanup')
            # Verification only: late app-close/quit handoffs or user interaction
            # must not trigger another focus write or be mislabeled as success.
            self._settle('after-cleanup', lambda value: self._matches(value, self.initial))
            self.verified, self.status = True, 'verified-after-cleanup'
            return True
        except Exception as error:
            self.verified, self.status, self.error = False, 'verification-failed', str(error)
            raise

    def report(self):
        return dict(status=self.status, verified=self.verified, error=self.error,
                    revoked=self.revoked, observations=list(self.observations), requests=list(self.requests))


class ExecutionFocus:
    """Read-only, fail-closed permission for the runner's foreground actions."""
    def __init__(self, controller, *, test_pid, owned_rows, workspaces, prefix):
        self.controller, self.test_pid, self.owned_rows = controller, test_pid, owned_rows
        self.workspaces, self.prefix = set(workspaces), prefix
        self.initial_allowed = True
        self.carry, self.carry_workspace = None, None

    def workspace_selected(self, workspace, source):
        if workspace not in self.workspaces:
            raise FocusError('Foreground transition requires a test workspace')
        # A deliberate switch to an empty workspace can leave the preceding
        # native application foreground until the first disposable window exists.
        self.initial_allowed = False
        self.carry, self.carry_workspace = source, workspace

    def check(self, phase):
        c = self.controller
        try:
            if c.revoked is not None:
                raise FocusError('Execution foreground ownership revoked: ' + c.revoked)
            c.guard()
            rows = self.owned_rows()
            current = c.observe(phase)
            if c.initial is None:
                raise FocusError('No captured desktop baseline')
            m, pid = current.model, self.test_pid()
            coherent_initial = (not m['window'] or (m['unique'] and m['pid'] == current.pid
                                                   and m['title'] == current.title))
            if self.initial_allowed and coherent_initial and c._matches(current, c.initial):
                return current
            matches = [r for r in rows if r[0] == m['window']]
            owned_model = (len(matches) == 1 and m['unique'] and m['pid'] == pid
                           and matches[0][1:] == [m['workspace'], str(pid), m['title']]
                           and m['title'] is not None and m['title'].startswith(self.prefix))
            owned_native = (owned_model and current.pid == pid and current.window is not None
                            and current.title == m['title'])
            if m['workspace'] in self.workspaces and owned_native:
                self.initial_allowed = False
                self.carry, self.carry_workspace = None, None
                return current
            # This exception belongs only to the explicitly requested workspace
            # transition, never a personal model window or a different native app.
            if (self.carry is not None and m['workspace'] == self.carry_workspace
                    and (not m['window'] or owned_model)):
                unchanged = c._native_matches(current, self.carry)
                window_hidden = (not m['window'] and current.window is None
                                 and current.pid == self.carry.pid
                                 and c.ax.same(current.app, self.carry.app))
                if unchanged or window_hidden:
                    return current
            raise FocusError('Foreground left the authorized test: ' + phase)
        except Exception as error:
            c.revoke(error)
            raise
