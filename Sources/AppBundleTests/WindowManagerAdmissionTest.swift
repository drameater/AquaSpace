@testable import AppBundle
import Foundation
import Testing

struct WindowManagerAdmissionTest {
    @Test func modelPreparationNeverGrantsNativeWritesAndFreezeRevokesBoth() {
        var permission = WindowControlPermission()
        #expect(permission.permitsInitialization)
        #expect(!permission.permitsWrites)
        let initial = permission.generation
        permission.freeze()
        #expect(!permission.permitsInitialization)
        #expect(!permission.permitsWrites)
        #expect(permission.generation != initial)
        let frozen = permission.generation
        permission.freeze()
        #expect(permission.generation != frozen)
    }

    @Test func peerFreezeCannotLookLikeInitialPreparation() throws {
        try withLock { lock, _ in
            var monitor = false
            var permission = WindowControlPermission()
            let admission = WindowManagerAdmission(lock: lock, inventory: {
                monitor ? [peer("bobko.aerospace")] : []
            })
            _ = try admission.prepare(build: .release, readOnly: false)
            try admission.claim(); permission.claim()
            let active = permission.generation
            monitor = true
            _ = try? admission.observedPeers { permission.freeze() }
            #expect(admission.ownership == .controlling)
            #expect(!permission.permitsWrites && !permission.permitsInitialization)
            #expect(permission.generation != active)
        }
    }

    private func peer(_ bundle: String?, name: String = "AeroSpace", pid: Int32 = 42) -> WindowManagerProcess {
        WindowManagerProcess(pid: pid, startedSeconds: 1, startedMicroseconds: 2,
                             executable: "/some other directory/\(name)", bundleId: bundle)
    }

    private func withLock(_ body: (WindowManagerControllerLock, String) throws -> Void) throws {
        let path = FileManager.default.temporaryDirectory.appending(path: "aquaspace-offline-\(UUID()).lock").path
        defer { try? FileManager.default.removeItem(atPath: path) }
        try body(WindowManagerControllerLock(path: path), path)
    }

    @Test func everyBuildMustOwnTheLockAndRecheckPeersBeforeWriting() throws {
        for build in [WindowManagerAdmission.Build.debug, .release] {
            try withLock { lock, _ in
                var peers: [WindowManagerProcess] = []
                let admission = WindowManagerAdmission(lock: lock, inventory: { peers })
                #expect(throws: (any Error).self) { try admission.claim() }
                #expect(admission.ownership == .preparing)
                #expect(try admission.prepare(build: build, readOnly: false).isEmpty)
                peers = [peer("bobko.aerospace")]
                #expect(throws: (any Error).self) { try admission.claim() }
                #expect(admission.ownership == .preparing)
                peers = []
                try admission.claim()
                #expect(admission.ownership == .controlling)
                admission.yield()
                #expect(throws: (any Error).self) { try admission.claim() }
                #expect(admission.ownership == .yielding)
            }
        }
    }

    @Test func debugRefusesToPauseOrTakeOverStock() throws {
        try withLock { lock, _ in
            let admission = WindowManagerAdmission(lock: lock, inventory: { [peer("bobko.aerospace")] })
            #expect(throws: (any Error).self) { try admission.prepare(build: .debug, readOnly: false) }
            #expect(admission.ownership == .preparing)
        }
    }

    @Test func releaseCanPrepareStockTakeoverButCannotClaimUntilStockExits() throws {
        try withLock { lock, _ in
            var peers = [peer("bobko.aerospace")]
            let admission = WindowManagerAdmission(lock: lock, inventory: { peers })
            #expect(try admission.prepare(build: .release, readOnly: false) == peers)
            #expect(throws: (any Error).self) { try admission.claim() }
            peers = []
            try admission.claim()
            #expect(admission.ownership == .controlling)
        }
    }

    @Test func bothBuildsRefuseDebugRelocatedAndUnbundledPeers() throws {
        for build in [WindowManagerAdmission.Build.release, .debug] {
            for bundle in ["bobko.aerospace.debug", "local.aquaspace.debug", "local.aquaspace", nil] as [String?] {
                try withLock { lock, _ in
                    let peers = [peer(bundle, name: "AeroSpaceApp")]
                    let admission = WindowManagerAdmission(lock: lock, inventory: { peers })
                    #expect(throws: (any Error).self) { try admission.prepare(build: build, readOnly: false) }
                    #expect(throws: (any Error).self) { try admission.claim() }
                    #expect(admission.ownership == .preparing)
                }
            }
        }
    }

    @Test func processClassifierUsesKernelExecutableRatherThanArgvOrLocation() {
        for bundle in ["bobko.aerospace", "bobko.aerospace.debug", "local.aquaspace.debug", nil] as [String?] {
            for name in ["AeroSpace", "AquaSpace", "AquaSpaceApp", "AeroSpaceApp"] {
                #expect(WindowManagerProcess.isManager(executable: "/arbitrary path/\(name)", bundleId: bundle, bundleExecutable: nil))
            }
        }
        #expect(WindowManagerProcess.isManager(executable: "/relocated/Fork.app/Contents/MacOS/RenamedManager",
                                               bundleId: "local.aquaspace", bundleExecutable: "RenamedManager"))
        for name in ["aquaspace-cli", "aerospace", "Helper", "Finder"] {
            #expect(!WindowManagerProcess.isManager(executable: "/app/Contents/MacOS/\(name)",
                                                   bundleId: "local.aquaspace", bundleExecutable: "AquaSpace"))
        }
    }

    @Test func anUnreadableInventoryCannotClaimControlInEitherBuild() throws {
        for build in [WindowManagerAdmission.Build.release, .debug] {
            try withLock { lock, _ in
                let admission = WindowManagerAdmission(lock: lock, inventory: { throw WindowManagerAdmissionError.uncertainInventory })
                #expect(throws: (any Error).self) { try admission.prepare(build: build, readOnly: false) }
                #expect(throws: (any Error).self) { try admission.claim() }
                #expect(admission.ownership == .preparing)
            }
        }
    }

    @Test func debugAndReleaseShareOnePersistentLockAndReadOnlyCannotClaimIt() throws {
        try withLock { lock, path in
            let first = WindowManagerAdmission(lock: lock, inventory: { [] })
            _ = try first.prepare(build: .debug, readOnly: false)
            let otherLock = WindowManagerControllerLock(path: path)
            let second = WindowManagerAdmission(lock: otherLock, inventory: { [] })
            #expect(throws: (any Error).self) { try second.prepare(build: .release, readOnly: false) }
            #expect(try second.prepare(build: .release, readOnly: true).isEmpty)
            #expect(!otherLock.isHeld)
            #expect(throws: (any Error).self) { try second.claim() }
            #expect(FileManager.default.fileExists(atPath: path))
        }
    }

    @Test func peerArrivalFreezesWritesBeforeReturningForEveryBuildAndPeerKind() throws {
        for build in [WindowManagerAdmission.Build.release, .debug] {
            for bundle in ["bobko.aerospace", "bobko.aerospace.debug", "local.aquaspace", "local.aquaspace.debug", nil] as [String?] {
                try withLock { lock, _ in
                    var peers: [WindowManagerProcess] = []
                    var writesAllowed = false
                    let admission = WindowManagerAdmission(lock: lock, inventory: { peers })
                    _ = try admission.prepare(build: build, readOnly: false)
                    try admission.claim()
                    writesAllowed = true
                    peers = [peer(bundle)]
                    let observed = try admission.observedPeers { writesAllowed = false }
                    #expect(!writesAllowed)
                    #expect(observed == peers)
                    admission.yield()
                    #expect(throws: (any Error).self) { try admission.claim() }
                }
            }
        }
    }

    // A failed scan is not evidence of a peer; callers retry with writes still enabled.
    @Test func failedMonitoringReportsErrorWithoutFreezingWrites() throws {
        try withLock { lock, _ in
            var fail = false
            var writesAllowed = true
            let admission = WindowManagerAdmission(lock: lock, inventory: {
                if fail { throw WindowManagerAdmissionError.uncertainInventory }
                return []
            })
            _ = try admission.prepare(build: .debug, readOnly: false)
            try admission.claim()
            fail = true
            #expect(throws: (any Error).self) {
                try admission.observedPeers { writesAllowed = false }
            }
            #expect(writesAllowed)
            #expect(admission.ownership == .controlling)
        }
    }

    @Test func controllerLockDoesNotFollowSymlinks() throws {
        try withLock { _, path in
            let link = path + "-symlink"
            defer { try? FileManager.default.removeItem(atPath: link) }
            try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: path)
            let lock = WindowManagerControllerLock(path: link)
            #expect(throws: (any Error).self) { try lock.acquire() }
            #expect(!lock.isHeld)
        }
    }
}
