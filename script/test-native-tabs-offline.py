#!/usr/bin/env python3
"""Replay native-tab reconciliation without apps, AX, WindowServer, or a network.

Copies the production state machine and matching functions into an isolated Swift
package. Only the three model-tree adapters are omitted; AX reads use memory-only
elements from script/offline-shared. No manager executable is built or launched.
"""
import offline_harness as harness

PACKAGE = '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "NativeTabReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "NativeTabReplay"),
    .testTarget(name: "NativeTabReplayTests", dependencies: ["NativeTabReplay"]),
])
'''
# These adapters bind windows in the model tree; the model runner covers them.
TREE_ADAPTERS = ('func transferNativeTabState(', 'func absorbNativeTabState(', 'func inheritNativeTabWorkspace(')
# The other tests in this file need the full model tree.
TREE_TESTS = ('func tabSelectionAfterWorkspaceMoveRetainsWorkspaceAndSlot(', 'func replacementPreservesWeightFullscreenAndCachedGeometry(',
              'func floatingTabKeepsItsWorkspaceAndSize(', 'func tabSwitchPreservesFocusBackAndForthHistory(')


def prepare(args):
    package = harness.package_for(args, 'native-tabs-offline')
    source, tests = 'Sources/NativeTabReplay/', 'Tests/NativeTabReplayTests/'
    for relative in ('model/NativeTabReplacement.swift', 'tree/nativeTabTopology.swift', 'tree/nativeTabs.swift',
                     'util/AxUiElementMock.swift'):
        production = harness.SwiftSource('Sources/AppBundle/' + relative)
        text = production.without(*TREE_ADAPTERS) if relative.endswith('/nativeTabs.swift') else production.text
        package.write(source + relative.split('/')[-1], harness.retarget_imports(
            text, drop=('AppKit', 'Common'), add=('Foundation', 'CoreFoundation')))
    for name in ('ReplayAX.swift', 'ReplayRuntime.swift', 'ReplayNativeTabEnvironment.swift'):
        package.copy(source + name, harness.SHARED_SWIFT / name)
    package.copy(tests + 'NativeTabReplayTest.swift', harness.ROOT / 'script/native-tabs-offline/NativeTabReplayTest.swift')
    # Keep the existing matcher and queue regressions, with memory-only AX identities.
    existing = harness.SwiftSource('Sources/AppBundleTests/tree/NativeTabReplacementTest.swift').without(*TREE_TESTS)
    package.write(tests + 'NativeTabReplacementTest.swift', harness.retarget_imports(
        existing.replace('@testable import AppBundle', '@testable import NativeTabReplay'),
        drop=('AppKit', 'Common'), add=('Foundation',)))
    package.write('Package.swift', PACKAGE)
    return package, {}


if __name__ == '__main__':
    raise SystemExit(harness.main(prepare, __doc__))
