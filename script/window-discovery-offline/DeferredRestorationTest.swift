@testable import WindowDiscoveryReplay
import Foundation
import Testing

private func owner(_ pid: Int32 = 9, birth: UInt64 = 100, version: Int32 = 1,
                   executable: String = "/fixture/app") -> WindowRestorationOwner {
    WindowRestorationOwner(pid: pid, uid: 501, startedSeconds: birth, startedMicroseconds: 2,
        executable: executable, uniqueId: 7, execVersion: version, imageUUID: Array(repeating: 1, count: 16))
}
private func pending(_ ids: [UInt32] = [42], identity: WindowRestorationOwner? = owner()) -> PendingWindowRestoration {
    PendingWindowRestoration(windows: ids.map { [String($0), "saved", "floating", "true"] },
        owners: identity.map { identity in Dictionary(uniqueKeysWithValues: Set(ids).map { (String($0), identity) }) } ?? [:])
}

struct PendingRestorationPolicyTest {
    @Test func delayedWindowRetainsItsWorkspaceLayoutAndFullscreenUntilSuccessfulApply() {
        var state = pending()
        state.reconcile(ownersByWindow: [42: [9]], process: { _ in owner() })
        var applied: [PendingWindowAssignment] = []
        #expect(!state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: false,
            controlsWindows: true, userChanged: false) { applied.append($0); return true })
        #expect(state.assignments.count == 1)
        #expect(state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: true, userChanged: false) { applied.append($0); return true })
        #expect(applied.count == 1)
        #expect(applied.first?.workspace == "saved")
        #expect(applied.first?.floating == true)
        #expect(applied.first?.fullscreen == true)
        #expect(state.assignments.isEmpty)
    }
    @Test func failedModelCommitIsRetryableAndSuccessIsNotReplayed() {
        var state = pending(); var calls = 0
        #expect(!state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: true, userChanged: false) { _ in calls += 1; return false })
        #expect(state.assignments.count == 1)
        #expect(state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: true, userChanged: false) { _ in calls += 1; return true })
        #expect(!state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: true, userChanged: false) { _ in calls += 1; return true })
        #expect(calls == 2)
    }
    @Test func legacyMissingAndDuplicateIdentitiesCannotAuthorizeDeferredMoves() {
        #expect(pending(identity: nil).assignments.isEmpty)
        let duplicated = PendingWindowRestoration(windows: [["42", "a", "floating", "false"], ["42", "b", "h_tiles", "false"]], owners: ["42": owner()])
        #expect(duplicated.assignments.isEmpty)
        let malformed = PendingWindowRestoration(windows: [["042", "a", "floating", "false"], ["43", "a", "unknown", "false"]], owners: ["042": owner(), "43": owner()])
        #expect(malformed.assignments.isEmpty)
    }
    @Test func numericWindowAndPIDReuseExecAndExecutableChangesAreRejected() {
        for changed in [owner(10), owner(birth: 101), owner(version: 2), owner(executable: "/fixture/replaced")] {
            var state = pending(); var called = false
            #expect(!state.attempt(42, owner: changed, nativeOwners: [changed.pid], axVerified: true,
                controlsWindows: true, userChanged: false) { _ in called = true; return true })
            #expect(!called)
            #expect(state.assignments.isEmpty)
        }
    }
    @Test func ambiguousNativeOwnershipAndMissingProcessPermanentlyRevoke() {
        for native: Set<Int32>? in [nil, [], [9, 10], [10]] {
            var state = pending()
            #expect(!state.attempt(42, owner: owner(), nativeOwners: native, axVerified: true,
                controlsWindows: true, userChanged: false) { _ in Issue.record("Unowned mutation"); return true })
            #expect(state.assignments.isEmpty)
        }
        var state = pending()
        state.reconcile(ownersByWindow: [42: [9]], process: { _ in nil })
        #expect(state.assignments.isEmpty)
    }
    @Test func disappearanceAndUnreadableInventoryCannotBeResurrectedByReusedIDs() {
        for inventory: [UInt32: Set<Int32>]? in [nil, [:], [42: [10]]] {
            var state = pending()
            state.reconcile(ownersByWindow: inventory, process: { _ in owner() })
            state.reconcile(ownersByWindow: [42: [9]], process: { _ in owner() })
            #expect(state.assignments.isEmpty)
        }
    }
    @Test func frozenControllerNeverCommitsAndUserChangeRevokes() {
        var state = pending()
        #expect(!state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: false, userChanged: false) { _ in Issue.record("Frozen write"); return true })
        #expect(state.assignments.count == 1)
        #expect(!state.attempt(42, owner: owner(), nativeOwners: [9], axVerified: true,
            controlsWindows: true, userChanged: true) { _ in Issue.record("User state overwritten"); return true })
        #expect(state.assignments.isEmpty)
    }
    @Test func unresolvedMetadataSurvivesUpdateExportWithoutReplayingDesktopFocus() throws {
        let base = WindowManagerSession(windows: "99\tcurrent\th_tiles\tfalse", workspaces: "current\th_tiles", workspace: "current", focusedWindow: "99")
        let merged = base.retainingPending(pending().assignments)
        #expect(merged.windows.contains(["42", "saved", "floating", "true"]))
        #expect(merged.owners?["42"] == owner())
        #expect(merged.workspace == "current" && merged.focusedWindow == "99")
        let decoded = try JSONDecoder().decode(WindowManagerSession.self, from: JSONEncoder().encode(merged))
        #expect(decoded == merged)
        var colliding = WindowManagerSession(windows: "42\tuser\th_tiles\tfalse", workspaces: "", workspace: "user", focusedWindow: "")
        colliding.owners = ["42": owner(10)]
        #expect(colliding.retainingPending(pending().assignments).windows == colliding.windows)
    }
    @Test func identityMatchedFailedAssignmentOverridesOnlyItsOwnStagingRowOnExport() {
        var base = WindowManagerSession(windows: "42\tstaging\th_tiles\tfalse\n99\tuser\th_tiles\tfalse", workspaces: "", workspace: "user", focusedWindow: "99")
        base.owners = ["42": owner()]
        let merged = base.retainingPending(pending().assignments)
        #expect(merged.windows.contains(["42", "saved", "floating", "true"]))
        #expect(merged.windows.contains(["99", "user", "h_tiles", "false"]))
    }

    @Test func pendingRootLayoutSurvivesExportButCannotReplaceExistingUserRootMetadata() {
        let state = PendingWindowRestoration(windows: [["42", "saved", "h_tiles", "false"]], owners: ["42": owner()],
            workspaces: [["saved", "v_accordion"]])
        let absent = WindowManagerSession(windows: "", workspaces: "", workspace: "user", focusedWindow: "")
        #expect(absent.retainingPending(state.assignments).workspaces == [["saved", "v_accordion"]])
        let current = WindowManagerSession(windows: "", workspaces: "saved\th_tiles", workspace: "user", focusedWindow: "")
        #expect(current.retainingPending(state.assignments).workspaces == [["saved", "h_tiles"]])
    }
    @Test func oldSessionJSONDecodesWithoutAuthorizingPendingAssignments() throws {
        let data = Data(#"{"windows":[["42","saved","floating","false"]],"workspaces":[],"workspace":"user","focusedWindow":""}"#.utf8)
        let old = try JSONDecoder().decode(WindowManagerSession.self, from: data)
        #expect(old.owners == nil)
        #expect(PendingWindowRestoration(windows: old.windows, owners: old.owners ?? [:]).assignments.isEmpty)
    }

}

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct DeferredRestorationAdapterTest {
    func setup() -> (ReplayAquaApp, MacWindow, AXUIElement) {
        Simulation.owners = [42: [9]]; Simulation.processes = [9: owner()]
        Simulation.permission = WindowControlPermission()
        Simulation.controls = true; Simulation.peers = []; Simulation.inventoryError = false
        Simulation.yields = 0; Simulation.stops = 0; Simulation.mutations = []; Simulation.foreground = 99
        Simulation.detectedRule = nil; Simulation.callbacks = nil; Simulation.beforeDetection = nil
        Simulation.awaitSize = nil; Simulation.awaitType = nil; Simulation.awaitFullscreen = nil
        Simulation.awaitMinimized = nil; Simulation.awaitWindowCount = nil
        Simulation.nativeWrites = []; Simulation.unbinds = []; Simulation.awaitCenter = nil
        Simulation.commandRefresh = nil
        Simulation.geometryTarget = nil; Simulation.mruWrites = []
        Simulation.normalizeOrientation = false
        global_layoutForNextDetectedWindow = nil
        TrayMenuModel.shared.isEnabled = true; focus.windowOrNil = nil; Workspace.registry = [:]
        ReplayCoordinator.pendingRestoration = pending()
        ReplayCoordinator.restoringPending = false
        ReplayCoordinator.restorationAnchors = [:]; ReplayCoordinator.restorationRootTokens = [:]
        let app = ReplayAquaApp(), root = AXUIElement(owner: 42)
        root.set(Ax.roleAttr, kAXWindowRole); root.set(Ax.subroleAttr, kAXStandardWindowSubrole)
        root.set(Ax.childrenAttr, [AXUIElement]())
        root.attributes["pid"] = Int32(9)
        app.seed([root]); app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: root)])
        let window = MacWindow(42, app: app); MacWindow.allWindowsMap = [42: window]
        return (app, window, root)
    }
    @Test func actualAdapterRestoresOnlyTheMatchingBackgroundWindowOnce() async {
        let (_, window, _) = setup()
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(window.parent?.floating == true)
        #expect(window.isFullscreen)
        #expect(Simulation.mutations == [42])
        #expect(focus.windowOrNil == nil)
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations == [42])
    }
    @Test func rediscoveryAndTransientAXFailurePreservePendingIntent() async {
        let (app, window, root) = setup()
        MacWindow.allWindowsMap = [:]
        await ReplayCoordinator.restorePendingAssignments()
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        MacWindow.allWindowsMap = [42: window]
        app.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: root)])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations == [42])
    }
    @Test func freshAXProofRefusesWrongPIDRootAndDuplicateMembership() async throws {
        let (app, _, root) = setup()
        root.attributes["pid"] = Int32(10)
        #expect(try await app.restorationWindowProof(42, level: .normalWindow) == nil)
        root.attributes["pid"] = Int32(9)
        let foreign = AXUIElement(owner: 42); foreign.attributes["pid"] = Int32(9)
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: foreign)])
        #expect(try await app.restorationWindowProof(42, level: .normalWindow) == nil)
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: root), (windowId: UInt32(42), ax: root)])
        #expect(try await app.restorationWindowProof(42, level: .normalWindow) == nil)
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: root), (windowId: UInt32(42), ax: foreign)])
        #expect(try await app.restorationWindowProof(42, level: .normalWindow) == nil)
    }
    @Test func popupAndUnconventionalWindowsAreNotPromoted() async {
        let (_, window, root) = setup(); root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        root.attributes["windowType"] = AxUiElementWindowType.window; window.specialParent = .macosFullscreenWindowsContainer
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
    }
    @Test func dialogClassificationStillRequiresFloating() async {
        let (_, window, root) = setup()
        ReplayCoordinator.pendingRestoration = PendingWindowRestoration(windows: [["42", "saved", "h_tiles", "false"]], owners: ["42": owner()])
        root.attributes["windowType"] = AxUiElementWindowType.dialog
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.parent?.floating == true)
    }
    @Test func foregroundWindowAndUserMovementAcrossAXReadAreNeverOverridden() async {
        let (_, window, _) = setup(); focus.windowOrNil = window
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        let (app, second, _) = setup()
        app.thread?.afterRead = { second.parent = Workspace.get(byName: "user").rootTilingContainer }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(second.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }
    @Test func nativeForegroundChangeAcrossReadIsNotOverridden() async {
        let (app, _, _) = setup()
        app.nsApp.isActive = true
        app.axApp.threadGuarded.set(Ax.focusedWindowAttr, (windowId: UInt32(42), ax: AXUIElement(owner: 42)))
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }
    @Test func changedProcessAndNativeOwnerAcrossAXReadCannotMoveTheWindow() async {
        let (app, _, _) = setup()
        app.thread?.afterRead = { Simulation.processes[9] = owner(birth: 101) }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        let (next, _, _) = setup()
        next.thread?.afterRead = { Simulation.owners = [42: [10]] }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
    }
    @Test func competitorFreezesAndUncertainInventoryRetriesBeforeAnyCommit() async {
        let (app, _, _) = setup(); app.thread?.afterRead = { Simulation.peers = [123] }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(!Simulation.controls)
        #expect(Simulation.yields == 1)
        let (next, _, _) = setup(); next.thread?.afterRead = { Simulation.inventoryError = true }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(Simulation.stops == 0) // A failed scan is retried; it does not stop AquaSpace.
        #expect(!ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }
    @Test func disabledControllerAndModelReplacementCannotCommit() async {
        _ = setup(); TrayMenuModel.shared.isEnabled = false
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        let (app, _, _) = setup()
        app.thread?.afterRead = { MacWindow.allWindowsMap[42] = MacWindow(42, app: app) }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }
    @Test func changedRegisteredAXRootBetweenRetriesRevokesTheAssignment() async {
        let (app, _, root) = setup(); root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        let replacement = AXUIElement(owner: 42); replacement.attributes["pid"] = Int32(9)
        replacement.set(Ax.childrenAttr, [AXUIElement]())
        app.seed([replacement]); app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: replacement)])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }
    @Test func finalNativeForegroundAndUnreadableForegroundCannotBeOverridden() async {
        let (app, _, _) = setup()
        app.thread?.afterRead = { Simulation.foreground = 9 }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        let (next, _, _) = setup()
        next.thread?.afterRead = { Simulation.foreground = nil }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func replacementAXRootDuringTheFirstReadCannotInheritOldIntent() async {
        let (app, _, _) = setup()
        app.thread?.afterRead = {
            let replacement = AXUIElement(owner: 42); replacement.attributes["pid"] = Int32(9)
        replacement.set(Ax.childrenAttr, [AXUIElement]())
            app.seed([replacement])
            app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: replacement)])
        }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }

    @Test func nativeTabGroupCannotBeMovedByAStockPerTabAssignment() async {
        let (_, _, root) = setup()
        let bar = AXUIElement(owner: 42); bar.set(Ax.roleAttr, kAXTabGroupRole)
        bar.set(Ax.tabsAttr, (0..<2).map { _ in let tab = AXUIElement(owner: 42); tab.set(Ax.subroleAttr, "AXTabButton"); return tab })
        root.set(Ax.childrenAttr, [bar])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
    }
    @Test func provenNativeTabOwnershipSupersedesOnlyTheMatchingAppsPendingIntent() {
        let updates = [
            NativeTabUpdate(replacements: [42: 99], absorptions: [:], inheritances: [:], exposedWindowIds: [42]),
            NativeTabUpdate(replacements: [:], absorptions: [42: 99], inheritances: [:], exposedWindowIds: [99]),
            NativeTabUpdate(replacements: [:], absorptions: [:], inheritances: [42: 99], exposedWindowIds: [42, 99]),
        ]
        for update in updates {
            _ = setup()
            ReplayCoordinator.cancelPendingNativeTabAssignments(update, pid: 10)
            #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
            ReplayCoordinator.cancelPendingNativeTabAssignments(update, pid: 9)
            #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        }
    }
    @Test func unreadableNativeTitlebarMembershipCannotAuthorizeDelayedMoves() async {
        let (_, _, root) = setup()
        root.attributes.removeValue(forKey: Ax.childrenAttr.name)
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        let bar = AXUIElement(owner: 42); bar.set(Ax.roleAttr, kAXTabGroupRole)
        root.set(Ax.childrenAttr, [bar])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func recreatedEmptyWorkspaceRecoversRootLayoutWithoutOverwritingAnExistingWorkspace() async {
        let (_, window, _) = setup()
        ReplayCoordinator.pendingRestoration = PendingWindowRestoration(windows: [["42", "saved", "h_tiles", "false"]],
            owners: ["42": owner()], workspaces: [["saved", "v_accordion"]])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.rootTilingContainer.layout == .accordion)
        #expect(window.nodeWorkspace?.rootTilingContainer.orientation == .v)
        let (_, second, _) = setup()
        let existing = Workspace.get(byName: "saved")
        ReplayCoordinator.pendingRestoration = PendingWindowRestoration(windows: [["42", "saved", "h_tiles", "false"]],
            owners: ["42": owner()], workspaces: [["saved", "v_accordion"]])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(second.nodeWorkspace === existing)
        #expect(existing.rootTilingContainer.layout == .tiles)
        #expect(existing.rootTilingContainer.orientation == .h)
    }

    @Test func ordinaryNativeNormalizationDoesNotDiscardAnUnrestoredAssignment() async {
        let (_, window, _) = setup()
        window.specialParent = .macosFullscreenWindowsContainer
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        window.specialParent = nil
        window.bind(to: Workspace.get(byName: "normalized").rootTilingContainer, adaptiveWeight: 0, index: 0)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func explicitBackgroundMoveBeforeFirstRestorationAttemptWins() async {
        let (_, window, _) = setup()
        let result = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
            workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
        #expect(result.rawValue == 0)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }

    @Test func failedSecondProofCannotForgetTheFirstRootBeforeReplacement() async {
        let (app, window, root) = setup()
        app.thread?.afterRead = { root.attributes.removeValue(forKey: Ax.childrenAttr.name) }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        let replacement = AXUIElement(owner: 42)
        replacement.attributes["pid"] = Int32(9)
        replacement.set(Ax.childrenAttr, [AXUIElement]())
        app.seed([replacement])
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: replacement)])
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "default")
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }

    @Test func exportCannotLaunderRememberedRootReplacementThroughRestart() async throws {
        let (app, _, root) = setup()
        root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        #expect(ReplayCoordinator.restorationRootTokens[42] != nil)
        let replacement = AXUIElement(owner: 42)
        replacement.attributes["pid"] = Int32(9)
        replacement.set(Ax.childrenAttr, [AXUIElement]())
        app.seed([replacement])
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: replacement)])
        let exported = try await ReplayCoordinator.captureCurrentModel()
        #expect(exported.windows.first?.dropFirst().first == "default")
        let decoded = try JSONDecoder().decode(WindowManagerSession.self, from: JSONEncoder().encode(exported))
        ReplayCoordinator.pendingRestoration = PendingWindowRestoration(windows: decoded.windows,
            owners: decoded.owners ?? [:], workspaces: decoded.workspaces)
        ReplayCoordinator.restorationRootTokens = [:]
        ReplayCoordinator.restorationAnchors = [:]
        await ReplayCoordinator.restorePendingAssignments()
        #expect(MacWindow.allWindowsMap[42]?.nodeWorkspace?.name == "default")
    }

    @Test func explicitFullscreenBeforeFirstAnchorSupersedesSavedState() async {
        let (_, window, _) = setup()
        let result = await Shell<any Command>.cmd(FullscreenCommand(args: FullscreenCmdArgs(
            window: window, toggle: .on))).run(CmdEnv(), CmdIo())
        #expect(result.rawValue == 0 && window.isFullscreen)
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "default")
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
    }

    @Test func unrelatedCommandCannotCancelAnotherWindowsPendingAssignment() async {
        let (app, window, _) = setup()
        let unrelated = MacWindow(99, app: app)
        MacWindow.allWindowsMap[99] = unrelated
        _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: unrelated,
            workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func internalStartupCommandProvenanceDoesNotLeakToLaterUserCommand() async {
        let (_, window, _) = setup()
        _ = await WindowRestorationMutation.$isSessionRestore.withValue(true) {
            await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "startup"))).run(CmdEnv(), CmdIo())
        }
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
            workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func failedSecondProofWithUnchangedRootRemainsRetryable() async {
        let (app, window, root) = setup()
        let originalToken = app.windows.threadGuarded[42]?.restorationToken
        app.thread?.afterRead = { root.attributes.removeValue(forKey: Ax.childrenAttr.name) }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(ReplayCoordinator.restorationRootTokens[42] == originalToken)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        root.set(Ax.childrenAttr, [AXUIElement]())
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func unreadableEstablishedRootBlocksExportWithoutDroppingRetryableIntent() async throws {
        let (app, _, root) = setup()
        root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        let token = ReplayCoordinator.restorationRootTokens[42]
        app.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        do {
            _ = try await ReplayCoordinator.sessionForUpdate()
            Issue.record("Unverifiable root must not be exported")
        } catch {}
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        #expect(ReplayCoordinator.restorationRootTokens[42] == token)
        app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: root)])
        let json = try await ReplayCoordinator.sessionForUpdate()
        let exported = try JSONDecoder().decode(WindowManagerSession.self, from: Data(json.utf8))
        #expect(exported.windows.contains(["42", "saved", "floating", "true"]))
        #expect(exported.owners?["42"] == owner())
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func undiscoveredUntouchedAssignmentStillSurvivesExport() async throws {
        _ = setup()
        MacWindow.allWindowsMap = [:]
        let json = try await ReplayCoordinator.sessionForUpdate()
        let exported = try JSONDecoder().decode(WindowManagerSession.self, from: Data(json.utf8))
        #expect(exported.windows == [["42", "saved", "floating", "true"]])
        #expect(exported.owners?["42"] == owner())
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func rootReplacementDuringExportReadCannotCarryOldIntent() async throws {
        let (app, _, root) = setup()
        root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        app.thread?.afterRead = {
            let replacement = AXUIElement(owner: 42)
            replacement.attributes["pid"] = Int32(9)
            replacement.set(Ax.childrenAttr, [AXUIElement]())
            app.seed([replacement])
            app.axApp.threadGuarded.set(Ax.windowsAttr, [(windowId: UInt32(42), ax: replacement)])
        }
        let json = try await ReplayCoordinator.sessionForUpdate()
        let exported = try JSONDecoder().decode(WindowManagerSession.self, from: Data(json.utf8))
        #expect(exported.windows.first?.dropFirst().first == "default")
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func peerArrivalAndUncertainInventoryDuringExportCannotReturnAUsableSession() async {
        for unreadable in [false, true] {
            let (app, _, root) = setup()
            root.attributes["windowType"] = AxUiElementWindowType.popup
            await ReplayCoordinator.restorePendingAssignments()
            app.thread?.afterRead = {
                if unreadable { Simulation.inventoryError = true }
                else { Simulation.peers = [123] }
            }
            do {
                _ = try await ReplayCoordinator.sessionForUpdate()
                Issue.record("Changed controller admission must reject export")
            } catch {}
            #expect(Simulation.controls == unreadable) // A failed scan alone does not freeze control.
            #expect(Simulation.mutations.isEmpty)
        }
    }

    @Test func independentUserTaskIsNotSuppressedByAnAwaitedStartupRestore() async {
        let (_, window, _) = setup()
        // Create input outside the restore scope, like the server listener.
        let input = Task { @MainActor in
            #expect(!WindowRestorationMutation.isSessionRestore)
            return await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo()).rawValue
        }
        await WindowRestorationMutation.$isSessionRestore.withValue(true) {
            #expect(WindowRestorationMutation.isSessionRestore)
            _ = await input.value
            #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        }
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func reusedNumericIDOnAnUnregisteredModelCannotCancelTheRealAssignment() async {
        let (app, real, _) = setup()
        let impostor = MacWindow(42, app: app)
        _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: impostor,
            workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(real.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func unavailableSecondExportProofPreservesEstablishedRootAndPendingState() async throws {
        let (app, _, root) = setup()
        root.attributes["windowType"] = AxUiElementWindowType.popup
        await ReplayCoordinator.restorePendingAssignments()
        let token = ReplayCoordinator.restorationRootTokens[42]
        app.thread?.afterRead = { root.attributes.removeValue(forKey: Ax.childrenAttr.name) }
        do {
            _ = try await ReplayCoordinator.sessionForUpdate()
            Issue.record("A failed second export proof must block export")
        } catch {}
        #expect(ReplayCoordinator.restorationRootTokens[42] == token)
        #expect(ReplayCoordinator.pendingRestoration.assignments.count == 1)
        root.set(Ax.childrenAttr, [AXUIElement]())
        let json = try await ReplayCoordinator.sessionForUpdate()
        let exported = try JSONDecoder().decode(WindowManagerSession.self, from: Data(json.utf8))
        #expect(exported.windows.contains(["42", "saved", "floating", "true"]))
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func automaticDiscoveryFloatingRuleCannotDiscardSavedWorkspace() async {
        let (_, window, _) = setup()
        Simulation.detectedRule = { detected in
            _ = await Shell<any Command>.cmd(ReplayFloatCommand(window: detected)).run(CmdEnv(), CmdIo())
        }
        await runOnWindowDetected(ifConventional: window)
        #expect(window.isFloating)
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func independentUserMoveWhileAutomaticDetectionAwaitsStillWins() async {
        let (_, window, _) = setup()
        let input = Task { @MainActor in
            await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo()).rawValue
        }
        Simulation.detectedRule = { _ in _ = await input.value }
        await runOnWindowDetected(ifConventional: window)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "user")
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func manuallyRequestedDetectionRuleStillSupersedesSavedIntent() async {
        let (_, window, _) = setup()
        Simulation.detectedRule = { detected in
            _ = await Shell<any Command>.cmd(ReplayFloatCommand(window: detected)).run(CmdEnv(), CmdIo())
        }
        // Explicit run-callback invokes this entrypoint directly, outside the
        // registration-only automatic-discovery scope.
        _ = await onWindowDetected(CmdEnv(), CmdIo(), window)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "default")
        #expect(window.isFloating)
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func pendingRestoreWaitsForAwaitedDiscoveryMatcherAndItsMutation() async {
        let (_, window, _) = setup()
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            await ReplayCoordinator.restorePendingAssignments()
            #expect(window.nodeWorkspace?.name == "default")
            #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
            #expect(Simulation.mutations.isEmpty)
            return true
        }, run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        await runOnWindowDetected(ifConventional: window)
        #expect(window.nodeWorkspace?.name == "rule")
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(window.isFloating && window.isFullscreen)
        #expect(Simulation.mutations == [42])
    }

    @Test func explicitUserMoveInvalidatesTheAwaitedAutomaticMatcher() async {
        let (_, window, _) = setup()
        let matched = ReplayGate(), resume = ReplayGate()
        // Created outside initialization, matching an independent input task.
        let input = Task { @MainActor in
            await matched.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            matched.open(); await resume.wait(); return true
        }, run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(ReplayCoordinator.pendingRestoration.assignments.isEmpty)
        #expect(Simulation.mutations == [42])
    }

    @Test func userMoveStopsLaterCommandsInTheSameAutomaticCallback() async {
        let (_, window, _) = setup()
        let matched = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await matched.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            .cmd(ReplayAwaitCommand { matched.open(); await resume.wait() }),
            .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "late"))),
        ]))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations == [42])
    }

    @Test func userMoveSkipsDeferredNextWindowLayoutAfterMatcherAwait() async {
        let (_, window, _) = setup()
        let matched = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await matched.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            _ = await Shell<any Command>.cmd(ReplayTileCommand(window: window)).run(CmdEnv(), CmdIo())
            resume.open()
        }
        global_layoutForNextDetectedWindow = .floating
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            matched.open(); await resume.wait(); return false
        }, run: .empty)]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(!window.isFloating)
    }

    @Test func registrationDefersRestorationBeforeTheFirstCallbackRead() async throws {
        let (_, window, _) = setup()
        MacWindow.allWindowsMap = [:]
        Simulation.beforeDetection = {
            #expect(MacWindow.allWindowsMap[42] === window)
            await ReplayCoordinator.restorePendingAssignments()
            #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
            #expect(Simulation.mutations.isEmpty)
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true },
            run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        try await MacWindow.initializeRegistered(window)
        #expect(window.discoveryInitialization?.isInProgress == false)
        Simulation.mutations = []
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
        #expect(Simulation.mutations == [42])
    }

    @Test func failedRegistrationReleasesTheInitializationBarrier() async {
        let (_, window, _) = setup()
        Simulation.beforeDetection = { throw CancellationError() }
        do { try await MacWindow.initializeRegistered(window); Issue.record("Expected interrupted initialization") }
        catch {}
        #expect(window.discoveryInitialization?.isInProgress == false)
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
    }

    @Test func userMoveBeforeDiscoveryCallbacksPreventsTheirDispatch() async throws {
        let (_, window, _) = setup()
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.beforeDetection = { entered.open(); await resume.wait() }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            Issue.record("Superseded initialization ran a callback matcher"); return true
        }, run: .cmd(ReplayFloatCommand(window: window)))]
        try await MacWindow.initializeRegistered(window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations == [42])
    }

    @Test func userMoveWithoutPendingAssignmentStillInvalidatesAutomaticCallback() async {
        let (_, window, _) = setup()
        ReplayCoordinator.pendingRestoration.removeAll()
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            entered.open(); await resume.wait(); return true
        }, run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations == [42])
    }

    @Test func replacingTheRegisteredModelRejectsItsOldCallbackContinuation() async {
        let (app, window, _) = setup()
        let replacement = MacWindow(42, app: app)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            MacWindow.allWindowsMap[42] = replacement; return true
        }, run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        await runOnWindowDetected(ifConventional: window)
        #expect(Simulation.mutations.isEmpty)
        #expect(replacement.nodeWorkspace?.name == "default")
    }

    @Test func userMutationOfAnotherWindowDoesNotCancelThisInitialization() async {
        let (app, window, _) = setup()
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: other,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            entered.open(); await resume.wait(); return true
        }, run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "rule"))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "rule")
        #expect(other.nodeWorkspace?.name == "user")
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
    }

    @Test func initializationStartingDuringNativeProofDelaysTheCommit() async {
        let (app, window, _) = setup()
        var initialization: WindowDiscoveryInitialization?
        app.thread?.afterRead = { initialization = WindowDiscoveryInitialization(window) }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        initialization?.finish()
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
    }

    @Test func initializationFinishingDuringNativeProofAlsoRequiresANewAttempt() async {
        let (app, window, _) = setup()
        app.thread?.afterRead = { WindowDiscoveryInitialization(window).finish() }
        await ReplayCoordinator.restorePendingAssignments()
        #expect(Simulation.mutations.isEmpty)
        #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
        await ReplayCoordinator.restorePendingAssignments()
        #expect(window.nodeWorkspace?.name == "saved")
    }

    @Test func initializingWindowCannotEnterTheImmediateStartupCommandPlan() async {
        let (_, window, _) = setup()
        let initialization = WindowDiscoveryInitialization(window)
        defer { initialization.finish() }
        var session = WindowManagerSession(windows: "42\tsaved\tfloating\ttrue", workspaces: "", workspace: "", focusedWindow: "42")
        session.owners = ["42": owner()]
        ReplayHandoff.startupSession = session; ReplayHandoff.commands = []; ReplayHandoff.onCommand = nil
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.commands.isEmpty)
        #expect(ReplayHandoff.pendingRestoration.assignments[42] != nil)
    }

    @Test func initializationBeginningDuringStartupCommandsKeepsItsAssignmentPending() async {
        let (_, window, _) = setup()
        var session = WindowManagerSession(windows: "42\tsaved\tfloating\ttrue", workspaces: "", workspace: "", focusedWindow: "")
        session.owners = ["42": owner()]
        ReplayHandoff.startupSession = session; ReplayHandoff.commands = []
        var initialization: WindowDiscoveryInitialization?
        ReplayHandoff.onCommand = { _ in initialization = WindowDiscoveryInitialization(window) }
        defer { ReplayHandoff.onCommand = nil; initialization?.finish() }
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.commands.count == 1)
        #expect(ReplayHandoff.pendingRestoration.assignments[42] != nil)
    }

}
}
