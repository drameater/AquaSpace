import Foundation

// Small runtime helpers that production code takes from Sources/Common and
// Sources/AppBundle/util. The offline replays do not link those modules.

enum CancellationMode: Equatable, Sendable { case cancellable, nonCancellable }
func checkCancellation(_ cm: CancellationMode = .cancellable) throws(CancellationError) {
    if cm == .cancellable && Task.isCancelled { throw CancellationError() }
}
func check(_ condition: Bool, _ message: @autoclosure () -> String = "") {
    if !condition { fatalError(message()) }
}
extension Optional {
    func orDie(_ message: String = "") -> Wrapped {
        guard let value = self else { fatalError("orDie: " + message) }
        return value
    }
}

/// A job that a test can cancel at once, or after a set number of checks.
final class RunLoopJob {
    var cancelled = false
    var checksUntilCancellation: Int?
    func cancel() { cancelled = true }
    func checkCancellation() throws {
        if cancelled || checksUntilCancellation == 0 { throw CancellationError() }
        if let count = checksUntilCancellation { checksUntilCancellation = count - 1 }
    }
}
