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
@MainActor struct ReplayFocus { var windowOrNil: MacWindow? }
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
@MainActor final class Workspace {
    let name: String
    lazy var rootTilingContainer = ReplayParent(self, floating: false)
    lazy var floatingContainer = ReplayParent(self, floating: true)
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
enum ReplayParentCase { case tilingContainer, floatingWindowsContainer, native, popup }
@MainActor final class MacWindow {
    static var allWindowsMap: [UInt32: MacWindow] = [:]
    let windowId: UInt32
    let macApp: ReplayAquaApp
    var parent: ReplayParent?
    var isFullscreen = false
    var specialParent: ReplayParentCase?
    var nodeWorkspace: Workspace? { parent?.workspace }
    var isBound: Bool { parent != nil }
    var windowParentCases: ReplayParentCase { specialParent ?? (parent?.floating == true ? .floatingWindowsContainer : .tilingContainer) }
    init(_ id: UInt32, app: ReplayAquaApp) {
        windowId = id; macApp = app; parent = Workspace.get(byName: "default").rootTilingContainer
    }
    func bindAsFloatingWindow(to workspace: Workspace) { bind(to: workspace.floatingContainer, adaptiveWeight: 0, index: 0) }
    func bind(to parent: ReplayParent, adaptiveWeight: Int, index: Int) {
        Simulation.mutations.append(windowId); self.parent = parent
    }
}

@MainActor enum NSWorkspace {
    static let shared = ReplayWorkspace()
}
@MainActor final class ReplayWorkspace {
    struct App { let processIdentifier: Int32 }
    var frontmostApplication: App? { Simulation.foreground.map { App(processIdentifier: $0) } }
}
