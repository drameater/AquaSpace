@testable import AppBundle
import AppKit
import Common
import Testing

@Suite(.serialized)
@MainActor
struct NativeTabReplacementTest {
    private func snapshot(_ id: UInt32, _ x: CGFloat = 10, tabs: Int? = nil, isNew: Bool = false) -> NativeTabWindowSnapshot {
        NativeTabWindowSnapshot(windowId: id, frame: CGRect(x: x, y: 20, width: 800, height: 600), tabCount: tabs, isNewWindow: isNew)
    }

    @Test func firstTabNeedsNativeBarAndDisappearingWindow() {
        let old = snapshot(1)
        let tab = snapshot(2, tabs: 2, isNew: true)
        #expect(isNativeTabReplacement(previous: old, current: tab, exposedWindowIds: [2], sharesTabBar: false))
        #expect(!isNativeTabReplacement(previous: old, current: tab, exposedWindowIds: [1, 2], sharesTabBar: false))
        #expect(!isNativeTabReplacement(previous: old, current: snapshot(2, tabs: 2), exposedWindowIds: [2], sharesTabBar: false))
        #expect(!isNativeTabReplacement(previous: old, current: snapshot(2), exposedWindowIds: [2], sharesTabBar: false))
        #expect(!isNativeTabReplacement(previous: old, current: snapshot(2, 900, tabs: 2), exposedWindowIds: [2], sharesTabBar: false))
    }

    @Test func unobservableSessionDoesNotReplaceWindows() {
        #expect(!isNativeTabReplacement(previous: snapshot(1, tabs: 2), current: snapshot(2, tabs: 2), exposedWindowIds: [], sharesTabBar: true))
    }

    @Test func sharedBarSurvivesResizeAndClosingSelectedTab() {
        #expect(isNativeTabReplacement(previous: snapshot(1, tabs: 2), current: snapshot(2, 900, tabs: 1), exposedWindowIds: [2], sharesTabBar: true))
    }

    @Test func knownGroupsNeedIdentityEvenWhenGeometryMatches() {
        #expect(!isNativeTabReplacement(previous: snapshot(1, tabs: 2), current: snapshot(2, tabs: 2), exposedWindowIds: [2], sharesTabBar: false))
    }

    @Test func closingOneOfTwoTabsKeepsTheFinalTabSlot() {
        #expect(isNativeTabReplacement(previous: snapshot(1, tabs: 2), current: snapshot(2), exposedWindowIds: [2], sharesTabBar: false, previousWindowClosed: true))
        #expect(!isNativeTabReplacement(previous: snapshot(1, tabs: 2), current: snapshot(2), exposedWindowIds: [2], sharesTabBar: false))
        #expect(!isNativeTabReplacement(previous: snapshot(1), current: snapshot(2), exposedWindowIds: [2], sharesTabBar: false, previousWindowClosed: true))
    }

    @Test func queuedHandoffsRecognizeAlreadyTransferredSuccessors() {
        #expect(nativeTabReplacementHasManagedSuccessor(2, replacements: [2: 1, 3: 2], managedWindowIds: [3]))
        #expect(!nativeTabReplacementHasManagedSuccessor(2, replacements: [2: 1], managedWindowIds: [9]))
        #expect(nativeTabReplacementHasManagedSuccessor(2, replacements: [2: 1, 1: 2], managedWindowIds: [1]))
        #expect(!nativeTabReplacementHasManagedSuccessor(2, replacements: [2: 1, 1: 2], managedWindowIds: []))
    }

    @Test func interruptedCommitAndSwitchBackDoNotReplayTheInactiveTab() {
        // 1 -> 2 committed, but cancellation prevented its acknowledgement. The
        // user then selects 1, leaving both directions queued and 2 managed.
        let queued: [UInt32: UInt32] = [2: 1, 1: 2]
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [2], exposedWindowIds: [1]) == [1: 2])
        // The next retry must acknowledge the cycle without moving the slot again.
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [1], exposedWindowIds: [1]).isEmpty)
    }

    @Test func queuedSelectionsTransferDirectlyToTheExposedTab() {
        let queued: [UInt32: UInt32] = [2: 1, 3: 2]
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [1], exposedWindowIds: [3]) == [3: 1])
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [2], exposedWindowIds: [3]) == [3: 2])
        // A staged bare window may already have its own model. Its predecessor
        // still owns the physical group's workspace until the handoff commits.
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [1, 2], exposedWindowIds: [3]) == [3: 1])
        #expect(nativeTabTransferPlan(replacements: queued, managedWindowIds: [1], exposedWindowIds: []).isEmpty)
        // An exposed source is a separate physical window after detachment.
        #expect(nativeTabTransferPlan(replacements: [2: 1], managedWindowIds: [1], exposedWindowIds: [1, 2]).isEmpty)
        #expect(nativeTabTransferPlan(replacements: [2: 1, 3: 1], managedWindowIds: [1], exposedWindowIds: [2, 3]).isEmpty)
    }

    private func observation(_ snapshot: NativeTabWindowSnapshot, bar: AXUIElement? = nil) -> AxNativeTabObservation {
        AxNativeTabObservation(snapshot: snapshot, tabBar: bar, tabs: [])
    }

    @Test func mergingGroupsRetiresTheSourceWithoutMovingTheSurvivor() {
        let firstBar = AXUIElementCreateApplication(123)
        let destinationBar = AXUIElementCreateApplication(124)
        let previous: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, tabs: 2), bar: firstBar), 3: observation(snapshot(3, 900, tabs: 2), bar: destinationBar)]
        var refreshed = previous
        refreshed[1] = observation(snapshot(1, 900, tabs: 2), bar: firstBar)
        let current: [UInt32: AxNativeTabObservation] = [3: observation(snapshot(3, 900, tabs: 4), bar: destinationBar)]
        let change = nativeTabTopology(previous: previous, refreshed: refreshed, current: current, representatives: [:])
        #expect(change.replacements.isEmpty)
        #expect(change.absorptions == [1: 3])
        let ambiguous = nativeTabTopology(previous: previous, refreshed: refreshed, current: [3: observation(snapshot(3, 900, tabs: 3), bar: destinationBar)], representatives: [:])
        #expect(ambiguous.absorptions.isEmpty)
    }

    @Test func selectedIncomingTabUsesTheExistingDestinationContainer() {
        let oldBar = AXUIElementCreateApplication(123)
        let destinationBar = AXUIElementCreateApplication(124)
        let previous: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, tabs: 2), bar: oldBar), 3: observation(snapshot(3, 900, tabs: 2), bar: destinationBar)]
        let current: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, 900, tabs: 4), bar: destinationBar)]
        let change = nativeTabTopology(previous: previous, refreshed: previous, current: current, representatives: [:])
        #expect(change.replacements == [1: 3])
        #expect(change.absorptions.isEmpty)
    }

    @Test func crossWorkspaceMergeCanRetainTheIncomingOffscreenOrigin() {
        let bar = AXUIElementCreateApplication(123)
        let previous: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, 1709, tabs: 2), bar: bar), 3: observation(snapshot(3))]
        let current: [UInt32: AxNativeTabObservation] = [3: observation(snapshot(3, tabs: 3), bar: AXUIElementCreateApplication(124))]
        let change = nativeTabTopology(previous: previous, refreshed: previous, current: current, representatives: [:])
        #expect(change.absorptions == [1: 3])
        let concurrentCreation = nativeTabTopology(previous: previous, refreshed: previous, current: current, representatives: [:], createdWindowIds: [4])
        #expect(concurrentCreation.absorptions.isEmpty)
    }

    @Test func detachingSelectedTabExposesAnotherWindowInTheSameWorkspace() {
        let bar = AXUIElementCreateApplication(123)
        let previous: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, tabs: 3), bar: bar)]
        let current: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, 100)), 2: observation(snapshot(2, tabs: 2), bar: bar)]
        let change = nativeTabTopology(previous: previous, refreshed: previous, current: current, representatives: [:])
        #expect(change.replacements.isEmpty)
        #expect(change.inheritances == [2: 1])
    }

    @Test func inactiveDetachedTabPrefersShrinkingGroupOverStaleMembership() {
        let firstBar = AXUIElementCreateApplication(123)
        let secondBar = AXUIElementCreateApplication(124)
        let previous: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, tabs: 3), bar: firstBar), 3: observation(snapshot(3, 900, tabs: 2), bar: secondBar)]
        let current: [UInt32: AxNativeTabObservation] = [1: observation(snapshot(1, tabs: 2), bar: firstBar), 2: observation(snapshot(2, 100)), 3: observation(snapshot(3, 900, tabs: 2), bar: secondBar)]
        let change = nativeTabTopology(previous: previous, refreshed: previous, current: current, representatives: [2: 3])
        #expect(change.inheritances == [2: 1])
    }

    @Test func concurrentGroupsKeepTheirOwnSlots() {
        let previous = [1: observation(snapshot(1)), 3: observation(snapshot(3, 900))] as [UInt32: AxNativeTabObservation]
        let current = [2: observation(snapshot(2, tabs: 2, isNew: true)), 4: observation(snapshot(4, 900, tabs: 2, isNew: true))] as [UInt32: AxNativeTabObservation]
        #expect(nativeTabReplacements(previous: previous, current: current) == [2: 1, 4: 3])
    }

    @Test func ambiguousGeometryDoesNotMergeGroups() {
        let previous = [1: observation(snapshot(1)), 3: observation(snapshot(3))] as [UInt32: AxNativeTabObservation]
        let current = [2: observation(snapshot(2, tabs: 2, isNew: true))] as [UInt32: AxNativeTabObservation]
        #expect(nativeTabReplacements(previous: previous, current: current).isEmpty)
    }

    @Test func actualBarIdentityWinsOverCoincidentFrames() {
        let bar = AXUIElementCreateApplication(123)
        let previous = [1: observation(snapshot(1, tabs: 2), bar: bar), 3: observation(snapshot(3, tabs: 2))] as [UInt32: AxNativeTabObservation]
        let current = [2: observation(snapshot(2, tabs: 2), bar: bar)] as [UInt32: AxNativeTabObservation]
        #expect(nativeTabReplacements(previous: previous, current: current) == [2: 1])
    }

    @Test func tabSelectionAfterWorkspaceMoveRetainsWorkspaceAndSlot() async {
        setUpWorkspacesForTests()
        let workspace = Workspace.get(byName: "native-tabs-a")
        let first = TestWindow.new(id: 1, parent: workspace.rootTilingContainer, adaptiveWeight: 3)
        TestWindow.new(id: 9, parent: workspace.rootTilingContainer)
        _ = first.focusWindow()
        let move = parseCommand("move-node-to-workspace --focus-follows-window native-tabs-b").cmdOrDie
        #expect(await move.run(.defaultEnv, .emptyStdin).exitCode.rawValue == 0)
        let parent = first.parent!
        let firstIndex = first.ownIndex
        let nextTab = TestWindow.new(id: 2, parent: Workspace.get(byName: "native-tabs-a").rootTilingContainer)

        transferNativeTabState(from: first, to: nextTab)
        _ = nextTab.focusWindow()

        #expect(focus.workspace.name == "native-tabs-b")
        #expect(nextTab.parent === parent)
        #expect(nextTab.ownIndex == firstIndex)
        #expect(parent.children.count == 1)
        #expect(!first.isBound)
    }

    @Test func replacementPreservesWeightFullscreenAndCachedGeometry() {
        setUpWorkspacesForTests()
        let parent = Workspace.get(byName: "native-tabs-state").rootTilingContainer
        TestWindow.new(id: 9, parent: parent)
        let next = TestWindow.new(id: 2, parent: parent)
        let first = TestWindow.new(id: 1, parent: parent, adaptiveWeight: 3)
        first.isFullscreen = true
        first.noOuterGapsInFullscreen = true
        first.lastFloatingSize = CGSize(width: 400, height: 300)
        first.lastAppliedLayoutPhysicalRect = CGRect(x: 10, y: 20, width: 800, height: 600).toRect()
        transferNativeTabState(from: first, to: next)
        #expect(next.ownIndex == 1)
        #expect(next.getWeight(parent.orientation) == 3)
        #expect(next.isFullscreen && next.noOuterGapsInFullscreen)
        #expect(next.lastFloatingSize == CGSize(width: 400, height: 300))
        #expect(next.lastAppliedLayoutPhysicalRect?.width == 800)
        #expect(parent.children.map { ($0 as! Window).windowId } == [9, 2])
    }

    @Test func floatingTabKeepsItsWorkspaceAndSize() {
        setUpWorkspacesForTests()
        let workspace = Workspace.get(byName: "native-tabs-floating")
        let first = TestWindow.new(id: 1, parent: workspace.floatingWindowsContainer)
        first.lastFloatingSize = CGSize(width: 900, height: 700)
        let next = TestWindow.new(id: 2, parent: Workspace.get(byName: "elsewhere").rootTilingContainer)
        transferNativeTabState(from: first, to: next)
        #expect(next.parent === workspace.floatingWindowsContainer)
        #expect(next.nodeWorkspace === workspace)
        #expect(next.lastFloatingSize == CGSize(width: 900, height: 700))
        #expect(workspace.floatingWindowsContainer.children.count == 1)
    }

    @Test func tabSwitchPreservesFocusBackAndForthHistory() async {
        setUpWorkspacesForTests()
        let other = TestWindow.new(id: 9, parent: Workspace.get(byName: "previous-window").rootTilingContainer)
        _ = other.focusWindow()
        await checkOnFocusChangedCallbacks_nonCancellable()
        let first = TestWindow.new(id: 1, parent: Workspace.get(byName: "native-tab-focus").rootTilingContainer)
        _ = first.focusWindow()
        await checkOnFocusChangedCallbacks_nonCancellable()
        #expect(prevFocus?.windowOrNil === other)
        let next = TestWindow.new(id: 2, parent: Workspace.get(byName: "staging").rootTilingContainer)
        transferNativeTabState(from: first, to: next)
        await checkOnFocusChangedCallbacks_nonCancellable()
        #expect(focus.windowOrNil === next)
        #expect(focus.workspace.name == "native-tab-focus")
        #expect(prevFocus?.windowOrNil === other)
    }
}
