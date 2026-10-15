#!/usr/bin/env python3
"""Test recovered workspace bindings against the real model, without app launch.

Builds the AppBundle library and two Swift Testing tests using TestWindow/TestApp.
The test marker selects simulated monitors/config; no AX reads or app entrypoint
are invoked. Resolution/update are disabled: dependencies must already be cached.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--scratch-path', type=Path)
    args = parser.parse_args()
    package = root / '.debug/native-tab-model-offline'
    tests = package / 'Tests/NativeTabModelTests'
    tests.mkdir(parents=True, exist_ok=True)
    for name in ['TestApp.swift', 'TestWindow.swift', 'NativeTabWorkspaceRecoveryTest.swift']:
        shutil.copy2(root / 'Sources/AppBundleTests/tree' / name, tests / name)
    shutil.copy2(root / 'script/native-tabs-smoke/Sources/NativeTabSmoke/Support.swift', tests / 'Support.swift')
    shutil.copy2(root / 'Package.resolved', package / 'Package.resolved')
    (package / 'Package.swift').write_text('''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "NativeTabModelOffline", platforms: [.macOS(.v13)],
    dependencies: [.package(path: ROOT_PATH)], targets: [
        .testTarget(name: "NativeTabModelTests", dependencies: [.product(name: "AppBundle", package: "aerospace")]),
    ])
'''.replace('ROOT_PATH', json.dumps(str(root))))
    scratch = args.scratch_path or package / 'build'
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['CLANG_MODULE_CACHE_PATH'] = str(scratch / 'clang-cache')
    env['SWIFTPM_MODULECACHE_OVERRIDE'] = str(scratch / 'swift-cache')
    plugin = Path('/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib')
    flags = ['-Xswiftc', '-load-plugin-library', '-Xswiftc', str(plugin)] if plugin.exists() else []
    if args.sdk: flags += ['--sdk', str(args.sdk)]
    return subprocess.run(['swift', 'test', '--package-path', str(package), '--scratch-path', str(scratch),
                           '--disable-sandbox', '--disable-automatic-resolution', '--skip-update',
                           '--jobs', '2', *flags], env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
