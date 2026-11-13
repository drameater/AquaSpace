import Foundation

// Inputs of the production native-tab state machine (Sources/AppBundle/tree/nativeTabs.swift)
// that come from outside AX: mouse state, WindowServer inventory and time.

let isLeftMouseButtonDown = false

// No CG calls: initialization starts with an empty synthetic server inventory.
func getWindowServerWindowIdsByPid() -> [Int32: Set<UInt32>]? { [getpid(): []] }

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
