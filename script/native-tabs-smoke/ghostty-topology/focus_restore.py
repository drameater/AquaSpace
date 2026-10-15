"""Desktop focus transaction for the Ghostty harness. Import is entirely inert."""
from __future__ import annotations

from dataclasses import dataclass
import time


class FocusError(RuntimeError):
    pass


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
                    if may_write():
                        self._request('focus-window', window=self.initial.model['window'],
                                      workspace=self.initial.model['workspace'], native_pid=self.initial.pid)
                        self.cli('focus', '--window-id', self.initial.model['window'])
                else:
                    if self.ax.optional_element(self.initial.app, 'AXFocusedWindow') is not None:
                        raise FocusError('Originally empty application gained a window; refusing activation')
                    if self.cli('list-windows', '--workspace', self.initial.model['workspace'],
                                '--format', '%{window-id}'):
                        raise FocusError('Originally empty workspace gained a window; refusing restoration')
                    if may_write():
                        self._request('focus-empty-workspace', workspace=self.initial.model['workspace'])
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
                    observations=list(self.observations), requests=list(self.requests))
