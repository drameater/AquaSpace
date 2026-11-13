import Foundation
import Darwin
import PrivateApi

struct WindowManagerProcessBirth: Equatable {
    let pid: Int32
    let uid: UInt32
    let seconds: UInt64
    let microseconds: UInt64
}

struct WindowManagerBundleMetadata {
    var identifier: String?
    var executable: String?
}

struct WindowManagerExecGeneration: Equatable {
    let uuid: [UInt8]
    let uniqueId: UInt64
    let version: Int32
}

protocol WindowManagerProcessAPI {
    var userId: UInt32 { get }
    var ownPid: Int32 { get }
    func pids() throws -> [Int32]
    func executable(_ pid: Int32) throws -> String?
    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth?
    func metadata(_ executable: String) throws -> WindowManagerBundleMetadata
    func generation(_ pid: Int32) throws -> WindowManagerExecGeneration
}

protocol WindowRestorationProcessAPI {
    var userId: UInt32 { get }
    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth?
    func executable(_ pid: Int32) throws -> String?
    func generation(_ pid: Int32) throws -> WindowManagerExecGeneration
}

/// Classify kernel executable paths before requesting BSD identity. macOS can
/// deny BSD records for unrelated helpers; those records are irrelevant to control.
/// A process whose path or bundle cannot be read (for example, an app replaced on
/// disk while it runs, or a process exiting mid-scan) is not a known manager. It is
/// skipped, so one unrelated process can never stop AquaSpace from controlling windows.
enum WindowManagerProcesses {
    static func peers() throws -> [WindowManagerProcess] { try peers(using: NativeWindowManagerProcessAPI()) }

    /// Ordinary application identity, not manager admission. Every read is
    /// bracketed by kernel birth and exec-generation reads; uncertainty is nil.
    static func restorationOwner(_ pid: Int32) -> WindowRestorationOwner? {
        restorationOwner(pid, using: NativeWindowManagerProcessAPI())
    }

    static func restorationOwner(_ pid: Int32, using api: any WindowRestorationProcessAPI) -> WindowRestorationOwner? {
        do {
            guard let before = try api.identity(pid), before.pid == pid, before.uid == api.userId,
                  let executable = try api.executable(pid) else { return nil }
            let generation = try api.generation(pid)
            guard try api.identity(pid) == before, try api.executable(pid) == executable,
                  try api.generation(pid) == generation else { return nil }
            let owner = WindowRestorationOwner(pid: pid, uid: before.uid, startedSeconds: before.seconds,
                startedMicroseconds: before.microseconds, executable: executable,
                uniqueId: generation.uniqueId, execVersion: generation.version, imageUUID: generation.uuid)
            return owner.isValid ? owner : nil
        } catch { return nil }
    }


    static func peers(using api: any WindowManagerProcessAPI) throws -> [WindowManagerProcess] {
        var result: [WindowManagerProcess] = []
        for pid in Set(try api.pids()) where pid > 0 && pid != api.ownPid {
            guard let candidate = classify(pid, using: api), candidate.isManager else { continue }
            if let peer = try verifiedPeer(pid, candidate, using: api) { result.append(peer) }
        }
        return result
    }

    struct Classification {
        let path: String
        let metadata: WindowManagerBundleMetadata
        /// False when the bundle Info.plist could not be read; the verdict then rests on the name only.
        let metadataComplete: Bool
        var isManager: Bool {
            WindowManagerProcess.isManager(executable: path, bundleId: metadata.identifier, bundleExecutable: metadata.executable)
        }
    }

    /// nil when the path cannot be read: the process is skipped, never invented.
    static func classify(_ pid: Int32, using api: any WindowManagerProcessAPI) -> Classification? {
        guard let path = try? api.executable(pid) else { return nil }
        let metadata = try? api.metadata(path)
        return Classification(path: path, metadata: metadata ?? WindowManagerBundleMetadata(), metadataComplete: metadata != nil)
    }

    /// Kernel birth identity brackets a second path read. A change in between is
    /// uncertainty, not absence, so the whole inventory fails closed.
    static func verifiedPeer(_ pid: Int32, _ candidate: Classification, using api: any WindowManagerProcessAPI) throws -> WindowManagerProcess? {
        let path = candidate.path
        guard let before = try api.identity(pid), before.uid == api.userId else { return nil }
        guard let verifiedPath = try api.executable(pid), let after = try api.identity(pid) else { return nil }
        guard before == after, path == verifiedPath else { throw WindowManagerAdmissionError.uncertainInventory }
        let identifier = candidate.metadata.executable == URL(filePath: path).lastPathComponent ? candidate.metadata.identifier : nil
        return WindowManagerProcess(pid: pid, startedSeconds: after.seconds,
            startedMicroseconds: after.microseconds, executable: path, bundleId: identifier)
    }
}

/// Incremental inventory for the steady-state watch loop only. Authoritative
/// decisions (startup prepare/claim, launch notifications, session export,
/// deferred restoration, status) use the uncached `WindowManagerProcesses.peers()`.
///
/// Only "not a manager" verdicts are cached, keyed by PID plus the kernel exec
/// generation (unique process ID, exec version and image UUID). The BSD start time
/// is not enough: exec keeps it. A reused PID has a new unique ID, and any exec,
/// even of a binary at the same path, bumps the exec version. So an unchanged
/// generation means the same image as when its path and bundle were classified.
/// A process whose generation cannot be read is classified uncached on every scan.
/// Manager candidates always take the full verified path, so a cached verdict can
/// only skip work, never report a peer.
final class WindowManagerProcessCache {
    private struct Entry {
        let generation: WindowManagerExecGeneration
        /// Set when the Info.plist was unreadable: re-read it after this scan, not every tick.
        let retryAfterScan: Int?
    }
    /// About 5 s at the 200 ms watch interval.
    static let metadataRetryScans = 25
    private let api: any WindowManagerProcessAPI
    private var unrelated: [Int32: Entry] = [:]
    private var scans = 0

    init(api: any WindowManagerProcessAPI = NativeWindowManagerProcessAPI()) { self.api = api }

    func peers() throws -> [WindowManagerProcess] {
        scans += 1
        let pids = Set(try api.pids())
        unrelated = unrelated.filter { pids.contains($0.key) }
        var result: [WindowManagerProcess] = []
        for pid in pids where pid > 0 && pid != api.ownPid {
            let generation = try? api.generation(pid)
            if let generation, let entry = unrelated[pid], entry.generation == generation,
               entry.retryAfterScan.map({ scans <= $0 }) ?? true { continue }
            unrelated[pid] = nil
            guard let candidate = WindowManagerProcesses.classify(pid, using: api) else { continue }
            if candidate.isManager {
                if let peer = try WindowManagerProcesses.verifiedPeer(pid, candidate, using: api) { result.append(peer) }
            } else if let generation, (try? api.generation(pid)) == generation {
                // Cache only a verdict whose image did not change while it was read.
                unrelated[pid] = Entry(generation: generation,
                    retryAfterScan: candidate.metadataComplete ? nil : scans + Self.metadataRetryScans)
            }
        }
        return result
    }
}

struct NativeWindowManagerProcessAPI: WindowManagerProcessAPI, WindowRestorationProcessAPI {
    var userId: UInt32 { getuid() }
    var ownPid: Int32 { getpid() }

    private func scanError(_ stage: String, pid: Int32 = 0, count: Int32 = 0, code: Int32 = 0) -> NSError {
        NSError(domain: "AquaSpace.ProcessInventory", code: Int(code), userInfo: [NSLocalizedDescriptionKey:
            "Cannot establish process identity (\(stage), pid \(pid), bytes \(count), errno \(code)). Refusing window control."])
    }

    func pids() throws -> [Int32] {
        let needed = proc_listpids(UInt32(PROC_UID_ONLY), userId, nil, 0)
        guard needed > 0 else { throw scanError("list-size", count: needed, code: errno) }
        var capacity = Int(needed) / MemoryLayout<Int32>.size + 64
        for _ in 0 ..< 3 {
            var pids = [Int32](repeating: 0, count: capacity)
            let bytes = pids.count * MemoryLayout<Int32>.size
            let count = unsafe pids.withUnsafeMutableBytes {
                unsafe proc_listpids(UInt32(PROC_UID_ONLY), userId, $0.baseAddress, Int32(bytes))
            }
            guard count > 0, count % Int32(MemoryLayout<Int32>.size) == 0 else {
                throw scanError("list-pids", count: count, code: errno)
            }
            if count >= bytes { capacity *= 2; continue }
            return Array(pids.prefix(Int(count) / MemoryLayout<Int32>.size))
        }
        throw scanError("list-truncated")
    }

    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth? {
        var value = proc_bsdinfo()
        errno = 0
        let count = unsafe proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &value, Int32(MemoryLayout<proc_bsdinfo>.size))
        let code = errno
        guard count == MemoryLayout<proc_bsdinfo>.size else {
            if code == ESRCH { return nil }
            throw scanError("bsd-info", pid: pid, count: count, code: code)
        }
        if value.pbi_status == 5 { return nil } // SZOMB cannot control windows.
        guard value.pbi_pid == pid else { throw scanError("pid-mismatch", pid: pid, count: Int32(value.pbi_pid)) }
        return WindowManagerProcessBirth(pid: pid, uid: value.pbi_uid,
                                        seconds: value.pbi_start_tvsec, microseconds: value.pbi_start_tvusec)
    }

    func executable(_ pid: Int32) throws -> String? {
        var path = [CChar](repeating: 0, count: 4096 /* PROC_PIDPATHINFO_MAXSIZE */)
        errno = 0
        let count = unsafe path.withUnsafeMutableBytes { unsafe proc_pidpath(pid, $0.baseAddress, UInt32($0.count)) }
        let code = errno
        guard count > 0 else {
            if code == ESRCH { return nil }
            throw scanError("pid-path", pid: pid, count: count, code: code)
        }
        return String(decoding: path.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    func generation(_ pid: Int32) throws -> WindowManagerExecGeneration {
        var value = AquaSpaceProcUniqueInfo()
        let count = unsafe proc_pidinfo(pid, 17 /* PROC_PIDUNIQIDENTIFIERINFO */, 0, &value, 56)
        guard count == 56 else { throw scanError("exec-generation", pid: pid, count: count, code: errno) }
        let uuid = withUnsafeBytes(of: value.uuid) { unsafe Array($0) }
        return WindowManagerExecGeneration(uuid: uuid, uniqueId: value.uniqueid, version: value.idversion)
    }

    func metadata(_ executable: String) throws -> WindowManagerBundleMetadata {
        let url = URL(filePath: executable)
        guard url.deletingLastPathComponent().lastPathComponent == "MacOS",
              url.deletingLastPathComponent().deletingLastPathComponent().lastPathComponent == "Contents"
        else { return WindowManagerBundleMetadata() }
        let plist = url.deletingLastPathComponent().deletingLastPathComponent().appending(path: "Info.plist")
        guard let metadata = try unsafe PropertyListSerialization.propertyList(from: Data(contentsOf: plist), format: nil) as? [String: Any] else {
            throw scanError("bundle-plist")
        }
        return WindowManagerBundleMetadata(identifier: metadata["CFBundleIdentifier"] as? String,
                                           executable: metadata["CFBundleExecutable"] as? String)
    }
}
