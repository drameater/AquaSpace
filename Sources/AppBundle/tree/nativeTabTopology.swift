import AppKit

struct NativeTabTopology {
    var replacements: [UInt32: UInt32]
    var absorptions: [UInt32: UInt32] = [:]
    var inheritances: [UInt32: UInt32] = [:]
    var continuations: [UInt32: UInt32] = [:] // Previous owner -> remaining group.
}

/// A merge recreates the incoming group's AX buttons. Its old AX root remains valid,
/// but adopts the surviving frame. Require an established destination and exact tab
/// count accounting; geometry or AXWindows disappearance alone is insufficient.
func nativeTabTopology(
    previous: [UInt32: AxNativeTabObservation],
    refreshed: [UInt32: AxNativeTabObservation],
    current: [UInt32: AxNativeTabObservation],
    representatives: [UInt32: UInt32],
    createdWindowIds: Set<UInt32> = [],
    independentWindowIds: Set<UInt32> = [],
) -> NativeTabTopology {
    var result = NativeTabTopology(replacements: nativeTabReplacements(previous: refreshed, current: current, independentWindowIds: independentWindowIds))
    let exposed = Set(current.keys)
    var owners: [UInt32: UInt32] = [:]
    for (newId, observation) in current {
        let matches = previous.filter { $0.value.sharesTabContainer(with: observation) }.map(\.key)
        if matches.count == 1, let oldId = matches.first {
            owners[newId] = oldId
            result.continuations[oldId] = newId
            if oldId != newId {
                if exposed.contains(oldId) {
                    // The selected NSWindow detached; both physical windows inherit
                    // the original workspace, and the original model keeps focus.
                    result.inheritances[newId] = oldId
                    result.replacements.removeValue(forKey: newId)
                } else {
                    result.replacements[newId] = oldId
                }
            }
        } else if previous[newId] != nil {
            owners[newId] = newId
        } else if let oldId = result.replacements[newId] {
            owners[newId] = oldId
        }
    }

    // Detaching from a two-tab group removes its bar. The remaining NSWindow
    // keeps the group's former frame; the detached NSWindow gets a new frame.
    for (oldId, old) in previous where old.snapshot.tabCount == 2 && result.continuations[oldId] == nil {
        guard old.window.map({ $0.containingWindowId() == nil }) != true else { continue }
        let remaining = current.filter { id, candidate in
            id != oldId && previous[id] == nil && !independentWindowIds.contains(id) && candidate.tabBar == nil &&
                !candidate.snapshot.isNewWindow && !candidate.snapshot.isRecentWindowCreation &&
                nativeTabFramesMatch(old.snapshot.frame, candidate.snapshot.frame)
        }
        if remaining.count == 1, let newId = remaining.keys.first {
            result.continuations[oldId] = newId
            result.inheritances[newId] = oldId
        }
    }

    // An inactive tab can detach without replacing the selected representative.
    // Prefer the observed shrinking group; use remembered membership only when
    // the transition was coalesced and the source is still exposed.
    for (newId, new) in current where previous[newId] == nil && !independentWindowIds.contains(newId) && result.replacements[newId] == nil && result.inheritances[newId] == nil {
        let shrinking = previous.compactMap { oldId, old -> UInt32? in
            guard let count = old.snapshot.tabCount, count >= 2,
                  let ownerId = result.continuations[oldId] ?? (current[oldId] != nil ? oldId : nil),
                  ownerId != newId, let remaining = current[ownerId],
                  (remaining.snapshot.tabCount ?? 1) == count - 1,
                  !new.snapshot.isNewWindow, !new.snapshot.isRecentWindowCreation
            else { return nil }
            return oldId
        }
        if shrinking.count == 1 {
            result.inheritances[newId] = shrinking[0]
        } else if shrinking.isEmpty, let ownerId = representatives[newId], current[ownerId] != nil {
            result.inheritances[newId] = ownerId
        }
    }

    var proposed: [UInt32: [UInt32]] = [:]
    for (newId, destination) in current {
        guard let newCount = destination.snapshot.tabCount, let ownerId = owners[newId],
              let owner = previous[ownerId]
        else { continue }
        let growth = newCount - (owner.snapshot.tabCount ?? 1)
        guard growth > 0 else { continue }
        var candidates: [UInt32: Int] = [:]
        for (oldId, old) in previous where oldId != ownerId && (!exposed.contains(oldId) || oldId == newId) {
            guard old.window.map({ $0.containingWindowId() == nil }) != true else { continue }
            let sourceFrame = (refreshed[oldId] ?? old).snapshot.frame
            // AppKit resizes an incoming tab to its destination but can retain its
            // offscreen origin until selected. A destroyed source bar plus exact
            // tab-count accounting identifies this cross-workspace merge. For a
            // bare source, also exclude concurrent NSWindow creation.
            let lostBar = old.tabBar.map { $0.containingWindowId() == nil } == true
            let sameSize = sourceFrame.flatMap { a in destination.snapshot.frame.map { b in
                a.width > 0 && a.height > 0 && abs(a.width - b.width) <= 1 && abs(a.height - b.height) <= 1
            } } == true
            guard nativeTabFramesMatch(sourceFrame, destination.snapshot.frame) ||
                (sameSize && (lostBar || old.tabBar == nil) && createdWindowIds.isEmpty)
            else { continue }
            let remainingCount = result.continuations[oldId].flatMap { current[$0] }.map { $0.snapshot.tabCount ?? 1 } ?? 0
            let movedCount = (old.snapshot.tabCount ?? 1) - remainingCount
            if movedCount > 0 { candidates[oldId] = movedCount }
        }
        if !candidates.isEmpty && candidates.values.reduce(0, +) == growth {
            proposed[newId] = Array(candidates.keys)
        }
    }
    for (destination, sources) in proposed {
        for source in sources where source != destination && proposed.values.filter({ $0.contains(source) }).count == 1 {
            result.absorptions[source] = destination
        }
    }
    return result
}
