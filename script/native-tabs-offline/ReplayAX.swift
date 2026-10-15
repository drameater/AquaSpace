import Foundation

// These are memory-only substitutes, not wrappers around ApplicationServices.
struct ReplayAttribute<Value>: Sendable { let name: String }
final class AXUIElement: NSObject {
    var owner: UInt32?
    var attributes: [String: Any] = [:]
    init(owner: UInt32?) { self.owner = owner }
    func containingWindowId() -> UInt32? { owner }
    func get<T>(_ attribute: ReplayAttribute<T>) -> T? { attributes[attribute.name] as? T }
    func set<T>(_ attribute: ReplayAttribute<T>, _ value: T) { attributes[attribute.name] = value }
}
enum Ax {
    static let roleAttr = ReplayAttribute<String>(name: "role")
    static let subroleAttr = ReplayAttribute<String>(name: "subrole")
    static let topLeftCornerAttr = ReplayAttribute<CGPoint>(name: "position")
    static let sizeAttr = ReplayAttribute<CGSize>(name: "size")
    static let childrenAttr = ReplayAttribute<[AXUIElement]>(name: "children")
    static let tabsAttr = ReplayAttribute<[AXUIElement]>(name: "tabs")
}
let kAXWindowRole = "AXWindow"
let kAXStandardWindowSubrole = "AXStandardWindow"
let kAXTabGroupRole = "AXTabGroup"
let isLeftMouseButtonDown = false
typealias WindowIdAndAxUiElement = (windowId: UInt32, ax: AXUIElement)

final class RunLoopJob {
    var cancelled = false
    var checksUntilCancellation: Int?
    func checkCancellation() throws {
        if cancelled || checksUntilCancellation == 0 { throw CancellationError() }
        if let count = checksUntilCancellation { checksUntilCancellation = count - 1 }
    }
}

func AXUIElementCreateApplication(_ pid: Int32) -> AXUIElement { AXUIElement(owner: nil) }

// No CG calls: initialization starts with an empty synthetic server inventory.
func getWindowServerWindowIdsByPid() -> [Int32: Set<UInt32>]? { [getpid(): []] }

// Task-local deterministic time keeps test cases independent and avoids sleeps.
enum ReplayTime {
    @TaskLocal static var elapsed: Duration = .zero
}
struct ContinuousClock {
    struct Instant {
        let elapsed: Duration
        func duration(to other: Instant) -> Duration { other.elapsed - elapsed }
    }
    var now: Instant { Instant(elapsed: ReplayTime.elapsed) }
}
