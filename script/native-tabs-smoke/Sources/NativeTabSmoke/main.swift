@testable import AppBundle
import AppKit
import Common

@MainActor
enum SmokeState {
    static var windows: [UInt32: NSWindow] = [:]
    static var model: [UInt32: TestWindow] = [:]
    static var activeIds: [UInt32] = []

    static func create(_ title: String, _ x: CGFloat) -> UInt32 {
        let w = NSWindow(contentRect: NSRect(x: x, y: 200, width: 600, height: 400), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        w.title = title
        w.isReleasedWhenClosed = false
        w.orderFront(nil)
        let id = UInt32(w.windowNumber)
        windows[id] = w
        return id
    }
    static func select(_ ids: [UInt32]) {
        for id in ids {
            let w = windows[id]!
            w.tabGroup?.selectedWindow = w
            w.orderFront(nil)
        }
    }
    static func adopt(_ replacements: [UInt32: UInt32], exposedWindowIds: Set<UInt32>) {
        let focusedWorkspace = focus.workspace
        let plan = nativeTabTransferPlan(replacements: replacements, managedWindowIds: Set(model.keys), exposedWindowIds: exposedWindowIds)
        for (newId, oldId) in plan {
            let old = model[oldId]!
            let workspace = old.nodeWorkspace!
            let index = old.ownIndex
            let new = model[newId] ?? TestWindow.new(id: newId, parent: Workspace.get(byName: "would-be-wrong-workspace").rootTilingContainer)
            transferNativeTabState(from: old, to: new)
            precondition(new.nodeWorkspace === workspace && new.ownIndex == index)
            precondition(focus.workspace === focusedWorkspace, "Background tab switch changed focused workspace")
            precondition(workspace.allLeafWindowsRecursive.count == 1)
            model.removeValue(forKey: oldId)
            model[newId] = new
        }
    }
    static func adoptTopology(_ update: NativeTabUpdate) {
        for (newId, sourceId) in update.inheritances where model[newId] == nil {
            if let workspace = model[sourceId]?.nodeWorkspace {
                model[newId] = TestWindow.new(id: newId, parent: workspace.rootTilingContainer)
            }
        }
        let plan = nativeTabTransferPlan(replacements: update.replacements, managedWindowIds: Set(model.keys), exposedWindowIds: update.exposedWindowIds)
        for (newId, oldId) in plan {
            let old = model[oldId]!
            let next = model[newId] ?? TestWindow.new(id: newId, parent: Workspace.get(byName: "wrong-workspace").rootTilingContainer)
            transferNativeTabState(from: old, to: next)
            model.removeValue(forKey: oldId)
            model[newId] = next
        }
        for (oldId, newId) in update.absorptions {
            if let old = model[oldId], let survivor = model[newId] {
                absorbNativeTabState(from: old, into: survivor)
                model.removeValue(forKey: oldId)
            }
        }
    }
    static func close(_ ids: [UInt32]) { for id in ids { windows[id]!.close() } }
    static func shutdown(_ status: Int32) {
        for w in windows.values { w.close() }
        fflush(stdout)
        exit(status)
    }
}

final class AxSmokeRunner: @unchecked Sendable {
    // Called only by the serial AX worker. AX objects never cross to the main actor.
    private var previous: [UInt32: AxNativeTabObservation] = [:]
    private let app = AXUIElementCreateApplication(getpid())
    private let state = AxNativeTabState()
    private func observe() throws -> [UInt32: AxNativeTabObservation] {
        var result: [UInt32: AxNativeTabObservation] = [:]
        var raw: AnyObject?
        guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &raw) == .success,
              let windows = raw as? [AXUIElement] else { throw NSError(domain: "own-AX", code: 1) }
        for w in windows {
            guard let id = w.containingWindowId() else { continue }
            result[id] = try AxNativeTabObservation.read(id, w, RunLoopJob(.nonCancellable))
        }
        return result
    }
    private func main(_ body: @MainActor @escaping () -> Void) {
        DispatchQueue.main.sync { MainActor.assumeIsolated { body() } }
    }
    private func transition(_ ids: [UInt32], expected: [UInt32: UInt32], acknowledge: Bool = true) throws {
        main { SmokeState.select(ids) }
        let current = try observe()
        let live = current.map { (windowId: $0.key, ax: $0.value.window!) }
        let cancelled = RunLoopJob(.cancellable)
        cancelled.cancel()
        do { _ = try state.reconcile(live, cancelled, isAppHidden: false); preconditionFailure("Canceled scan committed") } catch is CancellationError {}
        let replacements = try state.reconcile(live, RunLoopJob(.nonCancellable), isAppHidden: false)
        let inaccessible = try state.reconcile(nil, RunLoopJob(.nonCancellable), isAppHidden: false)
        precondition(inaccessible == replacements)
        print("expected=\(expected) actual=\(replacements) exposed=\(Array(current.keys))")
        precondition(replacements == expected, "Native tab membership mismatch")
        let exposedIds = Set(current.keys)
        main {
            SmokeState.adopt(replacements, exposedWindowIds: exposedIds)
            precondition(Set(SmokeState.model.keys) == exposedIds, "Queued handoff ended on an inactive tab")
        }
        if acknowledge {
            state.acknowledge(replacements)
            precondition(state.pendingReplacements.isEmpty)
        }
        previous = current
    }
    func topologyProbe(_ a: UInt32, _ b: UInt32, _ c: UInt32, _ d: UInt32) {
        do {
            func scan(_ note: String, acknowledge: Bool = true) throws {
                let current = try observe()
                print("TOPOLOGY \(note) exposed=\(current.mapValues { $0.snapshot.tabCount ?? 0 })")
                for (oldId, old) in previous {
                    for (newId, new) in current {
                        let barEqual = old.tabBar.flatMap { lhs in new.tabBar.map { CFEqual(lhs, $0) } } ?? false
                        let overlap = old.tabs.filter { t in new.tabs.contains { CFEqual(t, $0) } }.count
                        print("TOPOLOGY old=\(oldId) new=\(newId) barEqual=\(barEqual) owner=\(String(describing: old.tabBar?.containingWindowId())) overlap=\(overlap) shares=\(old.sharesTabBar(with: new)) oldRoot=\(String(describing: old.window?.containingWindowId())) frame=\(String(describing: new.snapshot.frame))")
                    }
                }
                let result = try state.reconcile(current.map { (windowId: $0.key, ax: $0.value.window!) }, RunLoopJob(.nonCancellable), isAppHidden: false)
                let update = state.update
                print("TOPOLOGY replacements=\(result) absorbed=\(update.absorptions) inherited=\(update.inheritances)")
                main { SmokeState.adoptTopology(update) }
                if acknowledge {
                    state.acknowledge(result)
                    state.acknowledgeTopology(absorptions: update.absorptions, inheritances: update.inheritances)
                }
                previous = current
            }
            try scan("initial")
            main {
                for id in [a, b] { SmokeState.windows[c]!.addTabbedWindow(SmokeState.windows[id]!, ordered: .above) }
                SmokeState.select([c])
            }
            try scan("merge group a into c", acknowledge: false)
            try scan("retry committed merge")
            main {
                precondition(SmokeState.model.count == 1, "Merged groups retain duplicate workspace slots")
            }
            main { SmokeState.windows[c]!.moveTabToNewWindow(nil) }
            try scan("detach selected c", acknowledge: false)
            try scan("retry committed detach")
            main {
                precondition(SmokeState.model.count == 2)
                precondition(SmokeState.model.values.allSatisfy { $0.nodeWorkspace?.name == "workspace-2" })
            }
            main { SmokeState.windows[d]!.addTabbedWindow(SmokeState.windows[c]!, ordered: .above); SmokeState.select([d]) }
            try scan("merge standalone c into d")
            main { precondition(SmokeState.model.count == 1) }
            main { SmokeState.windows[a]!.moveTabToNewWindow(nil) }
            try scan("detach inactive a")
            main {
                precondition(SmokeState.model.count == 2)
                precondition(SmokeState.model.values.allSatisfy { $0.nodeWorkspace?.name == "workspace-2" })
            }
            print("TOPOLOGY probe complete")
            main { SmokeState.shutdown(0) }
        } catch { print("TOPOLOGY FAILED: \(error)"); main { SmokeState.shutdown(1) } }
    }
    func run(_ a: UInt32, _ b: UInt32, _ c: UInt32, _ d: UInt32) {
        do {
            previous = try observe()
            _ = try state.reconcile(previous.map { (windowId: $0.key, ax: $0.value.window!) }, RunLoopJob(.nonCancellable), isAppHidden: false)
            print("trusted=\(AXIsProcessTrusted()) baseline=\(Array(previous.keys))")
            precondition(Set(previous.keys) == [a, c])
            for _ in 0..<10 {
                try transition([b, d], expected: [b: a, d: c])
                try transition([a, c], expected: [a: b, c: d])
            }
            // Commit without acknowledgement, switch back, then retry the same
            // queued cycle. The model must stay on the exposed tab throughout.
            try transition([b, d], expected: [b: a, d: c], acknowledge: false)
            try transition([a, c], expected: [b: a, d: c, a: b, c: d], acknowledge: false)
            try transition([a, c], expected: [b: a, d: c, a: b, c: d])
            try transition([b, d], expected: [b: a, d: c])
            main { SmokeState.close([b, d]) }
            let current = try observe()
            let replacements = try state.reconcile(current.map { (windowId: $0.key, ax: $0.value.window!) }, RunLoopJob(.nonCancellable), isAppHidden: false)
            for (id, o) in previous { print("closed source \(id): root=\(String(describing: o.window?.containingWindowId())) frame=\(String(describing: o.snapshot.frame)) tabs=\(String(describing: o.snapshot.tabCount))") }
            for (id, o) in current { print("remaining \(id): frame=\(String(describing: o.snapshot.frame)) tabs=\(String(describing: o.snapshot.tabCount))") }
            print("close selected, final tabs: expected=\([a: b, c: d]) actual=\(replacements)")
            precondition(replacements == [a: b, c: d], "Closing last sibling lost native group")
            let exposedIds = Set(current.keys)
            main { SmokeState.adopt(replacements, exposedWindowIds: exposedIds) }
            state.acknowledge(replacements)
            previous = current
            let newIds: [UInt32] = DispatchQueue.main.sync { MainActor.assumeIsolated {
                let e = SmokeState.create("Group 1 new tab", 100)
                let f = SmokeState.create("Group 2 new tab", 1000)
                SmokeState.model[e] = TestWindow.new(id: e, parent: Workspace.get(byName: "staged-new-window").rootTilingContainer)
                SmokeState.model[f] = TestWindow.new(id: f, parent: Workspace.get(byName: "staged-new-window").rootTilingContainer)
                return [e, f]
            } }
            let bare = try observe()
            let noReplacement = try state.reconcile(bare.map { (windowId: $0.key, ax: $0.value.window!) }, RunLoopJob(.nonCancellable), isAppHidden: false)
            precondition(noReplacement.isEmpty)
            main {
                SmokeState.windows[a]!.addTabbedWindow(SmokeState.windows[newIds[0]]!, ordered: .above)
                SmokeState.windows[c]!.addTabbedWindow(SmokeState.windows[newIds[1]]!, ordered: .above)
            }
            try transition(newIds, expected: [newIds[0]: a, newIds[1]: c])
            print("PASS: first-tab creation, cancellation before reads and after tree commits, 50 native tab selections across 2 groups, workspace custody, and closing selected tabs; no Accessibility grant")
            main { SmokeState.shutdown(0) }
        } catch {
            print("FAILED: \(error)")
            main { SmokeState.shutdown(1) }
        }
    }
}

setbuf(stdout, nil)
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
app.finishLaunching()
NSWindow.allowsAutomaticWindowTabbing = false
setUpWorkspacesForTests()
let a = SmokeState.create("Group 1 A", 100)
let b = SmokeState.create("Group 1 B", 100)
let c = SmokeState.create("Group 2 A", 1000)
let d = SmokeState.create("Group 2 B", 1000)
SmokeState.windows[a]!.addTabbedWindow(SmokeState.windows[b]!, ordered: .above)
SmokeState.windows[c]!.addTabbedWindow(SmokeState.windows[d]!, ordered: .above)
SmokeState.select([a, c])
SmokeState.model[a] = TestWindow.new(id: a, parent: Workspace.get(byName: "workspace-1").rootTilingContainer, adaptiveWeight: 3)
SmokeState.model[c] = TestWindow.new(id: c, parent: Workspace.get(byName: "workspace-2").rootTilingContainer, adaptiveWeight: 5)
_ = SmokeState.model[c]!.focusWindow()
let worker = DispatchQueue(label: "native-tab-smoke.ax")
worker.async {
    let runner = AxSmokeRunner()
    if CommandLine.arguments.contains("--topology-probe") { runner.topologyProbe(a, b, c, d) }
    else { runner.run(a, b, c, d) }
}
app.run()
