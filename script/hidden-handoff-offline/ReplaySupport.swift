import Foundation

// Local types intentionally shadow the real CG APIs. Only synthetic dictionaries
// enter the actual production cache/classifier; there is no live desktop access.
// The AX fakes and CGWindowID come from script/offline-shared.
typealias CFDictionary = NSDictionary
typealias CFNumber = NSNumber
let kCGNullWindowID: UInt32 = 0
let kCGWindowLayer = "layer"
let kCGWindowNumber = "number"
let kCGWindowOwnerPID = "pid"
struct CGWindowListOption: OptionSet, ExpressibleByArrayLiteral, Sendable {
    let rawValue: UInt32
    static let optionAll: Self = []
    static let optionOnScreenOnly = Self(rawValue: 1)
    static let excludeDesktopElements = Self(rawValue: 16)
    init(rawValue: UInt32) { self.rawValue = rawValue }
    init(arrayLiteral elements: Self...) { self = elements.reduce(Self(rawValue: 0)) { $0.union($1) } }
}
struct ReplayWindow: Sendable {
    var id: UInt32
    var pid: Int32
    var layer: Int
    var onScreen: Bool
}
// The production level cache reads WindowServer off the main actor, so the
// synthetic server state is lock-protected rather than main-actor isolated.
final class ReplayServerState: @unchecked Sendable {
    private let lock = NSLock()
    private var rows: [ReplayWindow] = []
    private var available = true
    private var queries: [CGWindowListOption] = []
    func with<T>(_ body: (inout [ReplayWindow], inout Bool, inout [CGWindowListOption]) -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body(&rows, &available, &queries)
    }
}
let replayServerState = ReplayServerState()
@MainActor enum ReplayServer {
    static var available: Bool {
        get { replayServerState.with { _, available, _ in available } }
        set { replayServerState.with { _, available, _ in available = newValue } }
    }
    static var queries: [CGWindowListOption] { replayServerState.with { _, _, queries in queries } }
    static func reset(_ rows: [ReplayWindow]) {
        replayServerState.with { storedRows, available, queries in
            storedRows = rows
            available = true
            queries = []
        }
        resetReplayCache()
    }
}
func CGWindowListCopyWindowInfo(_ options: CGWindowListOption, _ relative: CGWindowID) -> Any? {
    let rows: [ReplayWindow]? = replayServerState.with { rows, available, queries in
        queries.append(options)
        guard available else { return nil }
        return rows.filter { !options.contains(.optionOnScreenOnly) || $0.onScreen }
    }
    return rows?.map { [kCGWindowLayer: $0.layer, kCGWindowNumber: $0.id, kCGWindowOwnerPID: $0.pid] as [String: Any] }
}

enum Json {
    case string(String), int(Int), null
}
enum NSApplication {
    enum ActivationPolicy { case regular, accessory, prohibited }
}
