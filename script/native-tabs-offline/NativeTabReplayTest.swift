@testable import NativeTabReplay
import Foundation
import Testing

private final class Replay {
    let state = AxNativeTabState()
    let job = RunLoopJob()
    var windows: [UInt32: AXUIElement] = [:]
    var serverIds: Set<UInt32> = []

    func window(_ id: UInt32, x: CGFloat = 10, width: CGFloat = 800,
                count: Int? = nil, bar: AXUIElement? = nil) -> AXUIElement {
        let root = windows[id] ?? AXUIElement(owner: id)
        root.set(Ax.roleAttr, kAXWindowRole)
        root.set(Ax.subroleAttr, kAXStandardWindowSubrole)
        root.set(Ax.topLeftCornerAttr, CGPoint(x: x, y: 20))
        root.set(Ax.sizeAttr, CGSize(width: width, height: 600))
        if let count, let bar {
            bar.owner = id
            bar.set(Ax.roleAttr, kAXTabGroupRole)
            bar.set(Ax.tabsAttr, (0..<count).map { _ in
                let button = AXUIElement(owner: id)
                button.set(Ax.subroleAttr, "AXTabButton")
                return button
            })
            root.set(Ax.childrenAttr, [bar])
        } else { root.set(Ax.childrenAttr, [AXUIElement]()) }
        windows[id] = root
        serverIds.insert(id)
        return root
    }

    @discardableResult
    func scan(_ ids: [UInt32], acknowledge: Bool = true) throws -> NativeTabUpdate {
        _ = try state.reconcile(ids.map { (windowId: $0, ax: windows[$0]!) }, job,
                                isAppHidden: false, windowServerIds: serverIds)
        let update = state.update
        if acknowledge {
            state.acknowledge(update.replacements)
            state.acknowledgeTopology(absorptions: update.absorptions, inheritances: update.inheritances)
        }
        return update
    }

    func mergeSetup(sourceWidth: CGFloat = 800) throws -> AXUIElement {
        let sourceBar = AXUIElement(owner: 2)
        _ = window(2, x: 20000, width: sourceWidth, count: 2, bar: sourceBar)
        serverIds.insert(1) // Inactive sibling, absent from AXWindows.
        try scan([2])
        _ = window(3)
        try scan([2, 3]) // Bare destination is genuinely observed before merge.
        return sourceBar
    }
}

struct NativeTabReplayTest {
    @Test func newlyCreatedBareDestinationCanAbsorbAnExistingGroup() throws {
        for age in [0, 3] {
            let replay = Replay()
            let sourceBar = try replay.mergeSetup()
            sourceBar.owner = nil
            _ = replay.window(2, x: 20000)
            _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
            let update = try ReplayTime.$elapsed.withValue(.seconds(age)) { try replay.scan([3]) }
            #expect(update.absorptions == [2: 3])
        }
    }

    @Test func selectedDetachWithSurvivingBarInheritsTheGroupOwner() throws {
        let replay = Replay()
        let bar = AXUIElement(owner: 3)
        _ = replay.window(3, count: 3, bar: bar)
        replay.serverIds.formUnion([1, 2])
        try replay.scan([3])
        _ = replay.window(3, x: 100)
        _ = replay.window(2, count: 2, bar: bar)
        let update = try replay.scan([2, 3])
        #expect(update.inheritances == [2: 3])
        #expect(update.replacements.isEmpty)
    }

    @Test func mergeEvidenceSurvivesAnAuxiliaryWindowServerCreation() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup()
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        replay.serverIds.insert(99) // Nonstandard helper; not an exposed managed window.
        let first = try replay.scan([3])
        let retry = try replay.scan([3])
        #expect(first.absorptions.isEmpty) // Concurrent creation still defers matching.
        #expect(first.absorptions[2] == 3 || retry.absorptions[2] == 3)
    }

    @Test func mergeEvidenceSurvivesGeometrySettlingAfterTabCount() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        let destinationBar = AXUIElement(owner: 3)
        _ = replay.window(3, count: 3, bar: destinationBar)
        let first = try replay.scan([3])
        _ = replay.window(2, x: 20000, width: 800)
        let retry = try replay.scan([3])
        #expect(first.absorptions[2] == 3 || retry.absorptions[2] == 3)
    }

    @Test func establishedDestinationAlsoLosesDelayedMergeEvidence() throws {
        let replay = Replay()
        let sourceBar = AXUIElement(owner: 2)
        let destinationBar = AXUIElement(owner: 3)
        _ = replay.window(2, x: 20000, width: 400, count: 2, bar: sourceBar)
        _ = replay.window(3, count: 2, bar: destinationBar)
        replay.serverIds.formUnion([1, 4])
        try replay.scan([2, 3])
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 4, bar: destinationBar)
        let first = try replay.scan([3])
        _ = replay.window(2, x: 20000, width: 800)
        let retry = try replay.scan([3])
        #expect(first.absorptions[2] == 3 || retry.absorptions[2] == 3)
    }

    @Test func firstTabWithImmediatelyMatchingGeometryTransfersItsSlot() throws {
        let replay = Replay()
        _ = replay.window(1)
        try replay.scan([1])
        _ = replay.window(2, count: 2, bar: AXUIElement(owner: 2))
        #expect(try replay.scan([2]).replacements == [2: 1])
    }

    @Test func firstTabRecoversAfterGeometrySettles() throws {
        let replay = Replay()
        _ = replay.window(1, width: 400)
        try replay.scan([1])
        _ = replay.window(2, count: 2, bar: AXUIElement(owner: 2))
        let first = try replay.scan([2])
        _ = replay.window(1, width: 800)
        let retry = try replay.scan([2])
        #expect(first.replacements[2] == 1 || retry.replacements[2] == 1)
    }

    @Test func firstTabRecoversAfterAXWindowsBrieflyExposesBothSiblings() throws {
        let replay = Replay()
        _ = replay.window(1)
        try replay.scan([1])
        let bar = AXUIElement(owner: 2)
        _ = replay.window(2, count: 2, bar: bar)
        let overlap = try replay.scan([1, 2])
        let retry = try replay.scan([2])
        #expect(overlap.replacements[2] == 1 || retry.replacements[2] == 1)
    }

    @Test func inactiveDetachWithImmediateCountDecreaseInheritsOwner() throws {
        let replay = Replay()
        let bar = AXUIElement(owner: 3)
        _ = replay.window(3, count: 3, bar: bar)
        replay.serverIds.formUnion([1, 2])
        try replay.scan([3])
        try ReplayTime.$elapsed.withValue(.seconds(3)) {
            _ = replay.window(2, x: 100)
            _ = replay.window(3, count: 2, bar: bar)
            let update = try replay.scan([2, 3])
            #expect(update.inheritances == [2: 3])
        }
    }

    @Test func inactiveDetachRecoversWhenTabCountLagsWindowExposure() throws {
        let replay = Replay()
        let bar = AXUIElement(owner: 3)
        _ = replay.window(3, count: 3, bar: bar)
        replay.serverIds.formUnion([1, 2])
        try replay.scan([3])
        try ReplayTime.$elapsed.withValue(.seconds(3)) {
            _ = replay.window(2, x: 100)
            let first = try replay.scan([2, 3]) // Bar still reports three tabs.
            _ = replay.window(3, count: 2, bar: bar)
            let retry = try replay.scan([2, 3])
            #expect(first.inheritances[2] == 3 || retry.inheritances[2] == 3)
        }
    }

    @Test func coincidentIndependentWindowsAreNeverMerged() throws {
        let replay = Replay()
        _ = replay.window(1)
        _ = replay.window(2)
        try replay.scan([1, 2])
        let update = try replay.scan([1, 2])
        #expect(update.replacements.isEmpty)
        #expect(update.absorptions.isEmpty)
        #expect(update.inheritances.isEmpty)
    }

    @Test func competingMergeSourcesRemainAmbiguousOnRetry() throws {
        let replay = Replay()
        let firstBar = try replay.mergeSetup()
        let secondBar = AXUIElement(owner: 4)
        _ = replay.window(4, x: 21000, count: 2, bar: secondBar)
        try replay.scan([2, 3, 4])
        firstBar.owner = nil
        secondBar.owner = nil
        _ = replay.window(2, x: 20000)
        _ = replay.window(4, x: 21000)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        #expect(try replay.scan([3]).absorptions.isEmpty)
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func competingDestinationsCannotBothClaimOneSourceOnRetry() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup()
        _ = replay.window(5)
        try replay.scan([2, 3, 5])
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        _ = replay.window(5, count: 3, bar: AXUIElement(owner: 5))
        #expect(try replay.scan([3, 5]).absorptions.isEmpty)
        #expect(try replay.scan([3, 5]).absorptions.isEmpty)
    }

    @Test func firstTabRetryStillRequiresAUniqueBarePredecessor() throws {
        let replay = Replay()
        _ = replay.window(1, width: 400)
        _ = replay.window(4, width: 400)
        try replay.scan([1, 4])
        _ = replay.window(2, count: 2, bar: AXUIElement(owner: 2))
        #expect(try replay.scan([2]).replacements.isEmpty)
        _ = replay.window(1)
        _ = replay.window(4)
        #expect(try replay.scan([2]).replacements.isEmpty)
    }

    @Test func twoShrinkingGroupsCannotClaimTheSameDetachedWindowOnRetry() throws {
        let replay = Replay()
        let firstBar = AXUIElement(owner: 3)
        let secondBar = AXUIElement(owner: 4)
        _ = replay.window(3, count: 3, bar: firstBar)
        _ = replay.window(4, x: 900, count: 3, bar: secondBar)
        replay.serverIds.insert(2)
        try replay.scan([3, 4])
        try ReplayTime.$elapsed.withValue(.seconds(3)) {
            _ = replay.window(2, x: 100)
            let first = try replay.scan([2, 3, 4])
            #expect(first.inheritances.isEmpty)
            _ = replay.window(3, count: 2, bar: firstBar)
            _ = replay.window(4, x: 900, count: 2, bar: secondBar)
            let retry = try replay.scan([2, 3, 4])
            #expect(retry.inheritances.isEmpty)
        }
    }

    @Test func retryStillRequiresExactTabCountAccounting() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 4, bar: AXUIElement(owner: 3)) // Source has only two tabs.
        #expect(try replay.scan([3]).absorptions.isEmpty)
        _ = replay.window(2, x: 20000)
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func independentNewWindowRemainsAnAmbiguityCandidateAfterDisappearing() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        _ = replay.window(99, x: 21000)
        #expect(try replay.scan([3, 99]).absorptions.isEmpty)
        _ = replay.window(2, x: 20000)
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func disappearingProvisionalGroupCannotRemoveAnAmbiguityCandidate() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        _ = replay.window(99, x: 21000, count: 2, bar: AXUIElement(owner: 99))
        #expect(try replay.scan([3, 99]).absorptions.isEmpty)
        _ = replay.window(2, x: 20000)
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func expiredRecoveryEvidenceCannotClaimAWindow() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3])
        _ = replay.window(2, x: 20000)
        let update = try ReplayTime.$elapsed.withValue(.seconds(2)) { try replay.scan([3]) }
        #expect(update.absorptions.isEmpty)
    }

    @Test func hiddenUnobservableOrNativeSpaceInvalidationDiscardsRecovery() throws {
        for interruption in ["hidden", "nil", "empty", "space"] {
            let replay = Replay()
            let sourceBar = try replay.mergeSetup(sourceWidth: 400)
            sourceBar.owner = nil
            _ = replay.window(2, x: 20000, width: 400)
            _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
            try replay.scan([3])
            switch interruption {
                case "hidden": _ = try replay.state.reconcile([(windowId: 3, ax: replay.windows[3]!)], replay.job, isAppHidden: true)
                case "nil": _ = try replay.state.reconcile(nil, replay.job, isAppHidden: false)
                case "empty": _ = try replay.state.reconcile([], replay.job, isAppHidden: false)
                default: replay.state.invalidateObservations()
            }
            _ = replay.window(2, x: 20000)
            #expect(try replay.scan([3]).absorptions.isEmpty)
        }
    }

    @Test func closedSourceCannotBeRecoveredAsAMergedGroup() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3])
        _ = replay.window(2, x: 20000)
        replay.windows[2]!.owner = nil
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func cancelledAXReadDoesNotConsumeRecovery() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3])
        _ = replay.window(2, x: 20000)
        replay.job.checksUntilCancellation = 3
        #expect(throws: CancellationError.self) { try replay.scan([3]) }
        replay.job.checksUntilCancellation = nil
        #expect(try replay.scan([3]).absorptions == [2: 3])
        #expect(try replay.scan([3]).absorptions.isEmpty) // Acknowledged recovery is consumed.
    }

    @Test func unrelatedConfirmedTransitionDoesNotEraseOrReplayRecovery() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        _ = replay.window(6, x: 900)
        try replay.scan([2, 3, 6])
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3, 6])
        _ = replay.window(7, x: 900, count: 2, bar: AXUIElement(owner: 7))
        #expect(try replay.scan([3, 7]).replacements == [7: 6])
        _ = replay.window(2, x: 20000)
        let recovered = try replay.scan([3, 7])
        #expect(recovered.absorptions == [2: 3])
        #expect(recovered.replacements.isEmpty)
        #expect(try replay.scan([3, 7]).absorptions.isEmpty)
    }

    @Test func unrelatedTransitionCannotExtendTheRecoveryDeadline() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        _ = replay.window(6, x: 900)
        try replay.scan([2, 3, 6])
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3, 6])
        try ReplayTime.$elapsed.withValue(.milliseconds(1900)) {
            _ = replay.window(7, x: 900, count: 2, bar: AXUIElement(owner: 7))
            let update = try replay.scan([3, 7])
            #expect(update.replacements == [7: 6])
        }
        _ = replay.window(2, x: 20000)
        let update = try ReplayTime.$elapsed.withValue(.milliseconds(2100)) { try replay.scan([3, 7]) }
        #expect(update.absorptions.isEmpty)
    }

    @Test func recoveredMergeRemainsQueuedUntilAcknowledged() throws {
        let replay = Replay()
        let sourceBar = try replay.mergeSetup(sourceWidth: 400)
        sourceBar.owner = nil
        _ = replay.window(2, x: 20000, width: 400)
        _ = replay.window(3, count: 3, bar: AXUIElement(owner: 3))
        try replay.scan([3])
        _ = replay.window(2, x: 20000)
        #expect(try replay.scan([3], acknowledge: false).absorptions == [2: 3])
        #expect(try replay.scan([3]).absorptions == [2: 3])
        #expect(try replay.scan([3]).absorptions.isEmpty)
    }

    @Test func recoverySwitchBackBeforeCommitKeepsTheOriginalManagedSlot() throws {
        let replay = Replay()
        _ = replay.window(1, width: 400)
        try replay.scan([1])
        let bar = AXUIElement(owner: 2)
        _ = replay.window(2, count: 2, bar: bar)
        try replay.scan([2]) // Both models can now exist, on different workspaces.
        _ = replay.window(1)
        #expect(try replay.scan([2], acknowledge: false).replacements == [2: 1])
        _ = replay.window(1, count: 2, bar: bar)
        let update = try replay.scan([1], acknowledge: false)
        #expect(update.replacements == [2: 1, 1: 2])
        #expect(nativeTabTransferPlan(replacements: update.replacements, managedWindowIds: [1, 2], exposedWindowIds: [1]).isEmpty)
    }
}
