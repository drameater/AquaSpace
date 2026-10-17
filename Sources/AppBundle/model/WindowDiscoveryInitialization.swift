import Foundation

/// One automatic initialization of one exact model object. The window retains
/// the finished token too, so an awaited restoration can detect a newer attempt.
@MainActor
final class WindowDiscoveryInitialization {
    private weak var window: Window?
    private(set) var isInProgress = true
    private var superseded = false

    init(_ window: Window) {
        self.window = window
        window.discoveryInitialization = self
    }

    var mayContinue: Bool {
        guard isInProgress, !superseded, let window else { return false }
        return window.isBound && window.discoveryInitialization === self &&
            Window.get(byId: window.windowId) === window
    }

    func supersede() { superseded = true }
    func finish() { isInProgress = false }

    static var mayRunAutomaticCommand: Bool {
        !WindowRestorationMutation.isAutomaticDiscovery ||
            WindowRestorationMutation.discoveryInitialization?.mayContinue == true
    }

    /// Registration owns the outer lifetime, including its first awaited reads.
    /// Popup promotion and test windows establish a lifetime here instead.
    static func run(for window: Window, body: @MainActor () async -> Void) async {
        if let current = window.discoveryInitialization, current.isInProgress {
            guard WindowRestorationMutation.discoveryInitialization === current,
                  current.mayContinue else { return }
            await body()
            return
        }
        let initialization = WindowDiscoveryInitialization(window)
        defer { initialization.finish() }
        await WindowRestorationMutation.$discoveryInitialization.withValue(initialization) {
            guard initialization.mayContinue else { return }
            await body()
        }
    }
}

/// Command entry checks do not cover suspension inside the command. Capture the
/// affected model too: a callback may explicitly target a different window.
@MainActor
struct WindowCommandContinuation {
    private let window: Window
    private let userMutationToken: UUID
    private let initialization: WindowDiscoveryInitialization?
    private let automatic: Bool
    private let hadWindowControl: Bool

    init(_ window: Window) {
        self.window = window
        userMutationToken = window.userMutationToken
        initialization = WindowRestorationMutation.discoveryInitialization
        automatic = WindowRestorationMutation.isAutomaticDiscovery
        hadWindowControl = WindowManagerHandoff.canControlWindows
    }

    var targetIsUnchanged: Bool {
        Window.get(byId: window.windowId) === window && window.isBound &&
            window.userMutationToken == userMutationToken
    }

    var mayContinue: Bool {
        !automatic || (initialization?.mayContinue == true && targetIsUnchanged &&
            !WindowManagerHandoff.isYielding && (!hadWindowControl || WindowManagerHandoff.canControlWindows))
    }
}
