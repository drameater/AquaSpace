@testable import WindowDiscoveryReplay
import Foundation
import Testing

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct CallbackSafetyTest {
    @Test(arguments: [LayoutCmdArgs.LayoutDescription.h_accordion, .vertical, .v_accordion, .h_tiles, .tiling])
    func explicitContainerChoiceSupersedesAWaitingRule(choice: LayoutCmdArgs.LayoutDescription) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        let parent = window.parent!
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [choice])))
                .run(CmdEnv(windowId: 42), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in entered.open(); await resume.wait(); return true },
            run: .cmd(LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.v_accordion]))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(parent.layout == (choice == .h_accordion || choice == .v_accordion ? .accordion : .tiles))
        #expect(parent.orientation == (choice == .vertical || choice == .v_accordion ? .v : .h))
    }

    @Test func normalizedAncestorChangeInvalidatesEveryAffectedBranch() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        Simulation.normalizeOrientation = true
        let root = window.parent!
        let child = ReplayParent(root.workspace, floating: false); child.parent = root
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other; other.parent = child
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.vertical])))
                .run(CmdEnv(windowId: 43), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in entered.open(); await resume.wait(); return true },
            run: .cmd(LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.v_accordion]))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(root.orientation == .h)
        #expect(root.layout == .tiles)
        #expect(child.orientation == .v)
    }

    enum FocusChoice: CaseIterable, Sendable { case window, workspace, sameWindow, externalWindow, externalWorkspace }
    @Test(arguments: FocusChoice.allCases)
    func focusDuringAnAutomaticNativeReadWins(choice: FocusChoice) async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let neighbor = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = neighbor
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        if choice == .sameWindow { _ = user.focusWindow() }
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            if choice == .externalWindow || choice == .externalWorkspace {
                // Native focus-cache and menu/mouse paths do not use Shell's
                // explicit-command provenance. Exercise the real setter too.
                _ = setFocus(to: ReplayFocus(windowOrNil: choice == .externalWorkspace ? nil : user,
                    emptyWorkspace: Workspace.get(byName: "user")))
            } else {
                _ = await Shell<any Command>.cmd(ReplayFocusChoice(window: choice == .workspace ? nil : user,
                    workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            }
            resume.open()
        }
        Simulation.awaitFullscreen = { entered.open(); await resume.wait(); return false }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true },
            run: .cmd(FocusCommand(args: FocusCmdArgs(failIfMacosNativeFullscreen: true))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(focus.workspace.name == "user")
        #expect(focus.windowOrNil === (choice == .workspace || choice == .externalWorkspace ? nil : user))
        #expect(window.discoveryInitialization?.mayContinue == false) // finished, not a leaked lifetime
    }

    @Test func focusDuringMatchingBlocksLaterFocusButAllowsBackgroundAssignment() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayFocusChoice(window: user, workspace: user.nodeWorkspace!)).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in entered.open(); await resume.wait(); return true },
            run: .seq([.cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "assigned"))),
                .cmd(FocusCommand(args: FocusCmdArgs(target: .windowId(42))))]))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "assigned")
        #expect(focus.windowOrNil === user)
        #expect(focus.workspace.name == "user")
    }

    @Test func focusDuringFloatingPreparationLeavesNewFocusAndBindingsAlone() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayFocusChoice(window: user, workspace: user.nodeWorkspace!)).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { _ in entered.open(); await resume.wait(); return .zero }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true },
            run: .cmd(FocusCommand(args: FocusCmdArgs(target: .windowId(42), floatingAsTiling: true))))]
        Simulation.mutations = []
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(focus.windowOrNil === user)
        #expect(window.isFloating)
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test func validAutomaticFocusStillRuns() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true },
            run: .cmd(FocusCommand(args: FocusCmdArgs(target: .windowId(42)))))]
        await runOnWindowDetected(ifConventional: window)
        #expect(focus.windowOrNil === window)
    }

    @Test(arguments: [false, true])
    func freezeBeforeDispatchRejectsNewAutomaticClose(inventoryFailure: Bool) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in
            Simulation.inventoryError = inventoryFailure
            Simulation.peers = inventoryFailure ? [] : [123]
            do { _ = try await ReplayCoordinator.sessionForUpdate(); Issue.record("Capture must fail closed") } catch {}
            // Only a detected peer freezes; a failed scan rejects the export but keeps control.
            #expect(ReplayCoordinator.canControlWindows == inventoryFailure)
            #expect(!ReplayCoordinator.isYielding) // precisely the pre-watcher interval
            return true
        }, run: .cmd(CloseCommand(args: CloseCmdArgs())))]
        await runOnWindowDetected(ifConventional: window)
        if !inventoryFailure {
            #expect(Simulation.nativeWrites.isEmpty)
            #expect(MacWindow.allWindowsMap[42] === window)
        }
    }

    @Test func explicitCloseCannotBypassFrozenAuthority() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        ReplayCoordinator.freezeAXWrites()
        _ = await Shell<any Command>.cmd(CloseCommand(args: CloseCmdArgs())).run(CmdEnv(windowId: 42), CmdIo())
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(MacWindow.allWindowsMap[42] === window)
    }

    @Test func startupMayInitializeTheModelButCannotTerminateAnApplication() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        Simulation.permission = WindowControlPermission()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            .cmd(LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.tiling]))),
            .cmd(CloseCommand(args: CloseCmdArgs())),
        ]))]
        await runOnWindowDetected(ifConventional: window)
        #expect(!window.isFloating)
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(MacWindow.allWindowsMap[42] === window)
    }

    @Test func aFreezePermanentlyRevokesTheOldInitializationEvenAfterAClaim() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let before = WindowCommandContinuation(window)
                ReplayCoordinator.freezeAXWrites()
                let after = WindowCommandContinuation(window)
                #expect(!before.mayContinue && !after.mayContinue)
                Simulation.permission.claim()
                #expect(!before.mayContinue && !after.mayContinue)
                #expect(!WindowDiscoveryInitialization.mayRunAutomaticCommand)
            }
        }
    }
}
}

@MainActor struct ReplayFocusChoice: Command {
    let window: MacWindow?
    let workspace: Workspace
    var shouldResetClosedWindowsCache: Bool { false }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        .from(bool: setFocus(to: ReplayFocus(windowOrNil: window, emptyWorkspace: workspace)))
    }
}
