@testable import AppBundle
import Testing

struct WindowManagerSessionTest {
    @Test func windowControlIsFrozenUntilOriginalHasStopped() {
        var ownership = WindowManagerOwnership.preparing
        let refused = ownership.claim(originalIsRunning: true)
        #expect(!refused)
        #expect(ownership == .preparing)
        let acquired = ownership.claim(originalIsRunning: false)
        #expect(acquired)
        #expect(ownership == .controlling)
    }

    @Test func yieldingCannotReacquireWindowControl() {
        var ownership = WindowManagerOwnership.preparing
        let acquired = ownership.claim(originalIsRunning: false)
        #expect(acquired)
        ownership.yield()
        let reacquired = ownership.claim(originalIsRunning: false)
        #expect(!reacquired)
        #expect(ownership == .yielding)
    }

    @Test func handoffPreservesWorkspaceNamesFloatingFullscreenAndFocus() {
        let session = WindowManagerSession(
            windows: "12\twork's space\tfloating\tfalse\n23\t5\th_tiles\ttrue\n99\told\th_tiles\tfalse",
            workspaces: "work's space\tv_tiles\n5\th_accordion",
            workspace: "5\n", focusedWindow: "23\n",
        )
        let commands = session.restorationCommands(availableWindowIds: ["12", "23"])
        #expect(commands.contains(["move-node-to-workspace", "--window-id", "12", "work's space"]))
        #expect(commands.contains(["layout", "--window-id", "12", "floating"]))
        #expect(commands.contains(["fullscreen", "on", "--window-id", "23"]))
        #expect(commands.contains(["layout", "--workspace", "5", "--root", "h_accordion"]))
        #expect(!commands.contains { $0.contains("99") })
        #expect(commands.suffix(2) == [["workspace", "5"], ["focus", "--window-id", "23"]])
    }

    @Test func handoffIgnoresClosedFocusAndMalformedWindowRows() {
        let session = WindowManagerSession(windows: "bad\trow\ninvalid\t1\tfloating\tfalse", workspaces: "", workspace: "", focusedWindow: "9")
        #expect(session.restorationCommands(availableWindowIds: []).isEmpty)
    }
}
