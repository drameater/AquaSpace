@testable import AppBundle
import Foundation
import Testing

private enum Fixture: Error { case takeOver, handOff, session, unreadable }

private func manager(_ bundle: String?, pid: Int32 = 42) -> WindowManagerProcess {
    WindowManagerProcess(pid: pid, startedSeconds: 1, startedMicroseconds: 2, executable: "/fixture/AeroSpace", bundleId: bundle)
}

/// A controller whose effects only record what they were asked to do. Its lock
/// is a temporary file and its inventories are in memory, so nothing here exits,
/// reports to the Console, reads processes or touches the desktop.
@MainActor private final class Probe {
    struct Report {
        let phase: WindowManagerFailureReport.Phase
        let error: any Error
        let yielding: Bool
        let writesOpen: Bool
        let initializationOpen: Bool
        let eventsBefore: [String]
        let suspendedBefore: Bool
    }

    let lockPath = FileManager.default.temporaryDirectory.appending(path: "aquaspace-controller-test-\(UUID()).lock").path
    var readOnly = false
    var peers: [WindowManagerProcess] = []
    var watchPeers: [WindowManagerProcess] = []
    var scanError: (any Error)?
    var gateOpenDuringScans: [Bool] = []
    var events: [String] = []
    var reports: [Report] = []
    var exits: [Int32] = []
    var takeOver: ([WindowManagerProcess]) async throws -> Void = { _ in }
    var handOff: () async throws -> Void = {}
    /// Set by a task that can run only after the controller suspends.
    var suspended = false
    private(set) var controller: WindowManagerController!

    /// nil `build` uses the controller's default, the kind of this build.
    init(build: WindowManagerAdmission.Build? = nil) {
        let admission = WindowManagerAdmission(lock: WindowManagerControllerLock(path: lockPath),
            inventory: { [weak self] in try MainActor.assumeIsolated { try self?.scan() ?? [] } },
            watchInventory: { [weak self] in try MainActor.assumeIsolated { try self?.watchScan() ?? [] } })
        let effects = WindowManagerController.Effects(
            isReadOnly: { [weak self] in self?.readOnly ?? true },
            forgetRestoration: { [weak self] in self?.events.append("forgetRestoration") },
            pauseManagement: { [weak self] in self?.events.append("pauseManagement") },
            drainWrites: { [weak self] in
                await Task.yield()
                self?.events.append("drainWrites")
            },
            takeOver: { [weak self] originals in
                self?.events.append("takeOver")
                try await self?.takeOver(originals)
            },
            handOff: { [weak self] in
                self?.events.append("handOff")
                try await self?.handOff()
            },
            report: { [weak self] phase, error in self?.record(phase, error) },
            exit: { [weak self] status in
                self?.events.append("exit")
                self?.exits.append(status)
            },
        )
        controller = if let build {
            WindowManagerController(admission: admission, build: build, watchInterval: .milliseconds(1), effects: effects)
        } else {
            WindowManagerController(admission: admission, watchInterval: .milliseconds(1), effects: effects)
        }
    }

    deinit { try? FileManager.default.removeItem(atPath: lockPath) }

    private func scan() throws -> [WindowManagerProcess] {
        gateOpenDuringScans.append(controller.gate.permitsWrites)
        if let scanError { throw scanError }
        return peers
    }

    private func watchScan() throws -> [WindowManagerProcess] {
        if let scanError { throw scanError }
        return watchPeers
    }

    private func record(_ phase: WindowManagerFailureReport.Phase, _ error: any Error) {
        reports.append(Report(phase: phase, error: error, yielding: controller.isYielding,
            writesOpen: controller.gate.permitsWrites, initializationOpen: controller.gate.permitsInitialization,
            eventsBefore: events, suspendedBefore: suspended))
    }

    /// Lets `suspended` turn true at the controller's first suspension point.
    func watchForSuspension() {
        suspended = false
        Task { @MainActor [weak self] in self?.suspended = true }
    }

    /// Startup admission and claim succeeded: the gate is open and the watch runs.
    static func started(build: WindowManagerAdmission.Build? = nil) async -> Probe {
        let probe = Probe(build: build)
        await probe.controller.prepareForStartup()
        await probe.controller.finishStartup()
        #expect(probe.controller.gate.permitsWrites && probe.exits.isEmpty)
        #expect(probe.events == ["takeOver"])
        probe.events = []
        return probe
    }
}

/// Lets other main-actor work run until `condition` holds; false after 5 s.
@MainActor private func eventually(_ condition: () -> Bool) async -> Bool {
    let deadline = ContinuousClock.now + .seconds(5)
    while !condition() {
        if ContinuousClock.now > deadline { return false }
        try? await Task.sleep(for: .milliseconds(1))
    }
    return true
}

// `swift test` builds the debug configuration, so the default build kind here is debug.
@MainActor
struct WindowManagerControllerTest {
    @Test func thisDebugBuildRefusesToReplaceAStockReleaseAtStartup() async {
        let probe = Probe()
        probe.peers = [manager("bobko.aerospace")]
        await probe.controller.prepareForStartup()
        #expect(!probe.events.contains("takeOver"))
        #expect(probe.reports.map(\.phase) == [.startup])
        #expect(probe.reports.first?.error as? WindowManagerAdmissionError == .competingManager)
        #expect(probe.exits == [EXIT_FAILURE])
        // A release build takes over from the same stock release.
        let release = Probe(build: .release)
        release.peers = probe.peers
        var originals: [WindowManagerProcess] = []
        release.takeOver = { originals = $0 }
        await release.controller.prepareForStartup()
        #expect(originals == probe.peers)
        #expect(release.reports.isEmpty && release.exits.isEmpty)
    }

    @Test func startupPreparationFailuresReportTheStartupPhaseAndExit() async throws {
        let locked = Probe()
        let other = WindowManagerControllerLock(path: locked.lockPath)
        try other.acquire() // Another AquaSpace holds the controller lock.
        await locked.controller.prepareForStartup()
        #expect(locked.reports.map(\.phase) == [.startup])
        #expect(locked.reports.first?.error as? WindowManagerAdmissionError == .lockUnavailable)
        #expect(locked.exits == [EXIT_FAILURE])

        let failedTakeOver = Probe(build: .release)
        failedTakeOver.peers = [manager("bobko.aerospace")]
        failedTakeOver.takeOver = { _ in throw Fixture.takeOver }
        await failedTakeOver.controller.prepareForStartup()
        #expect(failedTakeOver.reports.map(\.phase) == [.startup])
        #expect(failedTakeOver.reports.first?.error as? Fixture == .takeOver)
        #expect(failedTakeOver.exits == [EXIT_FAILURE])
    }

    @Test func finishStartupClaimsAdmissionBeforeItOpensTheWriteGate() async {
        let probe = Probe()
        await probe.controller.prepareForStartup()
        #expect(!probe.controller.gate.permitsWrites)
        probe.gateOpenDuringScans = []
        await probe.controller.finishStartup()
        #expect(probe.gateOpenDuringScans == [false])
        #expect(probe.controller.gate.permitsWrites)
        #expect(probe.reports.isEmpty && probe.exits.isEmpty)
    }

    @Test func failedStartupClaimStopsWithTheStartupPhase() async {
        let probe = Probe()
        await probe.controller.prepareForStartup()
        probe.peers = [manager("local.aquaspace.debug")]
        await probe.controller.finishStartup()
        #expect(probe.reports.map(\.phase) == [.startup])
        #expect(probe.reports.first?.error as? WindowManagerAdmissionError == .competingManager)
        #expect(probe.exits == [EXIT_FAILURE])
        #expect(!probe.controller.gate.permitsWrites)
        #expect(probe.controller.isYielding)
    }

    @Test func failedFinalStartupSessionReportsTheStartupPhaseAndExits() async {
        let beforeClaim = Probe()
        await beforeClaim.controller.prepareForStartup()
        await beforeClaim.controller.runFinalStartupSession { throw Fixture.session }
        #expect(beforeClaim.reports.map(\.phase) == [.startup])
        #expect(beforeClaim.reports.first?.error as? Fixture == .session)
        #expect(beforeClaim.exits == [EXIT_FAILURE])

        let afterClaim = Probe()
        await afterClaim.controller.prepareForStartup()
        await afterClaim.controller.runFinalStartupSession {
            await afterClaim.controller.finishStartup()
            throw Fixture.session
        }
        #expect(afterClaim.reports.map(\.phase) == [.startup])
        #expect(afterClaim.exits == [EXIT_FAILURE])
        #expect(!afterClaim.controller.gate.permitsWrites)
    }

    @Test func uncertainOwnershipFreezesAndPausesThenReportsBeforeItsFirstSuspension() async {
        // Two managers appear while AquaSpace controls windows.
        let probe = await Probe.started()
        probe.peers = [manager("bobko.aerospace", pid: 42), manager("local.aquaspace.debug", pid: 43)]
        probe.watchForSuspension()
        await probe.controller.observedPeerLaunch()
        let report = probe.reports.first
        #expect(probe.reports.count == 1)
        #expect(report?.phase == .monitoring)
        #expect(report?.error as? WindowManagerAdmissionError == .competingManager)
        #expect(report?.yielding == true)
        #expect(report?.writesOpen == false)
        #expect(report?.initializationOpen == false)
        #expect(report?.eventsBefore == ["forgetRestoration", "pauseManagement"])
        #expect(report?.suspendedBefore == false)
        #expect(probe.suspended) // The drain did suspend, after the report.
        #expect(probe.events == ["forgetRestoration", "pauseManagement", "drainWrites", "exit"])
        #expect(probe.exits == [EXIT_FAILURE])
    }

    @Test func unreadableInventoryDuringStartupStopsBeforeControlIsClaimed() async {
        let probe = Probe()
        await probe.controller.prepareForStartup()
        probe.scanError = Fixture.unreadable
        probe.watchForSuspension()
        await probe.controller.observedPeerLaunch()
        #expect(probe.reports.map(\.phase) == [.monitoring])
        #expect(probe.reports.first?.error as? WindowManagerAdmissionError == .uncertainInventory)
        #expect(probe.reports.first?.initializationOpen == false)
        #expect(probe.reports.first?.suspendedBefore == false)
        #expect(probe.exits == [EXIT_FAILURE])
    }

    @Test func aLaunchWithoutAConfirmedPeerResumesControl() async {
        let probe = await Probe.started()
        #expect(probe.controller.gate.freezeIfPeerLaunched(bundleId: "bobko.aerospace", pid: 42, ownPid: 7))
        #expect(!probe.controller.gate.permitsWrites)
        await probe.controller.observedPeerLaunch()
        #expect(probe.controller.gate.permitsWrites)
        #expect(probe.reports.isEmpty && probe.exits.isEmpty)
    }

    @Test func launchOfEveryManagerBundleFreezesWritesAtOnce() {
        for bundle in WindowManagerProcess.bundleIds.sorted() {
            let gate = WindowWriteGate()
            gate.claim()
            #expect(gate.freezeIfPeerLaunched(bundleId: bundle, pid: 42, ownPid: 7), "\(bundle)")
            #expect(!gate.permitsWrites, "\(bundle)")
        }
        let gate = WindowWriteGate()
        gate.claim()
        #expect(!gate.freezeIfPeerLaunched(bundleId: "local.aquaspace", pid: 7, ownPid: 7)) // AquaSpace itself.
        #expect(!gate.freezeIfPeerLaunched(bundleId: "com.apple.Safari", pid: 42, ownPid: 7))
        #expect(!gate.freezeIfPeerLaunched(bundleId: nil, pid: 42, ownPid: 7))
        #expect(gate.permitsWrites)
    }

    @Test func watchInThisDebugBuildStopsForAnyPeer() async {
        let probe = await Probe.started()
        probe.watchPeers = [manager("local.aquaspace.debug")]
        #expect(await eventually { !probe.exits.isEmpty })
        #expect(probe.reports.map(\.phase) == [.monitoring])
        #expect(probe.reports.first?.error as? WindowManagerAdmissionError == .competingManager)
        #expect(probe.exits == [EXIT_FAILURE])
    }

    @Test func watchInThisDebugBuildHandsOffToAStockRelease() async {
        let probe = await Probe.started()
        var gateDuringHandOff: Bool?
        probe.handOff = { [unowned probe] in gateDuringHandOff = probe.controller.gate.permitsWrites }
        probe.watchPeers = [manager("bobko.aerospace")]
        #expect(await eventually { !probe.exits.isEmpty })
        #expect(probe.events == ["pauseManagement", "handOff", "exit"])
        #expect(gateDuringHandOff == false)
        #expect(probe.reports.isEmpty)
        #expect(probe.exits == [EXIT_SUCCESS])
    }

    @Test func failedStockHandoffReportsTheHandoffPhaseAndExits() async {
        let probe = await Probe.started()
        probe.handOff = { throw Fixture.handOff }
        await probe.controller.switchToOriginal()
        #expect(probe.reports.map(\.phase) == [.handoff])
        #expect(probe.reports.first?.error as? Fixture == .handOff)
        #expect(probe.reports.first?.writesOpen == false)
        #expect(probe.exits == [EXIT_FAILURE])
        // A yielding controller never hands off twice.
        await probe.controller.switchToOriginal()
        #expect(probe.exits == [EXIT_FAILURE])
    }

    @Test func readOnlyProbeNeverClaimsStopsOrReports() async {
        let probe = Probe()
        probe.readOnly = true
        probe.peers = [manager("bobko.aerospace")]
        await probe.controller.prepareForStartup()
        await probe.controller.finishStartup()
        await probe.controller.stop(Fixture.unreadable)
        await probe.controller.switchToOriginal()
        #expect(probe.events.isEmpty && probe.reports.isEmpty && probe.exits.isEmpty)
        #expect(!probe.controller.gate.permitsWrites)
        #expect(probe.controller.ownership == .preparing)
    }
}
