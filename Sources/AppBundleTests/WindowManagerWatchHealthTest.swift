@testable import AppBundle
import Foundation
import Testing

struct WindowManagerWatchHealthTest {
    private let error = NSError(domain: "AquaSpace.ProcessInventory", code: 1,
        userInfo: [NSLocalizedDescriptionKey: "Cannot establish process identity (list-pids, errno 1)"])

    private func fail(_ health: inout WindowManagerWatchHealth, _ count: Int) -> [String] {
        (0 ..< count).compactMap { _ in health.scanFailed(error) }
    }

    @Test func shortRunsOfFailedScansAreNotLogged() {
        var health = WindowManagerWatchHealth()
        for _ in 0 ..< 10 {
            #expect(fail(&health, WindowManagerWatchHealth.failureThreshold - 1).isEmpty)
            #expect(health.scanSucceeded() == nil)
        }
    }

    @Test func aLongRunIsLoggedOnceWithTheErrorAndItsRecoveryOnce() {
        var health = WindowManagerWatchHealth()
        let messages = fail(&health, 10 * WindowManagerWatchHealth.failureThreshold)
        #expect(messages.count == 1)
        #expect(messages.first?.contains("list-pids, errno 1") == true)
        let recovery = health.scanSucceeded()
        #expect(recovery != nil)
        #expect(health.scanSucceeded() == nil)
        // A later run is logged again.
        #expect(fail(&health, WindowManagerWatchHealth.failureThreshold).count == 1)
    }
}
