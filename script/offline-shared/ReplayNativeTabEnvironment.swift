import Foundation

// Inputs of the production native-tab state machine (Sources/AppBundle/tree/nativeTabs.swift)
// that come from outside AX: mouse state, WindowServer inventory and time.

// Task-local values keep test cases independent. No CG call is made.
enum ReplayDesktop {
    @TaskLocal static var leftMouseButtonDown = false
    /// What a WindowServer read returns. By default it lists this process with no windows.
    @TaskLocal static var windowIdsByPid: [Int32: Set<UInt32>]? = [getpid(): []]
    /// Counts WindowServer reads when a test sets it.
    @TaskLocal static var reads: ReadCounter?
    final class ReadCounter: @unchecked Sendable { var count = 0 }
}

var isLeftMouseButtonDown: Bool { ReplayDesktop.leftMouseButtonDown }

func getWindowServerWindowIdsByPid() -> [Int32: Set<UInt32>]? {
    ReplayDesktop.reads?.count += 1
    return ReplayDesktop.windowIdsByPid
}

// Task-local deterministic time keeps test cases independent and avoids sleeps.
enum ReplayTime {
    @TaskLocal static var elapsed: Duration = .zero
}
struct ContinuousClock {
    struct Instant {
        let elapsed: Duration
        func duration(to other: Instant) -> Duration { other.elapsed - elapsed }
    }
    var now: Instant { Instant(elapsed: ReplayTime.elapsed) }
}
