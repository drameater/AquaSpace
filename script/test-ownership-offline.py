#!/usr/bin/env python3
"""Run isolated ownership tests. No app executable/SwiftUI, network or live APIs.

Only production admission/process-reader sources and their tests are compiled.
The native process reader is typechecked, but tests inject inventory instead of
calling it. The isolated Swift package has no external dependencies. Native libproc calls
are replaced with aborting test stubs; no test can inspect real processes.
"""
import os
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    package = root / '.debug/ownership-offline'
    package.mkdir(parents=True, exist_ok=True)
    source = package / 'Sources/Ownership'
    headers = package / 'Sources/PrivateApi/include'
    tests = package / 'Tests/OwnershipTests'
    for directory in [source, headers, tests]: directory.mkdir(parents=True, exist_ok=True)
    for path in ['model/WindowManagerAdmission.swift', 'util/windowManagerProcesses.swift', 'model/PendingWindowRestoration.swift',
                 'model/WindowManagerFailureReport.swift']:
        shutil.copy2(root / 'Sources/AppBundle' / path, source / Path(path).name)
    # Only process/signing declarations, no AppKit/AX or app entrypoint in this test.
    shutil.copy2(root / 'Sources/PrivateApi/include/process_inventory.h', headers / 'process_inventory.h')
    (headers / 'process.h').write_text('#include <libproc.h>\n#include <sys/proc_info.h>\n#include "process_inventory.h"\n')
    (headers.parent / 'process.c').write_text('''#include "process.h"
#include <stdlib.h>
// Fail immediately if a regression accidentally invokes native process APIs.
int proc_listpids(uint32_t kind, uint32_t uid, void *buf, int size) { abort(); }
int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buf, int size) { abort(); }
int proc_pidpath(int pid, void *buf, uint32_t size) { abort(); }
int csops_audittoken(pid_t pid, unsigned int op, void *buf, size_t size, void *token) { abort(); }
''')
    for name in ['WindowManagerAdmissionTest.swift', 'WindowManagerProcessScanTest.swift', 'WindowRestorationProcessTest.swift',
                 'WindowManagerFailureReportTest.swift']:
        path = root / 'Sources/AppBundleTests' / name
        (tests / path.name).write_text(path.read_text().replace('@testable import AppBundle', '@testable import Ownership'))
    (package / 'Package.swift').write_text('''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "OwnershipOffline", platforms: [.macOS(.v13)], targets: [
.target(name: "PrivateApi"),
.target(name: "Ownership", dependencies: ["PrivateApi"], swiftSettings: [.strictMemorySafety()]),
.testTarget(name: "OwnershipTests", dependencies: ["Ownership"]),
])
''')
    env = dict(os.environ)
    env['CLANG_MODULE_CACHE_PATH'] = str(package / 'clang-cache')
    plugins = Path('/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib')
    macro_flags = ['-Xswiftc', '-load-plugin-library', '-Xswiftc', str(plugins)] if plugins.exists() else []
    return subprocess.run(['swift', 'test', '--package-path', str(package), '--scratch-path',
                           str(package / 'build'), '--disable-sandbox', '--disable-automatic-resolution',
                           '--skip-update', '--jobs', '2', *macro_flags], env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
