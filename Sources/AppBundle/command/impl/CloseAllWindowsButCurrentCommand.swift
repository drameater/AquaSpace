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
        for window in workspace.allLeafWindowsRecursive where window != focused {
            guard continuation.mayContinue else { return .fail }
            let closed = await CloseCommand(args: args.closeArgs).run(env.withWindowId(window.windowId), io)
            // These nested commands do not pass through Shell. Revoke rejected
            // required targets before dispatching another destructive operation.
            WindowRestorationMutation.commandContinuations?.cancelStaleInitialization()
            guard WindowDiscoveryInitialization.mayRunAutomaticCommand, continuation.mayContinue else { return .fail }
            result = closed.and(result)
        }
        return result
    }
}
