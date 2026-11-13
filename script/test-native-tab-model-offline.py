#!/usr/bin/env python3
"""Test recovered workspace bindings against the real model, without app launch.

Builds the AppBundle library and two Swift Testing tests using TestWindow/TestApp.
The test marker selects simulated monitors/config; no AX reads or app entrypoint
are invoked. Resolution/update are disabled: dependencies must already be cached.
On Command Line Tools 26.x, pass an SDK that has the SwiftUI macro plugin, for
example --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk.
"""
import json
from pathlib import Path

import offline_harness as harness

PACKAGE = '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "NativeTabModelOffline", platforms: [.macOS(.v13)],
    dependencies: [.package(path: ROOT_PATH)], targets: [
        .testTarget(name: "NativeTabModelTests", dependencies: [.product(name: "AppBundle", package: IDENTITY)]),
    ])
'''


def configure(parser):
    parser.add_argument('--scratch-path', type=Path, help='build directory (default: <package>/build)')


def prepare(args):
    package = harness.package_for(args, 'native-tab-model-offline')
    tests = 'Tests/NativeTabModelTests/'
    for name in ('TestApp.swift', 'TestWindow.swift', 'NativeTabWorkspaceRecoveryTest.swift'):
        package.copy(tests + name, harness.ROOT / 'Sources/AppBundleTests/tree' / name)
    package.copy(tests + 'Support.swift', harness.ROOT / 'script/native-tabs-smoke/Sources/NativeTabSmoke/Support.swift')
    package.copy('Package.resolved', harness.ROOT / 'Package.resolved')
    # SwiftPM names a path dependency after its directory, so the checkout name decides it.
    package.write('Package.swift', PACKAGE.replace('ROOT_PATH', json.dumps(str(harness.ROOT))).replace(
        'IDENTITY', json.dumps(harness.path_package_identity(harness.ROOT))))
    # --skip-update keeps SwiftPM off the network for the remote dependencies.
    return package, dict(build_path=args.scratch_path, extra=('--skip-update',))


if __name__ == '__main__':
    raise SystemExit(harness.main(prepare, __doc__, configure))
