#!/usr/bin/env python3
"""Replay hidden-window discovery/handoff with memory-only WindowServer and AX APIs.

Compiles the actual window-level cache, window classifier and session command
planner in an isolated dependency-free test package. No AppKit, CoreGraphics,
AX, process inventory, application entrypoint or diagnostic helper is linked.
"""
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    package = root / '.debug/hidden-handoff-offline'
    source = package / 'Sources/HiddenHandoffReplay'
    tests = package / 'Tests/HiddenHandoffReplayTests'
    for directory in (source, tests):
        directory.mkdir(parents=True, exist_ok=True)
    production = root / 'Sources/AppBundle'
    for relative in ('windowLevelCache.swift', 'model/KnownBundleId.swift',
                     'model/AxUiElementWindowType.swift', 'util/AxUiElementMock.swift'):
        text = (production / relative).read_text()
        text = text.replace('import CoreGraphics\n', '').replace('import AppKit\n', '').replace('import Common\n', '')
        if relative == 'windowLevelCache.swift':
            # Private cache reset is exposed only inside this isolated copy.
            text += '\n@MainActor func resetReplayCache() { cache = [:] }\n'
        (source / Path(relative).name).write_text(text)
    handoff = (production / 'WindowManagerHandoff.swift').read_text()
    start, end = 'struct WindowManagerSession:', '\n@MainActor\nenum WindowManagerHandoff'
    if handoff.count(start) != 1 or handoff.count(end) != 1:
        raise RuntimeError('Session extraction boundaries changed; review the replay.')
    (source / 'WindowManagerSession.swift').write_text(handoff[handoff.index(start):handoff.index(end)])
    support = root / 'script/hidden-handoff-offline'
    (source / 'ReplaySupport.swift').write_text((support / 'ReplaySupport.swift').read_text())
    (tests / 'HiddenWindowHandoffTest.swift').write_text((support / 'HiddenWindowHandoffTest.swift').read_text())
    (package / 'Package.swift').write_text('''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "HiddenHandoffReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "HiddenHandoffReplay"),
    .testTarget(name: "HiddenHandoffReplayTests", dependencies: ["HiddenHandoffReplay"]),
])
''')
    env = dict(os.environ)
    env['CLANG_MODULE_CACHE_PATH'] = str(package / 'clang-cache')
    plugin = Path('/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib')
    flags = ['-Xswiftc', '-load-plugin-library', '-Xswiftc', str(plugin)] if plugin.exists() else []
    return subprocess.run(['swift', 'test', '--package-path', str(package), '--scratch-path',
                           str(package / 'build'), '--disable-sandbox', '--disable-automatic-resolution',
                           '--skip-update', '--jobs', '2', *flags], env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
