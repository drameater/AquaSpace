import Foundation
import Darwin

/// Startup may initialize the model before claim, but a frozen controller must
/// never be mistaken for that initial state. A freeze also revokes old work if
/// a later successful admission grants a new control period.
struct WindowControlPermission: Sendable {
    private(set) var permitsWrites = false
    private(set) var permitsInitialization = true
    private(set) var generation = UUID()

    mutating func freeze() {
        permitsWrites = false
        permitsInitialization = false
        generation = UUID()
    }

    mutating func claim() {
        permitsWrites = true
        permitsInitialization = true
    }
}

enum WindowManagerOwnership: String, Sendable {
    case preparing, controlling, yielding

    mutating func claim(originalIsRunning: Bool) -> Bool {
        guard self == .preparing, !originalIsRunning else { return false }
        self = .controlling
        return true
    }

    mutating func yield() { self = .yielding }
}

struct WindowManagerProcess: Hashable, Sendable {
    let pid: Int32
    let startedSeconds: UInt64
    let startedMicroseconds: UInt64
    let executable: String
    let bundleId: String?

    static let bundleIds: Set<String> = [
        "local.aquaspace", "local.aquaspace.debug", "bobko.aerospace", "bobko.aerospace.debug",
    ]
    static let executableNames: Set<String> = ["AquaSpace", "AeroSpace", "AeroSpaceApp"]

    static func isManager(executable: String, bundleId: String?, bundleExecutable: String?) -> Bool {
        let name = URL(filePath: executable).lastPathComponent
        // A bundled CLI shares the bundle ID, but is not its controller.
        return executableNames.contains(name) ||
            (bundleExecutable == name && bundleId.map(bundleIds.contains) == true)
    }

    var isStockRelease: Bool { bundleId == "bobko.aerospace" }
}

enum WindowManagerAdmissionError: Error, LocalizedError {
    case uncertainInventory, competingManager, lockUnavailable

    var errorDescription: String? {
        switch self {
            case .uncertainInventory: "Cannot establish window-manager process identity. Refusing window control."
            case .competingManager: "Another window manager is running. Refusing a second controller."
            case .lockUnavailable: "Another AquaSpace owns the controller lock. Refusing window control."
        }
    }
}

/// Held for the process lifetime, shared by release, debug, and unbundled builds.
/// Read-only probes never acquire it. The inode is persistent and never unlinked.
final class WindowManagerControllerLock {
    private var descriptor: Int32 = -1
    private let path: String
    var isHeld: Bool { descriptor >= 0 }

    init(path: String = "/tmp/.aquaspace-controller-\(getuid()).lock") { self.path = path }

    func acquire() throws {
        if isHeld { return }
        let fd = unsafe open(path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
        guard fd >= 0 else { throw WindowManagerAdmissionError.lockUnavailable }
        var info = stat()
        guard unsafe fstat(fd, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1,
              flock(fd, LOCK_EX | LOCK_NB) == 0
        else {
            close(fd)
            throw WindowManagerAdmissionError.lockUnavailable
        }
        descriptor = fd
    }

    deinit { if descriptor >= 0 { close(descriptor) } }
}

/// The same admission path is used by both build configurations. Dependencies are
/// injectable so regressions exercise the real policy without native processes.
final class WindowManagerAdmission {
    enum Build { case release, debug }
    private(set) var ownership = WindowManagerOwnership.preparing
    private let lock: WindowManagerControllerLock
    private let inventory: () throws -> [WindowManagerProcess]

    init(lock: WindowManagerControllerLock = WindowManagerControllerLock(),
         inventory: @escaping () throws -> [WindowManagerProcess]) {
        self.lock = lock
        self.inventory = inventory
    }

    func prepare(build: Build, readOnly: Bool) throws -> [WindowManagerProcess] {
        guard !readOnly else { return [] }
        try lock.acquire()
        let peers = try inventory()
        guard peers.isEmpty || (build == .release && peers.allSatisfy(\.isStockRelease)) else {
            throw WindowManagerAdmissionError.competingManager
        }
        return peers
    }

    func claim() throws {
        guard lock.isHeld, ownership.claim(originalIsRunning: try !inventory().isEmpty) else {
            throw WindowManagerAdmissionError.competingManager
        }
    }

    func observedPeers(freeze: () -> Void) throws -> [WindowManagerProcess] {
        do {
            let peers = try inventory()
            if !peers.isEmpty { freeze() }
            return peers
        } catch {
            freeze()
            throw error
        }
    }

    func yield() { ownership.yield() }
}
