import Foundation
import Darwin
import PrivateApi

/// Public libproc APIs: argv, app location and LaunchServices registration are
/// irrelevant. Failure to inspect a live same-user process fails closed.
enum WindowManagerProcesses {
    static func peers() throws -> [WindowManagerProcess] {
        let uid = getuid()
        let needed = proc_listpids(UInt32(PROC_UID_ONLY), uid, nil, 0)
        guard needed > 0 else { throw WindowManagerAdmissionError.uncertainInventory }
        var capacity = Int(needed) / MemoryLayout<Int32>.size + 64
        for _ in 0 ..< 3 {
            var pids = [Int32](repeating: 0, count: capacity)
            let bytes = pids.count * MemoryLayout<Int32>.size
            let count = unsafe pids.withUnsafeMutableBytes {
                unsafe proc_listpids(UInt32(PROC_UID_ONLY), uid, $0.baseAddress, Int32(bytes))
            }
            guard count > 0, count % Int32(MemoryLayout<Int32>.size) == 0 else {
                throw WindowManagerAdmissionError.uncertainInventory
            }
            if count >= bytes { capacity *= 2; continue }
            var result: [WindowManagerProcess] = []
            for pid in Set(pids.prefix(Int(count) / MemoryLayout<Int32>.size)) where pid > 0 && pid != getpid() {
                if let process = try inspect(pid), process.uid == uid,
                   WindowManagerProcess.isManager(executable: process.value.executable,
                                                  bundleId: process.value.bundleId,
                                                  bundleExecutable: process.bundleExecutable)
                { result.append(process.value) }
            }
            return result
        }
        throw WindowManagerAdmissionError.uncertainInventory
    }

    private static func info(_ pid: Int32) throws -> proc_bsdinfo? {
        var value = proc_bsdinfo()
        errno = 0
        let count = unsafe proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &value, Int32(MemoryLayout<proc_bsdinfo>.size))
        guard count == MemoryLayout<proc_bsdinfo>.size else {
            if errno == ESRCH { return nil }
            throw WindowManagerAdmissionError.uncertainInventory
        }
        if value.pbi_status == 5 { return nil } // SZOMB cannot control windows.
        guard value.pbi_pid == pid else { throw WindowManagerAdmissionError.uncertainInventory }
        return value
    }

    private static func inspect(_ pid: Int32) throws -> (value: WindowManagerProcess, uid: UInt32, bundleExecutable: String?)? {
        guard let before = try info(pid) else { return nil }
        var path = [CChar](repeating: 0, count: 4096 /* PROC_PIDPATHINFO_MAXSIZE */)
        errno = 0
        let count = unsafe path.withUnsafeMutableBytes { unsafe proc_pidpath(pid, $0.baseAddress, UInt32($0.count)) }
        guard count > 0 else {
            if errno == ESRCH { return nil }
            throw WindowManagerAdmissionError.uncertainInventory
        }
        guard let after = try info(pid) else { return nil }
        guard before.pbi_start_tvsec == after.pbi_start_tvsec,
              before.pbi_start_tvusec == after.pbi_start_tvusec, before.pbi_uid == after.pbi_uid else {
            throw WindowManagerAdmissionError.uncertainInventory
        }
        let executable = String(decoding: path.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
        let url = URL(filePath: executable)
        var identifier: String?
        var bundleExecutable: String?
        if url.deletingLastPathComponent().lastPathComponent == "MacOS",
           url.deletingLastPathComponent().deletingLastPathComponent().lastPathComponent == "Contents" {
            let plist = url.deletingLastPathComponent().deletingLastPathComponent().appending(path: "Info.plist")
            guard let metadata = try unsafe PropertyListSerialization.propertyList(from: Data(contentsOf: plist), format: nil) as? [String: Any] else {
                throw WindowManagerAdmissionError.uncertainInventory
            }
            bundleExecutable = metadata["CFBundleExecutable"] as? String
            if bundleExecutable == url.lastPathComponent { identifier = metadata["CFBundleIdentifier"] as? String }
        }
        return (WindowManagerProcess(pid: pid, startedSeconds: after.pbi_start_tvsec,
                    startedMicroseconds: after.pbi_start_tvusec, executable: executable, bundleId: identifier),
                after.pbi_uid, bundleExecutable)
    }
}
