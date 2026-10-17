import AppKit
import Common

struct FocusCommand: Command {
    let args: FocusCmdArgs
    /*conforms*/ let shouldResetClosedWindowsCache = false

    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        guard WindowDiscoveryInitialization.mayRunAutomaticFocus else { return .fail }
        guard let target = args.resolveTargetOrReportError(env, io) else { return .fail }
        // Accepted explicit focus intent supersedes older automatic focus even
        // when native reads suspend or a workspace boundary leaves focus as-is.
        WindowDiscoveryInitialization.recordUserFocus()
        let continuation = target.windowOrNil.map { WindowCommandContinuation($0) }
        if let window = target.windowOrNil, await shouldFailBecauseFullscreen_nonCancellable(
            window: window,
            failIfFullscreen: args.failIfFullscreen,
            failIfMacosNativeFullscreen: args.failIfMacosNativeFullscreen,
        ) {
            return .fail
        }
        guard WindowDiscoveryInitialization.mayRunAutomaticFocus,
              continuation?.mayContinue != false else { return .fail }
        // todo bug: floating windows break mru
        let floatingWindows = args.floatingAsTiling ? await makeFloatingWindowsSeenAsTiling(workspace: target.workspace) : []
        defer {
            if args.floatingAsTiling {
                restoreFloatingWindows(floatingWindows: floatingWindows, workspace: target.workspace)
            }
        }

        guard WindowDiscoveryInitialization.mayRunAutomaticFocus,
              continuation?.mayContinue != false else { return .fail }
        switch args.target {
            case .direction(let direction):
                let window = target.windowOrNil
                if let (parent, ownIndex) = window?.closestParent(hasChildrenInDirection: direction, withLayout: nil) {
                    guard let windowToFocus = parent.children[ownIndex + direction.focusOffset]
                        .findLeafWindowRecursive(snappedTo: direction.opposite) else { return .fail(io.err(bugPrompt())) }
                    return .from(bool: windowToFocus.focusWindow())
                } else {
                    return hitWorkspaceBoundaries(target, io, args, direction)
                }
            case .windowId(let windowId):
                if let windowToFocus = Window.get(byId: windowId) {
                    return .from(bool: windowToFocus.focusWindow())
                } else {
                    return .fail(io.err("Can't find window with ID \(windowId)"))
                }
            case .dfsIndex(let dfsIndex):
                if let windowToFocus = target.workspace.rootTilingContainer.allLeafWindowsRecursive.getOrNil(atIndex: Int(dfsIndex)) {
                    return .from(bool: windowToFocus.focusWindow())
                } else {
                    return .fail(io.err("Can't find window with DFS index \(dfsIndex)"))
                }
            case .dfsRelative(let nextPrev):
                let windows = target.workspace.rootTilingContainer.allLeafWindowsRecursive
                guard let currentIndex = windows.firstIndex(where: { $0 == target.windowOrNil }) else {
                    return .fail
                }
                var targetIndex = switch nextPrev {
                    case .dfsNext: currentIndex + 1
                    case .dfsPrev: currentIndex - 1
                }
                if !(0 ..< windows.count).contains(targetIndex) {
                    switch args.boundariesAction {
                        case .stop: return .succ
                        case .fail: return .fail
                        case .wrapAroundTheWorkspace: targetIndex = (targetIndex + windows.count) % windows.count
                        case .wrapAroundAllMonitors: return .fail(io.err(bugPrompt("Must be discarded by args parser")))
                    }
                }
                return .from(bool: windows[targetIndex].focusWindow())
        }
    }
}

@MainActor private func hitWorkspaceBoundaries(
    _ target: LiveFocus,
    _ io: CmdIo,
    _ args: FocusCmdArgs,
    _ direction: CardinalDirection,
) -> BinaryExitCode {
    switch args.boundaries {
        case .workspace:
            return switch args.boundariesAction {
                case .stop: .succ
                case .fail: .fail
                case .wrapAroundTheWorkspace: wrapAroundTheWorkspace(target, io, direction)
                case .wrapAroundAllMonitors: .fail(io.err("Must be discarded by args parser"))
            }
        case .allMonitorsOuterFrame:
            let currentMonitor = target.workspace.workspaceMonitor
            guard let (monitors, index) = currentMonitor.findRelativeMonitor(inDirection: direction) else {
                return .fail(io.err(bugPrompt("Should never happen. Can't find the current monitor")))
            }

            if let targetMonitor = monitors.getOrNil(atIndex: index) {
                return .from(bool: targetMonitor.activeWorkspace.focusWorkspace())
            } else {
                guard let wrapped = monitors.get(wrappingIndex: index) else { return .fail(io.err(bugPrompt("\(index) \(monitors)"))) }
                return hitAllMonitorsOuterFrameBoundaries(target, io, args, direction, wrapped)
            }
    }
}

@MainActor private func hitAllMonitorsOuterFrameBoundaries(
    _ target: LiveFocus,
    _ io: CmdIo,
    _ args: FocusCmdArgs,
    _ direction: CardinalDirection,
    _ wrappedMonitor: MonitorInfo,
) -> BinaryExitCode {
    switch args.boundariesAction {
        case .stop:
            return .succ
        case .fail:
            return .fail
        case .wrapAroundTheWorkspace:
            return wrapAroundTheWorkspace(target, io, direction)
        case .wrapAroundAllMonitors:
            wrappedMonitor.activeWorkspace.findLeafWindowRecursive(snappedTo: direction.opposite)?.markAsMostRecentChild()
            return .from(bool: wrappedMonitor.activeWorkspace.focusWorkspace())
    }
}

@MainActor private func wrapAroundTheWorkspace(_ target: LiveFocus, _ io: CmdIo, _ direction: CardinalDirection) -> BinaryExitCode {
    guard let windowToFocus = target.workspace.findLeafWindowRecursive(snappedTo: direction.opposite) else {
        return .fail(io.err(noWindowIsFocused))
    }
    return .from(bool: windowToFocus.focusWindow())
}

@MainActor private func makeFloatingWindowsSeenAsTiling(workspace: Workspace) async -> [FloatingWindowData] {
    let mruBefore = workspace.mostRecentWindowRecursive
    let mruContinuation = mruBefore.map { WindowCommandContinuation($0, optionalProbe: true) }
    defer {
        if WindowDiscoveryInitialization.mayRunAutomaticFocus, mruContinuation?.mayContinue != false {
            mruBefore?.markAsMostRecentChild()
        }
    }
    var candidates: [(Window, CGPoint, TilingContainer, Int, WindowCommandContinuation)] = []
    for window in workspace.floatingWindows {
        let continuation = WindowCommandContinuation(window, optionalProbe: true)
        // todo bug: we shouldn't access ax api here. What if the window was moved but it wasn't committed to ax yet?
        guard let center = try? await window.getCenter(.nonCancellable) else { continue }
        guard WindowDiscoveryInitialization.mayRunAutomaticFocus else { return [] }
        guard continuation.mayContinue, window.isFloating, window.nodeWorkspace === workspace else { continue }

        let tilingParent: TilingContainer
        let index: Int
        if let target = center.coerce(in: workspace.workspaceMonitor.visibleRectPaddedByOuterGaps)?
            .findWindowRecursively(in: workspace.rootTilingContainer, virtual: true, fullscreenCoversAll: false)
        {
            let targetContinuation = WindowCommandContinuation(target, optionalProbe: true)
            guard let targetCenter = try? await target.getCenter(.nonCancellable) else { continue }
            guard WindowDiscoveryInitialization.mayRunAutomaticFocus else { return [] }
            guard continuation.mayContinue, window.isFloating, window.nodeWorkspace === workspace else { continue }
            guard targetContinuation.targetIsUnchanged,
                  let _tilingParent = target.parent as? TilingContainer,
                  _tilingParent.nodeWorkspace === workspace else { continue }
            tilingParent = _tilingParent
            index = switch tilingParent.layout {
                case .tiles:
                    center.getProjection(tilingParent.orientation) >= targetCenter.getProjection(tilingParent.orientation)
                        ? target.ownIndex.orDie() + 1
                        : target.ownIndex.orDie()
                case .accordion:
                    center.getProjection(tilingParent.orientation) >= targetCenter.getProjection(tilingParent.orientation)
                        ? tilingParent.children.count
                        : 0
            }
        } else {
            index = 0
            tilingParent = workspace.rootTilingContainer
        }

        candidates.append((window, center, tilingParent, index, continuation))
    }
    // Complete every read before temporarily unbinding any window. An abort
    // during a later read must not leave earlier candidates outside the tree.
    guard WindowDiscoveryInitialization.mayRunAutomaticFocus else { return [] }
    let valid = candidates.filter { window, _, parent, index, continuation in
        continuation.mayContinue && window.isFloating && window.nodeWorkspace === workspace &&
            parent.nodeWorkspace === workspace && index >= 0 && index <= parent.children.count
    }
    let prepared = valid.map { window, center, parent, index, _ in
        let data = window.unbindFromParent()
        return FloatingWindowData(window: window, center: center, tilingParent: parent,
            adaptiveWeight: data.adaptiveWeight, index: index, userMutationToken: window.userMutationToken)
    }
    var floatingWindows: [FloatingWindowData] = prepared.sortedBy { $0.center.getProjection($0.tilingParent.orientation) }.reversed()

    for index in floatingWindows.indices { // Make floating windows be seen as tiling
        let floating = floatingWindows[index]
        floating.window.bind(to: floating.tilingParent, adaptiveWeight: 1, index: floating.index)
        floatingWindows[index].userMutationToken = floating.window.userMutationToken
    }
    return floatingWindows
}

@MainActor private func restoreFloatingWindows(floatingWindows: [FloatingWindowData], workspace: Workspace) {
    let mruBefore = workspace.mostRecentWindowRecursive
    defer {
        if WindowDiscoveryInitialization.mayRunAutomaticFocus { mruBefore?.markAsMostRecentChild() }
    }
    for floating in floatingWindows {
        // Undo only our untouched temporary binding, preserving newer user choices.
        guard Window.get(byId: floating.window.windowId) === floating.window,
              floating.window.parent === floating.tilingParent,
              floating.window.userMutationToken == floating.userMutationToken else { continue }
        floating.window.bind(to: workspace.floatingWindowsContainer, adaptiveWeight: floating.adaptiveWeight, index: INDEX_BIND_LAST)
    }
}

private struct FloatingWindowData {
    let window: Window
    let center: CGPoint

    let tilingParent: TilingContainer
    let adaptiveWeight: CGFloat
    let index: Int
    var userMutationToken: UUID
}

extension TreeNode {
    @MainActor
    func findLeafWindowRecursive(snappedTo direction: CardinalDirection) -> Window? {
        switch nodeCases {
            case .workspace(let workspace):
                return workspace.rootTilingContainer.findLeafWindowRecursive(snappedTo: direction)
            case .window(let window):
                return window
            case .tilingContainer(let container):
                if direction.orientation == container.orientation {
                    return (direction.isPositive ? container.children.last : container.children.first)?
                        .findLeafWindowRecursive(snappedTo: direction)
                } else {
                    return mostRecentChild?.findLeafWindowRecursive(snappedTo: direction)
                }
            case .macosMinimizedWindowsContainer, .macosFullscreenWindowsContainer,
                 .macosPopupWindowsContainer, .macosHiddenAppsWindowsContainer,
                 .floatingWindowsContainer:
                die("Impossible")
        }
    }
}
