import AppKit
import Common
import Network
import os
import Darwin

/// Only session metadata crosses between managers; terminal/window contents are never read.
struct WindowManagerSession: Codable, Equatable, Sendable {
    static let windowQuery = ["list-windows", "--all", "--format", "%{window-id}\t%{workspace}\t%{window-layout}\t%{window-is-fullscreen}"]
    static let workspaceQuery = ["list-workspaces", "--all", "--format", "%{workspace}\t%{workspace-root-container-layout}"]
    let windows: [[String]]
    let workspaces: [[String]]
    let workspace: String
    let focusedWindow: String
    var owners: [String: WindowRestorationOwner]? = nil

    init(windows: String, workspaces: String, workspace: String, focusedWindow: String) {
        self.windows = windows.split(separator: "\n").map { $0.components(separatedBy: "\t") }.filter { $0.count == 4 && UInt32($0[0]) != nil }
        self.workspaces = workspaces.split(separator: "\n").map { $0.components(separatedBy: "\t") }.filter { $0.count == 2 }
        self.workspace = workspace.trimmingCharacters(in: .whitespacesAndNewlines)
        self.focusedWindow = focusedWindow.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Carry authenticated unresolved intent through subsequent updates. Existing
    /// rows may be replaced only when their captured process identity agrees.
    func retainingPending(_ pending: [UInt32: PendingWindowAssignment]) -> WindowManagerSession {
        var rows = windows
        var identities = owners ?? [:]
        var workspaceRows = workspaces
        for (id, assignment) in pending.sorted(by: { $0.key < $1.key }) {
            let key = String(id)
            let matches = rows.indices.filter { rows[$0].first == key }
            guard assignment.owner.isValid, matches.count <= 1,
                  matches.isEmpty || identities[key] == assignment.owner else { continue }
            let row = [key, assignment.workspace, assignment.floating ? "floating" : "h_tiles", String(assignment.fullscreen)]
            if let index = matches.first { rows[index] = row } else { rows.append(row) }
            identities[key] = assignment.owner
            if let layout = assignment.rootLayout, !workspaceRows.contains(where: { $0.first == assignment.workspace }) {
                workspaceRows.append([assignment.workspace, layout])
            }
        }
        var result = WindowManagerSession(windows: rows.map { $0.joined(separator: "\t") }.joined(separator: "\n"),
            workspaces: workspaceRows.map { $0.joined(separator: "\t") }.joined(separator: "\n"),
            workspace: workspace, focusedWindow: focusedWindow)
        result.owners = identities
        return result
    }

    func restorationCommands(availableWindowIds: Set<String>) -> [[String]] {
        let surviving = windows.filter { $0.count == 4 && availableWindowIds.contains($0[0]) }
        var commands = surviving.map { ["move-node-to-workspace", "--window-id", $0[0], $0[1]] }
        commands += workspaces.filter { $0.count == 2 }.map { ["layout", "--workspace", $0[0], "--root", $0[1]] }
        commands += surviving.filter { $0[2] == "floating" }.map { ["layout", "--window-id", $0[0], "floating"] }
        commands += surviving.filter { $0[3] == "true" }.map { ["fullscreen", "on", "--window-id", $0[0]] }
        if !workspace.isEmpty { commands.append(["workspace", workspace]) }
        if availableWindowIds.contains(focusedWindow) { commands.append(["focus", "--window-id", focusedWindow]) }
        return commands
    }
}

@MainActor
enum WindowManagerHandoff {
    private static let admission = WindowManagerAdmission(inventory: WindowManagerProcesses.peers)
    private static var startupSession: WindowManagerSession?
    private static var pendingRestoration = PendingWindowRestoration(windows: [], owners: [:])
    private static var restoringPending = false
    private static var restorationAnchors: [UInt32: RestorationAnchor] = [:]
    private static var applicationWatch: Task<(), Never>?
    nonisolated private static let writePermission = OSAllocatedUnfairLock(initialState: WindowControlPermission())
    nonisolated static var permitsAXWrites: Bool { writePermission.withLock { $0.permitsWrites } }
    nonisolated static var controlGeneration: UUID { writePermission.withLock { $0.generation } }
    nonisolated static func freezeAXWrites() { writePermission.withLock { $0.freeze() } }
    static var isYielding: Bool { admission.ownership == .yielding }
    static var canControlWindows: Bool {
        !serverArgs.isReadOnly && (isUnitTest || permitsAXWrites)
    }
    static var canInitializeWindows: Bool {
        !isYielding && writePermission.withLock { $0.permitsInitialization }
    }
    static var canHandoff: Bool { !serverArgs.isReadOnly && !isYielding }
    static var status: String {
        let inventory = try? WindowManagerProcesses.peers()
        let peers = inventory?.map(\.pid) ?? []
        let data = try? JSONSerialization.data(withJSONObject: ["pid": myPid, "phase": admission.ownership.rawValue,
            "controlsWindows": canControlWindows && inventory != nil, "readOnly": serverArgs.isReadOnly, "originalPids": peers,
            "inventoryVerified": inventory != nil, "sessionSchema": 2])
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }
    private static let originalSocketPath = "/tmp/\(originalAeroSpaceAppId)-\(unixUserName).sock"

    static func watchForOriginal() {
        guard !serverArgs.isReadOnly, applicationWatch == nil else { return }
        applicationWatch = Task.startUnstructured { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(200))
                do {
                    let peers = try admission.observedPeers(freeze: freezeAXWrites)
                    if !peers.isEmpty {
                        await yieldToPeers(peers)
                        return
                    }
                } catch {
                    await stopForUncertainOwnership(error)
                    return
                }
            }
        }
    }

    /// Notifications freeze synchronously; polling also catches unbundled peers.
    static func observedPeerLaunch() async {
        do { await yieldToPeers(try admission.observedPeers(freeze: freezeAXWrites)) }
        catch { await stopForUncertainOwnership(error) }
    }

    private static func yieldToPeers(_ peers: [WindowManagerProcess]) async {
        if peers.count == 1 && peers[0].isStockRelease {
            await switchToOriginal(launch: false)
        } else {
            await stopForUncertainOwnership(WindowManagerAdmissionError.competingManager)
        }
    }

    private static func stopForUncertainOwnership(_ error: any Error) async {
        guard !serverArgs.isReadOnly else { return }
        admission.yield()
        pendingRestoration.removeAll()
        restorationAnchors.removeAll()
        restorationRootTokens.removeAll()
        freezeAXWrites()
        pauseWindowManagementForHandoff()
        await activateMode_nonCancellable(nil)
        for app in MacApp.allAppsMap.values { await app.drainWindowWrites() }
        showFailure(error)
        exit(EXIT_FAILURE)
    }

    static func prepareForStartup() async {
        guard !serverArgs.isReadOnly else { return }
        do {
            let originals = try admission.prepare(build: isDebug ? .debug : .release, readOnly: false)
            if originals.isEmpty {
                if let path = serverArgs.restoreSessionPath {
                    startupSession = try JSONDecoder().decode(WindowManagerSession.self, from: Data(contentsOf: URL(filePath: path)))
                }
                return
            }
            startupSession = try await capture(remoteCommand)
            // Revalidate kernel birth identity after the asynchronous session capture.
            guard Set(try WindowManagerProcesses.peers()) == Set(originals) else {
                throw WindowManagerAdmissionError.uncertainInventory
            }
            for original in originals {
                guard kill(original.pid, SIGKILL) == 0 || errno == ESRCH else {
                    throw WindowManagerAdmissionError.uncertainInventory
                }
            }
            for _ in 0 ..< 40 {
                if try WindowManagerProcesses.peers().isEmpty { return }
                try await Task.sleep(for: .milliseconds(50))
            }
            throw HandoffError.message("AeroSpace did not stop. AquaSpace will not start a second window manager.")
        } catch {
            showFailure(error)
            exit(EXIT_FAILURE)
        }
    }

    static func finishStartup() async {
        guard !serverArgs.isReadOnly else { return }
        do {
            try admission.claim()
            writePermission.withLock { $0.claim() }
            watchForOriginal()
        } catch {
            await stopForUncertainOwnership(error)
        }
    }

    static func restoreStartupSession() async {
        guard let session = startupSession else { return }
        startupSession = nil
        pendingRestoration = PendingWindowRestoration(windows: session.windows, owners: session.owners ?? [:], workspaces: session.workspaces)
        reconcilePendingRestoration()
        let ids = Set(MacWindow.allWindows.filter { $0.discoveryInitialization?.isInProgress != true }
            .map { String($0.windowId) })
        var failed: Set<UInt32> = []
        for args in session.restorationCommands(availableWindowIds: ids) {
            let id = args.firstIndex(of: "--window-id").flatMap { args.indices.contains($0 + 1) ? UInt32(args[$0 + 1]) : nil }
            if let id, session.owners?[String(id)] != nil,
               pendingRestoration.assignments[id] == nil { continue }
            if let id, MacWindow.allWindowsMap[id]?.discoveryInitialization?.isInProgress == true {
                failed.insert(id)
                continue
            }
            do { _ = try await localCommand(args) }
            catch { if let id, args.first != "focus" { failed.insert(id) } }
        }
        // Discovery can run while a command awaits. Only IDs included in the
        // original command plan have actually had their assignment restored.
        for text in ids {
            if let id = UInt32(text), !failed.contains(id) { pendingRestoration.discard(id) }
        }
    }

    static func switchToOriginal(launch: Bool) async {
        guard !serverArgs.isReadOnly, !isYielding else { return }
        admission.yield()
        freezeAXWrites()
        // Freeze synchronously, before any AX/network await. Disabled AeroSpace's
        // normal layout path unhides every window, which must not run in a handoff.
        pauseWindowManagementForHandoff()
        do {
            let session = try await captureCurrentModel()
            pendingRestoration.removeAll()
            restorationAnchors.removeAll()
            restorationRootTokens.removeAll()
            await activateMode_nonCancellable(nil)
            // Drain canceled AX work before launching another controller.
            for app in MacApp.allAppsMap.values { await app.drainWindowWrites() }
            if launch {
                guard NSWorkspace.shared.open(URL(filePath: "/Applications/AeroSpace.app")) else {
                    throw HandoffError.message("Could not open /Applications/AeroSpace.app")
                }
            }
            var available: Set<String>?
            for _ in 0 ..< 80 {
                if let answer = try? await remoteAnswer(["list-windows", "--all", "--format", "%{window-id}"]),
                   answer.exitCode == 0, answer.serverVersionAndHash != "\(aeroSpaceAppVersion) \(gitHash)"
                {
                    available = Set(answer.stdout.split(separator: "\n").map(String.init))
                    if available?.isSuperset(of: session.windows.map { $0[0] }) == true { break }
                }
                try await Task.sleep(for: .milliseconds(100))
            }
            guard let available else { throw HandoffError.message("AeroSpace did not become ready. AquaSpace has stopped window management.") }
            for args in session.restorationCommands(availableWindowIds: available) {
                _ = try await remoteCommand(args)
            }
            // A handoff must leave window geometry alone. The normal Quit path recenters
            // every window and would undo the new manager's restored workspaces.
            exit(EXIT_SUCCESS)
        } catch {
            // Fail closed. Never kill/restart AeroSpace, rebind its socket, or
            // re-enable AquaSpace while the original may already be managing windows.
            showFailure(error)
            exit(EXIT_FAILURE)
        }
    }

    private struct RestorationAnchor: Equatable {
        let window: ObjectIdentifier
        let parent: ObjectIdentifier?
        let workspace: String?
        let fullscreen: Bool
        @MainActor init(_ value: MacWindow) {
            window = ObjectIdentifier(value)
            parent = value.parent.map { ObjectIdentifier($0) }
            workspace = value.nodeWorkspace?.name
            fullscreen = value.isFullscreen
        }
    }
    private static var restorationRootTokens: [UInt32: UUID] = [:]

    private static func captureOwners(_ pids: [UInt32: Int32]) -> [String: WindowRestorationOwner] {
        guard let before = getRestorationWindowOwners() else { return [:] }
        var owners: [String: WindowRestorationOwner] = [:]
        for (id, pid) in pids where before[id] == [pid] {
            if let owner = WindowManagerProcesses.restorationOwner(pid) { owners[String(id)] = owner }
        }
        guard let after = getRestorationWindowOwners() else { return [:] }
        return owners.filter { id, owner in
            UInt32(id).map { after[$0] == [owner.pid] } == true &&
                WindowManagerProcesses.restorationOwner(owner.pid) == owner
        }
    }

    static func sessionForUpdate() async throws -> String {
        guard canControlWindows, TrayMenuModel.shared.isEnabled,
              try admission.observedPeers(freeze: freezeAXWrites).isEmpty else {
            throw HandoffError.message("Cannot capture a session without exclusive window control")
        }
        let session = try await captureCurrentModel()
        guard canControlWindows, try admission.observedPeers(freeze: freezeAXWrites).isEmpty else {
            throw HandoffError.message("Window-control ownership changed during capture")
        }
        return String(decoding: try JSONEncoder().encode(session), as: UTF8.self)
    }

    /// Called even before discovery, so observing a closed/reused native ID can
    /// never leave an old pending assignment alive until that ID is exposed again.
    static func reconcilePendingRestoration() {
        guard !pendingRestoration.assignments.isEmpty else {
            restorationAnchors.removeAll()
            restorationRootTokens.removeAll()
            return
        }
        pendingRestoration.reconcile(ownersByWindow: getRestorationWindowOwners(),
            process: WindowManagerProcesses.restorationOwner)
        restorationAnchors = restorationAnchors.filter { pendingRestoration.assignments[$0.key] != nil }
        restorationRootTokens = restorationRootTokens.filter { pendingRestoration.assignments[$0.key] != nil }
    }

    /// A proven native-tab transfer owns its workspace slot. Stock's old per-tab
    /// assignment must never override that slot when an inactive tab reappears.
    static func cancelPendingNativeTabAssignments(_ update: NativeTabUpdate, pid: Int32) {
        let participants = Set(Array(update.replacements.keys) + Array(update.replacements.values) +
            Array(update.absorptions.keys) + Array(update.absorptions.values) +
            Array(update.inheritances.keys) + Array(update.inheritances.values))
        for id in participants where pendingRestoration.assignments[id]?.owner.pid == pid {
            pendingRestoration.discard(id)
            restorationAnchors.removeValue(forKey: id)
            restorationRootTokens.removeValue(forKey: id)
        }
    }

    /// Explicit commands supersede saved intent even before the first anchor or
    /// conventional discovery. Automatic normalization and our startup commands
    /// do not acquire user-mutation provenance.
    static func cancelPendingForUserMutation(_ windows: [Window]) {
        guard WindowRestorationMutation.isUserCommand, !WindowRestorationMutation.isSessionRestore else { return }
        for window in windows where MacWindow.allWindowsMap[window.windowId] === window {
            window.userMutationToken = UUID()
            // Invalidate the delayed callback even if there is no saved intent.
            window.discoveryInitialization?.supersede()
            if pendingRestoration.assignments[window.windowId]?.owner.pid == window.app.pid {
                discardPendingRestoration(window.windowId)
            }
        }
    }

    private static func discardPendingRestoration(_ id: UInt32) {
        pendingRestoration.discard(id)
        restorationAnchors.removeValue(forKey: id)
        restorationRootTokens.removeValue(forKey: id)
    }

    /// Remember every successful read, including one followed by an unavailable
    /// second proof. Never replace established root history with a new token.
    private static func rememberRestorationRoot(_ token: UUID, for id: UInt32) -> Bool {
        guard pendingRestoration.assignments[id] != nil else { return false }
        if let previous = restorationRootTokens[id], previous != token {
            discardPendingRestoration(id)
            return false
        }
        restorationRootTokens[id] = token
        return true
    }

    static func restorePendingAssignments() async {
        guard canControlWindows, TrayMenuModel.shared.isEnabled, !restoringPending,
              !pendingRestoration.assignments.isEmpty else { return }
        restoringPending = true
        defer { restoringPending = false }
        reconcilePendingRestoration()
        for id in Array(pendingRestoration.assignments.keys).sorted() {
            guard let assignment = pendingRestoration.assignments[id],
                  let window = MacWindow.allWindowsMap[id], window.macApp.pid == assignment.owner.pid else { continue }
            if window.discoveryInitialization?.isInProgress == true { continue }
            let initialization = window.discoveryInitialization
            if focus.windowOrNil?.windowId == id || NSWorkspace.shared.frontmostApplication?.processIdentifier == assignment.owner.pid {
                pendingRestoration.discard(id)
                continue
            }
            // Automatic native hide/fullscreen normalization changes the parent.
            // Do not pin a user-change anchor until the window is conventional.
            switch window.windowParentCases {
                case .tilingContainer, .floatingWindowsContainer: break
                default: continue
            }
            let anchor = RestorationAnchor(window)
            if let old = restorationAnchors[id], old != anchor {
                pendingRestoration.discard(id)
                continue
            }
            restorationAnchors[id] = anchor
            var proof = try? await window.macApp.restorationWindowProof(id, level: getWindowLevel(for: id))
            let initialRootToken = proof?.token
            let initialRootAccepted = proof.map { rememberRestorationRoot($0.token, for: id) } ?? true
            if initialRootAccepted, proof != nil, proof?.type != .popup {
                proof = try? await window.macApp.restorationWindowProof(id, level: getWindowLevel(for: id))
            }
            // A fresh native read may suspend. Revalidate model, native ownership,
            // process generation and exclusive controller admission afterwards.
            guard canControlWindows, TrayMenuModel.shared.isEnabled else { return }
            do {
                let peers = try admission.observedPeers(freeze: freezeAXWrites)
                if !peers.isEmpty {
                    await yieldToPeers(peers)
                    return
                }
            } catch {
                await stopForUncertainOwnership(error)
                return
            }
            // Initialization may have begun while the fresh native proofs awaited.
            // Keep the assignment retryable; it must not race subsequent rules.
            if window.discoveryInitialization?.isInProgress == true ||
                window.discoveryInitialization !== initialization { continue }
            let changed = MacWindow.allWindowsMap[id] !== window || RestorationAnchor(window) != anchor ||
                focus.windowOrNil?.windowId == id || proof?.focused == true
            // Token checks are separate to keep an unavailable AX read retryable.
            let rootChanged = !initialRootAccepted || proof.map { value in
                initialRootToken != value.token || !rememberRestorationRoot(value.token, for: id)
            } ?? false
            let owners = getRestorationWindowOwners()
            if owners == nil { pendingRestoration.removeAll(); return }
            let currentOwner = WindowManagerProcesses.restorationOwner(assignment.owner.pid)
            let foregroundPid = NSWorkspace.shared.frontmostApplication?.processIdentifier
            _ = pendingRestoration.attempt(id,
                owner: currentOwner, nativeOwners: owners?[id],
                axVerified: proof != nil && proof?.type != .popup && proof?.isNativeTab == false && foregroundPid != nil, controlsWindows: canControlWindows,
                userChanged: changed || rootChanged || foregroundPid == assignment.owner.pid
            ) { desired in
                guard WorkspaceName.parse(desired.workspace).getIgnoringErrorsOrNil() != nil,
                      window.isBound else { return false }
                // Native hidden/minimized/fullscreen windows retain their native
                // containers until normal discovery exposes a conventional window.
                switch window.windowParentCases {
                    case .tilingContainer, .floatingWindowsContainer: break
                    default: return false
                }
                let existed = Workspace.all.contains { $0.name == desired.workspace }
                let workspace = Workspace.get(byName: desired.workspace)
                if !existed, let layout = desired.rootLayout {
                    workspace.rootTilingContainer.layout = layout.hasSuffix("accordion") ? .accordion : .tiles
                    workspace.rootTilingContainer.changeOrientation(layout.hasPrefix("v_") ? .v : .h)
                }
                if desired.floating || proof?.type == .dialog {
                    window.bindAsFloatingWindow(to: workspace)
                } else {
                    window.bind(to: workspace.rootTilingContainer, adaptiveWeight: WEIGHT_AUTO, index: INDEX_BIND_LAST)
                }
                window.isFullscreen = desired.fullscreen
                return true
            }
        }
    }

    private static func captureCurrentModel() async throws -> WindowManagerSession {
        reconcilePendingRestoration()
        // An update must not erase established root history. An unavailable AX
        // read blocks export (leaving the running manager alone); a proven root
        // replacement revokes stale intent before any saved row can override it.
        for id in Array(restorationRootTokens.keys).sorted() where pendingRestoration.assignments[id] != nil {
            guard let window = MacWindow.allWindowsMap[id],
                  let proof = try await window.macApp.restorationWindowProof(id, level: getWindowLevel(for: id)) else {
                throw HandoffError.message("Cannot verify pending window root continuity for export")
            }
            guard rememberRestorationRoot(proof.token, for: id) else { continue }
            guard let finalProof = try await window.macApp.restorationWindowProof(id, level: getWindowLevel(for: id)) else {
                throw HandoffError.message("Cannot verify pending window root continuity for export")
            }
            _ = rememberRestorationRoot(finalProof.token, for: id)
        }
        reconcilePendingRestoration()
        let windows = MacWindow.allWindows.compactMap { window -> String? in
            guard window.isBound, let workspace = window.nodeWorkspace,
                  let layout = toLayoutResult(w: window).getIgnoringErrorsOrNil()
            else { return nil }
            return [String(window.windowId), workspace.name, layout.toString(), String(window.isFullscreen)].joined(separator: "\t")
        }
        let workspaces = Workspace.all.map { [$0.name, toLayoutString(tc: $0.rootTilingContainer)].joined(separator: "\t") }
        var session = WindowManagerSession(windows: windows.joined(separator: "\n"), workspaces: workspaces.joined(separator: "\n"),
                                    workspace: focus.workspace.name, focusedWindow: focus.windowOrNil.map { String($0.windowId) } ?? "")
        let currentIds = Set(session.windows.compactMap { UInt32($0[0]) })
        session.owners = captureOwners(MacWindow.allWindowsMap.filter { currentIds.contains($0.key) }.mapValues { $0.macApp.pid })
        let foregroundPid = NSWorkspace.shared.frontmostApplication?.processIdentifier
        for (id, assignment) in pendingRestoration.assignments {
            let current = MacWindow.allWindowsMap[id]
            let changed = current.map { value in restorationAnchors[id].map { $0 != RestorationAnchor(value) } ?? false } ?? false
            if foregroundPid == nil || foregroundPid == assignment.owner.pid ||
                focus.windowOrNil?.windowId == id || changed {
                pendingRestoration.discard(id)
            }
        }
        return session.retainingPending(pendingRestoration.assignments)
    }

    private static func capture(_ command: ([String]) async throws -> String) async throws -> WindowManagerSession {
        let windows = try await command(WindowManagerSession.windowQuery)
        let workspaces = try await command(WindowManagerSession.workspaceQuery)
        let workspace = try await command(["list-workspaces", "--focused"])
        let focused = (try? await command(["list-windows", "--focused", "--format", "%{window-id}"])) ?? ""
        var session = WindowManagerSession(windows: windows, workspaces: workspaces, workspace: workspace, focusedWindow: focused)
        let rows = try await command(["list-windows", "--all", "--format", "%{window-id}\t%{app-pid}"])
        var pids: [UInt32: Int32] = [:]
        var duplicates: Set<UInt32> = []
        for row in rows.split(separator: "\n").map({ $0.components(separatedBy: "\t") }) {
            guard row.count == 2, let id = UInt32(row[0]), let pid = Int32(row[1]), pid > 0 else { continue }
            if pids.updateValue(pid, forKey: id) != nil { duplicates.insert(id) }
        }
        for id in duplicates { pids.removeValue(forKey: id) }
        session.owners = captureOwners(pids)
        guard try await command(WindowManagerSession.windowQuery) == windows else {
            throw HandoffError.message("Stock window assignments changed during capture")
        }
        return session
    }

    private static func localCommand(_ args: [String]) async throws -> String {
        guard let command = parseCommand(args).cmdOrNil else { throw HandoffError.message("Invalid handoff command") }
        let result = await WindowRestorationMutation.$isSessionRestore.withValue(true) {
            await command.run(.defaultEnv, .emptyStdin)
        }
        guard result.exitCode.rawValue == 0 else { throw HandoffError.message(result.stderr.joined(separator: "\n")) }
        return result.stdout.joined(separator: "\n")
    }

    private static func remoteCommand(_ args: [String]) async throws -> String {
        let result = try await remoteAnswer(args)
        guard result.exitCode == 0 else { throw HandoffError.message(result.stderr) }
        return result.stdout
    }

    private static func remoteAnswer(_ args: [String]) async throws -> ServerAnswer {
        let connection = NWConnection(to: .unix(path: originalSocketPath), using: .tcp)
        // Bound every socket exchange, including a manager that exits during handoff.
        let timeout = Task.startUnstructured {
            try? await Task.sleep(for: .seconds(2))
            if !Task.isCancelled { connection.cancel() }
        }
        defer { timeout.cancel(); connection.cancel() }
        guard await connection.initConnection().error == nil else { throw HandoffError.message("Cannot connect to the running window manager") }
        guard await connection.writeAtomic(ClientRequest(args: args, stdin: "", windowId: nil, workspace: nil)).error == nil,
              let data = await connection.readNonAtomic().getOrNil(onFailure: { _ in })
        else { throw HandoffError.message("The window manager stopped responding") }
        return try JSONDecoder().decode(ServerAnswer.self, from: data)
    }

    private static func showFailure(_ error: any Error) {
        // A modal alert can stall the main actor and leave a half-completed handoff.
        NSLog("AquaSpace handoff stopped: %@", error.localizedDescription)
    }

    private enum HandoffError: LocalizedError {
        case message(String)
        var errorDescription: String? { switch self { case .message(let message): message } }
    }
}
