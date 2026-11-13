import Foundation
import os

/// The AX write gate. AX threads read it; any thread can freeze it.
struct WindowWriteGate: Sendable {
    private let permission = OSAllocatedUnfairLock(initialState: WindowControlPermission())
    var permitsWrites: Bool { permission.withLock { $0.permitsWrites } }
    var permitsInitialization: Bool { permission.withLock { $0.permitsInitialization } }
    var generation: UUID { permission.withLock { $0.generation } }
    func freeze() { permission.withLock { $0.freeze() } }
    func claim() { permission.withLock { $0.claim() } }

    /// Launch and activation notifications call this synchronously. Every manager
    /// bundle ID freezes writes at once, in both build kinds. The caller then
    /// confirms the peer with an inventory scan.
    func freezeIfPeerLaunched(bundleId: String?, pid: Int32?, ownPid: Int32) -> Bool {
        guard let bundleId, WindowManagerProcess.bundleIds.contains(bundleId), pid != ownPid else { return false }
        freeze()
        return true
    }
}

/// Window control for the process lifetime: admission, the write gate, the peer
/// watch and every fail-closed stop. WindowManagerHandoff holds the one production
/// instance. Every effect outside this policy is a closure in `Effects`, so a test
/// builds its own instance and cannot exit, report to the Console or touch the desktop.
@MainActor
final class WindowManagerController {
    struct Effects {
        /// A read-only probe never takes, yields or stops control.
        var isReadOnly: @MainActor () -> Bool
        /// Forgets saved restoration intent.
        var forgetRestoration: @MainActor () -> Void
        /// Stops layout, refresh sessions and pending frame writes.
        var pauseManagement: @MainActor () -> Void
        /// Releases mode hotkeys and waits until canceled AX writes have finished.
        var drainWrites: @MainActor () async -> Void
        /// After startup admission: loads a saved session, or captures the session of
        /// the stock managers in `originals` and stops them.
        var takeOver: @MainActor (_ originals: [WindowManagerProcess]) async throws -> Void
        /// Captures this session, waits for the stock manager and restores the session in it.
        var handOff: @MainActor () async throws -> Void
        /// Publishes a failure report: production writes the file, the Console log and stderr.
        var report: @MainActor (WindowManagerFailureReport.Phase, any Error) -> Void
        /// Ends the process with this status. In production it does not return.
        var exit: @MainActor (Int32) -> Void
    }

    nonisolated let gate: WindowWriteGate
    private let admission: WindowManagerAdmission
    private let build: WindowManagerAdmission.Build
    private let watchInterval: Duration
    private let effects: Effects
    private var watch: Task<(), Never>?
    private var isStopping = false

    init(admission: WindowManagerAdmission, gate: WindowWriteGate = WindowWriteGate(), build: WindowManagerAdmission.Build = .current,
         watchInterval: Duration = .milliseconds(200), effects: Effects) {
        self.admission = admission
        self.gate = gate
        self.build = build
        self.watchInterval = watchInterval
        self.effects = effects
    }

    var ownership: WindowManagerOwnership { admission.ownership }
    var isYielding: Bool { admission.ownership == .yielding }

    /// An authoritative scan. A detected peer freezes writes; a failed scan does not.
    func observedPeers() throws -> [WindowManagerProcess] {
        try admission.observedPeers(freeze: gate.freeze)
    }

    func prepareForStartup() async {
        guard !effects.isReadOnly() else { return }
        do {
            let originals = try admission.prepare(build: build, readOnly: false)
            try await effects.takeOver(originals)
        } catch {
            effects.report(.startup, error)
            effects.exit(EXIT_FAILURE)
        }
    }

    /// Admission is claimed first. Only then does the write gate open.
    func finishStartup() async {
        guard !effects.isReadOnly() else { return }
        do {
            try admission.claim()
            gate.claim()
            watchForOriginal()
        } catch {
            await stop(error, phase: .startup)
        }
    }

    /// The last startup session ends with finishStartup(). If it throws, before or
    /// after the claim, control stops like a failed claim.
    func runFinalStartupSession(_ body: @MainActor () async throws -> Void) async {
        do { try await body() } catch { await stop(error, phase: .startup) }
    }

    private func watchForOriginal() {
        guard !effects.isReadOnly(), watch == nil else { return }
        let interval = watchInterval
        watch = Task.startUnstructured { @MainActor [weak self] in
            var health = WindowManagerWatchHealth()
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)
                guard let self else { return }
                // A failed scan is retried on the next tick; only a detected peer stops control.
                // The incremental scan reads only changed processes, so an unbundled peer
                // is still found within one interval (about 200 ms) of its exec.
                let peers: [WindowManagerProcess]
                do {
                    peers = try admission.watchedPeers(freeze: gate.freeze)
                    if let message = health.scanSucceeded() { WindowManagerFailureReport.logger.notice("\(message, privacy: .public)") }
                } catch {
                    if let message = health.scanFailed(error) { WindowManagerFailureReport.logger.error("\(message, privacy: .public)") }
                    continue
                }
                guard !peers.isEmpty else { continue }
                await yield(to: peers)
                return
            }
        }
    }

    /// Notifications freeze synchronously; polling also catches unbundled peers.
    /// The launch notification already froze writes. Yield only to a confirmed peer.
    /// Otherwise resume control; polling still catches a peer that appears later.
    func observedPeerLaunch() async {
        // A handoff or a stop already runs, and writes are frozen. Stock sends launch and
        // activation notifications during a handoff; a scan that fails then must not end it.
        guard !isYielding else { return }
        if let peers = try? observedPeers(), !peers.isEmpty {
            await yield(to: peers)
        } else if admission.ownership == .controlling {
            gate.claim()
        } else {
            await stop(WindowManagerAdmissionError.uncertainInventory)
        }
    }

    func yield(to peers: [WindowManagerProcess]) async {
        if peers.count == 1 && peers[0].isStockRelease {
            await switchToOriginal()
        } else {
            await stop(WindowManagerAdmissionError.competingManager)
        }
    }

    /// Fail closed. The report comes before the first suspension point, so the
    /// reason is saved even if a later wait never ends. A second stop during the
    /// drain of the first does nothing.
    func stop(_ error: any Error, phase: WindowManagerFailureReport.Phase = .monitoring) async {
        guard !effects.isReadOnly(), !isStopping else { return }
        isStopping = true
        admission.yield()
        effects.forgetRestoration()
        gate.freeze()
        effects.pauseManagement()
        effects.report(phase, error)
        await effects.drainWrites()
        effects.exit(EXIT_FAILURE)
    }

    /// Hands the session to a stock AeroSpace that was started while AquaSpace ran.
    func switchToOriginal() async {
        guard !effects.isReadOnly(), !isYielding else { return }
        admission.yield()
        gate.freeze()
        // Freeze synchronously, before any AX/network await. Disabled AeroSpace's
        // normal layout path unhides every window, which must not run in a handoff.
        effects.pauseManagement()
        do {
            try await effects.handOff()
            // A handoff must leave window geometry alone. The normal Quit path recenters
            // every window and would undo the new manager's restored workspaces.
            effects.exit(EXIT_SUCCESS)
        } catch {
            // Fail closed. Never kill/restart AeroSpace, rebind its socket, or
            // re-enable AquaSpace while the original may already be managing windows.
            effects.report(.handoff, error)
            effects.exit(EXIT_FAILURE)
        }
    }
}
