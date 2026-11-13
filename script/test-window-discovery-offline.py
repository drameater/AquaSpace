#!/usr/bin/env python3
"""Compare stock/fork discovery with captured AX visibility, entirely in memory.

Compiles the stock refresh method of upstream v0.21.3-Beta (a vendored copy) and
the current one, plus AquaSpace's real tab state machine, restoration, command
and callback code. No AppKit, AX, WindowServer, application, process inventory,
installed CLI or diagnostic helper is linked or invoked.

Most production files here mix the tested logic with model-tree code, so the
runner lifts named declarations out of them (see offline_harness.SwiftSource).
When production code renames one, the run stops with the file and the name.
"""
import hashlib
import json
import subprocess

import offline_harness as harness

PACKAGE = '''// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "WindowDiscoveryReplay", platforms: [.macOS(.v13)], targets: [
    .target(name: "WindowDiscoveryReplay"),
    .testTarget(name: "WindowDiscoveryReplayTests", dependencies: ["WindowDiscoveryReplay"]),
])
'''
FIXTURES = harness.ROOT / 'script/window-discovery-offline'
# The stock method is frozen upstream history, so a vendored copy replaces the
# git tag that earlier versions read. The tag, when present, verifies the copy.
STOCK_TAG, STOCK_COMMIT = 'v0.21.3-Beta', 'd56e1637c3a1ed660d0cadd7534e94fb3218d1c3'
STOCK_REFRESH = FIXTURES / 'stock-v0.21.3-Beta-refresh.swift.txt'
REFRESH = 'func refreshAndGetAliveWindowIds('
TESTS = ('WindowDiscoveryTest', 'DeferredRestorationTest', 'CallbackCommandContinuationTest', 'CallbackSafetyTest',
         'NoOpIntentTest', 'MutationIntentEntryTest', 'CrossWindowCallbackChainTest')
APP_WRAPPER = '''import Foundation
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
BODY}
'''
COORDINATOR_HEADER = '''import Foundation
@MainActor enum ReplayCoordinator {
    static var pendingRestoration = PendingWindowRestoration(windows: [], owners: [:])
    static var restoringPending = false
    static var restorationAnchors: [UInt32: RestorationAnchor] = [:]
    static var canControlWindows: Bool { Simulation.controls }
    static var canInitializeWindows: Bool { !isYielding && Simulation.permission.permitsInitialization }
    static var controlGeneration: UUID { Simulation.permission.generation }
    static var isYielding: Bool { Simulation.yields > 0 || Simulation.stops > 0 }
    static let admission = ReplayAdmission()
    static func freezeAXWrites() { Simulation.permission.freeze() }
    static func yieldToPeers(_ peers: [Int]) async { Simulation.controls = false; Simulation.yields += 1 }
    static func stopForUncertainOwnership(_ error: any Error) async { Simulation.controls = false; Simulation.stops += 1 }
    enum HandoffError: Error { case message(String) }
'''
HANDOFF_HEADER = '''@MainActor enum ReplayHandoff {
    static var startupSession: WindowManagerSession?
    static var commands: [[String]] = []
    static var pendingRestoration = PendingWindowRestoration(windows: [], owners: [:])
    static var onCommand: (([String]) throws -> Void)?
    static func reconcilePendingRestoration() {}
    static func localCommand(_ args: [String]) async throws { commands.append(args); try onCommand?(args) }
'''


def git(*args):
    return subprocess.run(['git', '-C', str(harness.ROOT), *args], capture_output=True, text=True)


def stock_refresh():
    """Return the vendored stock method; check it against the tag when the tag exists."""
    vendored = STOCK_REFRESH.read_text()
    tagged = git('show', STOCK_TAG + ':Sources/AppBundle/tree/MacApp.swift')
    if tagged.returncode != 0:
        return vendored, f'vendored copy (tag {STOCK_TAG} not present; not re-verified)'
    if harness.SwiftSource(f'MacApp.swift at {STOCK_TAG}', tagged.stdout).decl(REFRESH) != vendored:
        raise harness.HarnessError(f'{STOCK_REFRESH.relative_to(harness.ROOT)} differs from {REFRESH}...) at tag {STOCK_TAG}. '
                                   'The upstream tag is immutable; restore the vendored copy from it.')
    return vendored, f'vendored copy, verified against tag {STOCK_TAG}'


def without_imports(text):
    return harness.retarget_imports(text, drop=('AppKit', 'Common'), add=('Foundation',))


def prepare(args):
    package = harness.package_for(args, 'window-discovery-offline')
    source, tests = 'Sources/WindowDiscoveryReplay/', 'Tests/WindowDiscoveryReplayTests/'

    def production(relative):
        return harness.SwiftSource('Sources/AppBundle/' + relative)

    def write(name, text):
        package.write(source + name, text)

    mac_app = production('tree/MacApp.swift')
    stock, stock_origin = stock_refresh()
    bodies = {}
    for name, body, extra in (('Stock', stock, ''),
                              ('Aqua', mac_app.decl(REFRESH),
                               mac_app.decl('struct RestorationWindowProof:', 'func restorationWindowProof('))):
        body = body.replace('private func', 'func', 1)
        bodies[name] = hashlib.sha256(body.encode()).hexdigest()
        write(f'Replay{name}App.swift', APP_WRAPPER.replace('NAME', name).replace('BODY', body + extra))
    # Whole files. nativeTabSlotTransfer.swift binds windows through the local tree stubs.
    for relative in ('model/NativeTabReplacement.swift', 'tree/nativeTabTopology.swift', 'tree/nativeTabs.swift',
                     'tree/nativeTabSlotTransfer.swift', 'util/AxUiElementMock.swift', 'model/KnownBundleId.swift',
                     'model/PendingWindowRestoration.swift', 'model/WindowControlPermission.swift',
                     'model/WindowManagerSession.swift'):
        write(relative.split('/')[-1], harness.retarget_imports(
            production(relative).text, drop=('AppKit', 'Common'), add=('Foundation', 'CoreFoundation')))
    write('DictionaryEx.swift', harness.SwiftSource('Sources/Common/util/DictionaryEx.swift').text)
    for name in ('ReplayAX.swift', 'ReplayRuntime.swift', 'ReplayNativeTabEnvironment.swift'):
        package.copy(source + name, harness.SHARED_SWIFT / name)

    handoff = production('WindowManagerHandoff.swift')
    write('ReplayHandoff.swift', HANDOFF_HEADER + handoff.decl('static func restoreStartupSession(') + '}\n')
    anchor = handoff.between('struct RestorationAnchor:', 'static func captureOwners(').replace('private ', '')
    capture_owners = handoff.between('static func captureOwners(', 'static func reconcilePendingRestoration(')
    coordinator = handoff.between('static func reconcilePendingRestoration(', 'static func capture(_ command:').replace(
        'private static func captureCurrentModel', 'static func captureCurrentModel')
    write('ReplayCoordinator.swift', COORDINATOR_HEADER + anchor + capture_owners + coordinator + '}\n')

    # Exercise the real shell command entrypoint and movement helper. Only tree
    # storage/AX are replaced; the pre-bind policy is in WindowRestorationMutation.swift.
    shell = production('shell/Shell.swift')
    write('ReplayCommandRunner.swift', 'extension Shell where T == any Command {\n' +
          shell.decl('func run(_ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode') + '}\n' +
          shell.between('func runShellOr('))
    write('MoveWindow.swift', production('command/impl/MoveNodeToWorkspaceCommand.swift')
          .between('func moveWindowToWorkspace(').replace('WindowManagerHandoff.', 'ReplayCoordinator.'))
    focus_command = production('command/impl/FocusCommand.swift')
    write('FocusCommand.swift', harness.retarget_imports(
        focus_command.before('func makeFloatingWindowsSeenAsTiling('), drop=('AppKit', 'Common')))
    write('FloatingFocusPreparation.swift', 'import Foundation\n' + focus_command.between(
        'func makeFloatingWindowsSeenAsTiling(', 'extension TreeNode {').replace('private ', ''))
    write('FocusEffect.swift', production('focus.swift').between('func setFocus(', 'extension Window {').replace('_focus', 'focus'))
    write('FocusFullscreenCheck.swift', '@MainActor\n' + production('command/impl/MoveCommand.swift')
          .between('func shouldFailBecauseFullscreen_nonCancellable('))
    write('OrientationEffect.swift', 'extension ReplayParent {\n' + production('tree/TilingContainer.swift')
          .decl('func changeOrientation(').replace('WindowManagerHandoff.', 'ReplayCoordinator.') + '}\n')
    for name in ('FullscreenCommand', 'LayoutCommand', 'CloseCommand', 'CloseAllWindowsButCurrentCommand',
                 'MacosNativeFullscreenCommand', 'MacosNativeMinimizeCommand'):
        write(name + '.swift', without_imports(production(f'command/impl/{name}.swift').text)
              .replace('WindowManagerHandoff.', 'ReplayCoordinator.'))

    mac_window = production('tree/MacWindow.swift')
    write('AutomaticDetection.swift', mac_window.between('func runOnWindowDetected(', 'extension WindowDetectedCallback {'))
    write('RegistrationInitialization.swift', 'extension MacWindow {\n' +
          mac_window.decl('static func initializeRegistered(').replace('private static', 'static') + '}\n')
    write('WindowRelayout.swift', 'import Foundation\nextension Window {\n' + mac_window.decl('func relayoutWindow(') +
          '}\n\n' + mac_window.decl('func unbindAndGetBindingDataForNewWindow('))
    for relative in ('model/WindowRestorationMutation.swift', 'model/WindowDiscoveryInitialization.swift'):
        write(relative.split('/')[-1], production(relative).text.replace('WindowManagerHandoff.', 'ReplayCoordinator.'))

    package.copy(source + 'ReplaySupport.swift', FIXTURES / 'ReplaySupport.swift')
    for name in TESTS:
        package.copy(f'{tests}{name}.swift', FIXTURES / f'{name}.swift')
    fixture = json.loads((FIXTURES / 'captured-inventory.json').read_text())
    rows = [f'    DiscoveryFixture(pid: {r["pid"]}, retained: {r["retained_ids"]}, exposed: {r["exposed_ids"]})' for r in fixture['apps']]
    write('CapturedFixture.swift', 'let capturedFixtures: [DiscoveryFixture] = [\n' + ',\n'.join(rows) + '\n]\n')
    fork = git('rev-parse', 'HEAD')
    package.write('source-record.json', json.dumps(dict(
        stock_revision=STOCK_COMMIT, stock_source=stock_origin,
        fork_revision=fork.stdout.strip() if fork.returncode == 0 else None,
        extracted_method_sha256=bodies), indent=2) + '\n')
    package.write('Package.swift', PACKAGE)
    return package, {}


if __name__ == '__main__':
    raise SystemExit(harness.main(prepare, __doc__))
