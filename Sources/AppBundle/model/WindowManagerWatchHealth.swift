import Foundation

/// Counts consecutive failed peer scans of the periodic watch. A failed scan is
/// not evidence of a peer, so it never stops window control (commit c37cb9c4).
/// A long run of failures is logged once, and its recovery once, so a watch that
/// cannot scan is visible in the Console.
struct WindowManagerWatchHealth {
    /// About 5 s at the 200 ms watch interval.
    static let failureThreshold = 25
    private(set) var consecutiveFailures = 0

    /// Returns the message to log when this failure completes a run of `failureThreshold`.
    mutating func scanFailed(_ error: any Error) -> String? {
        consecutiveFailures += 1
        guard consecutiveFailures == Self.failureThreshold else { return nil }
        return "The window-manager peer scan failed \(consecutiveFailures) times in a row. " +
            "AquaSpace keeps window control and keeps scanning. Last error: \(error.localizedDescription)"
    }

    /// Returns the message to log when this success ends a logged run of failures.
    mutating func scanSucceeded() -> String? {
        defer { consecutiveFailures = 0 }
        guard consecutiveFailures >= Self.failureThreshold else { return nil }
        return "The window-manager peer scan works again after \(consecutiveFailures) failed scans."
    }
}
