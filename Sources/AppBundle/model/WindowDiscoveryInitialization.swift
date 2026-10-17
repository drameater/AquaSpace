import Foundation

/// One automatic initialization of one exact model object. The window retains
/// the finished token too, so an awaited restoration can detect a newer attempt.
@MainActor
final class WindowDiscoveryInitialization {
    private weak var window: Window?
    private(set) var isInProgress = true
    private var superseded = false
    private let controlGeneration = WindowManagerHandoff.controlGeneration
    private let focusToken = WindowDiscoveryInitialization.userFocusToken
    @MainActor private static var userFocusToken = UUID()

    init(_ window: Window) {
        self.window = window
        window.discoveryInitialization = self
    }

    var mayContinue: Bool {
        guard isInProgress, !superseded, let window else { return false }
        return WindowManagerHandoff.canInitializeWindows &&
            controlGeneration == WindowManagerHandoff.controlGeneration &&
            window.isBound && window.discoveryInitialization === self &&
            Window.get(byId: window.windowId) === window
    }

    func supersede() { superseded = true }
    func finish() { isInProgress = false }

    static var mayRunAutomaticCommand: Bool {
        if WindowRestorationMutation.isAutomaticDiscovery {
            WindowRestorationMutation.commandContinuations?.cancelStaleInitialization()
        }
        return !WindowRestorationMutation.isAutomaticDiscovery ||
            WindowRestorationMutation.discoveryInitialization?.mayContinue == true
    }

    /// Focus intent is separate from window assignment: a user's workspace
    /// change must stop delayed focus without canceling background placement.
    static var mayRunAutomaticFocus: Bool {
        !WindowRestorationMutation.isAutomaticDiscovery ||
            (mayRunAutomaticCommand && WindowRestorationMutation.discoveryInitialization?.focusToken == userFocusToken)
    }

    static func recordUserFocus(changed: Bool = false) {
        guard !WindowRestorationMutation.isAutomaticDiscovery, !WindowRestorationMutation.isSessionRestore,
              WindowRestorationMutation.isUserCommand || changed else { return }
        userFocusToken = UUID()
    }

    /// Registration owns the outer lifetime, including its first awaited reads.
    /// Popup promotion and test windows establish a lifetime here instead.
    static func run(for window: Window, body: @MainActor () async -> Void) async {
        if let current = window.discoveryInitialization, current.isInProgress {
            guard WindowRestorationMutation.discoveryInitialization === current,
                  current.mayContinue else { return }
            await WindowRestorationMutation.$commandContinuations.withValue(WindowAutomaticCommandContinuations()) {
                await body()
            }
            return
        }
        let initialization = WindowDiscoveryInitialization(window)
        defer { initialization.finish() }
        await WindowRestorationMutation.$discoveryInitialization.withValue(initialization) {
            guard initialization.mayContinue else { return }
            await WindowRestorationMutation.$commandContinuations.withValue(WindowAutomaticCommandContinuations()) {
                await body()
            }
        }
    }
}

/// Required targets retain their intent through command execution and refresh,
/// including successful commands. Optional probes check their own snapshots but
/// never acquire authority over unrelated placement.
@MainActor
final class WindowAutomaticCommandContinuations {
    private var continuations: [WindowCommandContinuation] = []
    private let parent: WindowAutomaticCommandContinuations?

    init(parent: WindowAutomaticCommandContinuations? = nil) { self.parent = parent }

    func register(_ continuation: WindowCommandContinuation) {
        continuations.append(continuation)
        parent?.register(continuation)
    }

    var requiredTargetsAreCurrent: Bool {
        parent?.requiredTargetsAreCurrent != false && continuations.allSatisfy(\.mayContinueChain)
    }

    func cancelStaleInitialization() {
        parent?.cancelStaleInitialization()
        for continuation in continuations {
            continuation.supersedeStaleAutomaticInitialization()
        }
    }
}

/// Command entry checks do not cover suspension inside the command. Capture the
/// affected model too: a callback may explicitly target a different window.
@MainActor
final class WindowCommandContinuation {
    /// Accepted assignment intent must supersede discovery before a native read,
    /// even if that read later fails or proves the requested state is a no-op.
    static func beforeMutation(_ window: Window) -> Self {
        WindowManagerHandoff.cancelPendingForUserMutation([window])
        return Self(window)
    }

    private let window: Window
    private let userMutationToken: UUID
    private let initialization: WindowDiscoveryInitialization?
    private let automatic: Bool
    private let hadWindowControl: Bool
    private let controlGeneration: UUID
    private var completedClose = false

    init(_ window: Window, optionalProbe: Bool = false) {
        self.window = window
        userMutationToken = window.userMutationToken
        initialization = WindowRestorationMutation.discoveryInitialization
        automatic = WindowRestorationMutation.isAutomaticDiscovery
        hadWindowControl = WindowManagerHandoff.canControlWindows
        controlGeneration = WindowManagerHandoff.controlGeneration
        if automatic, !optionalProbe { WindowRestorationMutation.commandContinuations?.register(self) }
    }

    var targetIsUnchanged: Bool {
        Window.get(byId: window.windowId) === window && window.isBound &&
            window.userMutationToken == userMutationToken
    }

    var mayContinue: Bool {
        mayContinue(targetIsUnchanged: targetIsUnchanged) &&
            WindowRestorationMutation.commandContinuations?.requiredTargetsAreCurrent != false
    }

    private func mayContinue(targetIsUnchanged: Bool) -> Bool {
        !automatic || (initialization?.mayContinue == true && targetIsUnchanged &&
            WindowManagerHandoff.canInitializeWindows && controlGeneration == WindowManagerHandoff.controlGeneration &&
            !WindowManagerHandoff.isYielding && (!hadWindowControl || WindowManagerHandoff.canControlWindows))
    }

    /// A dispatched close may remove this exact model. That expected absence is
    /// allowed only when checking the chain: it grants no authority for another
    /// native effect, a reused ID, or a changed user/control token.
    func didDispatchClose() { completedClose = true }

    var isCompletedRemoval: Bool {
        completedClose && Window.get(byId: window.windowId) == nil && window.userMutationToken == userMutationToken
    }

    fileprivate var mayContinueChain: Bool {
        mayContinue(targetIsUnchanged: targetIsUnchanged || isCompletedRemoval)
    }

    fileprivate func supersedeStaleAutomaticInitialization() {
        if automatic, !mayContinueChain { initialization?.supersede() }
    }
}
