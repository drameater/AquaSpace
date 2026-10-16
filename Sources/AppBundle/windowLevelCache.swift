import CoreGraphics
import Foundation

/// IDs only; no titles, images, or screen-recording permission are needed. Include inactive
/// native Spaces and background tabs so becoming AX-visible is not mistaken for creation.
func getWindowServerWindowIdsByPid() -> [Int32: Set<UInt32>]? {
    guard let rows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    var result: [Int32: Set<UInt32>] = [:]
    for row in rows {
        guard let pid = row[kCGWindowOwnerPID as String] as? Int32,
              let windowId = row[kCGWindowNumber as String] as? UInt32
        else { continue }
        result[pid, default: []].insert(windowId)
    }
    return result
}

/// Strict ownership inventory for delayed restoration. Malformed/partial rows
/// revoke pending intent instead of authorizing a later numeric-ID match.
func getRestorationWindowOwners() -> [UInt32: Set<Int32>]? {
    guard let rows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    var result: [UInt32: Set<Int32>] = [:]
    for row in rows {
        guard let pid = row[kCGWindowOwnerPID as String] as? Int32, pid > 0,
              let id = row[kCGWindowNumber as String] as? UInt32, id > 0 else { return nil }
        result[id, default: []].insert(pid)
    }
    return result
}

@MainActor
private var cache: [UInt32: MacOsWindowLevel] = [:]

@MainActor
func getWindowLevel(for windowId: UInt32) -> MacOsWindowLevel? {
    if let existing = cache[windowId] { return existing }

    var result: [UInt32: MacOsWindowLevel] = [:]
    // Hidden apps and windows on inactive native Spaces still need their real
    // layer at startup. An on-screen-only scan gives them nil, which makes the
    // strict browser classifier mistake ordinary windows for unbound popups.
    let options = CGWindowListOption(arrayLiteral: .excludeDesktopElements, .optionAll)
    guard let cfArray = CGWindowListCopyWindowInfo(options, CGWindowID(0)) as? [CFDictionary] else { return nil }
    for elem in cfArray {
        let dict = elem as NSDictionary

        guard let _windowLayer = dict[kCGWindowLayer] else { continue }
        let windowLayer = ((_windowLayer as! CFNumber) as NSNumber).intValue

        guard let _windowId = dict[kCGWindowNumber] else { continue }
        let windowId = ((_windowId as! CFNumber) as NSNumber).uint32Value

        result[windowId] = .new(windowLevel: windowLayer)
    }
    cache = result
    return result[windowId]
}

enum MacOsWindowLevel: Sendable, Equatable {
    case normalWindow
    case alwaysOnTopWindow
    case unknown(windowLevel: Int)

    static func new(windowLevel: Int) -> MacOsWindowLevel {
        switch windowLevel {
            case 0: .normalWindow
            case 3: .alwaysOnTopWindow
            default: .unknown(windowLevel: windowLevel)
        }
    }

    static func fromJson(_ json: Json) -> MacOsWindowLevel? {
        switch json {
            case .string("normalWindow"): .normalWindow
            case .string("alwaysOnTopWindow"): .alwaysOnTopWindow
            case .int(let int): .new(windowLevel: Int(exactly: int).orDie())
            default: nil
        }
    }

    func toJson() -> Json {
        switch self {
            case .normalWindow: .string("normalWindow")
            case .alwaysOnTopWindow: .string("alwaysOnTopWindow")
            case .unknown(let layerNumber): .int(layerNumber)
        }
    }
}
