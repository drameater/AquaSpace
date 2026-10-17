import AppKit
import Common

struct CloseCommand: Command {
    let args: CloseCmdArgs
    /*conforms*/ let shouldResetClosedWindowsCache = false

    func run(_ env: CmdEnv, _ io: CmdIo) async -> BinaryExitCode {
        guard let target = args.resolveTargetOrReportError(env, io) else { return .fail }
        guard let window = target.windowOrNil else {
            return .fail(io.err("Empty workspace"))
        }
        // Access ax directly. Not cool :(
        let continuation = WindowCommandContinuation.beforeMutation(window)
        let quit = await args.quitIfLastWindow.andAsync({ @MainActor @Sendable in (try? await window.macAppUnsafe.getAxWindowsCount(.nonCancellable)) == 1 })
        guard continuation.mayContinue else { return .fail }
        // Termination is a native effect outside the AX write adapter. Startup
        // model initialization is not authority to quit another application.
        guard WindowManagerHandoff.canControlWindows else { return .fail }
        if quit {
            let app = window.macAppUnsafe
            if app.nsApp.terminate() {
                for workspace in Workspace.all {
                    for window in workspace.allLeafWindowsRecursive where window.app.pid == app.pid {
                        (window as! MacWindow).garbageCollect(skipClosedWindowsCache: true)
                    }
                }
                continuation.didDispatchClose()
                return .succ
            } else {
                return .fail(io.err("Failed to quit '\(window.app.name ?? "Unknown app")'"))
            }
        } else {
            window.closeAxWindow()
            continuation.didDispatchClose()
            return .succ
        }
    }
}
