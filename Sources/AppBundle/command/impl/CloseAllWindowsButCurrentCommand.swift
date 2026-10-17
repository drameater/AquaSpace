import AppKit
import Common

struct CloseAllWindowsButCurrentCommand: Command {
    let args: CloseAllWindowsButCurrentCmdArgs
    /*conforms*/ let shouldResetClosedWindowsCache = false

    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        guard let target = args.resolveTargetOrReportError(env, io) else { return .fail }
        guard let focused = target.windowOrNil else {
            return .fail(io.err("Empty workspace"))
        }
        guard let workspace = focused.nodeWorkspace else {
            return .fail(io.err("Focused window '\(focused.windowId)' doesn't belong to workspace"))
        }
        var result = BinaryExitCode.succ
        let continuation = WindowCommandContinuation(focused)
        // Pin the entire accepted plan before the first native read. A later
        // victim's newer intent must stop even an earlier app-termination effect.
        let victims = workspace.allLeafWindowsRecursive.filter { $0 != focused }.map {
            ($0, WindowCommandContinuation.beforeMutation($0))
        }
        for (window, planned) in victims {
            if planned.isCompletedRemoval { continue }
            guard continuation.mayContinue, planned.targetIsUnchanged, planned.mayContinue else { return .fail }
            let closed = await CloseCommand(args: args.closeArgs).run(env.withWindowId(window.windowId), io)
            if closed.rawValue == 0 {
                planned.didDispatchClose()
                // Successful termination can remove every planned model owned
                // by this app. Preserve expected removals, never reused IDs.
                for (victim, proof) in victims where victim.app.pid == window.app.pid && Window.get(byId: victim.windowId) == nil {
                    proof.didDispatchClose()
                }
            }
            // These nested commands do not pass through Shell. Revoke rejected
            // required targets before dispatching another destructive operation.
            WindowRestorationMutation.commandContinuations?.cancelStaleInitialization()
            guard WindowDiscoveryInitialization.mayRunAutomaticCommand, continuation.mayContinue else { return .fail }
            result = closed.and(result)
        }
        return result
    }
}
