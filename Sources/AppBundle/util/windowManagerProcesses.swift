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

struct WindowManagerSignedImage: Equatable {
    var flags: UInt32
    var category: UInt32
    var identifier: String
    var team: String
    var cdhash: [UInt8]

    var isKnownNonManager: Bool {
        let required: UInt32 = 0x20011101 // SIGNED, RUNTIME, ENFORCEMENT, HARD, VALID
        let forbidden: UInt32 = 0x11000026 // DEBUGGED, KILLED, INVALID_ALLOWED, GET_TASK_ALLOW, ADHOC
        return flags & required == required && flags & forbidden == 0 && flags & 0x2010 != 0 &&
            category == 6 && // Kernel-validated Developer ID; a team string alone is insufficient.
            team == "UBF8T346G9" &&
            ["com.microsoft.Word.widgetextension", "com.microsoft.Excel.widgetextension"].contains(identifier) &&
            cdhash.count == 20 && cdhash.contains { $0 != 0 }
    }
}

enum WindowManagerMissingExecutable {
    static func resolve(code: Int32, identity: () throws -> WindowManagerProcessBirth?,
                        generation: () throws -> WindowManagerExecGeneration,
                        signature: (WindowManagerExecGeneration) throws -> WindowManagerSignedImage,
                        userId: UInt32) throws -> Bool {
        guard code == ENOENT else { return false }
        guard let before = try identity() else { return true } // Kernel-confirmed exit/zombie.
        let firstGeneration = try generation()
        guard before.uid == userId, firstGeneration.uniqueId != 0, firstGeneration.version > 0,
              firstGeneration.uuid.count == 16, firstGeneration.uuid.contains(where: { $0 != 0 })
        else { throw WindowManagerAdmissionError.uncertainInventory }
        let first = try signature(firstGeneration)
        guard first.isKnownNonManager else { throw WindowManagerAdmissionError.uncertainInventory }
        let second = try signature(firstGeneration)
        guard let after = try identity(), before == after, try generation() == firstGeneration, first == second
        else { throw WindowManagerAdmissionError.uncertainInventory }
        return true
    }
}

protocol WindowManagerProcessAPI {
    var userId: UInt32 { get }
    var ownPid: Int32 { get }
    func pids() throws -> [Int32]
    func executable(_ pid: Int32) throws -> String?
    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth?
    func metadata(_ executable: String) throws -> WindowManagerBundleMetadata
}

/// Classify kernel executable paths before requesting BSD identity. macOS can
/// deny BSD records for unrelated helpers; those records are irrelevant to control.
enum WindowManagerProcesses {
    static func peers() throws -> [WindowManagerProcess] { try peers(using: NativeWindowManagerProcessAPI()) }

    static func peers(using api: any WindowManagerProcessAPI) throws -> [WindowManagerProcess] {
        var result: [WindowManagerProcess] = []
        for pid in Set(try api.pids()) where pid > 0 && pid != api.ownPid {
            guard let path = try api.executable(pid) else { continue }
            let metadata = try api.metadata(path)
            guard WindowManagerProcess.isManager(executable: path, bundleId: metadata.identifier,
                                                  bundleExecutable: metadata.executable)
            else { continue }
            guard let before = try api.identity(pid), before.uid == api.userId else { continue }
            guard let verifiedPath = try api.executable(pid), let after = try api.identity(pid) else { continue }
            guard before == after, path == verifiedPath else { throw WindowManagerAdmissionError.uncertainInventory }
            let identifier = metadata.executable == URL(filePath: path).lastPathComponent ? metadata.identifier : nil
            result.append(WindowManagerProcess(pid: pid, startedSeconds: after.seconds,
                startedMicroseconds: after.microseconds, executable: path, bundleId: identifier))
        }
        return result
    }
}

private struct NativeWindowManagerProcessAPI: WindowManagerProcessAPI {
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
            if try WindowManagerMissingExecutable.resolve(code: code, identity: { try identity(pid) },
                generation: { try generation(pid) }, signature: { try signature(pid, generation: $0) }, userId: userId) {
                return nil // Positively identified immutable nonmanager, not an unexamined process.
            }
            throw scanError("pid-path", pid: pid, count: count, code: code)
        }
        return String(decoding: path.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    private func generation(_ pid: Int32) throws -> WindowManagerExecGeneration {
        var value = AquaSpaceProcUniqueInfo()
        let count = unsafe proc_pidinfo(pid, 17 /* PROC_PIDUNIQIDENTIFIERINFO */, 0, &value, 56)
        guard count == 56 else { throw scanError("exec-generation", pid: pid, count: count, code: errno) }
        let uuid = withUnsafeBytes(of: value.uuid) { unsafe Array($0) }
        return WindowManagerExecGeneration(uuid: uuid, uniqueId: value.uniqueid, version: value.idversion)
    }

    private func signature(_ pid: Int32, generation: WindowManagerExecGeneration) throws -> WindowManagerSignedImage {
        // csops_audittoken verifies PID and exec version for every signing read.
        var token: [UInt32] = [0, 0, 0, 0, 0, UInt32(pid), 0, UInt32(generation.version)]
        func read(_ operation: UInt32, _ size: Int) throws -> [UInt8] {
            var data = [UInt8](repeating: 0, count: size)
            let result = unsafe token.withUnsafeMutableBytes { tokenBytes in
                unsafe data.withUnsafeMutableBytes { bytes in
                    unsafe csops_audittoken(pid, operation, bytes.baseAddress, size, tokenBytes.baseAddress)
                }
            }
            guard result == 0 else { throw scanError("running-signature", pid: pid, code: errno) }
            return data
        }
        func number(_ operation: UInt32) throws -> UInt32 {
            let bytes = try read(operation, 4)
            return bytes.enumerated().reduce(UInt32(0)) { $0 | UInt32($1.element) << (8 * $1.offset) }
        }
        func string(_ operation: UInt32) throws -> String {
            let data = try read(operation, 1024)
            let length = data[4 ..< 8].reduce(0) { ($0 << 8) | Int($1) }
            guard data.prefix(4).allSatisfy({ $0 == 0 }), (10 ... data.count).contains(length),
                  data[length - 1] == 0, data[8 ..< length - 1].allSatisfy({ $0 > 0 && $0 < 128 })
            else { throw scanError("signature-encoding", pid: pid) }
            return String(decoding: data[8 ..< length - 1], as: UTF8.self)
        }
        return try WindowManagerSignedImage(flags: number(0), category: number(17),
            identifier: string(11), team: string(14), cdhash: read(5, 20))
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
