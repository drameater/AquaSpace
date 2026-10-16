@testable import WindowDiscoveryReplay
import Testing
import Foundation

@Suite(.serialized) struct OfflineWindowDiscoveryTests {}

extension OfflineWindowDiscoveryTests {
@Suite(.serialized) @MainActor struct WindowDiscoveryTest {
    @Test func stockWarmCacheAndAquaColdStartReproduceExactlySevenMissingIDs() async throws {
        var stockIds: Set<UInt32> = [], aquaIds: Set<UInt32> = []
        for fixture in capturedFixtures {
            let roots = fixture.roots(), stock = ReplayStockApp(), aqua = ReplayAquaApp()
            stock.seed(fixture.retained.map { roots[$0]! })
            let live = fixture.exposed.map { (windowId: $0, ax: roots[$0]!) }
            stock.axApp.threadGuarded.set(Ax.windowsAttr, live)
            aqua.axApp.threadGuarded.set(Ax.windowsAttr, live)
            stockIds.formUnion(try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil))
            let update = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: Set(roots.keys))
            aquaIds.formUnion(update.aliveWindowIds)
            #expect(update.nativeTabUpdate.replacements.isEmpty)
            #expect(update.nativeTabUpdate.absorptions.isEmpty)
            #expect(update.nativeTabUpdate.inheritances.isEmpty)
        }
        #expect(stockIds.subtracting(aquaIds) == [30583, 29724, 30197, 29754, 29598, 30290, 30682])
        #expect(aquaIds == [30881])
    }

    @Test(arguments: capturedFixtures)
    func bothImplementationsRetainTheSameWarmReferences(fixture: DiscoveryFixture) async throws {
        let roots = fixture.roots(), stock = ReplayStockApp(), aqua = ReplayAquaApp()
        let warm = fixture.retained.map { roots[$0]! }
        stock.seed(warm); aqua.seed(warm)
        let live = fixture.exposed.map { (windowId: $0, ax: roots[$0]!) }
        stock.axApp.threadGuarded.set(Ax.windowsAttr, live)
        aqua.axApp.threadGuarded.set(Ax.windowsAttr, live)
        let stockIds = try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil)
        let aquaUpdate = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: Set(roots.keys))
        #expect(Set(stockIds) == Set(aquaUpdate.aliveWindowIds))
        #expect(Set(stockIds) == Set(fixture.retained + fixture.exposed))
    }

    @Test func reversingWhichManagerHasWarmReferencesReversesTheDifference() async throws {
        let fixture = capturedFixtures[0], roots = fixture.roots()
        let stock = ReplayStockApp(), aqua = ReplayAquaApp()
        aqua.seed(fixture.retained.map { roots[$0]! })
        let live = fixture.exposed.map { (windowId: $0, ax: roots[$0]!) }
        stock.axApp.threadGuarded.set(Ax.windowsAttr, live)
        aqua.axApp.threadGuarded.set(Ax.windowsAttr, live)
        let stockIds = try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil)
        let aquaUpdate = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: Set(roots.keys))
        #expect(Set(aquaUpdate.aliveWindowIds).subtracting(stockIds) == Set(fixture.retained))
    }

    @Test func windowServerIDsAloneDoNotDiscoverAnAccessibilityWindow() async throws {
        let stock = ReplayStockApp(), aqua = ReplayAquaApp()
        stock.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        aqua.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        #expect(try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil).isEmpty)
        let update = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: [30197, 29754])
        #expect(update.aliveWindowIds.isEmpty)
        #expect(update.nativeTabUpdate.replacements.isEmpty)
        #expect(update.nativeTabUpdate.absorptions.isEmpty)
    }

    @Test func reExposingTheSameRootsLetsBothColdManagersDiscoverThem() async throws {
        for fixture in capturedFixtures {
            let roots = fixture.roots(), stock = ReplayStockApp(), aqua = ReplayAquaApp()
            let live = roots.map { (windowId: $0.key, ax: $0.value) }
            stock.axApp.threadGuarded.set(Ax.windowsAttr, live)
            aqua.axApp.threadGuarded.set(Ax.windowsAttr, live)
            #expect(Set(try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil)) == Set(roots.keys))
            let update = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: Set(roots.keys))
            #expect(Set(update.aliveWindowIds) == Set(roots.keys))
        }
    }

    @Test func confirmedInvalidReferencesAreCollectedByBothManagers() async throws {
        let root = AXUIElement(owner: 30197), stock = ReplayStockApp(), aqua = ReplayAquaApp()
        stock.seed([root]); aqua.seed([root]); root.owner = nil
        stock.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        aqua.axApp.threadGuarded.set(Ax.windowsAttr, [WindowIdAndAxUiElement]())
        #expect(try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil).isEmpty)
        let update = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: [30197])
        #expect(update.aliveWindowIds.isEmpty)
    }

    @Test func absentAXWindowsAttributeHasTheSameColdDiscoveryLimit() async throws {
        let stock = ReplayStockApp(), aqua = ReplayAquaApp()
        #expect(try await stock.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil).isEmpty)
        let update = try await aqua.refreshAndGetAliveWindowIds(frontmostAppBundleId: nil, windowServerIds: [29598])
        #expect(update.aliveWindowIds.isEmpty)
    }
    @Test func handoffRetainsIdentityBackedUndiscoveredAssignments() async {
        let missing: [UInt32] = [30583, 29724, 30197, 29754, 29598, 30290, 30682]
        let lines = missing.map { "\($0)\t1\th_tiles\tfalse" }.joined(separator: "\n")
        ReplayHandoff.commands = []
        ReplayHandoff.startupSession = WindowManagerSession(windows: lines,
            workspaces: "1\th_tiles", workspace: "1", focusedWindow: "")
        let owner = WindowRestorationOwner(pid: 9, uid: 501, startedSeconds: 100, startedMicroseconds: 1,
            executable: "/fixture/app", uniqueId: 7, execVersion: 1, imageUUID: Array(repeating: 1, count: 16))
        ReplayHandoff.startupSession?.owners = Dictionary(uniqueKeysWithValues: missing.map { (String($0), owner) })
        MacWindow.allWindowsMap = [30881: MacWindow(30881, app: ReplayAquaApp())]
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.startupSession == nil)
        #expect(Set(ReplayHandoff.pendingRestoration.assignments.keys) == Set(missing))
        #expect(!ReplayHandoff.commands.contains { $0.first == "move-node-to-workspace" })
        let issued = ReplayHandoff.commands
        for id in missing { MacWindow.allWindowsMap[id] = MacWindow(id, app: ReplayAquaApp()) }
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.commands == issued)
    }
    @Test func discoveryDuringStartupCommandsIsNotMistakenForCompletedRestoration() async {
        let owner = WindowRestorationOwner(pid: 9, uid: 501, startedSeconds: 100, startedMicroseconds: 1,
            executable: "/fixture/app", uniqueId: 7, execVersion: 1, imageUUID: Array(repeating: 1, count: 16))
        var session = WindowManagerSession(windows: "42\tsaved\tfloating\tfalse", workspaces: "saved\th_tiles", workspace: "current", focusedWindow: "")
        session.owners = ["42": owner]
        ReplayHandoff.startupSession = session
        ReplayHandoff.commands = []
        MacWindow.allWindowsMap = [:]
        ReplayHandoff.onCommand = { _ in MacWindow.allWindowsMap[42] = MacWindow(42, app: ReplayAquaApp()) }
        defer { ReplayHandoff.onCommand = nil }
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.pendingRestoration.assignments[42]?.workspace == "saved")
        #expect(!ReplayHandoff.commands.contains { $0.first == "move-node-to-workspace" })
    }
    @Test func startupCommandFailureRetainsIdentityBackedAssignmentButLegacyCannotDefer() async {
        let owner = WindowRestorationOwner(pid: 9, uid: 501, startedSeconds: 100, startedMicroseconds: 1,
            executable: "/fixture/app", uniqueId: 7, execVersion: 1, imageUUID: Array(repeating: 1, count: 16))
        var session = WindowManagerSession(windows: "42\tsaved\tfloating\tfalse", workspaces: "", workspace: "", focusedWindow: "")
        session.owners = ["42": owner]
        ReplayHandoff.startupSession = session
        MacWindow.allWindowsMap = [42: MacWindow(42, app: ReplayAquaApp())]
        ReplayHandoff.onCommand = { _ in throw CancellationError() }
        defer { ReplayHandoff.onCommand = nil }
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.pendingRestoration.assignments[42] != nil)
        session.owners = nil; ReplayHandoff.startupSession = session
        await ReplayHandoff.restoreStartupSession()
        #expect(ReplayHandoff.pendingRestoration.assignments.isEmpty)
    }

}
}
