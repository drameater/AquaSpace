import AppKit

/// Confined to the application's AX thread. Acknowledgement comes after the main actor
/// transfers the slot, so cancellation cannot consume a tab switch prematurely.
final class AxNativeTabState {
    private let pid: Int32
    private var knownWindowIds: Set<UInt32>?
    private var recentCreations: [UInt32: ContinuousClock.Instant] = [:]
    var observations: [UInt32: AxNativeTabObservation] = [:]
    private(set) var pendingReplacements: [UInt32: UInt32] = [:]
    private(set) var exposedWindowIds: Set<UInt32> = []
    private(set) var representatives: [UInt32: UInt32] = [:]
    private var pendingAbsorptions: [UInt32: UInt32] = [:]
    private var pendingInheritances: [UInt32: UInt32] = [:]
    private struct Recovery {
        let started: ContinuousClock.Instant
        var observations: [UInt32: AxNativeTabObservation]
    }
    private var recovery: Recovery?

    var update: NativeTabUpdate {
        NativeTabUpdate(replacements: pendingReplacements, absorptions: pendingAbsorptions, inheritances: pendingInheritances, exposedWindowIds: exposedWindowIds)
    }
    var inactiveWindowIds: Set<UInt32> { Set(representatives.keys).subtracting(exposedWindowIds) }

    init(pid: Int32 = getpid()) {
        self.pid = pid
        knownWindowIds = getWindowServerWindowIdsByPid().map { $0[pid] ?? [] }
    }

    func reconcile(_ liveWindows: [WindowIdAndAxUiElement]?, _ job: RunLoopJob, isAppHidden: Bool, windowServerIds: Set<UInt32>? = nil) throws -> [UInt32: UInt32] {
        try job.checkCancellation()
        guard let liveWindows, !liveWindows.isEmpty, !isAppHidden, !isLeftMouseButtonDown else {
            recovery = nil
            return pendingReplacements
        }
        let serverIds = windowServerIds ?? getWindowServerWindowIdsByPid().map { $0[pid] ?? [] }
        let now = ContinuousClock().now
        var births = recentCreations.filter { $0.value.duration(to: now) < .seconds(2) }
        if let knownWindowIds, let serverIds {
            for id in serverIds.subtracting(knownWindowIds) { births[id] = now }
        }
        var current: [UInt32: AxNativeTabObservation] = [:]
        for (id, window) in liveWindows {
            guard window.get(Ax.roleAttr) == kAXWindowRole,
                  window.get(Ax.subroleAttr) == kAXStandardWindowSubrole
            else { continue }
            let isNew = knownWindowIds.map { !$0.contains(id) && serverIds?.contains(id) == true } == true
            current[id] = try AxNativeTabObservation.read(id, window, job, isNewWindow: isNew, isRecentWindowCreation: births[id] != nil)
        }
        // AXWindows, native bars and geometry can settle in different reads. Keep
        // the pre-transition counts/eligibility briefly, without relaxing matching.
        let retry = recovery.flatMap { $0.started.duration(to: now) < .seconds(2) ? $0 : nil }
        // An unclassified newly exposed group must not disappear from the set
        // of competing sources on retry. Its disappearance starts a new epoch;
        // there is no safe attribution for the preceding partial transition.
        let lostProvisional = retry.map {
            !Set(observations.keys).intersection(exposedWindowIds)
                .subtracting($0.observations.keys).subtracting(current.keys).isEmpty
        } == true
        let history = lostProvisional ? current : retry?.observations ?? observations
        var previous = history
        for oldId in previous.keys where current[oldId] == nil {
            if let cached = previous[oldId], let window = cached.window, window.containingWindowId() != nil {
                let refreshed = try AxNativeTabObservation.read(oldId, window, job)
                if cached.tabBar != nil, refreshed.tabBar == nil {
                    // The selected window now owns the shared bar. Keep its old AX identity.
                    let snapshot = NativeTabWindowSnapshot(windowId: oldId, frame: refreshed.snapshot.frame, tabCount: cached.snapshot.tabCount)
                    previous[oldId] = AxNativeTabObservation(snapshot: snapshot, tabBar: cached.tabBar, tabs: cached.tabs, window: window)
                } else {
                    // First tab creation: the previous snapshot predates the native bar.
                    previous[oldId] = refreshed
                }
            } else if previous[oldId]?.tabBar == nil {
                // An ordinary closed window cannot establish a native tab group.
                previous.removeValue(forKey: oldId)
            }
        }
        let createdIds = serverIds.map { $0.subtracting(knownWindowIds ?? $0) } ?? []
        let topology = nativeTabTopology(previous: history, refreshed: previous, current: current, representatives: representatives, createdWindowIds: createdIds)
        let nextHistory = nativeTabHistoryAfterUpdate(previous: history, current: current, topology: topology)
        try job.checkCancellation()
        let replacements = topology.replacements
        // Do not mutate the state until all cancellable AX reads have completed.
        recovery = nextHistory.unresolved ? Recovery(started: retry?.started ?? now, observations: nextHistory.observations) : nil
        if lostProvisional { observations = current }
        for oldId in Array(replacements.values) + Array(topology.absorptions.keys) { observations.removeValue(forKey: oldId) }
        observations.merge(current) { _, new in new }
        exposedWindowIds = Set(liveWindows.map(\.windowId))
        if let serverIds { knownWindowIds = (knownWindowIds ?? []).union(serverIds) }
        // AppKit can expose a new NSWindow before attaching its native tab bar. Give that
        // short creation transition time to settle, without treating older windows as new.
        for id in Array(replacements.keys) + Array(replacements.values) { births.removeValue(forKey: id) }
        recentCreations = births
        pendingReplacements.merge(replacements) { _, new in new }
        func advance(_ old: UInt32, _ new: UInt32) {
            guard old != new else { return }
            for id in Array(representatives.keys) where representatives[id] == old { representatives[id] = new }
            representatives[old] = new
        }
        for (old, new) in topology.continuations { advance(old, new) }
        for (new, old) in replacements { advance(old, new) }
        for (old, new) in topology.absorptions { advance(old, new) }
        for id in current.keys { representatives.removeValue(forKey: id) }
        pendingAbsorptions = pendingAbsorptions.filter { !exposedWindowIds.contains($0.key) }.mapValues { representatives[$0] ?? $0 }
        pendingAbsorptions.merge(topology.absorptions) { _, new in new }
        pendingInheritances = pendingInheritances.filter { exposedWindowIds.contains($0.key) }.mapValues { representatives[$0] ?? $0 }
        pendingInheritances.merge(topology.inheritances) { _, new in new }
        return pendingReplacements
    }

    func acknowledge(_ replacements: [UInt32: UInt32]) {
        for (newId, oldId) in replacements where pendingReplacements[newId] == oldId {
            pendingReplacements.removeValue(forKey: newId)
        }
    }

    func acknowledgeTopology(absorptions: [UInt32: UInt32], inheritances: [UInt32: UInt32]) {
        for (id, target) in absorptions where pendingAbsorptions[id] == target { pendingAbsorptions.removeValue(forKey: id) }
        for (id, source) in inheritances where pendingInheritances[id] == source { pendingInheritances.removeValue(forKey: id) }
    }

    func focusedRepresentative(_ nativeId: UInt32) -> UInt32 {
        var id = nativeId
        var seen: Set<UInt32> = []
        // FocusedWindow can lag AXWindows during a tab change. An exposed ID is already
        // authoritative, even when queued rapid selections form a cycle of handoffs.
        while !exposedWindowIds.isEmpty, !exposedWindowIds.contains(id), seen.insert(id).inserted,
              let next = representatives[id] ?? pendingReplacements.first(where: { $0.value == id })?.key
        {
            id = next
        }
        return id
    }

    func invalidateObservations() {
        observations = [:]
        recovery = nil
        exposedWindowIds = []
        recentCreations = [:]
        representatives = [:]
        if let serverIds = getWindowServerWindowIdsByPid()?[pid] { knownWindowIds = (knownWindowIds ?? []).union(serverIds) }
    }
}

/// Accept settled observations and confirmed ownership changes into the retry
/// baseline. Unmatched count/bar changes and potential newly exposed tabs keep
/// their original eligibility. Every other window remains an ambiguity candidate.
private func nativeTabHistoryAfterUpdate(
    previous: [UInt32: AxNativeTabObservation],
    current: [UInt32: AxNativeTabObservation],
    topology: NativeTabTopology,
) -> (observations: [UInt32: AxNativeTabObservation], unresolved: Bool) {
    var history = previous
    var unresolved = false
    let settled = Set(Array(topology.replacements.keys) + Array(topology.replacements.values) +
        Array(topology.absorptions.keys) + Array(topology.absorptions.values) +
        Array(topology.inheritances.keys) + Array(topology.inheritances.values))
    for id in settled { history.removeValue(forKey: id) }
    for (id, new) in current {
        if settled.contains(id) {
            history[id] = new
        } else if let old = history[id] {
            let sameContainer = (old.tabBar == nil && new.tabBar == nil) || old.sharesTabContainer(with: new)
            if old.snapshot.tabCount == new.snapshot.tabCount && sameContainer {
                history[id] = new
            } else {
                unresolved = true
            }
        } else {
            let firstTab = (new.snapshot.isNewWindow || new.snapshot.isRecentWindowCreation) &&
                (new.snapshot.tabCount ?? 0) >= 2 && previous.values.contains { $0.tabBar == nil }
            let split = !new.snapshot.isNewWindow && !new.snapshot.isRecentWindowCreation &&
                previous.values.contains { ($0.snapshot.tabCount ?? 0) >= 2 }
            if firstTab || split {
                unresolved = true
            } else {
                history[id] = new
            }
        }
    }
    history = history.filter { $0.value.window.map { $0.containingWindowId() == nil } != true }
    return (history, unresolved)
}

/// Collapse queued selections onto the currently exposed tab. A canceled session may
/// have committed a transfer without acknowledging it; replaying every edge of a
/// resulting cycle would then end on an inactive tab.
func nativeTabTransferPlan(replacements: [UInt32: UInt32], managedWindowIds: Set<UInt32>, exposedWindowIds: Set<UInt32>) -> [UInt32: UInt32] {
    var candidates: [UInt32: UInt32] = [:]
    for newId in exposedWindowIds {
        var id = newId
        var seen: Set<UInt32> = [newId]
        var oldestManagedId: UInt32?
        while let previousId = replacements[id], seen.insert(previousId).inserted {
            // A detached, exposed window owns its own slot.
            if exposedWindowIds.contains(previousId) { break }
            if managedWindowIds.contains(previousId) {
                oldestManagedId = previousId
            }
            id = previousId
        }
        // Recovery can leave a staged sibling model alongside the original slot.
        // If selection returns to that slot before a queued cycle commits, keep
        // its binding rather than copying the staging workspace back onto it.
        if managedWindowIds.contains(newId), replacements[id] == newId { continue }
        candidates[newId] = oldestManagedId
    }
    return candidates.filter { _, oldId in candidates.values.filter { $0 == oldId }.count == 1 }
}

func nativeTabReplacementHasManagedSuccessor(_ newId: UInt32, replacements: [UInt32: UInt32], managedWindowIds: Set<UInt32>) -> Bool {
    var id = newId
    var seen: Set<UInt32> = []
    while seen.insert(id).inserted {
        if managedWindowIds.contains(id) { return true }
        guard let next = replacements.first(where: { $0.value == id })?.key else { return false }
        id = next
    }
    return false
}

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

/// AX objects stay on their application's AX thread; only the replacement IDs cross threads.
struct AxNativeTabObservation {
    let snapshot: NativeTabWindowSnapshot
    let tabBar: AXUIElement?
    let tabs: [AXUIElement]
    var window: AXUIElement? = nil

    func sharesTabContainer(with other: AxNativeTabObservation) -> Bool {
        guard let tabBar, let otherBar = other.tabBar else { return false }
        if CFEqual(tabBar, otherBar) { return true }
        return tabBar.containingWindowId() == other.snapshot.windowId
    }

    func sharesTabBar(with other: AxNativeTabObservation) -> Bool {
        if let tabBar, let otherBar = other.tabBar, CFEqual(tabBar, otherBar) { return true }
        // The shared titlebar moves to the selected NSWindow. This also handles the final
        // remaining tab when the bar is no longer an exposed child of the window.
        if let tabBar, tabBar.containingWindowId() == other.snapshot.windowId { return true }
        return tabs.contains { tab in other.tabs.contains { CFEqual(tab, $0) } }
    }

    static func read(_ windowId: UInt32, _ window: AXUIElement, _ job: RunLoopJob, isNewWindow: Bool = false, isRecentWindowCreation: Bool = false) throws -> AxNativeTabObservation {
        try job.checkCancellation()
        let position = window.get(Ax.topLeftCornerAttr)
        let size = window.get(Ax.sizeAttr)
        let frame = position.flatMap { position in size.map { CGRect(origin: position, size: $0) } }
        for child in window.get(Ax.childrenAttr) ?? [] {
            try job.checkCancellation()
            guard child.get(Ax.roleAttr) == kAXTabGroupRole,
                  let tabs = child.get(Ax.tabsAttr), !tabs.isEmpty,
                  tabs.allSatisfy({ $0.get(Ax.subroleAttr) == "AXTabButton" })
            else { continue }
            // Content tab strips in Firefox/editors are deeper in the tree. A native titlebar
            // tab group is a direct child of the window and contains AXTabButton elements.
            return AxNativeTabObservation(
                snapshot: NativeTabWindowSnapshot(windowId: windowId, frame: frame, tabCount: tabs.count, isNewWindow: isNewWindow, isRecentWindowCreation: isRecentWindowCreation),
                tabBar: child, tabs: tabs, window: window,
            )
        }
        return AxNativeTabObservation(
            snapshot: NativeTabWindowSnapshot(windowId: windowId, frame: frame, tabCount: nil, isNewWindow: isNewWindow, isRecentWindowCreation: isRecentWindowCreation),
            tabBar: nil, tabs: [], window: window,
        )
    }
}

/// Match in both directions so two concurrent tab switches cannot take each other's slots.
func nativeTabReplacements(
    previous: [UInt32: AxNativeTabObservation],
    current: [UInt32: AxNativeTabObservation],
) -> [UInt32: UInt32] {
    let exposedIds = Set(current.keys)
    var candidates: [UInt32: [UInt32]] = [:]
    for (newId, newObservation) in current {
        // An already exposed representative owns its own slot. Merging another group's
        // tabs into it must not pull it onto the disappearing group's workspace.
        if let existing = previous[newId], !(newObservation.snapshot.isRecentWindowCreation && existing.tabBar == nil) { continue }
        var exactMatches: [UInt32] = []
        for (oldId, oldObservation) in previous where !exposedIds.contains(oldId) && isNativeTabReplacement(
            previous: oldObservation.snapshot, current: newObservation.snapshot,
            exposedWindowIds: exposedIds, sharesTabBar: oldObservation.sharesTabBar(with: newObservation),
            previousWindowClosed: oldObservation.window.map { $0.containingWindowId() == nil } == true,
        ) {
            candidates[newId, default: []].append(oldId)
            if oldObservation.sharesTabBar(with: newObservation) { exactMatches.append(oldId) }
        }
        if !exactMatches.isEmpty { candidates[newId] = exactMatches }
    }
    var result: [UInt32: UInt32] = [:]
    for (newId, oldIds) in candidates {
        guard oldIds.count == 1, let oldId = oldIds.first,
              candidates.values.filter({ $0.contains(oldId) }).count == 1
        else { continue }
        result[newId] = oldId
    }
    return result
}
