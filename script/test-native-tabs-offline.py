#!/usr/bin/env python3
"""Replay native-tab reconciliation without apps, AX, WindowServer, or a network.

Copies the production state machine and matching functions into an isolated Swift
package. Only the two model-tree adapters are omitted; AX reads use memory-only
elements supplied by the test support. No manager executable is built or launched.
"""
import os
from pathlib import Path
import subprocess


def write_if_changed(path, text):
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def main():
    root = Path(__file__).resolve().parents[1]
    package = root / '.debug/native-tabs-offline'
    source = package / 'Sources/NativeTabReplay'
    tests = package / 'Tests/NativeTabReplayTests'
    source.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    production = root / 'Sources/AppBundle'
    for relative in ['model/NativeTabReplacement.swift', 'tree/nativeTabTopology.swift', 'tree/nativeTabs.swift']:
        text = (production / relative).read_text()
        if relative.endswith('/nativeTabs.swift'):
            start = '/// Read the binding at the instant of replacement:'
            end = '/// AX objects stay on their application'
            if text.count(start) != 1 or text.count(end) != 1:
                raise RuntimeError('Tree-adapter boundaries changed; review the offline replay extraction.')
            text = text[:text.index(start)] + text[text.index(end):]
        text = text.replace('import AppKit\n', 'import Foundation\nimport CoreFoundation\n')
        write_if_changed(source / Path(relative).name, text)
    support = root / 'script/native-tabs-offline'
    write_if_changed(source / 'ReplayAX.swift', (support / 'ReplayAX.swift').read_text())
    write_if_changed(tests / 'NativeTabReplayTest.swift', (support / 'NativeTabReplayTest.swift').read_text())
    # Preserve the existing matcher/queue regressions too, with memory-only AX
    # identities. The remaining four tests require the full model-tree runtime.
    existing = (root / 'Sources/AppBundleTests/tree/NativeTabReplacementTest.swift').read_text()
    boundary = '    @Test func tabSelectionAfterWorkspaceMoveRetainsWorkspaceAndSlot()'
    if existing.count(boundary) != 1:
        raise RuntimeError('Existing pure/tree test boundary changed; review extraction.')
    existing = existing[:existing.index(boundary)] + '}\n'
    existing = existing.replace('@testable import AppBundle', '@testable import NativeTabReplay')
    existing = existing.replace('import AppKit\n', 'import Foundation\n').replace('import Common\n', '')
    write_if_changed(tests / 'NativeTabReplacementTest.swift', existing)
    write_if_changed(package / 'Package.swift', '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "NativeTabReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "NativeTabReplay"),
    .testTarget(name: "NativeTabReplayTests", dependencies: ["NativeTabReplay"]),
])
''')
    env = dict(os.environ)
    env['CLANG_MODULE_CACHE_PATH'] = str(package / 'clang-cache')
    plugin = Path('/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib')
    macro_flags = ['-Xswiftc', '-load-plugin-library', '-Xswiftc', str(plugin)] if plugin.exists() else []
    return subprocess.run(['swift', 'test', '--package-path', str(package), '--scratch-path',
                           str(package / 'build'), '--disable-sandbox', '--disable-automatic-resolution',
                           '--skip-update', '--jobs', '2', *macro_flags], env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
