import Foundation

// Memory-only substitutes for the Accessibility API, shared by the offline replays.
// Nothing here calls ApplicationServices. The names and value types mirror
// Sources/AppBundle/util/accessibility.swift, so production code compiles unchanged.
// When a production attribute changes its type, change it here once.
// The replays also compile the production AxUiElementMock.swift.

typealias CGWindowID = UInt32

protocol ReadableAttr: Sendable {
    associatedtype T
    var key: String { get }
}
struct ReplayAttribute<T>: ReadableAttr { let key: String }

/// A tree node with in-memory attributes. `owner` is the window ID that
/// `containingWindowId()` reports; nil models an element with no window.
final class AXUIElement: NSObject, AxUiElementMock {
    var owner: UInt32?
    var attributes: [String: Any] = [:]
    init(owner: UInt32? = nil) { self.owner = owner }
    func containingWindowId() -> CGWindowID? { owner }
    func get<Attr: ReadableAttr>(_ attr: Attr) -> Attr.T? { attributes[attr.key] as? Attr.T }
    func set<Attr: ReadableAttr>(_ attr: Attr, _ value: Attr.T) { attributes[attr.key] = value }
}
typealias WindowIdAndAxUiElement = (windowId: UInt32, ax: AXUIElement)
typealias WindowIdAndAxUiElementMock = (windowId: UInt32, ax: any AxUiElementMock)

enum Ax {
    static let childrenAttr = ReplayAttribute<[AXUIElement]>(key: "AXChildren")
    static let tabsAttr = ReplayAttribute<[AXUIElement]>(key: "AXTabs")
    static let titleAttr = ReplayAttribute<String>(key: "AXTitle")
    static let roleAttr = ReplayAttribute<String>(key: "AXRole")
    static let subroleAttr = ReplayAttribute<String>(key: "AXSubrole")
    static let identifierAttr = ReplayAttribute<String>(key: "AXIdentifier")
    static let enabledAttr = ReplayAttribute<Bool>(key: "AXEnabled")
    static let isFocused = ReplayAttribute<Bool>(key: "AXFocused")
    static let isMainAttr = ReplayAttribute<Bool>(key: "AXMain")
    static let sizeAttr = ReplayAttribute<CGSize>(key: "AXSize")
    static let topLeftCornerAttr = ReplayAttribute<CGPoint>(key: "AXPosition")
    static let windowsAttr = ReplayAttribute<[WindowIdAndAxUiElement]>(key: "AXWindows")
    static let focusedWindowAttr = ReplayAttribute<WindowIdAndAxUiElementMock>(key: "AXFocusedWindow")
    static let closeButtonAttr = ReplayAttribute<any AxUiElementMock>(key: "AXCloseButton")
    static let fullscreenButtonAttr = ReplayAttribute<any AxUiElementMock>(key: "AXFullScreenButton")
    static let zoomButtonAttr = ReplayAttribute<any AxUiElementMock>(key: "AXZoomButton")
    static let minimizeButtonAttr = ReplayAttribute<any AxUiElementMock>(key: "AXMinimizeButton")
}
let kAXWindowRole = "AXWindow"
let kAXTabGroupRole = "AXTabGroup"
let kAXStandardWindowSubrole = "AXStandardWindow"
let kAXDialogSubrole = "AXDialog"
let kAXFloatingWindowSubrole = "AXFloatingWindow"

func AXUIElementCreateApplication(_ pid: Int32) -> AXUIElement { AXUIElement(owner: nil) }

enum ReplayAXError { case success, failure }
/// Reads the "pid" attribute that a test sets; fails when the test sets none.
func AXUIElementGetPid(_ element: AXUIElement, _ pid: inout Int32) -> ReplayAXError {
    guard let value = element.attributes["pid"] as? Int32 else { return .failure }
    pid = value
    return .success
}
