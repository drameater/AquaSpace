/// Task-local provenance keeps an awaited startup restore from suppressing
/// unrelated user commands running on the main actor at the same time.
enum WindowRestorationMutation {
    @TaskLocal static var isUserCommand = false
    @TaskLocal static var isSessionRestore = false
    @TaskLocal static var isAutomaticDiscovery = false
    @TaskLocal static var discoveryInitialization: WindowDiscoveryInitialization?
    @TaskLocal static var commandContinuations: WindowAutomaticCommandContinuations?
}

extension TreeNode {
    /// An explicit user command that rebinds a node supersedes the saved
    /// restoration intent of every window under it. Startup restore does not.
    @MainActor func cancelPendingRestorationForUserBind() {
        if WindowRestorationMutation.isUserCommand && !WindowRestorationMutation.isSessionRestore {
            WindowManagerHandoff.cancelPendingForUserMutation(allLeafWindowsRecursive)
        }
    }
}

extension TilingContainer {
    /// A user orientation change supersedes the saved intent of every window under
    /// each container that changeOrientation flips, in the order it flips them.
    @MainActor func cancelPendingRestorationForOrientationChange() {
        let flipped = config.enableNormalizationOppositeOrientationForNestedContainers
            ? parentsWithSelf.filterIsInstance(of: TilingContainer.self) : [self]
        WindowManagerHandoff.cancelPendingForUserMutation(flipped.flatMap(\.allLeafWindowsRecursive))
    }
}
