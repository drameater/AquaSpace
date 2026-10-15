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

    init(windows: String, workspaces: String, workspace: String, focusedWindow: String) {
        self.windows = windows.split(separator: "\n").map { $0.components(separatedBy: "\t") }.filter { $0.count == 4 && UInt32($0[0]) != nil }
        self.workspaces = workspaces.split(separator: "\n").map { $0.components(separatedBy: "\t") }.filter { $0.count == 2 }
        self.workspace = workspace.trimmingCharacters(in: .whitespacesAndNewlines)
        self.focusedWindow = focusedWindow.trimmingCharacters(in: .whitespacesAndNewlines)
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
    private static var applicationWatch: Task<(), Never>?
    nonisolated private static let writePermission = OSAllocatedUnfairLock(initialState: false)
    nonisolated static var permitsAXWrites: Bool { writePermission.withLock { $0 } }
    nonisolated static func freezeAXWrites() { writePermission.withLock { $0 = false } }
    static var isYielding: Bool { admission.ownership == .yielding }
    static var canControlWindows: Bool {
        !serverArgs.isReadOnly && (isUnitTest || permitsAXWrites)
    }
    static var canHandoff: Bool { !serverArgs.isReadOnly && !isYielding }
    static var status: String {
        let inventory = try? WindowManagerProcesses.peers()
        let peers = inventory?.map(\.pid) ?? []
        let data = try? JSONSerialization.data(withJSONObject: ["pid": myPid, "phase": admission.ownership.rawValue,
            "controlsWindows": canControlWindows && inventory != nil, "readOnly": serverArgs.isReadOnly, "originalPids": peers,
            "inventoryVerified": inventory != nil])
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
            writePermission.withLock { $0 = true }
            watchForOriginal()
        } catch {
            await stopForUncertainOwnership(error)
        }
    }

    static func restoreStartupSession() async {
        guard let session = startupSession else { return }
        startupSession = nil
        let ids = Set(MacWindow.allWindowsMap.keys.map(String.init))
        for args in session.restorationCommands(availableWindowIds: ids) {
            _ = try? await localCommand(args)
        }
    }

    static func switchToOriginal(launch: Bool) async {
        guard !serverArgs.isReadOnly, !isYielding else { return }
        admission.yield()
        freezeAXWrites()
        // Freeze synchronously, before any AX/network await. Disabled AeroSpace's
        // normal layout path unhides every window, which must not run in a handoff.
        pauseWindowManagementForHandoff()
        let session = captureCurrentModel()
        do {
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

    private static func captureCurrentModel() -> WindowManagerSession {
        let windows = MacWindow.allWindows.compactMap { window -> String? in
            guard window.isBound, let workspace = window.nodeWorkspace,
                  let layout = toLayoutResult(w: window).getIgnoringErrorsOrNil()
            else { return nil }
            return [String(window.windowId), workspace.name, layout.toString(), String(window.isFullscreen)].joined(separator: "\t")
        }
        let workspaces = Workspace.all.map { [$0.name, toLayoutString(tc: $0.rootTilingContainer)].joined(separator: "\t") }
        return WindowManagerSession(windows: windows.joined(separator: "\n"), workspaces: workspaces.joined(separator: "\n"),
                                    workspace: focus.workspace.name, focusedWindow: focus.windowOrNil.map { String($0.windowId) } ?? "")
    }

    private static func capture(_ command: ([String]) async throws -> String) async throws -> WindowManagerSession {
        let windows = try await command(WindowManagerSession.windowQuery)
        let workspaces = try await command(WindowManagerSession.workspaceQuery)
        let workspace = try await command(["list-workspaces", "--focused"])
        let focused = (try? await command(["list-windows", "--focused", "--format", "%{window-id}"])) ?? ""
        return WindowManagerSession(windows: windows, workspaces: workspaces, workspace: workspace, focusedWindow: focused)
    }

    private static func localCommand(_ args: [String]) async throws -> String {
        guard let command = parseCommand(args).cmdOrNil else { throw HandoffError.message("Invalid handoff command") }
        let result = await command.run(.defaultEnv, .emptyStdin)
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
