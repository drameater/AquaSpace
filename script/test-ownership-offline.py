#!/usr/bin/env python3
"""Run isolated ownership tests. No app executable/SwiftUI, network or live APIs.

Only production admission/process-reader sources and their tests are compiled.
The native process reader is typechecked, but tests inject inventory instead of
calling it. The isolated Swift package has no external dependencies. Native libproc calls
are replaced with aborting test stubs; no test can inspect real processes.
"""
import offline_harness as harness

PACKAGE = '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "OwnershipOffline", platforms: [.macOS(.v13)], targets: [
.target(name: "PrivateApi"),
.target(name: "Ownership", dependencies: ["PrivateApi"], swiftSettings: [.strictMemorySafety()]),
.testTarget(name: "OwnershipTests", dependencies: ["Ownership"]),
])
'''
PROCESS_STUBS = '''#include "process.h"
#include <stdlib.h>
// Fail immediately if a regression accidentally invokes native process APIs.
int proc_listpids(uint32_t kind, uint32_t uid, void *buf, int size) { abort(); }
int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buf, int size) { abort(); }
int proc_pidpath(int pid, void *buf, uint32_t size) { abort(); }
'''


def prepare(args):
    package = harness.package_for(args, 'ownership-offline')
    for path in ('model/WindowManagerAdmission.swift', 'util/windowManagerProcesses.swift',
                 'model/PendingWindowRestoration.swift', 'model/WindowManagerFailureReport.swift',
                 'model/WindowManagerWatchHealth.swift'):
        package.copy('Sources/Ownership/' + path.split('/')[-1], harness.ROOT / 'Sources/AppBundle' / path)
    # Only process declarations, no AppKit/AX or app entrypoint in this test.
    package.copy('Sources/PrivateApi/include/process_inventory.h', harness.ROOT / 'Sources/PrivateApi/include/process_inventory.h')
    package.write('Sources/PrivateApi/include/process.h',
                  '#include <libproc.h>\n#include <sys/proc_info.h>\n#include "process_inventory.h"\n')
    package.write('Sources/PrivateApi/process.c', PROCESS_STUBS)
    for name in ('WindowManagerAdmissionTest.swift', 'WindowManagerProcessScanTest.swift', 'WindowRestorationProcessTest.swift',
                 'WindowManagerFailureReportTest.swift', 'WindowManagerWatchHealthTest.swift'):
        text = (harness.ROOT / 'Sources/AppBundleTests' / name).read_text()
        package.write('Tests/OwnershipTests/' + name, text.replace('@testable import AppBundle', '@testable import Ownership'))
    package.write('Package.swift', PACKAGE)
    return package, {}


if __name__ == '__main__':
    raise SystemExit(harness.main(prepare, __doc__))
