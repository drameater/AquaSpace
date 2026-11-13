#!/usr/bin/env python3
"""Replay hidden-window discovery/handoff with memory-only WindowServer and AX APIs.

Compiles the actual window-level cache, window classifier and session command
planner in an isolated dependency-free test package. No AppKit, CoreGraphics,
AX, process inventory, application entrypoint or diagnostic helper is linked.
"""
import offline_harness as harness

PACKAGE = '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "HiddenHandoffReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "HiddenHandoffReplay"),
    .testTarget(name: "HiddenHandoffReplayTests", dependencies: ["HiddenHandoffReplay"]),
])
'''


def prepare(args):
    package = harness.package_for(args, 'hidden-handoff-offline')
    source = 'Sources/HiddenHandoffReplay/'
    # The level cache coalesces concurrent cold reads with the production latch.
    # Dropping CoreGraphics makes the local WindowServer fakes the only ones in scope.
    for relative in ('AppBundle/windowLevelCache.swift', 'AppBundle/model/KnownBundleId.swift',
                     'AppBundle/model/AxUiElementWindowType.swift', 'AppBundle/util/AxUiElementMock.swift',
                     'AppBundle/util/AwaitableOneTimeBroadcastLatch.swift', 'AppBundle/util/UniqueToken.swift',
                     'AppBundle/model/PendingWindowRestoration.swift',
                     'Common/util/Nullable.swift', 'Common/util/TaskEx.swift'):
        text = harness.retarget_imports(harness.SwiftSource('Sources/' + relative).text,
                                        drop=('CoreGraphics', 'AppKit', 'Common'))
        if relative.endswith('/windowLevelCache.swift'):
            # Private cache reset is exposed only inside this isolated copy.
            text += '\n@MainActor func resetReplayCache() { cache = [:] }\n'
        package.write(source + relative.split('/')[-1], text)
    handoff = harness.SwiftSource('Sources/AppBundle/WindowManagerHandoff.swift')
    package.write(source + 'WindowManagerSession.swift', handoff.decl('struct WindowManagerSession:'))
    for name in ('ReplayAX.swift', 'ReplayRuntime.swift'):
        package.copy(source + name, harness.SHARED_SWIFT / name)
    support = harness.ROOT / 'script/hidden-handoff-offline'
    package.copy(source + 'ReplaySupport.swift', support / 'ReplaySupport.swift')
    package.copy('Tests/HiddenHandoffReplayTests/HiddenWindowHandoffTest.swift', support / 'HiddenWindowHandoffTest.swift')
    package.write('Package.swift', PACKAGE)
    return package, {}


if __name__ == '__main__':
    raise SystemExit(harness.main(prepare, __doc__))
