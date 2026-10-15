"""Native Ghostty menus through retained, PID-bound accessibility elements.

Importing this module never loads native libraries or inspects applications.
The policy takes an injected backend so its race/error paths run wholly offline.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import sys
import time


class MenuError(RuntimeError):
    pass


@dataclass(frozen=True)
class Request:
    operation: str
    pid: int
    protected_pid: int
    prefix: str
    title: str

    def validate(self):
        if self.operation not in ('merge', 'detach'):
            raise MenuError('Unsupported native menu operation')
        if (type(self.pid) is not int or type(self.protected_pid) is not int
                or not 0 < self.pid <= 0x7fffffff or not 0 < self.protected_pid <= 0x7fffffff or self.pid == self.protected_pid):
            raise MenuError('Refusing invalid or protected process')
        if not isinstance(self.prefix,str) or not self.prefix.startswith('AeroSpace-NativeTabs-') or len(self.prefix) < 32:
            raise MenuError('Missing unique test prefix')
        if not isinstance(self.title,str) or not self.title.startswith(self.prefix):
            raise MenuError('Selected title is not a test title')


def canonical_membership(value, prefix):
    if not isinstance(value,dict) or not isinstance(value.get('groups'),list) or not value['groups']:
        raise MenuError('Missing exact native membership')
    groups, group_ids, tab_ids, terminals, titles = [], set(), set(), set(), set()
    for group in value['groups']:
        if (not isinstance(group,dict) or any(not isinstance(group.get(k),str) or not group[k] for k in ('window','selected'))
                or group['window'] in group_ids or not isinstance(group.get('members'),list) or not group['members']):
            raise MenuError('Malformed or ambiguous native group')
        group_ids.add(group['window']); members=[]
        for member in group['members']:
            if (not isinstance(member,dict) or any(not isinstance(member.get(k),str) or not member[k] for k in ('tab','terminal','title'))
                    or member['tab'] in tab_ids or member['terminal'] in terminals or member['title'] in titles
                    or not member['title'].startswith(prefix)):
                raise MenuError('Malformed, ambiguous or unowned native member')
            tab_ids.add(member['tab']);terminals.add(member['terminal']);titles.add(member['title'])
            members.append((member['tab'],member['terminal'],member['title']))
        if group['selected'] not in {member[0] for member in members}:
            raise MenuError('Native selection is not a recorded member')
        groups.append((group['window'],group['selected'],tuple(sorted(members))))
    return tuple(sorted(groups))


def run_menu(request, backend, *, expected, read_membership):
    """All mutations use retained elements; no process names/ordinals are used."""
    request.validate()
    expected=canonical_membership(expected,request.prefix)
    if not callable(read_membership): raise MenuError('Fresh exact membership reader required')
    selected_titles=[next(m[2] for m in members if m[0]==selected) for _,selected,members in expected]
    recorded_titles={member[2] for _,_,members in expected for member in members}
    if request.title not in selected_titles: raise MenuError('Requested selection is not recorded')
    app = backend.application(request.pid)
    roots=None

    def checked_membership():
        if canonical_membership(read_membership(),request.prefix)!=expected:
            raise MenuError('Native membership changed; refusing menu actions')

    def owned(element):
        if backend.pid(element) != request.pid:
            raise MenuError('Accessibility element belongs to another process')
        return element

    def checked_windows():
        nonlocal roots
        checked_membership()
        owned(app)
        windows = backend.elements(app, 'AXWindows')
        if not windows:
            raise MenuError('No test windows')
        actual_titles=[]
        for window in windows:
            title = backend.title(owned(window))
            if title not in recorded_titles:
                raise MenuError('Unowned window; refusing native menu actions')
            actual_titles.append(title)
        # macOS may expose either just selected group windows or additional
        # inactive tab roots. Every exposed root must name an exact recorded
        # native member, and every selected group must be represented.
        if len(set(actual_titles))!=len(actual_titles) or not set(selected_titles).issubset(actual_titles):
            raise MenuError('Native and AX membership disagree')
        if any(backend.same(w,other) for index,w in enumerate(windows) for other in windows[:index]):
            raise MenuError('Duplicate accessibility windows')
        if roots is not None and (len(windows)!=len(roots) or any(not any(backend.same(w,r) for r in roots) for w in windows)):
            raise MenuError('Retained native window roots changed')
        focused = owned(backend.element(app, 'AXFocusedWindow'))
        if (not any(backend.same(focused, w) for w in windows)
                or backend.title(focused) != request.title):
            raise MenuError('Selected test window changed')
        checked_membership()
        if roots is None: roots=tuple(windows)

    def named_child(parent, role, title):
        matches = []
        for child in backend.elements(owned(parent), 'AXChildren'):
            owned(child)
            if backend.role(child) == role and backend.title(child) == title:
                matches.append(child)
        if len(matches) != 1:
            raise MenuError('Native menu item is missing or ambiguous: ' + title)
        return matches[0]

    def require_foreground():
        # Parent selection already made this process foreground. Never activate
        # it here: a user may have switched away during subprocess setup/AX reads.
        if not backend.flag(owned(app), 'AXFrontmost'):
            raise MenuError('Disposable Ghostty lost foreground ownership')
        owned(app)

    checked_windows()
    require_foreground()
    checked_windows()
    menu_bar = owned(backend.element(app, 'AXMenuBar'))
    window_menu = named_child(menu_bar, 'AXMenuBarItem', 'Window')
    checked_windows()
    opened = False
    menu = None
    try:
        # Mark intent first: AXPress may succeed even if the reply fails.
        target = owned(window_menu)
        require_foreground()
        opened = True
        backend.press(target)
        menus = [owned(c) for c in backend.elements(window_menu, 'AXChildren')
                 if backend.role(owned(c)) == 'AXMenu']
        if len(menus) != 1:
            raise MenuError('Native Window menu is unavailable or ambiguous')
        menu = menus[0]
        title = ('Merge All Windows' if request.operation == 'merge'
                 else 'Move Tab to New Window')
        action = named_child(menu, 'AXMenuItem', title)
        if not backend.flag(owned(action), 'AXEnabled'):
            raise MenuError('Native tab operation is disabled')
        checked_windows()
        target = owned(action)
        require_foreground()
        backend.press(target)
    finally:
        # Cancel only this process's retained menu, never send Escape globally.
        if opened:
            try:
                backend.cancel(owned(menu if menu is not None else window_menu))
            except MenuError:
                pass
    return {'operation': request.operation, 'pid': request.pid, 'target': 'retained-AX-element'}


class NativeAX:
    """Thin public AX/CoreFoundation adapter. No application launch APIs."""
    UTF8 = 0x08000100
    ABSENT = (-25205, -25212)  # unsupported attribute / no value

    def __init__(self, *, frontmost_pid=None):
        self.frontmost_pid = frontmost_pid
        self.ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.refs = []
        pointer = ctypes.c_void_p
        index = ctypes.c_long
        self._signature(self.ax, 'AXUIElementCreateApplication', [ctypes.c_int], pointer)
        self._signature(self.ax, 'AXUIElementCreateSystemWide', [], pointer)
        self._signature(self.ax, 'AXUIElementGetPid', [pointer, ctypes.POINTER(ctypes.c_int)], ctypes.c_int)
        self._signature(self.ax, 'AXUIElementGetTypeID', [], ctypes.c_ulong)
        self._signature(self.ax, 'AXUIElementCopyAttributeValue', [pointer, pointer, ctypes.POINTER(pointer)], ctypes.c_int)
        self._signature(self.ax, 'AXUIElementSetAttributeValue', [pointer, pointer, pointer], ctypes.c_int)
        self._signature(self.ax, 'AXUIElementPerformAction', [pointer, pointer], ctypes.c_int)
        self._signature(self.ax, 'AXUIElementSetMessagingTimeout', [pointer, ctypes.c_float], ctypes.c_int)
        self._signature(self.cf, 'CFRelease', [pointer], None)
        self._signature(self.cf, 'CFRetain', [pointer], pointer)
        self._signature(self.cf, 'CFEqual', [pointer, pointer], ctypes.c_ubyte)
        self._signature(self.cf, 'CFGetTypeID', [pointer], ctypes.c_ulong)
        self._signature(self.cf, 'CFStringCreateWithCString', [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer)
        self._signature(self.cf, 'CFStringGetLength', [pointer], index)
        self._signature(self.cf, 'CFStringGetMaximumSizeForEncoding', [index, ctypes.c_uint32], index)
        self._signature(self.cf, 'CFStringGetCString', [pointer, pointer, index, ctypes.c_uint32], ctypes.c_ubyte)
        self._signature(self.cf, 'CFArrayGetCount', [pointer], index)
        self._signature(self.cf, 'CFArrayGetValueAtIndex', [pointer, index], pointer)
        self._signature(self.cf, 'CFBooleanGetValue', [pointer], ctypes.c_ubyte)
        for name in ('CFStringGetTypeID', 'CFArrayGetTypeID', 'CFBooleanGetTypeID'):
            self._signature(self.cf, name, [], ctypes.c_ulong)
        self.true = pointer.in_dll(self.cf, 'kCFBooleanTrue').value

    @staticmethod
    def _signature(library, name, args, result):
        function = getattr(library, name)
        function.argtypes, function.restype = args, result

    def _own(self, reference):
        if not reference:
            raise MenuError('Native accessibility returned a null reference')
        self.refs.append(reference)
        return reference

    def close(self):
        while self.refs:
            self.cf.CFRelease(self.refs.pop())

    def _string(self, text):
        return self._own(self.cf.CFStringCreateWithCString(None, text.encode('utf-8'), self.UTF8))

    @staticmethod
    def _check(error):
        if error:
            raise MenuError('Native accessibility request failed: ' + str(error))

    def _type(self, reference, type_id):
        if not reference or self.cf.CFGetTypeID(reference) != type_id:
            raise MenuError('Unexpected native accessibility attribute type')
        return reference

    def _attribute(self, element, attribute, *, optional=False, allow_no_value=False):
        result = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValue(element, self._string(attribute), ctypes.byref(result))
        if allow_no_value and error == -25212:
            return None
        if optional and error in self.ABSENT:
            return None
        self._check(error)
        return self._own(result.value)

    def application(self, pid):
        app = self._own(self.ax.AXUIElementCreateApplication(pid))
        self._check(self.ax.AXUIElementSetMessagingTimeout(app, 2.0))
        return app

    def focused_application(self):
        # AppKit's actual foreground process is independent of the manager model.
        # Some macOS versions fail the system-wide AX attribute even though
        # direct application AX reads work. Do not infer focus from model state.
        if getattr(self, 'frontmost_pid', None) is not None:
            pid = self.frontmost_pid()
            if type(pid) is not int or pid <= 0:
                raise MenuError('AppKit returned an invalid foreground PID')
            app = self.application(pid)
            if self.pid(app) != pid or self.frontmost_pid() != pid:
                raise MenuError('Native foreground application changed during capture')
            return app
        system = self._own(self.ax.AXUIElementCreateSystemWide())
        self._check(self.ax.AXUIElementSetMessagingTimeout(system, 2.0))
        return self.element(system, 'AXFocusedApplication')

    def pid(self, element):
        result = ctypes.c_int()
        self._check(self.ax.AXUIElementGetPid(element, ctypes.byref(result)))
        return result.value

    def element(self, parent, attribute):
        return self._type(self._attribute(parent, attribute), self.ax.AXUIElementGetTypeID())

    def optional_element(self, parent, attribute):
        # Only an explicit AX no-value means no focused window. Unsupported or
        # unreadable attributes remain errors, never a fabricated empty desktop.
        value = self._attribute(parent, attribute, allow_no_value=True)
        return None if value is None else self._type(value, self.ax.AXUIElementGetTypeID())

    def elements(self, parent, attribute):
        array = self._type(self._attribute(parent, attribute), self.cf.CFArrayGetTypeID())
        result = []
        for i in range(self.cf.CFArrayGetCount(array)):
            # Values in CFArray are borrowed; retain each element independently.
            item = self._own(self.cf.CFRetain(self.cf.CFArrayGetValueAtIndex(array, i)))
            result.append(self._type(item, self.ax.AXUIElementGetTypeID()))
        return result

    def _text(self, element, attribute, *, optional=False):
        value = self._attribute(element, attribute, optional=optional)
        if value is None:
            return None
        self._type(value, self.cf.CFStringGetTypeID())
        length = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(value), self.UTF8) + 1
        buffer = ctypes.create_string_buffer(length)
        if not self.cf.CFStringGetCString(value, buffer, length, self.UTF8):
            raise MenuError('Cannot decode native accessibility string')
        return buffer.value.decode('utf-8')

    def title(self, element):
        return self._text(element, 'AXTitle', optional=True)

    def role(self, element):
        return self._text(element, 'AXRole')

    def flag(self, element, attribute):
        value = self._type(self._attribute(element, attribute), self.cf.CFBooleanGetTypeID())
        return bool(self.cf.CFBooleanGetValue(value))

    def same(self, left, right):
        return bool(self.cf.CFEqual(left, right))

    def set_frontmost(self, app):
        self._check(self.ax.AXUIElementSetAttributeValue(app, self._string('AXFrontmost'), self.true))

    def press(self, element):
        self._check(self.ax.AXUIElementPerformAction(element, self._string('AXPress')))

    def cancel(self, element):
        self._check(self.ax.AXUIElementPerformAction(element, self._string('AXCancel')))

    @staticmethod
    def pause():
        time.sleep(.05)


def main(argv=None, *, backend_factory=NativeAX):
    # Exact membership must come from the runner's recorded identities and its
    # pinned in-process native reader. The old prefix-only CLI cannot supply it.
    parser=argparse.ArgumentParser(description='Retired prefix-only menu entrypoint; use the guarded live harness.')
    parser.add_argument('operation',choices=('merge','detach'))
    parser.add_argument('pid',type=int)
    parser.add_argument('protected_pid',type=int)
    parser.add_argument('prefix')
    parser.add_argument('title')
    parser.parse_args(argv)
    print('Standalone native-menu entrypoint retired; use the guarded live harness.',file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
