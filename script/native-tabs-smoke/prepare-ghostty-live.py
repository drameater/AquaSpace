#!/usr/bin/env python3
"""Build a copy of AeroSpace that can register only fresh, uniquely titled Ghostty fixtures.

Does not launch apps, change permissions, or edit production sources. The live
harness refuses a binary whose checksum does not match the emitted scope manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"Source changed; review the test-scope gate for {path.name}")
    path.write_text(text.replace(old, new, 1))


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--application-id", default="com.mitchellh.ghostty")
    parser.add_argument("--scratch-path", type=Path, default=root / ".debug/ghostty-live-build")
    parser.add_argument("--output", type=Path, default=root / ".debug/native-tabs/ghostty-isolated")
    parser.add_argument("--source-path", type=Path, help="Reuse an isolated source copy for incremental QA builds")
    parser.add_argument("--handoff-qa", action="store_true", help="Build a release candidate isolated to background fixtures and a simulated peer")
    parser.add_argument("--peer-socket", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    source = args.source_path or Path(tempfile.mkdtemp(prefix="aerospace-ghostty-source-", dir="/private/tmp"))
    source.mkdir(parents=True, exist_ok=True)
    for name in ("Package.swift", "Package.resolved"):
        shutil.copy2(root / name, source / name)
    shutil.copytree(root / "Sources", source / "Sources", dirs_exist_ok=True)
    bundle = source / "Sources/AppBundle"
    app = bundle / "tree/MacApp.swift"
    replace_once(app, "        let pid = nsApp.processIdentifier\n        // AX requests",
                 f'        guard nsApp.bundleIdentifier == "{args.application_id}" else {{ return nil }}\n'
                 "        let pid = nsApp.processIdentifier\n        // AX requests")
    replace_once(app, "nativeTabs.threadGuarded.reconcile(axApp.threadGuarded.get(Ax.windowsAttr), job",
                 "nativeTabs.threadGuarded.reconcile(axApp.threadGuarded.get(Ax.windowsAttr)?.filter { nativeTabLiveTestAllows($0.windowId, $0.ax) }, job")
    replace_once(app, "let liveWindows = axApp.threadGuarded.get(Ax.windowsAttr)",
                 "let liveWindows = axApp.threadGuarded.get(Ax.windowsAttr)?.filter { nativeTabLiveTestAllows($0.windowId, $0.ax) }")
    replace_once(app, "        if let existing = self[id] { return existing }",
                 "        guard nativeTabLiveTestAllows(id, axWindow) else { return nil }\n"
                 "        if let existing = self[id] { return existing }")
    replace_once(bundle / "tree/nativeTabs.swift", "        try job.checkCancellation()\n        let replacements = topology.replacements",
                 '        print("LIVE TOPOLOGY old=\\(observations.mapValues { $0.snapshot }) refreshed=\\(previous.mapValues { $0.snapshot }) new=\\(current.mapValues { $0.snapshot }) result=\\(topology)")\n'
                 "        try job.checkCancellation()\n        let replacements = topology.replacements")
    (bundle / "NativeTabLiveTestScope.swift").write_text('''import AppKit
// Test-only gates; never part of the production patch.
let nativeTabLiveTestPrefix: String = {
    guard let prefix = ProcessInfo.processInfo.environment["AEROSPACE_NATIVE_TAB_TEST_PREFIX"], prefix.hasPrefix("AeroSpace-NativeTabs-"), prefix.count > 25 else { fatalError("Missing unique test title prefix") }
    return prefix
}()
let nativeTabLiveTestExcludedWindowIds: Set<UInt32> = {
    guard let windows = getWindowServerWindowIdsByPid() else { fatalError("Cannot isolate live test without a WindowServer snapshot") }
    return Set(windows.values.flatMap { $0 })
}()
func nativeTabLiveTestAllows(_ id: UInt32, _ window: AXUIElement) -> Bool {
    !nativeTabLiveTestExcludedWindowIds.contains(id) && window.get(Ax.titleAttr)?.hasPrefix(nativeTabLiveTestPrefix) == true
}
''')
    replace_once(bundle / "initAppBundle.swift", "        initServerArgs()",
                 "        initServerArgs()\n        _ = nativeTabLiveTestPrefix\n        _ = nativeTabLiveTestExcludedWindowIds")
    if args.handoff_qa:
        if args.peer_socket is None or not str(args.peer_socket).startswith("/private/tmp/aquaspace-qa-"):
            raise RuntimeError("QA peer socket must be uniquely named under /private/tmp/aquaspace-qa-")
        metadata = source / "Sources/Common/appMetadata.swift"
        metadata.write_text(metadata.read_text().replace("public let commandSocketAppId: String = aeroSpaceAppId", 'public let commandSocketAppId: String = "local.aquaspace.qa"'))
        handoff = bundle / "WindowManagerHandoff.swift"
        body = handoff.read_text()
        start = body.index("    static func prepareForStartup() async {")
        end = body.index("    static func finishStartup() async {", start)
        # The external harness freezes the real manager before launch. This copy
        # cannot stop, start, or adopt the real user's manager during the test.
        body = body[:start] + "    static func prepareForStartup() async {}\n\n" + body[end:]
        handoff.write_text(body)
        replace_once(handoff, 'private static let originalSocketPath = "/tmp/\\(originalAeroSpaceAppId)-\\(unixUserName).sock"',
                     "private static let originalSocketPath = " + json.dumps(str(args.peer_socket)))
        replace_once(handoff, '''            if launch {
                guard NSWorkspace.shared.open(URL(filePath: "/Applications/AeroSpace.app")) else {
                    throw HandoffError.message("Could not open /Applications/AeroSpace.app")
                }
            }''', '''            if launch { _ = try await remoteCommand(["qa-target-start"]) }''')
        text = app.read_text()
        start = text.index("    @MainActor func nativeFocus(_ windowId: UInt32) {")
        end = text.index("    @MainActor func setAxFrame(", start)
        app.write_text(text[:start] + "    @MainActor func nativeFocus(_ windowId: UInt32) {}\n\n" + text[end:])
        replace_once(app, '''            try disableAnimations(app: axApp.threadGuarded, job) {
                try setFrame(window, topLeft, size, job)''', '''            try disableAnimations(app: axApp.threadGuarded, job) {
                try setFrame(window, topLeft.map { CGPoint(x: $0.x + 20000, y: $0.y) }, size, job)''')
    flags = ["--package-path", str(source), "--scratch-path", str(args.scratch_path), "--jobs", str(args.jobs), "--disable-sandbox", "--skip-update"]
    if args.handoff_qa:
        flags += ["-c", "release"]
    if args.sdk:
        flags += ["--sdk", str(args.sdk)]
    args.scratch_path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("CLANG_MODULE_CACHE_PATH", str(args.scratch_path / "clang-cache"))
    env.setdefault("SWIFTPM_MODULECACHE_OVERRIDE", str(args.scratch_path / "swift-cache"))
    subprocess.run(["swift", "build", *flags, *([] if args.handoff_qa else ["--product", "AeroSpaceApp"])], check=True, env=env)
    binary_dir = Path(subprocess.check_output(["swift", "build", *flags, "--show-bin-path"], text=True, env=env).strip())
    target = args.output / "AeroSpace Native Tabs.app"
    executable = target / "Contents/MacOS/AeroSpaceApp"
    executable.parent.mkdir(parents=True, exist_ok=True)
    resources = target / "Contents/Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary_dir / "AeroSpaceApp", executable)
    if args.handoff_qa:
        shutil.copy2(binary_dir / "aquaspace", target / "Contents/MacOS/aquaspace-cli")
    shutil.copy2(root / "docs/config-examples/default-config.toml", resources / "default-config.toml")
    with (target / "Contents/Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleIdentifier": "local.aquaspace" if args.handoff_qa else "local.aquaspace.debug", "CFBundleExecutable": "AeroSpaceApp",
                      "CFBundleName": "AeroSpace Native Tabs", "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
                      "LSUIElement": True, "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True}, file)
    subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
    (args.output / "scope.json").write_text(json.dumps({"kind": "ghostty-prefix-and-baseline-exclusion", "application_id": args.application_id,
        "handoff_qa": args.handoff_qa, "peer_socket": str(args.peer_socket) if args.peer_socket else None,
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "source_copy": str(source)}, indent=2) + "\n")
    print(f"Scoped test app: {target}")


if __name__ == "__main__":
    main()
