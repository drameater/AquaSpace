#!/usr/bin/env python3
"""Compare stock/fork discovery with captured AX visibility, entirely in memory.

Extracts the actual refresh methods from the local stock tag and current source,
plus AquaSpace's real tab state machine. No AppKit, AX, WindowServer, application,
process inventory, installed CLI or diagnostic helper is linked or invoked.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    package = root / '.debug/window-discovery-offline'
    source = package / 'Sources/WindowDiscoveryReplay'
    tests = package / 'Tests/WindowDiscoveryReplayTests'
    for directory in (source, tests):
        directory.mkdir(parents=True, exist_ok=True)
    production = root / 'Sources/AppBundle'
    stock = subprocess.check_output(['git', '-C', str(root), 'show',
        'v0.21.3-Beta:Sources/AppBundle/tree/MacApp.swift'], text=True)
    fork = (production / 'tree/MacApp.swift').read_text()
    prefix = '    private func refreshAndGetAliveWindowIds('
    bodies = {}
    for name, text, end in [('Stock', stock, '\n    private func destroy()'),
                            ('Aqua', fork, '\n    @MainActor\n    func invalidateNativeTabObservations')]:
        if text.count(prefix) != 1 or text.count(end) != 1:
            raise RuntimeError('Discovery method boundaries changed; review extraction.')
        body = text[text.index(prefix):text.index(end)].replace('private func', 'func', 1)
        bodies[name] = hashlib.sha256(body.encode()).hexdigest()
        wrapper = '''import Foundation
import CoreFoundation
@MainActor final class ReplayNAMEApp {
    let nsApp = ReplayApplication()
    var pid: Int32 { nsApp.processIdentifier }
    let axApp = ThreadGuardedValue(AXUIElement(owner: nil))
    let windows = ThreadGuardedValue([UInt32: AxWindow]())
    let nativeTabs = ThreadGuardedValue(AxNativeTabState())
    var windowsCount = 0
    var setFrameJobs: [UInt32: RunLoopJob] = [:]
    var thread: ReplayThread? = ReplayThread()
    struct WindowRefresh {
        var aliveWindowIds: [UInt32] = []
        var nativeTabUpdate = NativeTabUpdate()
    }
    func destroy() async {}
    func seed(_ roots: [AXUIElement]) {
        windows.threadGuarded = Dictionary(uniqueKeysWithValues: roots.map { ($0.owner!, AxWindow($0)) })
    }
BODY
}
'''.replace('NAME', name).replace('BODY', body)
        if name == 'Aqua':
            proof_start = '    struct RestorationWindowProof:'
            proof_end = '\n    @MainActor\n    func applyNativeTabUpdate'
            if fork.count(proof_start) != 1 or fork.count(proof_end) != 1:
                raise RuntimeError('AX proof extraction boundaries changed.')
            wrapper = wrapper[:-2] + fork[fork.index(proof_start):fork.index(proof_end)] + '\n}\n'
        (source / ('Replay' + name + 'App.swift')).write_text(wrapper)
    for relative in ('model/NativeTabReplacement.swift', 'tree/nativeTabTopology.swift', 'tree/nativeTabs.swift'):
        text = (production / relative).read_text()
        if relative.endswith('/nativeTabs.swift'):
            start, end = '/// Read the binding at the instant of replacement:', '/// AX objects stay on their application'
            if text.count(start) != 1 or text.count(end) != 1:
                raise RuntimeError('Tab adapter extraction boundaries changed.')
            text = text[:text.index(start)] + text[text.index(end):]
        (source / Path(relative).name).write_text(text.replace('import AppKit\n', 'import Foundation\nimport CoreFoundation\n'))
    support = (root / 'script/native-tabs-offline/ReplayAX.swift').read_text()
    support = support.replace('enum Ax {', 'enum Ax {\n    static let windowsAttr = ReplayAttribute<[WindowIdAndAxUiElement]>(name: "windows")')
    support = support.replace('enum Ax {', 'enum Ax {\n    static let focusedWindowAttr = ReplayAttribute<WindowIdAndAxUiElement>(name: "focused")')
    (source / 'ReplayAX.swift').write_text(support)
    dictionary = (root / 'Sources/Common/util/DictionaryEx.swift').read_text()
    boundary = '\nextension Dictionary where Key: Comparable'
    if dictionary.count(boundary) != 1:
        raise RuntimeError('Dictionary helper boundary changed.')
    (source / 'DictionaryEx.swift').write_text(dictionary[:dictionary.index(boundary)])
    (source / 'PendingWindowRestoration.swift').write_text((production / 'model/PendingWindowRestoration.swift').read_text())
    handoff = (production / 'WindowManagerHandoff.swift').read_text()
    begin, end = 'struct WindowManagerSession:', '\n@MainActor\nenum WindowManagerHandoff'
    restore, switch = '    static func restoreStartupSession() async {', '\n    static func switchToOriginal('
    if any(handoff.count(marker) != 1 for marker in (begin, end, restore, switch)):
        raise RuntimeError('Handoff extraction boundaries changed.')
    (source / 'WindowManagerSession.swift').write_text(handoff[handoff.index(begin):handoff.index(end)])
    (source / 'ReplayHandoff.swift').write_text("""@MainActor enum ReplayHandoff {
    static var startupSession: WindowManagerSession?
    static var commands: [[String]] = []
    static var pendingRestoration = PendingWindowRestoration(windows: [], owners: [:])
    static var onCommand: (([String]) throws -> Void)?
    static func reconcilePendingRestoration() {}
    static func localCommand(_ args: [String]) async throws { commands.append(args); try onCommand?(args) }
""" + handoff[handoff.index(restore):handoff.index(switch)] + '\n}\n')
    anchor_start, anchor_end = '    private struct RestorationAnchor:', '    private static func captureOwners('
    coordinator_start, coordinator_end = '    static func reconcilePendingRestoration()', '    private static func capture(_ command:'
    if any(handoff.count(marker) != 1 for marker in (anchor_start, anchor_end, coordinator_start, coordinator_end)):
        raise RuntimeError('Deferred adapter extraction boundaries changed.')
    anchor = handoff[handoff.index(anchor_start):handoff.index(anchor_end)].replace('private ', '')
    coordinator = handoff[handoff.index(coordinator_start):handoff.index(coordinator_end)].replace('private static func captureCurrentModel', 'static func captureCurrentModel')
    capture_owners = handoff[handoff.index(anchor_end):handoff.index(coordinator_start)]
    (source / 'ReplayCoordinator.swift').write_text('''import Foundation
@MainActor enum ReplayCoordinator {
    static var pendingRestoration = PendingWindowRestoration(windows: [], owners: [:])
    static var restoringPending = false
    static var restorationAnchors: [UInt32: RestorationAnchor] = [:]
    static var canControlWindows: Bool { Simulation.controls }
    static let admission = ReplayAdmission()
    static func freezeAXWrites() { Simulation.controls = false }
    static func yieldToPeers(_ peers: [Int]) async { Simulation.controls = false; Simulation.yields += 1 }
    static func stopForUncertainOwnership(_ error: any Error) async { Simulation.controls = false; Simulation.stops += 1 }
    enum HandoffError: Error { case message(String) }
''' + anchor + capture_owners + coordinator + '\n}\n')
    # Exercise the real shell command entrypoint and movement helper. Only tree
    # storage/AX are replaced; the pre-bind policy is extracted from production.
    shell = (production / 'shell/Shell.swift').read_text()
    shell_start = '    @MainActor func run(_ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode {'
    shell_end = '\n    @discardableResult @MainActor func run('
    if shell.count(shell_start) != 1 or shell.count(shell_end) != 1:
        raise RuntimeError('Shell command extraction boundaries changed.')
    (source / 'ReplayCommandRunner.swift').write_text('extension Shell where T == any Command {\n' +
        shell[shell.index(shell_start):shell.index(shell_end)] + '\n}\n')
    move = (production / 'command/impl/MoveNodeToWorkspaceCommand.swift').read_text()
    move_start = '@MainActor\nfunc moveWindowToWorkspace('
    if move.count(move_start) != 1:
        raise RuntimeError('Move command extraction boundary changed.')
    (source / 'MoveWindow.swift').write_text(move[move.index(move_start):])
    fullscreen = (production / 'command/impl/FullscreenCommand.swift').read_text()
    (source / 'FullscreenCommand.swift').write_text(fullscreen.replace('import AppKit\n', '').replace('import Common\n', '')
        .replace('WindowManagerHandoff.', 'ReplayCoordinator.'))
    mac_window = (production / 'tree/MacWindow.swift').read_text()
    detected_start = '@MainActor\nfunc runOnWindowDetected('
    detected_end = '@MainActor\nfunc onWindowDetected('
    if mac_window.count(detected_start) != 1 or mac_window.count(detected_end) != 1:
        raise RuntimeError('Automatic detection callback boundaries changed.')
    (source / 'AutomaticDetection.swift').write_text(mac_window[
        mac_window.index(detected_start):mac_window.index(detected_end)])
    tree = (production / 'tree/TreeNode.swift').read_text()
    bind_start = 'func bind(to newParent: NonLeafTreeNodeObject, adaptiveWeight: CGFloat, index: Int) -> BindingData? {'
    bind_end = '        let result = unbindIfBound()'
    if tree.count(bind_start) != 1 or tree.count(bind_end) != 1:
        raise RuntimeError('Tree mutation extraction boundaries changed.')
    before_bind = tree[tree.index(bind_start) + len(bind_start):tree.index(bind_end)]
    (source / 'ReplayBeforeBind.swift').write_text('extension MacWindow {\nfunc beforeBind() {\n' +
        before_bind.replace('WindowManagerHandoff.', 'ReplayCoordinator.') + '\n}\n}\n')
    mutation = production / 'model/WindowRestorationMutation.swift'
    destination = source / mutation.name
    if mutation.exists():
        destination.write_text(mutation.read_text())
    elif destination.exists():
        destination.unlink()
    fixture_root = root / 'script/window-discovery-offline'
    (source / 'ReplaySupport.swift').write_text((fixture_root / 'ReplaySupport.swift').read_text())
    (tests / 'WindowDiscoveryTest.swift').write_text((fixture_root / 'WindowDiscoveryTest.swift').read_text())
    (tests / 'DeferredRestorationTest.swift').write_text((fixture_root / 'DeferredRestorationTest.swift').read_text())
    fixture = json.loads((fixture_root / 'captured-inventory.json').read_text())
    rows = [f'    DiscoveryFixture(pid: {r["pid"]}, retained: {r["retained_ids"]}, exposed: {r["exposed_ids"]})' for r in fixture['apps']]
    (source / 'CapturedFixture.swift').write_text('let capturedFixtures: [DiscoveryFixture] = [\n' + ',\n'.join(rows) + '\n]\n')
    (package / 'source-record.json').write_text(json.dumps(dict(stock_revision=subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'v0.21.3-Beta^{commit}'], text=True).strip(),
        fork_revision=subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        extracted_method_sha256=bodies), indent=2) + '\n')
    (package / 'Package.swift').write_text('''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "WindowDiscoveryReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "WindowDiscoveryReplay"),
    .testTarget(name: "WindowDiscoveryReplayTests", dependencies: ["WindowDiscoveryReplay"]),
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
