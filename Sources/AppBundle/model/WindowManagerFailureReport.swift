import Foundation
import os

/// Reporting has no control, process, UI or retry capabilities. A failed manager
/// still exits; the reason remains readable after its menu disappears.
struct WindowManagerFailureReport {
    enum Phase: String { case startup, monitoring, handoff }
    static var defaultDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/AquaSpace")
    }
    private static let logger = Logger(subsystem: "local.aquaspace", category: "Lifecycle")
    let text: String

    init(error: any Error, phase: Phase, version: String, executable: String, pid: Int32, date: Date = Date()) {
        var reasons: [String] = []
        var current: NSError? = error as NSError
        var seen: Set<ObjectIdentifier> = []
        while let value = current, reasons.count < 8, seen.insert(ObjectIdentifier(value)).inserted {
            reasons.append("\(value.localizedDescription) [\(value.domain), code \(value.code)]")
            current = value.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        text = """
        AquaSpace could not complete \(phase.rawValue).
        Time: \(date.ISO8601Format())
        Version: \(version)
        Executable: \(executable)
        PID: \(pid)
        Reason: \(reasons.joined(separator: "\nCaused by: "))

        AquaSpace has refused window control. No replacement manager is started by this report.
        Resolve the reported cause before trying again. Resetting Accessibility permission does not resolve process-inventory or controller-lock failures.
        """
    }

    /// Injected sinks keep offline tests away from Console and standard streams.
    /// A persistence failure must never swallow the original refusal reason.
    func publish(
        to directory: URL = Self.defaultDirectory,
        publicLog: (String) -> Void = { logger.error("\($0, privacy: .public)") },
        standardError: (Data) -> Void = { FileHandle.standardError.write($0) },
    ) {
        var message = text
        do {
            let manager = FileManager.default
            try manager.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            let file = directory.appending(path: "last-failure.txt")
            try Data((text + "\n").utf8).write(to: file, options: .atomic)
            try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            message += "\nReport: \(file.path)"
        } catch {
            message += "\nCould not save failure report: \(error.localizedDescription)"
        }
        publicLog(message)
        standardError(Data((message + "\n").utf8))
    }
}
