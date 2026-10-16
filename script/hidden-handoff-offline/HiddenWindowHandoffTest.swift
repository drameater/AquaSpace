@testable import HiddenHandoffReplay
import Testing

@Suite(.serialized) @MainActor
struct HiddenWindowHandoffTest {
    func window(_ id: UInt32, subrole: String = kAXStandardWindowSubrole) -> AXUIElement {
        let result = AXUIElement(id)
        result.put(Ax.subroleAttr, subrole)
        result.put(Ax.titleAttr, "Zen Browser")
        result.put(Ax.isFocused, false)
        result.put(Ax.isMainAttr, false)
        let button = AXUIElement()
        button.put(Ax.enabledAttr, true)
        result.put(Ax.closeButtonAttr, button)
        result.put(Ax.fullscreenButtonAttr, button)
        result.put(Ax.minimizeButtonAttr, button)
        return result
    }

    @Test func hiddenZenRemainsAWorkspaceWindowAtHandoffStartup() {
        // IDs and the hidden-app layout come from the saved failed handoff.
        ReplayServer.reset([
            ReplayWindow(id: 27918, pid: 3456, layer: 0, onScreen: true),
            ReplayWindow(id: 28122, pid: 15654, layer: 0, onScreen: false),
        ])
        let zen = window(28122)
        let type = zen.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, getWindowLevel(for: 28122))
        #expect(type == .window)
        let session = WindowManagerSession(
            windows: "28122\t1\tmacos_native_window_of_hidden_app\tfalse\n27918\t1\tv_tiles\tfalse",
            workspaces: "1\th_tiles", workspace: "1", focusedWindow: "27918")
        let commands = session.restorationCommands(availableWindowIds: ["28122", "27918"])
        #expect(commands.contains(["move-node-to-workspace", "--window-id", "28122", "1"]))
        #expect(commands.last == ["focus", "--window-id", "27918"])
        #expect(!commands.contains(["focus", "--window-id", "28122"]))
    }

    @Test func hiddenDialogSubroleIsStillManagedWithoutPretendingItIsStandard() {
        ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: false)])
        let hidden = window(12, subrole: kAXDialogSubrole)
        #expect(hidden.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, getWindowLevel(for: 12)) == .dialog)
    }

    @Test func coldCacheDiscoversHiddenAndVisibleWindowsTogether() {
        ReplayServer.reset([
            ReplayWindow(id: 10, pid: 7, layer: 0, onScreen: true),
            ReplayWindow(id: 11, pid: 7, layer: 0, onScreen: false),
        ])
        #expect(getWindowLevel(for: 10) == .normalWindow)
        #expect(getWindowLevel(for: 11) == .normalWindow)
        #expect(ReplayServer.queries.count == 1)
    }

    @Test func hiddenHighLayerWindowsRemainPopups() {
        for layer in [3, 8, 25] {
            ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: layer, onScreen: false)])
            #expect(getWindowLevel(for: 12) == .new(windowLevel: layer))
            #expect(window(12).getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, getWindowLevel(for: 12)) == .popup)
        }
    }

    @Test func unreadableOrAbsentMetadataDoesNotBecomeNormalWindow() {
        for available in [false, true] {
            ReplayServer.reset([])
            ReplayServer.available = available
            #expect(getWindowLevel(for: 12) == nil)
            #expect(window(12).getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, getWindowLevel(for: 12)) == .popup)
        }
    }

    @Test func normalLayerDoesNotTurnButtonlessTooltipIntoWindow() {
        ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: false)])
        let tooltip = AXUIElement(12)
        tooltip.put(Ax.subroleAttr, "AXUnknown")
        tooltip.put(Ax.titleAttr, "Hover tooltip")
        tooltip.put(Ax.isFocused, false)
        tooltip.put(Ax.isMainAttr, false)
        #expect(tooltip.getWindowType(axApp: AXUIElement(), .zenBrowser, .regular, getWindowLevel(for: 12)) == .popup)
    }

    @Test func discoveryDoesNotDependOnWhichNativeSpaceIsVisible() {
        for onScreen in [true, false] {
            ReplayServer.reset([ReplayWindow(id: 12, pid: 7, layer: 0, onScreen: onScreen)])
            for app: KnownBundleId in [.zenBrowser, .mozillaFirefox, .mozillaFirefoxDeveloperEdition, .mozillaFirefoxNightly, .chrome] {
                #expect(window(12).getWindowType(axApp: AXUIElement(), app, .regular, getWindowLevel(for: 12)) == .window)
            }
        }
    }
}
