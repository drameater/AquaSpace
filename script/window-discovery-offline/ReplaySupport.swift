import Foundation

enum CancellationMode { case cancellable, nonCancellable }
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
    @MainActor func terminate() -> Bool { Simulation.nativeWrites.append("terminate"); return true }
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
    static var permission = WindowControlPermission()
    static var controls: Bool {
        get { permission.permitsWrites }
        set { if newValue { permission.claim() } else { permission.freeze() } }
    }
    static var peers: [Int] = []
    static var inventoryError = false
    static var yields = 0
    static var stops = 0
    static var mutations: [UInt32] = []
    static var foreground: Int32? = 99
    static var detectedRule: ((MacWindow) async -> Void)?
    static var callbacks: [WindowDetectedCallback]?
    static var beforeDetection: (() async throws -> Void)?
    static var awaitSize: (() async throws -> CGSize?)?
    static var awaitType: (() async throws -> AxUiElementWindowType)?
    static var awaitFullscreen: (() async throws -> Bool)?
    static var awaitMinimized: (() async throws -> Bool)?
    static var awaitWindowCount: (() async throws -> Int?)?
    static var nativeWrites: [String] = []
    static var unbinds: [UInt32] = []
    static var awaitCenter: ((MacWindow) async throws -> CGPoint?)?
    static var commandRefresh: (() async -> Void)?
    static var geometryTarget: MacWindow?
    static var mruWrites: [UInt32] = []
    static var normalizeOrientation = false
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
@MainActor struct ReplayFocus: @MainActor Equatable {
    var windowOrNil: MacWindow?
    var emptyWorkspace: Workspace?
    var workspace: Workspace { windowOrNil?.nodeWorkspace ?? emptyWorkspace ?? Workspace.get(byName: "current") }
    var frozen: Self { self }
    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.windowOrNil === rhs.windowOrNil && lhs.workspace === rhs.workspace
    }
}
@MainActor var focus = ReplayFocus()
let WEIGHT_AUTO: CGFloat = 0
let INDEX_BIND_LAST = 0
@MainActor final class ReplayParent {
    let workspace: Workspace
    let floating: Bool
    var isAttached = true
    var nodeWorkspace: Workspace? { isAttached ? workspace : nil }
    var children: [MacWindow] { MacWindow.allWindows.filter { $0.parent === self }.sorted { $0.windowId < $1.windowId } }
    var parent: ReplayParent?
    var parentsWithSelf: [ReplayParent] { [self] + (parent?.parentsWithSelf ?? []) }
    var allLeafWindowsRecursive: [MacWindow] { MacWindow.allWindows.filter { $0.parent?.parentsWithSelf.contains { $0 === self } == true } }
    enum Layout { case tiles, accordion }
    enum Orientation { case h, v; var opposite: Self { self == .h ? .v : .h } }
    var layout: Layout = .tiles
    var _orientation: Orientation = .h
    var orientation: Orientation { _orientation }
    init(_ workspace: Workspace, floating: Bool) { self.workspace = workspace; self.floating = floating }
}
@MainActor final class Workspace: Equatable {
    nonisolated static func == (lhs: Workspace, rhs: Workspace) -> Bool { lhs === rhs }
    let name: String
    lazy var rootTilingContainer = ReplayParent(self, floating: false)
    lazy var floatingContainer = ReplayParent(self, floating: true)
    lazy var macOsNativeFullscreenWindowsContainer = ReplayParent(self, floating: false)
    var allLeafWindowsRecursive: [MacWindow] { MacWindow.allWindows.filter { $0.nodeWorkspace === self } }
    var floatingWindows: [MacWindow] { allLeafWindowsRecursive.filter { $0.isFloating }.sorted { $0.windowId < $1.windowId } }
    var mostRecentWindowRecursive: MacWindow? { allLeafWindowsRecursive.first }
    func focusWorkspace() -> Bool { setFocus(to: ReplayFocus(emptyWorkspace: self)) }
    func findLeafWindowRecursive(snappedTo: CardinalDirection) -> MacWindow? { rootTilingContainer.allLeafWindowsRecursive.first }
    var workspaceMonitor: ReplayMonitor { ReplayMonitor() }
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
    case tilingContainer(ReplayParent), floatingWindowsContainer(ReplayParent)
    case macosMinimizedWindowsContainer,
         macosFullscreenWindowsContainer, macosHiddenAppsWindowsContainer, macosPopupWindowsContainer, unbound
}
@MainActor final class MacWindow: Equatable {
    nonisolated static func == (lhs: MacWindow, rhs: MacWindow) -> Bool { lhs === rhs }
    static var allWindowsMap: [UInt32: MacWindow] = [:]
    static var allWindows: [MacWindow] { Array(allWindowsMap.values) }
    static func get(byId id: UInt32) -> MacWindow? { allWindowsMap[id] }
    let windowId: UInt32
    let macApp: ReplayAquaApp
    var app: ReplayAquaApp { macApp }
    var parent: ReplayParent?
    var isFullscreen = false
    var noOuterGapsInFullscreen = false
    var specialParent: ReplayParentCase?
    var discoveryInitialization: WindowDiscoveryInitialization?
    var userMutationToken = UUID()
    var lastFloatingSize: CGSize?
    var layoutReason: ReplayLayoutReason = .standard
    var visualWorkspace: Workspace? { nodeWorkspace }
    var macAppUnsafe: ReplayAquaApp { macApp }
    var nodeWorkspace: Workspace? { parent?.workspace }
    var isBound: Bool { parent != nil }
    var isFloating: Bool { parent?.floating == true }
    var allLeafWindowsRecursive: [MacWindow] { [self] }
    var windowParentCases: ReplayParentCase {
        if let specialParent { return specialParent }
        guard let parent else { return .unbound }
        return parent.floating ? .floatingWindowsContainer(parent) : .tilingContainer(parent)
    }
    init(_ id: UInt32, app: ReplayAquaApp) {
        windowId = id; macApp = app; parent = Workspace.get(byName: "default").rootTilingContainer
    }
    func bindAsFloatingWindow(to workspace: Workspace) { bind(to: workspace.floatingContainer, adaptiveWeight: 0, index: 0) }
    func bind(to parent: ReplayParent, adaptiveWeight: CGFloat, index: Int) {
        beforeBind()
        Simulation.mutations.append(windowId); self.parent = parent
    }
    func getAxSize(_ cm: CancellationMode) async throws -> CGSize? { try await Simulation.awaitSize?() }
    func isMacosFullscreen(_ cm: CancellationMode) async throws -> Bool { try await Simulation.awaitFullscreen?() ?? false }
    func isMacosMinimized(_ cm: CancellationMode) async throws -> Bool { try await Simulation.awaitMinimized?() ?? false }
    func asMacWindow() -> Self { self }
    func setNativeFullscreen(_ value: Bool) { Simulation.nativeWrites.append("fullscreen") }
    func setNativeMinimized(_ value: Bool) { Simulation.nativeWrites.append("minimize") }
    func setAxFrame(_ point: CGPoint?, _ size: CGSize?) { Simulation.nativeWrites.append("frame") }
    func garbageCollect(skipClosedWindowsCache: Bool) { MacWindow.allWindowsMap.removeValue(forKey: windowId) }
    func closeAxWindow() { Simulation.nativeWrites.append("close"); MacWindow.allWindowsMap.removeValue(forKey: windowId) }
    @discardableResult func unbindFromParent() -> BindingData {
        let data = BindingData(parent: parent!, adaptiveWeight: 1, index: 0)
        Simulation.unbinds.append(windowId); parent = nil
        return data
    }
    func focusWindow() -> Bool { setFocus(to: ReplayFocus(windowOrNil: self)) }
    func closestParent(hasChildrenInDirection direction: CardinalDirection, withLayout: Layout?) -> (ReplayParent, Int)? {
        guard let parent, let index = ownIndex, index + direction.focusOffset < parent.children.count else { return nil }
        return (parent, index)
    }
    func findLeafWindowRecursive(snappedTo: CardinalDirection) -> Self? { self }
    func markAsMostRecentChild() { Simulation.mruWrites.append(windowId) }
    func getCenter(_ cm: CancellationMode) async throws -> CGPoint? { try await Simulation.awaitCenter?(self) ?? CGPoint(x: Double(windowId), y: 0) }
    var ownIndex: Int? { parent?.children.firstIndex { $0 === self } }
}

struct ReplayMonitor {
    let visibleRectPaddedByOuterGaps = CGRect(x: 0, y: 0, width: 1000, height: 1000)
    @MainActor func setActiveWorkspace(_ workspace: Workspace) -> Bool { true }
    @MainActor var activeWorkspace: Workspace { Workspace.get(byName: "monitor") }
    @MainActor func findRelativeMonitor(inDirection: CardinalDirection) -> ([Self], Int)? { ([self], 1) }
}
typealias MonitorInfo = ReplayMonitor
extension CGPoint {
    func coerce(in rect: CGRect) -> Self? { self }
    @MainActor func findWindowRecursively(in parent: ReplayParent, virtual: Bool, fullscreenCoversAll: Bool) -> MacWindow? { Simulation.geometryTarget }
    func getProjection(_ orientation: ReplayParent.Orientation) -> CGFloat { orientation == .h ? x : y }
}
extension Array {
    func sortedBy<T: Comparable>(_ key: (Element) -> T) -> Self { sorted { key($0) < key($1) } }
    func filterIsInstance<T>(of: T.Type) -> [T] { compactMap { $0 as? T } }
    func getOrNil(atIndex index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
    func get(wrappingIndex index: Int) -> Element? { isEmpty ? nil : self[(index % count + count) % count] }
}
typealias TilingContainer = ReplayParent
typealias MacApp = ReplayAquaApp
typealias Window = MacWindow
typealias NonLeafTreeNodeObject = ReplayParent
struct CmdEnv {
    static let defaultEnv = Self()
    var windowId: UInt32?
    func withWindowId(_ id: UInt32) -> Self { Self(windowId: id) }
}
@MainActor class CmdIo {
    var stdout: [String] = [], stderr: [String] = []
    private var input: String
    init(input: String = "") { self.input = input }
    func readStdin() -> String { let result = input; input = ""; return result }
    @discardableResult func out(_ value: String) -> String { stdout.append(value); return value }
    @discardableResult func out(_ values: [String]) -> [String] { stdout += values; return values }
    @discardableResult func err(_ value: String) -> String { stderr.append(value); return value }
}
@MainActor final class CmdIoImpl: CmdIo {
    init(stdin: CmdStdin) { super.init(input: stdin.input) }
    static var emptyStdinIgnoringOut: CmdIo { CmdIo() }
}
@MainActor final class CmdIoForwardingStdin: CmdIo {
    let original: CmdIo
    init(stdin: CmdIo) { original = stdin; super.init() }
    override func readStdin() -> String { original.readStdin() }
}
struct BinaryExitCode: Sendable {
    let rawValue: Int32
    static let succ = Self(rawValue: 0)
    static var fail: Self { Self(rawValue: 1) }
    static func fail(_ message: String) -> Self { .fail }
    static func succ(_ message: String) -> Self { .succ }
    static func from(bool: Bool) -> Self { bool ? .succ : .fail }
}
struct Int32ExitCode: Sendable { let rawValue: Int32; static let fail = Self(rawValue: 1); static let succ = Self(rawValue: 0) }
let EXIT_CODE_ZERO: Int32 = 0
let EXIT_CODE_TWO: Int32 = 2
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
@MainActor struct ReplayAwaitCommand: Command {
    let body: () async -> Void
    var shouldResetClosedWindowsCache: Bool { false }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode { await body(); return .succ }
}
@MainActor struct ReplayTileCommand: Command {
    let window: MacWindow
    var shouldResetClosedWindowsCache: Bool { true }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        window.bind(to: window.nodeWorkspace!.rootTilingContainer, adaptiveWeight: 0, index: 0)
        return .succ
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
@MainActor func debugWindowsIfRecording(_ window: MacWindow, _ cm: CancellationMode) async throws {
    try await Simulation.beforeDetection?()
}
@MainActor func restoreClosedWindowsCacheIfNeeded(newlyDetectedWindow: MacWindow) async throws -> Bool { false }
// The production callback loop runs unchanged; only matching and storage are
// memory substitutes, allowing deterministic suspension without native reads.
@MainActor struct WindowDetectedCallback {
    let match: (Window) async -> Bool
    let run: Shell<any Command>
    var checkFurtherCallbacks = false
    func matches(_ window: Window) async -> Bool { await match(window) }
}
@MainActor struct ReplayConfig {
    var enableNormalizationOppositeOrientationForNestedContainers: Bool { Simulation.normalizeOrientation }
    var onWindowDetected: [WindowDetectedCallback] {
        Simulation.callbacks ?? [WindowDetectedCallback(match: { window in
            await Simulation.detectedRule?(window); return true
        }, run: .empty)]
    }
}
@MainActor let config = ReplayConfig()
struct ReplayEvent {
    static func windowDetected(windowId: UInt32, workspace: String?, appBundleId: String?, appName: String?) -> Self { Self() }
}
func broadcastEvent(_ event: ReplayEvent) {}
extension ReplayAquaApp {
    var rawAppBundleId: String? { nil }
    var name: String? { nil }
}
@MainActor final class ReplayGate {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func wait() async {
        if opened { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func open() {
        opened = true
        let pending = waiters; waiters = []
        for continuation in pending { continuation.resume() }
    }
}
struct ReplayValue<T> { var val: T }
struct LayoutCmdArgs {
    enum LayoutDescription: String { case accordion, tiles, horizontal, vertical, h_accordion, v_accordion, h_tiles, v_tiles, tiling, floating }
    var forNextDetectedWindow = false
    var root = false
    var failIfNoop = false
    let toggleBetween: ReplayValue<[LayoutDescription]>
    init(rawArgs: [String], toggleBetween: [LayoutDescription]) { self.toggleBetween = ReplayValue(val: toggleBetween) }
    @MainActor func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> ReplayTarget? { ReplayTarget.resolve(env) }
}
typealias LiveFocus = ReplayFocus
typealias ReplayTarget = ReplayFocus
extension ReplayFocus {
    static func resolve(_ env: CmdEnv) -> Self? {
        guard let id = env.windowId, let window = MacWindow.allWindowsMap[id] else { return nil }
        return Self(windowOrNil: window)
    }
}
enum CardinalDirection { case right; var focusOffset: Int { 1 }; var opposite: Self { self } }
struct FocusCmdArgs {
    enum Target { case direction(CardinalDirection), windowId(UInt32), dfsIndex(Int), dfsRelative(NextPrev) }
    enum NextPrev { case dfsNext, dfsPrev }
    enum BoundariesAction { case stop, fail, wrapAroundTheWorkspace, wrapAroundAllMonitors }
    enum Boundaries { case workspace, allMonitorsOuterFrame }
    var target: Target = .direction(.right)
    var failIfFullscreen = false
    var failIfMacosNativeFullscreen = false
    var floatingAsTiling = false
    var boundariesAction: BoundariesAction = .stop
    var boundaries: Boundaries = .workspace
    @MainActor func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> ReplayTarget? { ReplayTarget.resolve(env) }
}
@MainActor enum ConventionalWindowParentCases {
    case tilingContainer(ReplayParent), floatingWindowsContainer(ReplayParent)
    var tilingContainerOrNil: ReplayParent? { if case .tilingContainer(let p) = self { return p }; return nil }
    var floatingWindowsContainerOrNil: ReplayParent? { if case .floatingWindowsContainer(let p) = self { return p }; return nil }
}
typealias Layout = ReplayParent.Layout
typealias Orientation = ReplayParent.Orientation
struct BindingData { let parent: ReplayParent; let adaptiveWeight: CGFloat; let index: Int }
@MainActor func unbindAndGetBindingDataForNewTilingWindow(_ workspace: Workspace, window: Window?) -> BindingData {
    window?.unbindFromParent()
    return BindingData(parent: workspace.rootTilingContainer, adaptiveWeight: WEIGHT_AUTO, index: INDEX_BIND_LAST)
}
@MainActor extension ReplayAquaApp {
    func getAxUiElementWindowType(_ id: UInt32, _ level: MacOsWindowLevel?, _ cm: CancellationMode) async throws -> AxUiElementWindowType { try await Simulation.awaitType?() ?? .window }
    func getAxWindowsCount(_ cm: CancellationMode) async throws -> Int? { try await Simulation.awaitWindowCount?() ?? 1 }
}
@MainActor extension LayoutCommand {
    func run(_ env: CmdEnv, _ stdin: CmdStdin) async { _ = await Shell<any Command>.cmd(self).run(env, CmdIo()) }
}
struct CmdStdin {
    let input: String
    init(_ input: String) { self.input = input }
    static var emptyStdin: Self { Self("") }
}
extension Array { func singleOrNil() -> Element? { count == 1 ? first : nil }; func orDie() -> Self { self } }
extension Optional { func orDie() -> Wrapped { self! } }
@MainActor func bugPrompt() -> String { "replay error" }
@MainActor func bugPrompt(_ message: String) -> String { message }
@MainActor func windowIsntPartOfTree(_ window: Window) -> String { "unbound" }
enum ReplayLayoutReason { case standard, macos(prevParentKind: ReplayParentCase) }
@MainActor let macosPopupWindowsContainer = ReplayParent(Workspace("popup"), floating: false)
@MainActor let macosMinimizedWindowsContainer = ReplayParent(Workspace("minimized"), floating: false)
@MainActor func exitMacOsNativeUnconventionalState(window: Window, prevParentKind: ReplayParentCase, workspace: Workspace, _ cm: CancellationMode) async throws {
    try await window.relayoutWindow(on: workspace, cm)
}
struct MacosNativeFullscreenCmdArgs {
    enum Toggle { case on, off, toggle }
    var toggle: Toggle = .on
    var failIfNoop = false
    @MainActor func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> ReplayTarget? { ReplayTarget.resolve(env) }
}
struct MacosNativeMinimizeCmdArgs {
    @MainActor func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> ReplayTarget? { ReplayTarget.resolve(env) }
}
struct CloseCmdArgs {
    var quitIfLastWindow = true
    @MainActor func resolveTargetOrReportError(_ env: CmdEnv, _ io: CmdIo) -> ReplayTarget? { ReplayTarget.resolve(env) }
}
extension Bool {
    @MainActor func andAsync(_ body: @MainActor @Sendable () async -> Bool) async -> Bool { self ? await body() : false }
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
@MainActor func refreshModel_nonCancellable() async { await Simulation.commandRefresh?() }

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
