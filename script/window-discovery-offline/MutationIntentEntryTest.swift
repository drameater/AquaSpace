@testable import WindowDiscoveryReplay
import Foundation
import Testing

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct MutationIntentEntryTest {
    enum Operation: CaseIterable, Sendable { case tiling, relayout, fullscreen, minimize, close }

    @Test(arguments: [false, true], [false, true])
    func nativeFullscreenNoOpSupersedesAnOlderRule(on: Bool, failIfNoop: Bool) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        let matched = ReplayGate(), resume = ReplayGate()
        Simulation.awaitFullscreen = { on }
        let input = Task { @MainActor in
            await matched.wait()
            let result = await Shell<any Command>.cmd(MacosNativeFullscreenCommand(args:
                MacosNativeFullscreenCmdArgs(toggle: on ? .on : .off, failIfNoop: failIfNoop)))
                .run(CmdEnv(windowId: 42), CmdIo())
            #expect(result.rawValue == (failIfNoop ? 1 : 0))
            #expect(window.discoveryInitialization?.mayContinue == false)
            #expect(ReplayCoordinator.pendingRestoration.assignments[42] == nil)
            resume.open()
        }
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in matched.open(); await resume.wait(); return true },
            run: .cmd(ReplayMoveCommand(window: window, workspace: Workspace.get(byName: "stale"))))]
        await runOnWindowDetected(ifConventional: window)
        await input.value
        #expect(window.nodeWorkspace?.name == "default")
        #expect(Simulation.nativeWrites.isEmpty)
        #expect(Simulation.mutations.isEmpty)
    }

    @Test(arguments: Operation.allCases)
    func explicitMutationRevokesOlderCallbacksBeforeItsRead(operation: Operation) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        window.bindAsFloatingWindow(to: Workspace.get(byName: "before")); Simulation.mutations = []
        let matched = ReplayGate(), resumeOld = ReplayGate(), reading = ReplayGate(), resumeUser = ReplayGate(), oldDone = ReplayGate()
        let input = Task { @MainActor in
            await matched.wait()
            let helpers = CallbackCommandContinuationTest()
            let command: any Command = switch operation {
                case .tiling: helpers.command(.tiling, window: window)
                case .relayout: helpers.command(.relayout, window: window)
                case .fullscreen: helpers.command(.fullscreen, window: window)
                case .minimize: helpers.command(.minimize, window: window)
                case .close: helpers.command(.close, window: window)
            }
            _ = await Shell<any Command>.cmd(command).run(CmdEnv(windowId: 42), CmdIo())
        }
        let interleave = Task { @MainActor in
            await reading.wait(); resumeOld.open()
            await oldDone.wait()
            #expect(window.nodeWorkspace?.name == "before")
            #expect(MacWindow.allWindowsMap[42] === window)
            #expect(Simulation.nativeWrites.isEmpty)
            #expect(Simulation.mutations.isEmpty)
            resumeUser.open()
        }
        switch operation {
            case .tiling: Simulation.awaitSize = { reading.open(); await resumeUser.wait(); return nil }
            case .relayout: Simulation.awaitType = { reading.open(); await resumeUser.wait(); return .window }
            case .fullscreen: Simulation.awaitFullscreen = { reading.open(); await resumeUser.wait(); return false }
            case .minimize: Simulation.awaitMinimized = { reading.open(); await resumeUser.wait(); return false }
            case .close: Simulation.awaitWindowCount = { reading.open(); await resumeUser.wait(); return 1 }
        }
        // This is the actual close command's immediate close path, which must
        // never be dispatched while the newer user's native read is suspended.
        Simulation.callbacks = [WindowDetectedCallback(match: { _ in matched.open(); await resumeOld.wait(); return true },
            run: .cmd(CloseCommand(args: CloseCmdArgs(quitIfLastWindow: false))))]
        await runOnWindowDetected(ifConventional: window)
        oldDone.open()
        await interleave.value; await input.value
        switch operation {
            case .tiling, .relayout: #expect(!window.isFloating && window.nodeWorkspace?.name == "before")
            case .fullscreen: #expect(Simulation.nativeWrites == ["fullscreen"])
            case .minimize: #expect(Simulation.nativeWrites == ["minimize"])
            case .close: #expect(Simulation.nativeWrites == ["terminate"])
        }
    }

    @Test(arguments: [false, true])
    func automaticAndSessionNativeNoOpsDoNotSupersedeDiscovery(restore: Bool) async {
        let (_, window, _) = DeferredRestorationAdapterTest().setup()
        let token = window.userMutationToken
        await WindowDiscoveryInitialization.run(for: window) {
            await WindowRestorationMutation.$isSessionRestore.withValue(restore) {
                await WindowRestorationMutation.$isAutomaticDiscovery.withValue(!restore) {
                    let result = await Shell<any Command>.cmd(MacosNativeFullscreenCommand(args:
                        MacosNativeFullscreenCmdArgs(toggle: .off))).run(CmdEnv(windowId: 42), CmdIo())
                    #expect(result.rawValue == 0)
                    #expect(window.userMutationToken == token)
                    #expect(window.discoveryInitialization?.mayContinue == true)
                    #expect(ReplayCoordinator.pendingRestoration.assignments[42] != nil)
                }
            }
        }
        #expect(Simulation.nativeWrites.isEmpty)
    }
}
}
