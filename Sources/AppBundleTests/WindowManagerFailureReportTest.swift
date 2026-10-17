@testable import AppBundle
import Foundation
import Testing

struct WindowManagerFailureReportTest {
    private func report(_ error: any Error, phase: WindowManagerFailureReport.Phase = .startup) -> WindowManagerFailureReport {
        WindowManagerFailureReport(error: error, phase: phase, version: "fixture-version",
            executable: "/fixture/AquaSpace", pid: 123, date: Date(timeIntervalSince1970: 0))
    }

    @Test func everyRefusalSurvivesInAnOwnerPrivatePlainTextFile() throws {
        for error in [WindowManagerAdmissionError.uncertainInventory, .competingManager, .lockUnavailable] {
            let directory = FileManager.default.temporaryDirectory.appending(path: "aqua-report-\(UUID())")
            defer { try? FileManager.default.removeItem(at: directory) }
            var console: [String] = [], stderr = Data()
            report(error).publish(to: directory, publicLog: { console.append($0) }, standardError: { stderr.append($0) })
            let file = directory.appending(path: "last-failure.txt")
            let saved = try String(contentsOf: file, encoding: .utf8)
            #expect(saved.contains(error.localizedDescription))
            #expect(saved.contains("fixture-version") && saved.contains("/fixture/AquaSpace") && saved.contains("PID: 123"))
            #expect(saved.contains("1970-01-01T00:00:00Z"))
            #expect(console.count == 1 && console[0].contains(file.path))
            #expect(String(decoding: stderr, as: UTF8.self).contains(error.localizedDescription))
            #expect((try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber)?.intValue == 0o600)
            #expect((try FileManager.default.attributesOfItem(atPath: directory.path)[.posixPermissions] as? NSNumber)?.intValue == 0o700)
        }
    }

    @Test func persistenceFailureStillReportsTheOriginalCauseAndWriteFailure() throws {
        let blocked = FileManager.default.temporaryDirectory.appending(path: "aqua-report-blocked-\(UUID())")
        try Data("fixture".utf8).write(to: blocked)
        defer { try? FileManager.default.removeItem(at: blocked) }
        var console = "", stderr = Data()
        report(WindowManagerAdmissionError.lockUnavailable).publish(to: blocked,
            publicLog: { console = $0 }, standardError: { stderr = $0 })
        #expect(console.contains("controller lock"))
        #expect(console.contains("Could not save failure report"))
        #expect(String(decoding: stderr, as: UTF8.self) == console + "\n")
    }

    @Test func nestedErrorsRetainPidErrnoAndStageAndLatestFailureReplacesOldText() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: "aqua-report-nested-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let underlying = NSError(domain: "Fixture.Signature", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Signature proof unavailable"])
        let error = NSError(domain: "AquaSpace.ProcessInventory", code: 2,
            userInfo: [NSLocalizedDescriptionKey: "pid-path-proof, pid 456, errno 2", NSUnderlyingErrorKey: underlying])
        report(WindowManagerAdmissionError.competingManager).publish(to: directory, publicLog: { _ in }, standardError: { _ in })
        report(error).publish(to: directory, publicLog: { _ in }, standardError: { _ in })
        let saved = try String(contentsOf: directory.appending(path: "last-failure.txt"), encoding: .utf8)
        #expect(saved.contains("pid-path-proof, pid 456, errno 2"))
        #expect(saved.contains("Signature proof unavailable [Fixture.Signature, code 1]"))
        #expect(!saved.contains("Another window manager is running"))
    }

    @Test func phaseAndErrorChainAreBoundedWithoutReadingAnyProcess() {
        var error = NSError(domain: "Fixture", code: 99)
        for index in 0 ..< 20 {
            error = NSError(domain: "Fixture", code: index,
                userInfo: [NSLocalizedDescriptionKey: "layer", NSUnderlyingErrorKey: error])
        }
        for phase in [WindowManagerFailureReport.Phase.startup, .monitoring, .handoff] {
            let text = report(error, phase: phase).text
            #expect(text.contains("complete \(phase.rawValue)"))
            #expect(text.components(separatedBy: "Caused by:").count == 8)
        }
    }
}
