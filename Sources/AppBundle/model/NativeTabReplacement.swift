import CoreGraphics

/// A native tab switch replaces the AX window, but keeps one workspace/layout slot.
struct NativeTabWindowSnapshot: Sendable {
    let windowId: UInt32
    let frame: CGRect?
    let tabCount: Int?
    var isNewWindow: Bool = false
    var isRecentWindowCreation: Bool = false
}

struct NativeTabUpdate: Sendable {
    var replacements: [UInt32: UInt32] = [:] // Selected ID -> previous representative.
    var absorptions: [UInt32: UInt32] = [:] // Retired group representative -> survivor.
    var inheritances: [UInt32: UInt32] = [:] // Newly exposed split window -> former owner.
    var exposedWindowIds: Set<UInt32> = []
}

func nativeTabFramesMatch(_ first: CGRect?, _ second: CGRect?) -> Bool {
    guard let first, let second, first.width > 0, first.height > 0 else { return false }
    return abs(first.minX - second.minX) <= 1 && abs(first.minY - second.minY) <= 1 &&
        abs(first.width - second.width) <= 1 && abs(first.height - second.height) <= 1
}

/// Neither AXWindows absence nor equal geometry alone establishes tab membership.
func isNativeTabReplacement(
    previous: NativeTabWindowSnapshot,
    current: NativeTabWindowSnapshot,
    exposedWindowIds: Set<UInt32>,
    sharesTabBar: Bool,
    previousWindowClosed: Bool = false,
) -> Bool {
    guard !exposedWindowIds.isEmpty,
          previous.windowId != current.windowId,
          !exposedWindowIds.contains(previous.windowId),
          exposedWindowIds.contains(current.windowId)
    else { return false }
    if sharesTabBar { return true }
    // First tab creation has no old bar. Closing one of two tabs removes the bar entirely.
    // In either case, require the same physical frame and an unambiguous handoff.
    let firstTab = (current.isNewWindow || current.isRecentWindowCreation) && !previousWindowClosed && previous.tabCount == nil && (current.tabCount ?? 0) >= 2
    let finalTab = !current.isNewWindow && previousWindowClosed && previous.tabCount == 2 && current.tabCount == nil
    guard firstTab || finalTab,
          let previousFrame = previous.frame, let currentFrame = current.frame,
          previousFrame.width > 0, previousFrame.height > 0
    else { return false }
    return abs(previousFrame.minX - currentFrame.minX) <= 1 &&
        abs(previousFrame.minY - currentFrame.minY) <= 1 &&
        abs(previousFrame.width - currentFrame.width) <= 1 &&
        abs(previousFrame.height - currentFrame.height) <= 1
}
