@testable import WindowDiscoveryReplay
import Foundation
import Testing

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct CrossWindowCallbackChainTest {
    enum SuccessChain: CaseIterable, Sendable {
        case and, sequence, pipeline
        func joining(_ first: Shell<any Command>, _ next: Shell<any Command>) -> Shell<any Command> {
            switch self {
                case .and: .and([first, next])
                case .sequence: .seq([first, next])
                case .pipeline: .pipe([first, next])
            }
        }
    }
    enum Chain: CaseIterable, Sendable {
        case fallback, sequence, pipeline

        func joining(_ first: Shell<any Command>, _ next: Shell<any Command>) -> Shell<any Command> {
            switch self {
                case .fallback: .or([first, next])
                case .sequence: .seq([first, next])
                case .pipeline: .pipe([first, next])
            }
        }
    }

    @Test(arguments: Chain.allCases, CallbackCommandContinuationTest.Operation.allCases)
    func staleCrossWindowCommandStopsItsWholeChain(chain: Chain, operation: CallbackCommandContinuationTest.Operation) async {
        await rejectedChain(chain, operation: operation, readThrows: false)
    }

    @Test(arguments: Chain.allCases, CallbackCommandContinuationTest.Operation.allCases)
    func failedReadAlsoStopsItsStaleCrossWindowChain(chain: Chain, operation: CallbackCommandContinuationTest.Operation) async {
        await rejectedChain(chain, operation: operation, readThrows: true)
    }

    private func rejectedChain(_ chain: Chain, operation: CallbackCommandContinuationTest.Operation, readThrows: Bool) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        target.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: target,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        let helpers = CallbackCommandContinuationTest()
        helpers.installReadGate(operation, entered: entered, resume: resume)
        if readThrows {
            switch operation {
                case .tiling: Simulation.awaitSize = { entered.open(); await resume.wait(); throw CancellationError() }
                case .relayout: Simulation.awaitType = { entered.open(); await resume.wait(); throw CancellationError() }
                case .fullscreen: Simulation.awaitFullscreen = { entered.open(); await resume.wait(); throw CancellationError() }
                case .minimize: Simulation.awaitMinimized = { entered.open(); await resume.wait(); throw CancellationError() }
                case .close: Simulation.awaitWindowCount = { entered.open(); await resume.wait(); throw CancellationError() }
            }
        }
        let first: Shell<any Command> = .cmd(ReplayRetargetCommand(command: helpers.command(operation, window: target), id: 43))
        var nextCommands = 0, laterCallbacks = 0
        // Nest commands so rejection must survive both the outer operator and
        // inner dispatch; neither workspace changes nor close may be reached.
        let next: Shell<any Command> = .seq([
            .cmd(ReplayAwaitCommand { nextCommands += 1 }),
            .cmd(ReplayMoveCommand(window: target, workspace: Workspace.get(byName: "stale"))),
            .cmd(ReplayRetargetCommand(command: CloseCommand(args: CloseCmdArgs(quitIfLastWindow: false)), id: 43)),
        ])
        Simulation.callbacks = [
            WindowDetectedCallback(match: { _ in true }, run: chain.joining(first, next), checkFurtherCallbacks: true),
            WindowDetectedCallback(match: { _ in true }, run: .cmd(ReplayAwaitCommand { laterCallbacks += 1 })),
        ]
        await runOnWindowDetected(ifConventional: detected)
        await input.value
        #expect(nextCommands == 0 && laterCallbacks == 0)
        #expect(target.nodeWorkspace?.name == "user" && target.isFloating)
        #expect(MacWindow.allWindowsMap[43] === target)
        #expect(Simulation.mutations == [43])
        #expect(Simulation.unbinds.isEmpty && Simulation.nativeWrites.isEmpty)
        #expect(detected.nodeWorkspace?.name == "default")
        // Cancellation belongs to this initialization, not the process or a
        // task-global latch that would suppress independent window detection.
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(ReplayAwaitCommand { laterCallbacks += 1 }))]
        await runOnWindowDetected(ifConventional: target)
        #expect(laterCallbacks == 1)
    }

    @Test(arguments: Chain.allCases, [false, true])
    func ordinaryReadFailureKeepsFallbackAndLaterCommands(chain: Chain, unrelatedUserInput: Bool) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        let other = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = other
        target.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        Simulation.awaitFullscreen = {
            if unrelatedUserInput {
                await Task { @MainActor in
                    // Separate input provenance, as in the gated reproductions.
                    await WindowRestorationMutation.$isAutomaticDiscovery.withValue(false) {
                        _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: other,
                            workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
                    }
                }.value
            }
            throw CancellationError()
        }
        var reached = 0
        let first: Shell<any Command> = .cmd(ReplayRetargetCommand(
            command: MacosNativeFullscreenCommand(args: MacosNativeFullscreenCmdArgs()), id: 43))
        let next: Shell<any Command> = .seq([
            .cmd(ReplayAwaitCommand { reached += 1 }),
            .cmd(ReplayMoveCommand(window: target, workspace: Workspace.get(byName: "rule"))),
        ])
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: chain.joining(first, next))]
        await runOnWindowDetected(ifConventional: detected)
        #expect(reached == 1 && target.nodeWorkspace?.name == "rule")
        #expect(other.nodeWorkspace?.name == (unrelatedUserInput ? "user" : "default"))
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test func productionPipelineStillForwardsInputOutputAndErrors() async {
        _ = DeferredRestorationAdapterTest().setup()
        let io = CmdIo(input: "input")
        let pipeline: Shell<any Command> = .pipe([
            .cmd(ReplayIoCommand { io in #expect(io.readStdin() == "input"); io.out("middle"); io.err("first"); return .fail }),
            .cmd(ReplayIoCommand { io in #expect(io.readStdin() == "middle"); io.out("output"); io.err("second"); return .succ }),
        ])
        let result = await pipeline.run(CmdEnv(), io)
        #expect(result.rawValue != 0)
        #expect(io.stdout == ["output"] && io.stderr == ["first", "second"])
    }

    @Test(arguments: Chain.allCases)
    func newerTargetIntentDuringFailedCommandsRefreshStopsTheChain(chain: Chain) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        target.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: target,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitFullscreen = { throw CancellationError() }
        Simulation.commandRefresh = {
            guard WindowRestorationMutation.isAutomaticDiscovery else { return }
            Simulation.commandRefresh = nil
            entered.open(); await resume.wait()
        }
        var reached = 0
        let first: Shell<any Command> = .cmd(ReplayRetargetCommand(
            command: MacosNativeFullscreenCommand(args: MacosNativeFullscreenCmdArgs()), id: 43))
        let next: Shell<any Command> = .seq([
            .cmd(ReplayAwaitCommand { reached += 1 }),
            .cmd(ReplayMoveCommand(window: target, workspace: Workspace.get(byName: "stale"))),
        ])
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: chain.joining(first, next))]
        await runOnWindowDetected(ifConventional: detected)
        await input.value
        #expect(reached == 0 && target.nodeWorkspace?.name == "user")
        #expect(Simulation.mutations == [43] && Simulation.nativeWrites.isEmpty)
    }

    @Test func successfulOptionalProbeDoesNotCancelUnrelatedPlacement() async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        let workspace = Workspace.get(byName: "before")
        detected.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: other,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { window in
            if window === other { entered.open(); await resume.wait() }
            return CGPoint(x: Double(window.windowId), y: 0)
        }
        let probe: Shell<any Command> = .cmd(ReplayAwaitCommand {
            let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
            #expect(temporary.count == 1)
            restoreFloatingWindows(floatingWindows: temporary, workspace: workspace)
        })
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            probe, .cmd(ReplayMoveCommand(window: detected, workspace: Workspace.get(byName: "rule"))),
        ]))]
        await runOnWindowDetected(ifConventional: detected)
        await input.value
        #expect(detected.nodeWorkspace?.name == "rule" && detected.isFloating)
        #expect(other.nodeWorkspace?.name == "user" && other.isFloating)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test(arguments: SuccessChain.allCases)
    func successfulRequiredTargetStillRejectsNewIntentDuringRefresh(chain: SuccessChain) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        target.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: target,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitFullscreen = { false }
        Simulation.commandRefresh = {
            guard WindowRestorationMutation.isAutomaticDiscovery else { return }
            Simulation.commandRefresh = nil; entered.open(); await resume.wait()
        }
        var reached = 0
        let first: Shell<any Command> = .cmd(ReplayRetargetCommand(command: MacosNativeFullscreenCommand(args:
            MacosNativeFullscreenCmdArgs(toggle: .off)), id: 43))
        let next: Shell<any Command> = .seq([
            .cmd(ReplayAwaitCommand { reached += 1 }),
            .cmd(ReplayRetargetCommand(command: CloseCommand(args: CloseCmdArgs(quitIfLastWindow: false)), id: 43)),
        ])
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: chain.joining(first, next))]
        await runOnWindowDetected(ifConventional: detected); await input.value
        #expect(reached == 0 && target.nodeWorkspace?.name == "user")
        #expect(MacWindow.allWindowsMap[43] === target && Simulation.nativeWrites.isEmpty)
    }

    @Test func bulkCloseStopsBeforeItsNextNestedEffect() async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        for id: UInt32 in [43, 44] { MacWindow.allWindowsMap[id] = MacWindow(id, app: app) }
        let targets = detected.nodeWorkspace!.allLeafWindowsRecursive.filter { $0 !== detected }
        let victim = targets[0], untouched = targets[1]
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: victim,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitWindowCount = { entered.open(); await resume.wait(); return 2 }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run:
            .cmd(CloseAllWindowsButCurrentCommand(args: CloseAllWindowsButCurrentCmdArgs())))]
        await runOnWindowDetected(ifConventional: detected); await input.value
        #expect(victim.nodeWorkspace?.name == "user")
        #expect(MacWindow.allWindowsMap[untouched.windowId] === untouched)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test func bulkCloseProtectsLaterVictimsBeforeTheFirstAwait() async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        for id: UInt32 in [43, 44] { MacWindow.allWindowsMap[id] = MacWindow(id, app: app) }
        let targets = detected.nodeWorkspace!.allLeafWindowsRecursive.filter { $0 !== detected }
        let later = targets[1]
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: later,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        // One native window makes app termination particularly dangerous: no
        // earlier effect may execute using the stale bulk victim plan.
        Simulation.awaitWindowCount = { entered.open(); await resume.wait(); return 1 }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run:
            .cmd(CloseAllWindowsButCurrentCommand(args: CloseAllWindowsButCurrentCmdArgs())))]
        await runOnWindowDetected(ifConventional: detected); await input.value
        #expect(later.nodeWorkspace?.name == "user" && MacWindow.allWindowsMap[later.windowId] === later)
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(MacWindow.allWindowsMap.count == 3)
    }

    @Test(arguments: SuccessChain.allCases)
    func nestedShellTargetsSurviveTheWrappersFinalRefresh(chain: SuccessChain) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: target,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        var refreshes = 0, reached = 0
        Simulation.commandRefresh = {
            guard WindowRestorationMutation.isAutomaticDiscovery else { return }
            refreshes += 1
            if refreshes == 2 { Simulation.commandRefresh = nil; entered.open(); await resume.wait() }
        }
        // Production wrappers such as eval and trigger-binding call a nested
        // Shell, then their enclosing shell performs an additional refresh.
        let inner: Shell<any Command> = .cmd(ReplayRetargetCommand(command: MacosNativeFullscreenCommand(args:
            MacosNativeFullscreenCmdArgs(toggle: .off)), id: 43))
        let wrapper: Shell<any Command> = .cmd(ReplayAwaitCommand { _ = await inner.run(CmdEnv(), CmdIo()) })
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: chain.joining(wrapper,
            .cmd(ReplayAwaitCommand { reached += 1 })))]
        await runOnWindowDetected(ifConventional: detected); await input.value
        #expect(refreshes == 2 && reached == 0 && target.nodeWorkspace?.name == "user")
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test(arguments: [false, true])
    func targetlessSiblingRefreshCannotDiscardEarlierRequiredTargets(nested: Bool) async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: target,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        var refreshes = 0
        Simulation.commandRefresh = {
            guard WindowRestorationMutation.isAutomaticDiscovery else { return }
            refreshes += 1
            if refreshes == 2 { Simulation.commandRefresh = nil; entered.open(); await resume.wait() }
        }
        let commands: Shell<any Command> = .seq([
            .cmd(ReplayRetargetCommand(command: MacosNativeFullscreenCommand(args: MacosNativeFullscreenCmdArgs(toggle: .off)), id: 43)),
            .cmd(ReplayAwaitCommand {}),
            .cmd(ReplayMoveCommand(window: target, workspace: Workspace.get(byName: "stale"))),
        ])
        let chain: Shell<any Command> = nested ? .cmd(ReplayAwaitCommand { _ = await commands.run(CmdEnv(), CmdIo()) }) : commands
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: chain)]
        await runOnWindowDetected(ifConventional: detected); await input.value
        #expect(target.nodeWorkspace?.name == "user" && Simulation.nativeWrites.isEmpty)
    }

    @Test func validBulkCloseContinuesAfterItsOwnCompletedRemovals() async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        for id: UInt32 in [43, 44] { MacWindow.allWindowsMap[id] = MacWindow(id, app: app) }
        var reached = 0
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            .cmd(CloseAllWindowsButCurrentCommand(args: CloseAllWindowsButCurrentCmdArgs(closeArgs: CloseCmdArgs(quitIfLastWindow: false)))),
            .cmd(ReplayAwaitCommand { reached += 1 }),
        ]))]
        await runOnWindowDetected(ifConventional: detected)
        #expect(MacWindow.allWindowsMap.count == 1 && MacWindow.allWindowsMap[42] === detected)
        #expect(Simulation.nativeWrites == ["close", "close"] && reached == 1)
    }

    @Test func successfulAppTerminationAcknowledgesAllItsRemovedVictims() async {
        let (_, detected, _) = DeferredRestorationAdapterTest().setup()
        let app = ReplayAquaApp(); app.nsApp.processIdentifier = 10
        for id: UInt32 in [43, 44] { MacWindow.allWindowsMap[id] = MacWindow(id, app: app) }
        Simulation.awaitWindowCount = { 1 }
        var reached = 0
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            .cmd(CloseAllWindowsButCurrentCommand(args: CloseAllWindowsButCurrentCmdArgs())),
            .cmd(ReplayAwaitCommand { reached += 1 }),
        ]))]
        await runOnWindowDetected(ifConventional: detected)
        #expect(MacWindow.allWindowsMap.count == 1 && MacWindow.allWindowsMap[42] === detected)
        #expect(Simulation.nativeWrites == ["terminate"] && reached == 1)
    }

    @Test func completedCloseCannotAuthorizeAReusedWindowID() async {
        let (app, detected, _) = DeferredRestorationAdapterTest().setup()
        let target = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = target
        let replacement = MacWindow(43, app: app)
        Simulation.commandRefresh = {
            Simulation.commandRefresh = nil
            MacWindow.allWindowsMap[43] = replacement
        }
        var reached = 0
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .seq([
            .cmd(ReplayRetargetCommand(command: CloseCommand(args: CloseCmdArgs(quitIfLastWindow: false)), id: 43)),
            .cmd(ReplayAwaitCommand { reached += 1 }),
            .cmd(ReplayMoveCommand(window: replacement, workspace: Workspace.get(byName: "stale"))),
        ]))]
        await runOnWindowDetected(ifConventional: detected)
        #expect(reached == 0 && replacement.nodeWorkspace?.name == "default")
        #expect(MacWindow.allWindowsMap[43] === replacement && Simulation.nativeWrites == ["close"])
    }
}
}

@MainActor struct ReplayIoCommand: Command {
    let body: (CmdIo) -> BinaryExitCode
    var shouldResetClosedWindowsCache: Bool { false }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode { body(io) }
}
