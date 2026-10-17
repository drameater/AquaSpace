@testable import WindowDiscoveryReplay
import Foundation
import Testing

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct NoOpIntentTest {
    enum Assignment: CaseIterable, Sendable { case floating, workspace, fullscreenOn, fullscreenOff }
    enum FocusStop: CaseIterable, Sendable { case workspace, dfs, outerFrame }

    func prepare(_ choice: Assignment, _ window: MacWindow) {
        if choice == .floating { window.bindAsFloatingWindow(to: window.nodeWorkspace!) }
        window.isFullscreen = choice == .fullscreenOn
        Simulation.mutations = []
    }

    func assignment(_ choice: Assignment, _ window: MacWindow, failIfNoop: Bool = false) -> any Command {
        switch choice {
            case .floating:
                var args = LayoutCmdArgs(rawArgs: [], toggleBetween: [.floating]); args.failIfNoop = failIfNoop
                return LayoutCommand(args: args)
            case .workspace:
                return ReplaySameWorkspaceCommand(window: window, failIfNoop: failIfNoop)
            case .fullscreenOn, .fullscreenOff:
                return FullscreenCommand(args: FullscreenCmdArgs(window: window,
                    toggle: choice == .fullscreenOn ? .on : .off, failIfNoop: failIfNoop))
        }
    }

    func opposingRule(_ choice: Assignment, _ window: MacWindow) -> any Command {
        switch choice {
            case .floating: LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.tiling]))
            case .workspace: ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "automatic"))
            case .fullscreenOn, .fullscreenOff:
                FullscreenCommand(args: FullscreenCmdArgs(window: window,
                    toggle: choice == .fullscreenOn ? .off : .on))
        }
    }

    func focusArgs(_ stop: FocusStop) -> FocusCmdArgs {
        switch stop {
            case .workspace: FocusCmdArgs()
            case .dfs: FocusCmdArgs(target: .dfsRelative(.dfsNext))
            case .outerFrame: FocusCmdArgs(boundaries: .allMonitorsOuterFrame)
        }
    }

    @Test(arguments: Assignment.allCases, [false, true])
    func repeatedAssignmentSupersedesTheWaitingRule(choice: Assignment, failIfNoop: Bool) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        prepare(choice, window)
        let token = window.userMutationToken, parent = window.parent
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            let result = await Shell<any Command>.cmd(assignment(choice, window, failIfNoop: failIfNoop))
                .run(CmdEnv(windowId: 42), CmdIo())
            #expect(result.rawValue == (failIfNoop ? 1 : 0))
            #expect(window.userMutationToken != token)
            #expect(ReplayCoordinator.pendingRestoration.assignments[42] == nil)
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in entered.open(); await resume.wait(); return true },
            run: .cmd(opposingRule(choice, window)))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.parent === parent)
        #expect(window.isFullscreen == (choice == .fullscreenOn))
        #expect(Simulation.mutations.isEmpty)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test func floatingNoOpDoesNotInvalidateAnotherWindowInTheSameContainer() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: window.nodeWorkspace!)
        let sibling = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = sibling
        sibling.bindAsFloatingWindow(to: window.nodeWorkspace!)
        let token = window.userMutationToken, siblingToken = sibling.userMutationToken
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(assignment(.floating, sibling)).run(CmdEnv(windowId: 43), CmdIo())
            #expect(window.discoveryInitialization?.mayContinue == true)
            #expect(window.userMutationToken == token)
            #expect(sibling.userMutationToken != siblingToken)
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in entered.open(); await resume.wait(); return true },
            run: .cmd(opposingRule(.floating, window)))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(!window.isFloating)
        #expect(sibling.isFloating)
    }

    @Test(arguments: Assignment.allCases, [false, true])
    func automaticAndSessionNoOpsDoNotAcquireUserMutationProvenance(choice: Assignment, restore: Bool) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        prepare(choice, window)
        let token = window.userMutationToken
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isSessionRestore.withValue(restore) {
                await WindowRestorationMutation.$isAutomaticDiscovery.withValue(!restore) {
                    let result = await Shell<any Command>.cmd(assignment(choice, window)).run(CmdEnv(windowId: 42), CmdIo())
                    #expect(result.rawValue == 0)
                    #expect(window.userMutationToken == token)
                    #expect(window.discoveryInitialization?.mayContinue == true)
                    #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
                }
            }
        }
    }

    @Test(arguments: FocusStop.allCases)
    func acceptedFocusStopSupersedesOlderAutomaticFocus(stop: FocusStop) async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        _ = user.focusWindow() // Before initialization: this is not the newer input.
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            let result = await Shell<any Command>.cmd(FocusCommand(args: focusArgs(stop))).run(CmdEnv(windowId: 44), CmdIo())
            #expect(result.rawValue == 0)
            #expect(focus.windowOrNil === user)
            #expect(window.discoveryInitialization?.mayContinue == true)
            #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
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

    @Test func explicitFocusIntentPrecedesItsFirstAwait() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        _ = user.focusWindow()
        let matching = ReplayGate(), resumeOld = ReplayGate(), reading = ReplayGate(), resumeNew = ReplayGate(), oldDone = ReplayGate()
        let input = Task { @MainActor in
            await matching.wait()
            let result = await Shell<any Command>.cmd(FocusCommand(args: FocusCmdArgs(failIfMacosNativeFullscreen: true)))
                .run(CmdEnv(windowId: 44), CmdIo())
            #expect(result.rawValue == 0)
        }
        let interleave = Task { @MainActor in
            await reading.wait(); resumeOld.open()
            await oldDone.wait()
            #expect(focus.windowOrNil === user) // Older command must not focus even temporarily.
            resumeNew.open()
        }
        Simulation.awaitFullscreen = { reading.open(); await resumeNew.wait(); return false }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in matching.open(); await resumeOld.wait(); return true },
            run: .cmd(FocusCommand(args: FocusCmdArgs(target: .windowId(42)))))]
        await runOnWindowDetected(ifConventional: window)
        oldDone.open()
        await interleave.value; await input.value
        #expect(focus.windowOrNil === user)
    }

    @Test(arguments: FocusStop.allCases, [false, true])
    func automaticAndSessionFocusStopsDoNotCancelAutomaticFocus(stop: FocusStop, restore: Bool) async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let user = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = user
        user.parent = Workspace.get(byName: "user").rootTilingContainer
        _ = user.focusWindow()
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                await WindowRestorationMutation.$isSessionRestore.withValue(restore) {
                    await WindowRestorationMutation.$isAutomaticDiscovery.withValue(!restore) {
                        let result = await Shell<any Command>.cmd(FocusCommand(args: focusArgs(stop))).run(CmdEnv(windowId: 44), CmdIo())
                        #expect(result.rawValue == 0)
                    }
                }
                #expect(WindowDiscoveryInitialization.mayRunAutomaticFocus)
                _ = await FocusCommand(args: FocusCmdArgs(target: .windowId(42))).run(CmdEnv(windowId: 42), CmdIo())
                #expect(focus.windowOrNil === window)
            }
        }
    }

    @Test func rejectedFocusTargetDoesNotCancelUnrelatedAutomaticFocus() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        await WindowDiscoveryInitialization.run(for: window) {
            let result = await Shell<any Command>.cmd(FocusCommand(args: FocusCmdArgs())).run(CmdEnv(windowId: 999), CmdIo())
            #expect(result.rawValue == 1)
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                #expect(WindowDiscoveryInitialization.mayRunAutomaticFocus)
                _ = await FocusCommand(args: FocusCmdArgs(target: .windowId(42))).run(CmdEnv(windowId: 42), CmdIo())
                #expect(focus.windowOrNil === window)
            }
        }
    }
}
}

@MainActor struct ReplaySameWorkspaceCommand: Command {
    let window: MacWindow
    let failIfNoop: Bool
    var shouldResetClosedWindowsCache: Bool { true }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        moveWindowToWorkspace(window, window.nodeWorkspace!, io, focusFollowsWindow: false, failIfNoop: failIfNoop)
    }
}
