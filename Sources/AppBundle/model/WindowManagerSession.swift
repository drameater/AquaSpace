import Foundation

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
