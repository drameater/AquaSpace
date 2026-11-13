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
    var metadataReads: [String: Int] = [:]
    var generations: [Int32: WindowManagerExecGeneration] = [:]
    var deniedGenerations: Set<Int32> = []

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
        metadataReads[path, default: 0] += 1
        if deniedMetadata.contains(path) { throw NSError(domain: NSPOSIXErrorDomain, code: 1) }
        return bundles[path] ?? WindowManagerBundleMetadata()
    }
    func generation(_ pid: Int32) throws -> WindowManagerExecGeneration {
        guard paths[pid] != nil, !deniedGenerations.contains(pid) else { throw NSError(domain: NSPOSIXErrorDomain, code: 3) }
        return generations[pid] ?? WindowManagerExecGeneration(uuid: [1], uniqueId: UInt64(pid) + 1000, version: 1)
    }

    /// Kernel exec: same PID and unique ID, a new exec version and image.
    func exec(_ pid: Int32, _ path: String) {
        let old = try! generation(pid)
        paths[pid] = path
        generations[pid] = WindowManagerExecGeneration(uuid: [2], uniqueId: old.uniqueId, version: old.version + 1)
    }

    /// PID reuse: a different process, so a different unique ID.
    func reuse(_ pid: Int32, _ path: String) {
        let old = try! generation(pid)
        paths[pid] = path
        generations[pid] = WindowManagerExecGeneration(uuid: [3], uniqueId: old.uniqueId + 1, version: 1)
    }

    var totalMetadataReads: Int { metadataReads.values.reduce(0, +) }
    var totalPathReads: Int { pathReads.values.reduce(0, +) }
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

    // Regression: one app replaced on disk while running (path read fails with
    // ENOENT) or with an unreadable Info.plist made AquaSpace refuse or stop control.
    @Test func unreadableUnrelatedProcessesAreSkippedAndManagersAreStillFound() throws {
        let api = ProcessAPIStub()
        let unrelatedBundle = "/Applications/Updated.app/Contents/MacOS/Updated"
        api.paths = [10: "/usr/libexec/replaced-helper", 11: unrelatedBundle, 20: "/Applications/AeroSpace.app/Contents/MacOS/AeroSpace"]
        api.deniedPaths = [10]
        api.deniedMetadata = [unrelatedBundle, api.paths[20]!]
        let peers = try WindowManagerProcesses.peers(using: api)
        #expect(peers.map(\.pid) == [20]) // Executable name still identifies the manager.
        #expect(peers.first?.bundleId == nil)
        #expect(api.identityReads == [20, 20]) // Before/after reads for the candidate only.
    }

    @Test func exitedCandidateIsAbsentAndNeverInvented() throws {
        let api = ProcessAPIStub()
        api.paths[10] = "/tmp/AeroSpaceApp"
        api.missingIdentity = [10]
        #expect(try WindowManagerProcesses.peers(using: api).isEmpty)
    }
}

/// The periodic watch reuses verdicts only for an unchanged kernel exec generation.
struct WindowManagerProcessCacheTest {
    private func desktop() -> ProcessAPIStub {
        let api = ProcessAPIStub()
        api.paths = [10: "/Applications/Safari.app/Contents/MacOS/Safari", 11: "/usr/libexec/helper",
                     12: "/Applications/Ghostty.app/Contents/MacOS/ghostty"]
        api.bundles[api.paths[10]!] = WindowManagerBundleMetadata(identifier: "com.apple.Safari", executable: "Safari")
        api.bundles[api.paths[12]!] = WindowManagerBundleMetadata(identifier: "com.mitchellh.ghostty", executable: "ghostty")
        return api
    }

    @Test func unchangedProcessesNeedNoPathOrMetadataReads() throws {
        let api = desktop()
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        let metadata = api.totalMetadataReads, paths = api.totalPathReads
        #expect(metadata == 3 && paths == 3) // One classification per process.
        for _ in 0 ..< 10 { #expect(try cache.peers().isEmpty) }
        #expect(api.totalMetadataReads == metadata)
        #expect(api.totalPathReads == paths)
        #expect(api.identityReads.isEmpty)
    }

    @Test func cachedScanFindsTheSamePeersAsTheAuthoritativeScan() throws {
        let api = ProcessAPIStub()
        for (index, bundle) in WindowManagerProcess.bundleIds.sorted().enumerated() {
            let path = "/other directory/\(index).app/Contents/MacOS/RenamedManager"
            api.paths[Int32(index + 10)] = path
            api.bundles[path] = WindowManagerBundleMetadata(identifier: bundle, executable: "RenamedManager")
        }
        api.paths[20] = "/tmp/build/AeroSpaceApp"
        api.paths[21] = "/Applications/Ghostty.app/Contents/MacOS/ghostty"
        let cache = WindowManagerProcessCache(api: api)
        let expected = Set(try WindowManagerProcesses.peers(using: api))
        #expect(expected.count == 5)
        #expect(Set(try cache.peers()) == expected)
        // Managers are never cached: each scan verifies them again.
        #expect(Set(try cache.peers()) == expected)
    }

    @Test func pidReuseIsReclassified() throws {
        let api = desktop()
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        api.reuse(11, "/tmp/build/AeroSpaceApp")
        #expect(try cache.peers().map(\.pid) == [11])
    }

    @Test func execIntoAManagerIsFoundAlthoughStartTimeIsUnchanged() throws {
        let api = desktop()
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        // Identity (start time) stays the same in the stub, like a real exec.
        api.exec(11, "/Users/me/AquaSpace/.debug/AquaSpaceApp")
        #expect(try cache.peers().map(\.pid) == [11])
        // A bundled manager behind a renamed executable is found through its new bundle.
        let renamed = "/Applications/Renamed.app/Contents/MacOS/Renamed"
        api.bundles[renamed] = WindowManagerBundleMetadata(identifier: "bobko.aerospace", executable: "Renamed")
        api.exec(12, renamed)
        #expect(Set(try cache.peers().map(\.pid)) == [11, 12])
    }

    @Test func newPeerIsFoundOnTheNextScanAndOnlyItIsRead() throws {
        let api = desktop()
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        let metadata = api.totalMetadataReads, paths = api.totalPathReads
        api.paths[30] = "/tmp/build/AeroSpaceApp"
        let peers = try cache.peers()
        #expect(peers.map(\.pid) == [30])
        #expect(peers.first?.bundleId == nil)
        #expect(api.totalMetadataReads == metadata + 1)
        #expect(api.totalPathReads == paths + 2) // Classification plus verification of the candidate.
        #expect(api.identityReads == [30, 30])
    }

    @Test func vanishedProcessesAreSkippedAndForgotten() throws {
        let api = desktop()
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        let safari = api.paths.removeValue(forKey: 10)!
        #expect(try cache.peers().isEmpty)
        // The entry was pruned: the same PID and generation is classified from scratch.
        let metadata = api.metadataReads[safari]
        api.paths[10] = safari
        #expect(try cache.peers().isEmpty)
        #expect(api.metadataReads[safari] == metadata.map { $0 + 1 })
    }

    @Test func processExitingDuringClassificationIsSkippedAndNotCached() throws {
        let api = desktop()
        api.deniedPaths = [11]
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        api.deniedPaths = []
        api.paths[11] = "/tmp/build/AeroSpaceApp" // Unreadable, so never cached as unrelated.
        #expect(try cache.peers().map(\.pid) == [11])
    }

    @Test func unreadableBundleIsRetriedRarelyNotEveryScan() throws {
        let api = desktop()
        let renamed = "/Applications/Renamed.app/Contents/MacOS/Renamed"
        api.paths[13] = renamed
        api.deniedMetadata = [renamed]
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        for _ in 0 ..< WindowManagerProcessCache.metadataRetryScans - 1 { #expect(try cache.peers().isEmpty) }
        #expect(api.metadataReads[renamed] == 1)
        // The bundle becomes readable and is a manager: the bounded retry finds it.
        api.deniedMetadata = []
        api.bundles[renamed] = WindowManagerBundleMetadata(identifier: "bobko.aerospace", executable: "Renamed")
        var found: [WindowManagerProcess] = []
        for _ in 0 ..< 2 where found.isEmpty { found = try cache.peers() }
        #expect(found.map(\.pid) == [13])
        #expect(api.metadataReads[renamed] == 2)
    }

    @Test func unreadableGenerationIsClassifiedOnEveryScan() throws {
        let api = desktop()
        api.deniedGenerations = [11]
        let cache = WindowManagerProcessCache(api: api)
        #expect(try cache.peers().isEmpty)
        #expect(try cache.peers().isEmpty)
        #expect(api.pathReads[11] == 2)
        #expect(api.pathReads[10] == 1)
        api.paths[11] = "/tmp/build/AeroSpaceApp" // A path change with no readable generation.
        #expect(try cache.peers().map(\.pid) == [11])
    }

    @Test func changedIdentityDuringVerificationStillFailsClosed() {
        for changePath in [true, false] {
            let api = ProcessAPIStub()
            api.paths[10] = "/tmp/AeroSpaceApp"
            if changePath { api.changedExecutable = 10 } else { api.reusedPid = 10 }
            #expect(throws: (any Error).self) { try WindowManagerProcessCache(api: api).peers() }
        }
    }
}
