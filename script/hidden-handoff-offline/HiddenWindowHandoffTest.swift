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
    // read instead of starting another, and is woken when it completes.
    @Test func concurrentColdReadersShareOneQueryAndAllComplete() async throws {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 3, onScreen: false),
        ])
        async let first = getWindowLevel(for: 10, .nonCancellable)
        async let second = getWindowLevel(for: 11, .nonCancellable)
        let levels = try await [first, second]
        #expect(levels == [.normalWindow, .alwaysOnTopWindow])
        #expect(ReplayServer.queries.count == 1)
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
