@testable import NativeTabReplay
import Foundation
import Testing

/// The known-window baseline decides which windows count as newly created.
/// A WindowServer read happens on the application's AX thread, never on the main actor.
struct NativeTabBaselineTest {
    private let pid: Int32 = 4242

    private func bareWindow(_ id: UInt32) -> WindowIdAndAxUiElement {
        let root = AXUIElement(owner: id)
        root.set(Ax.roleAttr, kAXWindowRole)
        root.set(Ax.subroleAttr, kAXStandardWindowSubrole)
        root.set(Ax.topLeftCornerAttr, CGPoint(x: 10, y: 20))
        root.set(Ax.sizeAttr, CGSize(width: 800, height: 600))
        root.set(Ax.childrenAttr, [AXUIElement]())
        return (windowId: id, ax: root)
    }

    private func created(_ state: AxNativeTabState, _ id: UInt32) -> Bool? {
        state.observations[id].map { $0.snapshot.isNewWindow || $0.snapshot.isRecentWindowCreation }
    }

    // While the left mouse button is down the refresh skips its snapshot, and
    // reconciliation keeps its state and reads nothing.
    @Test func reconciliationDuringADragReadsNothingAndKeepsItsState() throws {
        let reads = ReplayDesktop.ReadCounter()
        try ReplayDesktop.$reads.withValue(reads) {
            let state = AxNativeTabState(pid: pid, serverIds: [pid: [1]])
            _ = try state.reconcile([bareWindow(1)], RunLoopJob(), isAppHidden: false, windowServerIds: [1])
            let before = Set(state.observations.keys)
            try ReplayDesktop.$leftMouseButtonDown.withValue(true) {
                _ = try state.reconcile([bareWindow(1), bareWindow(2)], RunLoopJob(), isAppHidden: false, windowServerIds: nil)
            }
            #expect(reads.count == 0)
            #expect(Set(state.observations.keys) == before)
            #expect(state.exposedWindowIds == [1])
        }
    }

    // The button was down when the refresh started, so it took no snapshot, and
    // was released before this application reconciled. Reconciliation then reads
    // its own inventory on the AX thread and uses it.
    @Test func reconciliationWithoutASnapshotReadsItsOwnInventory() throws {
        let reads = ReplayDesktop.ReadCounter()
        try ReplayDesktop.$reads.withValue(reads) {
            let state = AxNativeTabState(pid: pid, serverIds: [pid: [1]])
            try ReplayDesktop.$windowIdsByPid.withValue([pid: [1, 2]]) {
                _ = try state.reconcile([bareWindow(1), bareWindow(2)], RunLoopJob(), isAppHidden: false, windowServerIds: nil)
            }
            #expect(reads.count == 1)
            #expect(created(state, 1) == false)
            #expect(created(state, 2) == true)
        }
    }
}
