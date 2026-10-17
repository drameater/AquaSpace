/// Task-local provenance keeps an awaited startup restore from suppressing
/// unrelated user commands running on the main actor at the same time.
enum WindowRestorationMutation {
    @TaskLocal static var isUserCommand = false
    @TaskLocal static var isSessionRestore = false
    @TaskLocal static var isAutomaticDiscovery = false
    @TaskLocal static var discoveryInitialization: WindowDiscoveryInitialization?
    @TaskLocal static var commandContinuations: WindowAutomaticCommandContinuations?
}
