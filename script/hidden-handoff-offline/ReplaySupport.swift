import Foundation

// Local types intentionally shadow the real CG APIs. Only synthetic dictionaries
// enter the actual production cache/classifier; there is no live desktop access.
typealias CGWindowID = UInt32
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
@MainActor enum ReplayServer {
    static var rows: [ReplayWindow] = []
    static var available = true
    static var queries: [CGWindowListOption] = []
    static func reset(_ rows: [ReplayWindow]) {
        self.rows = rows
        available = true
        queries = []
        resetReplayCache()
    }
}
// Both production readers are main-actor consumers in this replay. The original
// ID reader has no actor annotation, so its stub may only run on that executor.
func CGWindowListCopyWindowInfo(_ options: CGWindowListOption, _ relative: CGWindowID) -> Any? {
    let rows: [ReplayWindow]? = MainActor.assumeIsolated {
        ReplayServer.queries.append(options)
        guard ReplayServer.available else { return nil }
        return ReplayServer.rows.filter { !options.contains(.optionOnScreenOnly) || $0.onScreen }
    }
    return rows?.map { [kCGWindowLayer: $0.layer, kCGWindowNumber: $0.id, kCGWindowOwnerPID: $0.pid] as [String: Any] }
}

enum Json {
    case string(String), int(Int), null
}
extension Optional {
    func orDie() -> Wrapped { guard let value = self else { fatalError("Unexpected nil") }; return value }
}
enum NSApplication {
    enum ActivationPolicy { case regular, accessory, prohibited }
}
protocol ReadableAttr { associatedtype T; var key: String { get } }
struct ReplayAttr<T>: ReadableAttr { let key: String }
final class AXUIElement: AxUiElementMock {
    var id: UInt32?
    var attributes: [String: Any] = [:]
    init(_ id: UInt32? = nil) { self.id = id }
    func containingWindowId() -> UInt32? { id }
    func get<Attr: ReadableAttr>(_ attr: Attr) -> Attr.T? { attributes[attr.key] as? Attr.T }
    func put<T>(_ attr: ReplayAttr<T>, _ value: T) { attributes[attr.key] = value }
}
typealias WindowIdAndAxUiElementMock = (windowId: UInt32, ax: any AxUiElementMock)
enum Ax {
    static let titleAttr = ReplayAttr<String>(key: "title")
    static let identifierAttr = ReplayAttr<String>(key: "identifier")
    static let subroleAttr = ReplayAttr<String>(key: "subrole")
    static let enabledAttr = ReplayAttr<Bool>(key: "enabled")
    static let isFocused = ReplayAttr<Bool>(key: "focused")
    static let isMainAttr = ReplayAttr<Bool>(key: "main")
    static let focusedWindowAttr = ReplayAttr<WindowIdAndAxUiElementMock>(key: "focusedWindow")
    static let closeButtonAttr = ReplayAttr<any AxUiElementMock>(key: "close")
    static let fullscreenButtonAttr = ReplayAttr<any AxUiElementMock>(key: "fullscreen")
    static let zoomButtonAttr = ReplayAttr<any AxUiElementMock>(key: "zoom")
    static let minimizeButtonAttr = ReplayAttr<any AxUiElementMock>(key: "minimize")
}
let kAXStandardWindowSubrole = "AXStandardWindow"
let kAXDialogSubrole = "AXDialog"
let kAXFloatingWindowSubrole = "AXFloatingWindow"
