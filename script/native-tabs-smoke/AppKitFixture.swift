import AppKit
import Foundation

let app = NSApplication.shared
let backgroundFixture = CommandLine.arguments.contains("--background")
app.setActivationPolicy(.regular)
app.finishLaunching()
NSWindow.allowsAutomaticWindowTabbing = false
var windows: [String: NSWindow] = [:]

final class FixtureWindow: NSWindow {
    override var canBecomeKey: Bool { !backgroundFixture && super.canBecomeKey }
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        backgroundFixture ? frameRect : super.constrainFrameRect(frameRect, to: screen)
    }
}

func reply(_ value: Any) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(data + Data([10]))
}
func own(_ id: String) -> NSWindow { windows[id]! }
func identifier(_ window: NSWindow) -> String { windows.first { $0.value === window }!.key }
func focus(_ window: NSWindow) {
    window.tabGroup?.selectedWindow = window
    if backgroundFixture { window.orderBack(nil); return }
    window.makeKeyAndOrderFront(nil)
    app.activate(ignoringOtherApps: true)
}
func perform(_ command: [String: Any]) {
    let operation = command["operation"] as! String
    let id = command["id"] as? String ?? ""
    switch operation {
    case "create", "add":
        let window = FixtureWindow(contentRect: NSRect(x: backgroundFixture ? 21000 : 150, y: 200, width: 700, height: 450), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = command["title"] as! String
        window.isReleasedWhenClosed = false
        window.tabbingIdentifier = "AeroSpaceNativeTabFixture"
        let token = UUID().uuidString
        windows[token] = window
        if operation == "add" { own(id).addTabbedWindow(window, ordered: .above) }
        focus(window)
        reply(["tab": token, "terminal": "fixture-" + token])
    case "select":
        focus(own(id)); reply(["selected": id])
    case "status":
        let window = own(id)
        reply(["selected": identifier(window.tabGroup?.selectedWindow ?? window), "count": window.tabGroup?.windows.count ?? 1])
    case "merge":
        let source = own(id)
        let target = own(command["target"] as! String)
        let sourceWindows = source.tabGroup?.windows ?? [source]
        if source.tabGroup == nil || source.tabGroup !== target.tabGroup {
            for window in sourceWindows where window !== target { target.addTabbedWindow(window, ordered: .above) }
        }
        focus(command["selectIncoming"] as? Bool == true ? source : target)
        reply(["merged": true])
    case "detach":
        own(id).moveTabToNewWindow(nil)
        if command["activate"] as? Bool != false { focus(own(id)) }
        reply(["detached": true])
    case "close":
        if let window = windows[id] { window.close(); windows.removeValue(forKey: id) }
        reply(["closed": true])
    case "quit":
        for window in windows.values { window.close() }
        reply(["closed": true]); exit(0)
    default:
        reply(["error": "Unknown fixture operation"])
    }
}
DispatchQueue.global().async {
    while let line = readLine() {
        guard let data = line.data(using: .utf8), let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
        DispatchQueue.main.async { perform(command) }
    }
    DispatchQueue.main.async { for window in windows.values { window.close() }; exit(0) }
}
app.run()
