@testable import AppBundle
import Foundation
import Testing

@Suite(.serialized)
@MainActor
struct NativeTabWorkspaceRecoveryTest {
    @Test func alreadyRegisteredDetachedWindowInheritsWorkspaceWithoutTakingFocus() {
        setUpWorkspacesForTests()
        let workspace = Workspace.get(byName: "source")
        let source = TestWindow.new(id: 3, parent: workspace.rootTilingContainer, adaptiveWeight: 3)
        let detached = TestWindow.new(id: 2, parent: Workspace.get(byName: "staging").rootTilingContainer)
        let focused = TestWindow.new(id: 9, parent: Workspace.get(byName: "unrelated").rootTilingContainer)
        _ = focused.focusWindow()
        detached.isFullscreen = true
        detached.noOuterGapsInFullscreen = true
        detached.lastFloatingSize = CGSize(width: 900, height: 700)

        inheritNativeTabWorkspace(detached, on: workspace)

        #expect(detached.parent === workspace.rootTilingContainer)
        #expect(source.parent === workspace.rootTilingContainer)
        #expect(workspace.rootTilingContainer.children.count == 2)
        #expect(source.getWeight(workspace.rootTilingContainer.orientation) == 3)
        #expect(detached.isFullscreen && detached.noOuterGapsInFullscreen)
        #expect(detached.lastFloatingSize == CGSize(width: 900, height: 700))
        #expect(focus.windowOrNil === focused)
    }

    @Test func alreadyRegisteredFloatingSplitKeepsSizeAndDoesNotRebindOnRetry() {
        setUpWorkspacesForTests()
        let workspace = Workspace.get(byName: "source")
        TestWindow.new(id: 3, parent: workspace.floatingWindowsContainer)
        let detached = TestWindow.new(id: 2, parent: Workspace.get(byName: "staging").floatingWindowsContainer)
        detached.lastFloatingSize = CGSize(width: 900, height: 700)

        inheritNativeTabWorkspace(detached, on: workspace)
        #expect(detached.parent === workspace.floatingWindowsContainer)
        #expect(detached.isFloating)
        #expect(detached.lastFloatingSize == CGSize(width: 900, height: 700))
        // An acknowledged/retried inheritance must not reorder an established slot.
        TestWindow.new(id: 4, parent: workspace.floatingWindowsContainer)
        let index = detached.ownIndex
        inheritNativeTabWorkspace(detached, on: workspace)
        #expect(detached.ownIndex == index)
        #expect(workspace.floatingWindowsContainer.children.count == 3)
    }
}
