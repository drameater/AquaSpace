@testable import WindowDiscoveryReplay
import Foundation
import Testing

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct CallbackCommandContinuationTest {
    enum Operation: CaseIterable, Sendable { case tiling, relayout, fullscreen, minimize, close }

    // These are real production command/relayout bodies. Only their awaited
    // native reads and native writes are replaced with memory-only gates/logs.
    func command(_ operation: Operation, window: MacWindow) -> any Command {
        switch operation {
            case .tiling: LayoutCommand(args: LayoutCmdArgs(rawArgs: [], toggleBetween: [.tiling]))
            case .relayout: ReplayRelayoutCommand(window: window, workspace: window.nodeWorkspace!)
            case .fullscreen: MacosNativeFullscreenCommand(args: MacosNativeFullscreenCmdArgs())
            case .minimize: MacosNativeMinimizeCommand(args: MacosNativeMinimizeCmdArgs())
            case .close: CloseCommand(args: CloseCmdArgs())
        }
    }

    func installReadGate(_ operation: Operation, entered: ReplayGate, resume: ReplayGate) {
        switch operation {
            case .tiling: Simulation.awaitSize = { entered.open(); await resume.wait(); return CGSize(width: 777, height: 888) }
            case .relayout: Simulation.awaitType = { entered.open(); await resume.wait(); return .window }
            case .fullscreen: Simulation.awaitFullscreen = { entered.open(); await resume.wait(); return false }
            case .minimize: Simulation.awaitMinimized = { entered.open(); await resume.wait(); return false }
            case .close: Simulation.awaitWindowCount = { entered.open(); await resume.wait(); return 1 }
        }
    }

    @Test(arguments: Operation.allCases)
    func userMoveWhileACommandAwaitsPreventsItsContinuation(operation: Operation) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        let cached = CGSize(width: 111, height: 222); window.lastFloatingSize = cached
        Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        installReadGate(operation, entered: entered, resume: resume)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(operation, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(window.isFloating)
        #expect(window.lastFloatingSize == cached)
        #expect(MacWindow.allWindowsMap[42] === window)
        #expect(Simulation.mutations == [42])
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test(arguments: Operation.allCases)
    func replacingTheModelDuringAReadRejectsTheOldContinuation(operation: Operation) async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        Simulation.mutations = []
        let replacement = MacWindow(42, app: app)
        let entered = ReplayGate(), resume = ReplayGate()
        let change = Task { @MainActor in
            await entered.wait(); MacWindow.allWindowsMap[42] = replacement; resume.open()
        }
        installReadGate(operation, entered: entered, resume: resume)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(operation, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        await change.value
        #expect(MacWindow.allWindowsMap[42] === replacement)
        #expect(replacement.nodeWorkspace?.name == "default")
        #expect(window.lastFloatingSize == nil)
        #expect(Simulation.mutations.isEmpty)
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test(arguments: Operation.allCases)
    func validAutomaticCommandStillRuns(operation: Operation) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        Simulation.mutations = []
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(operation, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        switch operation {
            case .tiling, .relayout:
                #expect(!window.isFloating)
                #expect(window.nodeWorkspace?.name == "before")
                #expect(Simulation.mutations == [42])
            case .fullscreen: #expect(Simulation.nativeWrites == ["fullscreen"])
            case .minimize: #expect(Simulation.nativeWrites == ["minimize"])
            case .close: #expect(Simulation.nativeWrites == ["terminate"])
        }
    }

    @Test(arguments: Operation.allCases)
    func frozenControllerStopsAnAlreadyRunningAutomaticCommand(operation: Operation) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let change = Task { @MainActor in await entered.wait(); Simulation.controls = false; resume.open() }
        installReadGate(operation, entered: entered, resume: resume)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(operation, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        await change.value
        #expect(window.isFloating)
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test(arguments: Operation.allCases)
    func explicitlyTargetedOtherWindowChecksItsOwnUserMutation(operation: Operation) async {
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
        installReadGate(operation, entered: entered, resume: resume)
        let retargeted = ReplayRetargetCommand(command: command(operation, window: target), id: 43)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(retargeted))]
        await runOnWindowDetected(ifConventional: detected)
        await input.value
        #expect(target.nodeWorkspace?.name == "user")
        #expect(target.isFloating)
        #expect(MacWindow.allWindowsMap[43] === target)
        #expect(Simulation.mutations == [43])
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.nativeWrites.isEmpty)
    }

    @Test func unrelatedUserInputDoesNotCancelAValidTilingContinuation() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: other,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        installReadGate(.tiling, entered: entered, resume: resume)
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(.tiling, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(!window.isFloating)
        #expect(window.nodeWorkspace?.name == "before")
        #expect(other.nodeWorkspace?.name == "user")
    }

    @Test func floatingFocusWaitsForAllReadsBeforeUnbindingAnyWindow() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        window.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            #expect(window.isBound && other.isBound)
            #expect(Simulation.unbinds.isEmpty)
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { w in
            if w === other { entered.open(); await resume.wait() }
            return CGPoint(x: Double(w.windowId), y: 0)
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.isEmpty)
            }
        }
        await input.value
        #expect(window.nodeWorkspace?.name == "user")
        #expect(window.isFloating && other.isFloating)
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.mutations == [42])
        #expect(Simulation.mruWrites.isEmpty)
    }

    @Test func floatingFocusPreservesAnotherWindowsNewUserBinding() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        window.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: other,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { w in
            if w === other { entered.open(); await resume.wait() }
            return CGPoint(x: Double(w.windowId), y: 0)
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.count == 1)
                restoreFloatingWindows(floatingWindows: temporary, workspace: workspace)
            }
        }
        await input.value
        #expect(window.isFloating)
        #expect(other.isFloating && other.nodeWorkspace?.name == "user")
        #expect(!Simulation.unbinds.contains(43))
    }

    @Test func floatingFocusCleanupDoesNotUndoANewerChoiceInTheSameParent() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        window.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        let prepared = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await prepared.wait()
            // An explicit tiling choice reuses the temporary parent. Parent
            // equality alone must not authorize cleanup to float it again.
            _ = await Shell<any Command>.cmd(ReplayTileCommand(window: window)).run(CmdEnv(), CmdIo())
            resume.open()
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.count == 2)
                prepared.open(); await resume.wait()
                restoreFloatingWindows(floatingWindows: temporary, workspace: workspace)
            }
        }
        await input.value
        #expect(!window.isFloating)
        #expect(other.isFloating)
    }

    @Test func floatingFocusRechecksAfterTheTargetCenterRead() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        window.bindAsFloatingWindow(to: workspace)
        let tiled = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = tiled
        tiled.parent = workspace.rootTilingContainer
        Simulation.geometryTarget = tiled
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { w in
            if w === tiled { entered.open(); await resume.wait() }
            return CGPoint(x: Double(w.windowId), y: 0)
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.isEmpty)
            }
        }
        await input.value
        #expect(window.isFloating && window.nodeWorkspace?.name == "user")
        #expect(Simulation.unbinds.isEmpty)
    }

    @Test func floatingFocusStillPreparesAndRestoresValidWindows() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        window.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.count == 2)
                #expect(!window.isFloating && !other.isFloating)
                restoreFloatingWindows(floatingWindows: temporary, workspace: workspace)
            }
        }
        #expect(window.isFloating && other.isFloating)
        #expect(window.nodeWorkspace === workspace && other.nodeWorkspace === workspace)
    }

    @Test func floatingFocusDoesNotFollowAGeometryTargetMovedDuringItsRead() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        window.bindAsFloatingWindow(to: workspace)
        let tiled = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = tiled
        tiled.parent = workspace.rootTilingContainer; Simulation.geometryTarget = tiled
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: tiled,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        Simulation.awaitCenter = { w in
            if w === tiled { entered.open(); await resume.wait() }
            return CGPoint(x: Double(w.windowId), y: 0)
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.isEmpty)
            }
        }
        await input.value
        #expect(window.isFloating && window.nodeWorkspace === workspace)
        #expect(tiled.nodeWorkspace?.name == "user")
        #expect(Simulation.unbinds.isEmpty)
    }

    @Test func floatingFocusRejectsADestinationDetachedDuringALaterRead() async {
        let (app, window, _) = DeferredRestorationAdapterTest().setup()
        let workspace = Workspace.get(byName: "before")
        let other = MacWindow(43, app: app); MacWindow.allWindowsMap[43] = other
        let tiled = MacWindow(44, app: app); MacWindow.allWindowsMap[44] = tiled
        window.bindAsFloatingWindow(to: workspace); other.bindAsFloatingWindow(to: workspace)
        tiled.parent = workspace.rootTilingContainer; Simulation.geometryTarget = tiled
        let oldParent = workspace.rootTilingContainer
        Simulation.awaitCenter = { w in
            if w === other {
                oldParent.isAttached = false
                workspace.rootTilingContainer = ReplayParent(workspace, floating: false)
                tiled.parent = workspace.rootTilingContainer
            }
            return CGPoint(x: Double(w.windowId), y: 0)
        }
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isAutomaticDiscovery.withValue(true) {
                let temporary = await makeFloatingWindowsSeenAsTiling(workspace: workspace)
                #expect(temporary.count == 1)
                restoreFloatingWindows(floatingWindows: temporary, workspace: workspace)
            }
        }
        #expect(window.isFloating && other.isFloating)
        #expect(window.nodeWorkspace === workspace && other.nodeWorkspace === workspace)
        #expect(!Simulation.unbinds.contains(42))
    }

    @Test(arguments: [Operation.tiling, .close])
    func aFailedReadCannotFallThroughToAStaleMutation(operation: Operation) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let entered = ReplayGate(), resume = ReplayGate()
        let input = Task { @MainActor in
            await entered.wait()
            _ = await Shell<any Command>.cmd(ReplayMoveCommand(window: window,
                workspace: Workspace.get(byName: "user"))).run(CmdEnv(), CmdIo())
            resume.open()
        }
        if operation == .tiling {
            Simulation.awaitSize = { entered.open(); await resume.wait(); throw CancellationError() }
        } else {
            Simulation.awaitWindowCount = { entered.open(); await resume.wait(); throw CancellationError() }
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in true }, run: .cmd(command(operation, window: window)))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "user" && window.isFloating)
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(Simulation.unbinds.isEmpty)
        #expect(Simulation.mutations == [42])
    }

    @Test func explicitTilingCommandRetainsOrdinarySemantics() async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before"))
        _ = await Shell<any Command>.cmd(command(.tiling, window: window)).run(CmdEnv(windowId: 42), CmdIo())
        #expect(!window.isFloating)
        #expect(window.nodeWorkspace?.name == "before")
    }
}
}

@MainActor struct ReplayRelayoutCommand: Command {
    let window: MacWindow
    let workspace: Workspace
    var shouldResetClosedWindowsCache: Bool { true }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        do { try await window.relayoutWindow(on: workspace, .nonCancellable); return .succ }
        catch { return .fail }
    }
}

@MainActor struct ReplayRetargetCommand: Command {
    let command: any Command
    let id: UInt32
    var shouldResetClosedWindowsCache: Bool { command.shouldResetClosedWindowsCache }
    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode { await command.run(env.withWindowId(id), io) }
}
