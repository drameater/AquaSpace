import AppKit

// Model-tree side of native-tab tracking. The detector in nativeTabs.swift decides
// which windows are tabs of one slot; these functions move that slot in the tree.

/// Read the binding at the instant of replacement: normalization may have changed the parent.
@MainActor
func transferNativeTabState(from previous: Window, to current: Window) {
    if current.isBound { current.unbindFromParent() }
    let binding = previous.unbindFromParent()
    current.bind(to: binding.parent, adaptiveWeight: binding.adaptiveWeight, index: binding.index)
    current.lastFloatingSize = previous.lastFloatingSize
    current.isFullscreen = previous.isFullscreen
    current.noOuterGapsInFullscreen = previous.noOuterGapsInFullscreen
    current.layoutReason = previous.layoutReason
    current.lastAppliedLayoutVirtualRect = previous.lastAppliedLayoutVirtualRect
    current.lastAppliedLayoutPhysicalRect = previous.lastAppliedLayoutPhysicalRect
    replaceNativeTabFocusId(previous.windowId, current.windowId)
}

@MainActor
func absorbNativeTabState(from previous: Window, into current: Window) {
    if focus.windowOrNil === previous { _ = current.focusWindow() }
    replaceNativeTabFocusId(previous.windowId, current.windowId)
    previous.unbindFromParent()
}

/// Recovery can identify a split after its window was registered elsewhere.
/// Keep its existing floating/fullscreen state and avoid new-window callbacks.
@MainActor
func inheritNativeTabWorkspace(_ window: Window, on workspace: Workspace) {
    guard window.nodeWorkspace !== workspace else { return }
    _ = moveWindowToWorkspace(window, workspace, CmdIoImpl.emptyStdinIgnoringOut, focusFollowsWindow: false, failIfNoop: false)
}
