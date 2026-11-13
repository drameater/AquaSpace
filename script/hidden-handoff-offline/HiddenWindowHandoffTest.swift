@testable import HiddenHandoffReplay
import Testing

@Suite(.serialized) @MainActor
struct HiddenWindowHandoffTest {
    func window(_ id: UInt32, subrole: String = kAXStandardWindowSubrole) -> AXUIElement {
        let result = AXUIElement(owner: id)
        result.set(Ax.subroleAttr, subrole)
        result.set(Ax.titleAttr, "Zen Browser")
        result.set(Ax.isFocused, false)
        result.set(Ax.isMainAttr, false)
        let button = AXUIElement()
        button.set(Ax.enabledAttr, true)
        result.set(Ax.closeButtonAttr, button)
        result.set(Ax.fullscreenButtonAttr, button)
        result.set(Ax.minimizeButtonAttr, button)
        return result
    }

    @Test func hiddenZenRemainsAWorkspaceWindowAtHandoffStartup() async throws {
        // IDs and the hidden-app layout come from the saved failed handoff.
        ReplayServer.reset([
            ReplayWindow(id: 27918, pid: 3456, layer: 0, onScreen: true),
            ReplayWindow(id: 28122, pid: 15654, layer: 0, onScreen: false),
        ])
        let zen = window(28122)
        let type = zen.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, try await getWindowLevel(for: 28122, .cancellable))
        #expect(type == .window)
        let session = WindowManagerSession(
            windows: "28122\t1\tmacos_native_window_of_hidden_app\tfalse\n27918\t1\tv_tiles\tfalse",
            workspaces: "1\th_tiles", workspace: "1", focusedWindow: "27918")
        let commands = session.restorationCommands(availableWindowIds: ["28122", "27918"])
        #expect(commands.contains(["move-node-to-workspace", "--window-id", "28122", "1"]))
        #expect(commands.last == ["focus", "--window-id", "27918"])
        #expect(!commands.contains(["focus", "--window-id", "28122"]))
    }

    @Test func hiddenDialogSubroleIsStillManagedWithoutPretendingItIsStandard() async throws {
        ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: false)])
        let hidden = window(12, subrole: kAXDialogSubrole)
        #expect(hidden.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, try await getWindowLevel(for: 12, .cancellable)) == .dialog)
    }

    @Test func coldCacheDiscoversHiddenAndVisibleWindowsTogether() async throws {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 0, onScreen: false),
        ])
        #expect(try await getWindowLevel(for: 10, .cancellable) == .normalWindow)
        #expect(try await getWindowLevel(for: 11, .cancellable) == .normalWindow)
        #expect(ReplayServer.queries.count == 1)
    }

    // Regression for upstream 0d498deb: a second cold reader waits on the first
    // read instead of starting another, and is woken when it completes. The gate
    // holds the first read until the second reader waits on it.
    @Test(.timeLimit(.minutes(1))) func concurrentColdReadersShareOneQueryAndAllComplete() async throws {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 3, onScreen: false),
        ])
        ReplayServer.gate.close()
        let first = LevelReader(10, .nonCancellable)
        #expect(await eventually { ReplayServer.queries.count == 1 })
        let second = LevelReader(11, .nonCancellable)
        #expect(await eventually { second.isWaiting })
        ReplayServer.gate.open()
        #expect(await eventually { first.isDone && second.isDone })
        #expect(try first.level() == .normalWindow)
        #expect(try second.level() == .alwaysOnTopWindow)
        #expect(ReplayServer.queries.count == 1)
    }

    // A read that was in flight took its list before the new window existed. The
    // reader that joined it must not accept that miss: it reads WindowServer again.
    // Otherwise the browser's new window is classified as a popup.
    @Test(.timeLimit(.minutes(1))) func joinedReadThatPredatesTheWindowIsNotTheFinalAnswer() async throws {
        ReplayServer.reset([ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true)])
        ReplayServer.gate.close()
        let refreshA = LevelReader(10, .cancellable)
        #expect(await eventually { ReplayServer.queries.count == 1 })
        ReplayServer.add(ReplayWindow(id: 11, pid: 7, layer: 0, onScreen: true))
        refreshA.task.cancel() // The refresh is cancelled; its WindowServer read continues.
        let refreshB = LevelReader(11, .cancellable)
        #expect(await eventually { refreshB.isWaiting })
        ReplayServer.gate.open()
        #expect(await eventually { refreshA.isDone && refreshB.isDone })
        let level = try refreshB.level()
        #expect(level == .normalWindow)
        #expect(window(11).getWindowType(axApp: AXUIElement(), .chrome, .regular, level) == .window)
        #expect(ReplayServer.queries.count == 2)
    }

    // Readers that join the same in-flight read still share one later read.
    @Test(.timeLimit(.minutes(1))) func readersThatMissTogetherShareOneLaterRead() async throws {
        ReplayServer.reset([ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true)])
        ReplayServer.gate.close()
        let first = LevelReader(10, .nonCancellable)
        #expect(await eventually { ReplayServer.queries.count == 1 })
        ReplayServer.add(ReplayWindow(id: 11, pid: 7, layer: 0, onScreen: true))
        ReplayServer.add(ReplayWindow(id: 12, pid: 7, layer: 3, onScreen: true))
        let second = LevelReader(11, .nonCancellable)
        let third = LevelReader(12, .nonCancellable)
        #expect(await eventually { second.isWaiting && third.isWaiting })
        ReplayServer.gate.open()
        #expect(await eventually { first.isDone && second.isDone && third.isDone })
        #expect(try second.level() == .normalWindow)
        #expect(try third.level() == .alwaysOnTopWindow)
        #expect(ReplayServer.queries.count == 2)
    }

    @Test(.timeLimit(.minutes(1))) func cancelledCancellableWaiterStopsWaitingAtOnce() async throws {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 0, onScreen: true),
        ])
        ReplayServer.gate.close()
        let first = LevelReader(10, .nonCancellable)
        #expect(await eventually { ReplayServer.queries.count == 1 })
        let second = LevelReader(11, .cancellable)
        #expect(await eventually { second.isWaiting })
        second.task.cancel()
        // The read is still held, so only cancellation can end this wait.
        #expect(await eventually { second.isDone })
        #expect(throws: CancellationError.self) { try second.level() }
        ReplayServer.gate.open()
        #expect(await eventually { first.isDone })
        #expect(try first.level() == .normalWindow)
        #expect(ReplayServer.queries.count == 1)
    }

    @Test(.timeLimit(.minutes(1))) func nonCancellableWaiterIgnoresCancellation() async throws {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 3, onScreen: true),
        ])
        ReplayServer.gate.close()
        let first = LevelReader(10, .nonCancellable)
        #expect(await eventually { ReplayServer.queries.count == 1 })
        let second = LevelReader(11, .nonCancellable)
        #expect(await eventually { second.isWaiting })
        second.task.cancel()
        try await Task.sleep(for: .milliseconds(50))
        #expect(!second.isDone)
        ReplayServer.gate.open()
        #expect(await eventually { first.isDone && second.isDone })
        #expect(try second.level() == .alwaysOnTopWindow)
    }

    // A failed WindowServer read must not discard levels that are already known.
    @Test func failedReadKeepsKnownLevels() async throws {
        ReplayServer.reset([ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true)])
        #expect(try await getWindowLevel(for: 10, .cancellable) == .normalWindow)
        ReplayServer.available = false
        #expect(try await getWindowLevel(for: 11, .cancellable) == nil)
        #expect(ReplayServer.queries.count == 2)
        #expect(try await getWindowLevel(for: 10, .cancellable) == .normalWindow)
        #expect(window(10).getWindowType(axApp: AXUIElement(), .chrome, .regular, try await getWindowLevel(for: 10, .cancellable)) == .window)
    }

    @Test func hiddenHighLayerWindowsRemainPopups() async throws {
        for layer in [3, 8, 25] {
            ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: layer, onScreen: false)])
            #expect(try await getWindowLevel(for: 12, .cancellable) == .new(windowLevel: layer))
            #expect(window(12).getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, try await getWindowLevel(for: 12, .cancellable)) == .popup)
        }
    }

    @Test func unreadableOrAbsentMetadataDoesNotBecomeNormalWindow() async throws {
        for available in [false, true] {
            ReplayServer.reset([])
            ReplayServer.available = available
            #expect(try await getWindowLevel(for: 12, .cancellable) == nil)
            #expect(window(12).getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, try await getWindowLevel(for: 12, .cancellable)) == .popup)
        }
    }

    @Test func normalLayerDoesNotTurnButtonlessTooltipIntoWindow() async throws {
        ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: false)])
        let tooltip = AXUIElement(owner: 12)
        tooltip.set(Ax.subroleAttr, "AXUnknown")
        tooltip.set(Ax.titleAttr, "Hover tooltip")
        tooltip.set(Ax.isFocused, false)
        tooltip.set(Ax.isMainAttr, false)
        #expect(tooltip.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, try await getWindowLevel(for: 12, .cancellable)) == .popup)
    }

    @Test func discoveryDoesNotDependOnWhichNativeSpaceIsVisible() async throws {
        for onScreen in [true, false] {
            ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: onScreen)])
            for app: KnownBundleId in [.zenBrowser, .mozillaFirefox, .mozillaFirefoxDeveloperEdition, .mozillaFirefoxNightly, .chrome] {
                #expect(window(12).getWindowType(axApp: AXUIElement(), app, .regular, try await getWindowLevel(for: 12, .cancellable)) == .window)
            }
        }
    }

    @Test func delayedRestorationOwnershipIncludesOffscreenRowsAndExposesAmbiguity() {
        ReplayServer.reset([
            ReplayWindow(id: 42, pid: 9, layer: 0, onScreen: false),
            ReplayWindow(id: 42, pid: 10, layer: 0, onScreen: true),
        ])
        #expect(getRestorationWindowOwners()?[42] == [9, 10])
    }
    @Test func malformedOrUnavailableNativeOwnershipCannotAuthorizeDelayedMoves() {
        ReplayServer.reset([ReplayWindow(id: 42, pid: 0, layer: 0, onScreen: false)])
        #expect(getRestorationWindowOwners() == nil)
        ReplayServer.available = false
        #expect(getRestorationWindowOwners() == nil)
    }
}

/// One `getWindowLevel` call in its own task. `isWaiting` turns true in the same
/// main-actor step that calls the cache, so when a test sees it, the reader has
/// reached its first suspension point (for a cold cache: the in-flight read).
@MainActor final class LevelReader {
    private(set) var isWaiting = false
    private var outcome: Result<MacOsWindowLevel?, any Error>?
    private(set) var task: Task<Void, Never>!
    var isDone: Bool { outcome != nil }

    init(_ windowId: UInt32, _ cm: CancellationMode) {
        task = Task { @MainActor in
            isWaiting = true
            do { outcome = .success(try await getWindowLevel(for: windowId, cm)) } catch { outcome = .failure(error) }
        }
    }

    struct NotDone: Error {}
    func level() throws -> MacOsWindowLevel? {
        guard let outcome else { throw NotDone() }
        return try outcome.get()
    }
}

/// Lets other main-actor work run until `condition` holds. False after 5 s, so
/// a lost wake-up fails the test instead of hanging it.
@MainActor func eventually(_ condition: () -> Bool) async -> Bool {
    let deadline = ContinuousClock.now + .seconds(5)
    while !condition() {
        if ContinuousClock.now > deadline { return false }
        try? await Task.sleep(for: .milliseconds(1))
    }
    return true
}
