@testable import AppBundle
import Foundation
import Testing

private final class ProcessAPIStub: WindowManagerProcessAPI {
    var userId: UInt32 { 501 }
    var ownPid: Int32 { 7 }
    var paths: [Int32: String] = [:]
    var bundles: [String: WindowManagerBundleMetadata] = [:]
    var forbiddenIdentity: Set<Int32> = []
    var identityReads: [Int32] = []
    var missingIdentity: Set<Int32> = []
    var reusedPid: Int32?
    var pathReads: [Int32: Int] = [:]
    var changedExecutable: Int32?
    var deniedPaths: Set<Int32> = []
    var deniedMetadata: Set<String> = []

    func pids() throws -> [Int32] { Array(paths.keys) + [ownPid, 0] }
    func executable(_ pid: Int32) throws -> String? {
        if deniedPaths.contains(pid) { throw NSError(domain: NSPOSIXErrorDomain, code: 1) }
        pathReads[pid, default: 0] += 1
        if changedExecutable == pid && pathReads[pid, default: 0] > 1 { return "/a/replacement/AeroSpaceApp" }
        return paths[pid]
    }
    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth? {
        identityReads.append(pid)
        if forbiddenIdentity.contains(pid) { throw NSError(domain: NSPOSIXErrorDomain, code: 1) }
        if missingIdentity.contains(pid) { return nil }
        return WindowManagerProcessBirth(pid: pid, uid: userId,
            seconds: reusedPid == pid && identityReads.filter { $0 == pid }.count > 1 ? 2 : 1, microseconds: 0)
    }
    func metadata(_ path: String) throws -> WindowManagerBundleMetadata {
        if deniedMetadata.contains(path) { throw NSError(domain: NSPOSIXErrorDomain, code: 1) }
        return bundles[path] ?? WindowManagerBundleMetadata()
    }
}

struct WindowManagerProcessScanTest {
    @Test func unrelatedProtectedProcessesAndEmbeddedClisNeedNoBSDRecords() throws {
        let api = ProcessAPIStub()
        api.paths = [1: "/usr/libexec/protected-helper", 2: "/Applications/AquaSpace.app/Contents/MacOS/aquaspace-cli"]
        api.bundles[api.paths[2]!] = WindowManagerBundleMetadata(identifier: "local.aquaspace", executable: "AquaSpace")
        api.forbiddenIdentity = [1, 2]
        #expect(try WindowManagerProcesses.peers(using: api).isEmpty)
        #expect(api.identityReads.isEmpty)
    }

    @Test func unreadableManagerCandidatesStillFailClosed() {
        let api = ProcessAPIStub()
        api.paths = [10: "/tmp/relocated/AeroSpaceApp"]
        api.forbiddenIdentity = [10]
        #expect(throws: (any Error).self) { try WindowManagerProcesses.peers(using: api) }
        #expect(api.identityReads == [10])
    }

    @Test func scanRecognizesAllBundleIdentitiesAndUnbundledManagers() throws {
        let api = ProcessAPIStub()
        for (index, bundle) in WindowManagerProcess.bundleIds.sorted().enumerated() {
            let path = "/other directory/\(index).app/Contents/MacOS/RenamedManager"
            api.paths[Int32(index + 10)] = path
            api.bundles[path] = WindowManagerBundleMetadata(identifier: bundle, executable: "RenamedManager")
        }
        api.paths[20] = "/tmp/build/AeroSpaceApp"
        let peers = try WindowManagerProcesses.peers(using: api)
        #expect(Set(peers.compactMap(\.bundleId)) == WindowManagerProcess.bundleIds)
        #expect(peers.count == 5)
        #expect(api.identityReads.count == 10)
        #expect(!api.identityReads.contains(api.ownPid))
    }

    @Test func reusedPidOrChangedExecutableCannotPassCandidateVerification() {
        for changePath in [true, false] {
            let api = ProcessAPIStub()
            api.paths[10] = "/tmp/AeroSpaceApp"
            if changePath { api.changedExecutable = 10 } else { api.reusedPid = 10 }
            #expect(throws: (any Error).self) { try WindowManagerProcesses.peers(using: api) }
        }
    }

    @Test func unreadableExecutableOrBundleCannotBeTreatedAsAbsent() {
        for failPath in [true, false] {
            let api = ProcessAPIStub()
            let path = "/tmp/Fork.app/Contents/MacOS/RenamedManager"
            api.paths[10] = path
            if failPath { api.deniedPaths = [10] } else { api.deniedMetadata = [path] }
            #expect(throws: (any Error).self) { try WindowManagerProcesses.peers(using: api) }
            #expect(api.identityReads.isEmpty)
        }
    }

    @Test func exitedCandidateIsAbsentAndNeverInvented() throws {
        let api = ProcessAPIStub()
        api.paths[10] = "/tmp/AeroSpaceApp"
        api.missingIdentity = [10]
        #expect(try WindowManagerProcesses.peers(using: api).isEmpty)
    }
}

private final class MissingExecutableStub {
    var birthReads = 0
    var generationReads = 0
    var signatureReads = 0
    var changeBirth = false
    var changeGeneration = false
    var changeHash = false
    var failGeneration = false
    var failSignature = false
    var image = WindowManagerSignedImage(flags: 0x22011111, category: 6,
        identifier: "com.microsoft.Word.widgetextension", team: "UBF8T346G9", cdhash: [UInt8](repeating: 42, count: 20))
    var code: Int32 = ENOENT

    func verify() throws -> Bool {
        try WindowManagerMissingExecutable.resolve(code: code, identity: {
            self.birthReads += 1
            return WindowManagerProcessBirth(pid: 1234, uid: 501,
                seconds: self.changeBirth && self.birthReads > 1 ? 2 : 1, microseconds: 0)
        }, generation: {
            self.generationReads += 1
            if self.failGeneration { throw WindowManagerAdmissionError.uncertainInventory }
            return WindowManagerExecGeneration(uuid: [UInt8](repeating: 1, count: 16), uniqueId: 42,
                version: self.changeGeneration && self.generationReads > 1 ? 8 : 7)
        }, signature: { generation in
            #expect(generation.version == 7)
            self.signatureReads += 1
            if self.failSignature { throw WindowManagerAdmissionError.uncertainInventory }
            var value = self.image
            if self.changeHash && self.signatureReads > 1 { value.cdhash[0] = 99 }
            return value
        }, userId: 501)
    }
}

struct WindowManagerMissingExecutableTest {
    @Test func deletedVendorWidgetsRequireFullKernelProof() throws {
        for identifier in ["com.microsoft.Word.widgetextension", "com.microsoft.Excel.widgetextension"] {
            let api = MissingExecutableStub()
            api.image.identifier = identifier
            #expect(try api.verify())
            #expect(api.birthReads == 2 && api.generationReads == 2 && api.signatureReads == 2)
        }
    }

    @Test func permissionErrorsCannotUseTheMissingPathFallback() throws {
        let api = MissingExecutableStub()
        api.code = EPERM
        #expect(try !api.verify())
        #expect(api.birthReads == 0 && api.signatureReads == 0)
    }

    @Test func unknownAndManagerIdentitiesStillBlock() {
        for identifier in ["unknown.widget", "com.microsoft.Word", "bobko.aerospace", "local.aquaspace"] {
            let api = MissingExecutableStub()
            api.image.identifier = identifier
            #expect(throws: (any Error).self) { try api.verify() }
        }
    }

    @Test func kernelTrustAndRuntimeRestrictionsCannotBeSpoofedByMatchingStrings() {
        for category: UInt32 in [0, 1, 3, 4, 5, 7, 10] {
            let api = MissingExecutableStub()
            api.image.category = category
            #expect(throws: (any Error).self) { try api.verify() }
        }
        for flags: UInt32 in [0, 0x22011113, 0x22011115, 0x32011111, 0x22011110, 0x22001111, 0x22011101] {
            let api = MissingExecutableStub()
            api.image.flags = flags
            #expect(throws: (any Error).self) { try api.verify() }
        }
    }

    @Test func changedProcessExecOrCodeHashCannotPass() {
        for change in 0 ..< 3 {
            let api = MissingExecutableStub()
            api.changeBirth = change == 0
            api.changeGeneration = change == 1
            api.changeHash = change == 2
            #expect(throws: (any Error).self) { try api.verify() }
        }
    }

    @Test func missingProofWrongTeamOrZeroHashCannotPass() {
        for failure in 0 ..< 4 {
            let api = MissingExecutableStub()
            api.failGeneration = failure == 0
            api.failSignature = failure == 1
            if failure == 2 { api.image.team = "OTHERTEAM" }
            if failure == 3 { api.image.cdhash = [UInt8](repeating: 0, count: 20) }
            #expect(throws: (any Error).self) { try api.verify() }
        }
    }
}
