import Foundation

enum CancellationMode { case cancellable }
@MainActor final class ReplayThread {
    var afterRead: (() -> Void)?
    func runInLoop<T>(_ mode: CancellationMode, _ body: (RunLoopJob) throws -> T) async throws -> T {
        let value = try body(RunLoopJob())
        afterRead?()
        afterRead = nil
        return value
    }
}
final class ThreadGuardedValue<T> {
    var threadGuarded: T
    init(_ value: T) { threadGuarded = value }
}
final class ReplayApplication {
    var isHidden = false
    var isTerminated = false
    var processIdentifier: Int32 = 9
    var bundleIdentifier: String? = nil
    var activationPolicy = "regular"
    var isActive = false
}
let lockScreenAppBundleId = "replay.lock-screen"
final class AxWindow {
    let restorationToken = UUID()
    let windowId: UInt32
    let ax: AXUIElement
    init(_ ax: AXUIElement) { windowId = ax.owner!; self.ax = ax }
}
extension Dictionary where Key == UInt32, Value == AxWindow {
    @discardableResult
    mutating func getOrRegisterAxWindow(windowId: UInt32, _ ax: AXUIElement,
                                       _ app: ReplayApplication, _ job: RunLoopJob) throws -> AxWindow? {
        // Subscription creation is a memory-only success in this discovery replay.
        if let cached = self[windowId] { return cached }
        let window = AxWindow(ax)
        self[windowId] = window
        return window
    }
}
extension RunLoopJob { func cancel() { cancelled = true } }
struct DiscoveryFixture: Sendable {
    let pid: Int32
    let retained: [UInt32]
    let exposed: [UInt32]
    func roots() -> [UInt32: AXUIElement] {
        Dictionary(uniqueKeysWithValues: Set(retained + exposed).map { id in
            let ax = AXUIElement(owner: id)
            ax.set(Ax.roleAttr, kAXWindowRole)
            ax.set(Ax.subroleAttr, kAXStandardWindowSubrole)
            ax.set(Ax.childrenAttr, [AXUIElement]())
            return (id, ax)
        })
    }
}


enum AxUiElementWindowType: Sendable { case window, dialog, popup }
enum MacOsWindowLevel { case normalWindow }
struct KnownBundleId { init?(rawValue: String) { return nil } }
enum ReplayAXError { case success, failure }
func AXUIElementGetPid(_ element: AXUIElement, _ pid: inout Int32) -> ReplayAXError {
    guard let value = element.attributes["pid"] as? Int32 else { return .failure }
    pid = value
    return .success
}
extension AXUIElement {
    func getWindowType(axApp: AXUIElement, _ bundle: KnownBundleId?, _ policy: String,
                       _ level: MacOsWindowLevel?) -> AxUiElementWindowType {
        attributes["windowType"] as? AxUiElementWindowType ?? .window
    }
}
@MainActor enum TrayMenuModel {
    static let shared = ReplayTray()
}
@MainActor final class ReplayTray { var isEnabled = true }
@MainActor enum Simulation {
    static var owners: [UInt32: Set<Int32>]? = [:]
    static var processes: [Int32: WindowRestorationOwner] = [:]
    static var controls = true
    static var peers: [Int] = []
    static var inventoryError = false
    static var yields = 0
    static var stops = 0
    static var mutations: [UInt32] = []
    static var foreground: Int32? = 99
    static var detectedRule: ((MacWindow) async -> Void)?
}
@MainActor func getRestorationWindowOwners() -> [UInt32: Set<Int32>]? { Simulation.owners }
@MainActor func getWindowLevel(for id: UInt32) -> MacOsWindowLevel? { .normalWindow }
@MainActor enum WindowManagerProcesses {
    static func restorationOwner(_ pid: Int32) -> WindowRestorationOwner? { Simulation.processes[pid] }
}
@MainActor final class ReplayAdmission {
    func observedPeers(freeze: () -> Void) throws -> [Int] {
        if Simulation.inventoryError { freeze(); throw CancellationError() }
        if !Simulation.peers.isEmpty { freeze() }
        return Simulation.peers
    }
}
@MainActor struct ReplayFocus {
    var windowOrNil: MacWindow?
    var workspace: Workspace { windowOrNil?.nodeWorkspace ?? Workspace.get(byName: "current") }
}
@MainActor var focus = ReplayFocus()
let WEIGHT_AUTO = 0
let INDEX_BIND_LAST = 0
@MainActor final class ReplayParent {
    let workspace: Workspace
    let floating: Bool
    enum Layout { case tiles, accordion }
    enum Orientation { case h, v }
    var layout: Layout = .tiles
    var orientation: Orientation = .h
    func changeOrientation(_ orientation: Orientation) { self.orientation = orientation }
    init(_ workspace: Workspace, floating: Bool) { self.workspace = workspace; self.floating = floating }
}
@MainActor final class Workspace: Equatable {
    nonisolated static func == (lhs: Workspace, rhs: Workspace) -> Bool { lhs === rhs }
    let name: String
    lazy var rootTilingContainer = ReplayParent(self, floating: false)
    lazy var floatingContainer = ReplayParent(self, floating: true)
    var floatingWindowsContainer: ReplayParent { floatingContainer }
    static var registry: [String: Workspace] = [:]
    static var all: [Workspace] { Array(registry.values) }
    init(_ name: String) { self.name = name }
    static func get(byName name: String) -> Workspace {
        if let old = registry[name] { return old }
        let value = Workspace(name); registry[name] = value; return value
    }
}
struct WorkspaceName {
    struct Parsed { let value: String?; func getIgnoringErrorsOrNil() -> String? { value } }
    static func parse(_ value: String) -> Parsed { Parsed(value: value.isEmpty ? nil : value) }
}
enum ReplayParentCase {
    case tilingContainer, floatingWindowsContainer, macosMinimizedWindowsContainer,
         macosFullscreenWindowsContainer, macosHiddenAppsWindowsContainer, macosPopupWindowsContainer, unbound
}
@MainActor final class MacWindow {
    static var allWindowsMap: [UInt32: MacWindow] = [:]
    static var allWindows: [MacWindow] { Array(allWindowsMap.values) }
    let windowId: UInt32
    let macApp: ReplayAquaApp
    var app: ReplayAquaApp { macApp }
    var parent: ReplayParent?
    var isFullscreen = false
    var noOuterGapsInFullscreen = false
    var specialParent: ReplayParentCase?
    var nodeWorkspace: Workspace? { parent?.workspace }
    var isBound: Bool { parent != nil }
    var isFloating: Bool { parent?.floating == true }
    var allLeafWindowsRecursive: [MacWindow] { [self] }
    var windowParentCases: ReplayParentCase { specialParent ?? (parent?.floating == true ? .floatingWindowsContainer : .tilingContainer) }
    init(_ id: UInt32, app: ReplayAquaApp) {
        windowId = id; macApp = app; parent = Workspace.get(byName: "default").rootTilingContainer
    }
    func bindAsFloatingWindow(to workspace: Workspace) { bind(to: workspace.floatingContainer, adaptiveWeight: 0, index: 0) }
    func bind(to parent: ReplayParent, adaptiveWeight: Int, index: Int) {
        beforeBind()
        Simulation.mutations.append(windowId); self.parent = parent
    }
    func focusWindow() -> Bool { focus.windowOrNil = self; return true }
    func markAsMostRecentChild() {}
}

typealias Window = MacWindow
typealias NonLeafTreeNodeObject = ReplayParent
struct CmdEnv {
    static let defaultEnv = Self()
    func withWindowId(_ id: UInt32) -> Self { self }
}
@MainActor final class CmdIo { func err(_ value: String) -> String { value } }
@MainActor enum CmdIoImpl { static var emptyStdinIgnoringOut: CmdIo { CmdIo() } }
struct BinaryExitCode: Sendable {
    let rawValue: Int32
    static let succ = Self(rawValue: 0)
    static var fail: Self { Self(rawValue: 1) }
    static func fail(_ message: String) -> Self { .fail }
    static func succ(_ message: String) -> Self { .succ }
    static func from(bool: Bool) -> Self { bool ? .succ : .fail }
}
struct Int32ExitCode: Sendable { let rawValue: Int32 }
let EXIT_CODE_ZERO: Int32 = 0
@MainActor protocol Command {
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode
    var shouldResetClosedWindowsCache: Bool { get }
}
indirect enum Shell<T> {
    case cmd(T), and([Self]), or([Self]), pipe([Self]), seq([Self]), empty
}
@MainActor struct ReplayMoveCommand: Command {
    let window: MacWindow
    let workspace: Workspace
    var shouldResetClosedWindowsCache: Bool { true }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        moveWindowToWorkspace(window, workspace, io, focusFollowsWindow: false, failIfNoop: false)
    }
}
@MainActor struct ReplayFloatCommand: Command {
    let window: MacWindow
    var shouldResetClosedWindowsCache: Bool { true }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        window.bindAsFloatingWindow(to: window.nodeWorkspace!)
        return .succ
    }
}
@MainActor func onWindowDetected(_ env: CmdEnv, _ io: CmdIo, _ window: Window) async -> Int32ExitCode {
    await Simulation.detectedRule?(window)
    return Int32ExitCode(rawValue: 0)
}
struct LayoutCmdArgs {
    enum LayoutDescription { case floating }
    let rawArgs: [String]
    let toggleBetween: [LayoutDescription]
}
@MainActor var global_layoutForNextDetectedWindow: LayoutCmdArgs.LayoutDescription?
enum CmdStdin { case emptyStdin }
struct LayoutCommand {
    let args: LayoutCmdArgs
    @MainActor func run(_ env: CmdEnv, _ stdin: CmdStdin) async { fatalError("Unused next-window layout") }
}
@MainActor struct FullscreenCmdArgs {
    enum Toggle { case on, off, toggle }
    let window: MacWindow
    let toggle: Toggle
    var failIfNoop = false
    var noOuterGaps = false
    struct Target { var windowOrNil: MacWindow? }
    func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> Target? { Target(windowOrNil: window) }
}
@MainActor func resetClosedWindowsCache() {}
@MainActor func refreshModel_nonCancellable() async {}
@MainActor func runShellAnd(_ commands: [Shell<any Command>], _ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode { fatalError("Unused compound command") }
@MainActor func runShellOr(_ commands: [Shell<any Command>], _ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode { fatalError("Unused compound command") }
@MainActor func runShellPipe(_ commands: [Shell<any Command>], _ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode { fatalError("Unused compound command") }
@MainActor func runShellSeq(_ commands: [Shell<any Command>], _ env: CmdEnv, _ io: CmdIo) async -> Int32ExitCode { fatalError("Unused compound command") }

struct ReplayLayoutResult {
    let value: String
    func getIgnoringErrorsOrNil() -> Self? { self }
    func toString() -> String { value }
}
@MainActor func toLayoutResult(w: MacWindow) -> ReplayLayoutResult {
    ReplayLayoutResult(value: w.isFloating ? "floating" : "h_tiles")
}
@MainActor func toLayoutString(tc: ReplayParent) -> String {
    (tc.orientation == .h ? "h_" : "v_") + (tc.layout == .tiles ? "tiles" : "accordion")
}

@MainActor enum NSWorkspace {
    static let shared = ReplayWorkspace()
}
@MainActor final class ReplayWorkspace {
    struct App { let processIdentifier: Int32 }
    var frontmostApplication: App? { Simulation.foreground.map { App(processIdentifier: $0) } }
}
