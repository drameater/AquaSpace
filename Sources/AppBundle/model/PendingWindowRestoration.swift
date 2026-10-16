import Foundation

/// A numeric PID/window ID never authorizes a delayed move on its own.
struct WindowRestorationOwner: Codable, Equatable, Sendable {
    let pid: Int32
    let uid: UInt32
    let startedSeconds: UInt64
    let startedMicroseconds: UInt64
    let executable: String
    let uniqueId: UInt64
    let execVersion: Int32
    let imageUUID: [UInt8]

    var isValid: Bool {
        pid > 0 && startedSeconds > 0 && startedMicroseconds < 1_000_000 &&
            executable.hasPrefix("/") && uniqueId > 0 && execVersion > 0 &&
            imageUUID.count == 16 && imageUUID.contains { $0 != 0 }
    }
}

struct PendingWindowAssignment: Equatable, Sendable {
    let windowId: UInt32
    let owner: WindowRestorationOwner
    let workspace: String
    let floating: Bool
    let fullscreen: Bool
    let rootLayout: String?
}

/// No commands, focus, AX or process APIs. The adapter supplies fresh proof and
/// commits a background model change synchronously, without an intervening await.
struct PendingWindowRestoration {
    private(set) var assignments: [UInt32: PendingWindowAssignment] = [:]

    init(windows: [[String]], owners: [String: WindowRestorationOwner], workspaces: [[String]] = []) {
        let counts = windows.reduce(into: [String: Int]()) { result, row in
            if let id = row.first { result[id, default: 0] += 1 }
        }
        let rootCounts = workspaces.reduce(into: [String: Int]()) { result, row in
            if let name = row.first { result[name, default: 0] += 1 }
        }
        var rootLayouts: [String: String] = [:]
        for row in workspaces where row.count == 2 && rootCounts[row[0]] == 1 &&
            ["h_tiles", "v_tiles", "h_accordion", "v_accordion"].contains(row[1]) {
            rootLayouts[row[0]] = row[1]
        }
        let layouts: Set<String> = ["floating", "h_tiles", "v_tiles", "h_accordion", "v_accordion",
            "macos_native_fullscreen", "macos_native_minimized", "macos_native_window_of_hidden_app"]
        for row in windows {
            guard row.count == 4, counts[row[0]] == 1, let id = UInt32(row[0]), id > 0,
                  String(id) == row[0], !row[1].isEmpty, layouts.contains(row[2]),
                  ["true", "false"].contains(row[3]), let owner = owners[row[0]], owner.isValid
            else { continue }
            assignments[id] = PendingWindowAssignment(windowId: id, owner: owner, workspace: row[1],
                floating: row[2] == "floating", fullscreen: row[3] == "true", rootLayout: rootLayouts[row[1]])
        }
    }

    mutating func discard(_ id: UInt32) { assignments.removeValue(forKey: id) }
    mutating func removeAll() { assignments.removeAll() }

    /// Losing observation continuity revokes the assignment. A later reused ID
    /// or an app restart must not inherit the original window's saved state.
    mutating func reconcile(ownersByWindow: [UInt32: Set<Int32>]?, process: (Int32) -> WindowRestorationOwner?) {
        guard let ownersByWindow else { removeAll(); return }
        assignments = assignments.filter { id, assignment in
            ownersByWindow[id] == [assignment.owner.pid] && process(assignment.owner.pid) == assignment.owner
        }
    }

    @discardableResult
    mutating func attempt(_ id: UInt32, owner: WindowRestorationOwner?, nativeOwners: Set<Int32>?,
                          axVerified: Bool, controlsWindows: Bool, userChanged: Bool,
                          apply: (PendingWindowAssignment) -> Bool) -> Bool {
        guard let assignment = assignments[id] else { return false }
        guard !userChanged, owner == assignment.owner, nativeOwners == [assignment.owner.pid] else {
            discard(id)
            return false
        }
        guard controlsWindows, axVerified else { return false }
        guard apply(assignment) else { return false }
        discard(id)
        return true
    }
}
