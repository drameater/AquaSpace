import Foundation

/// Startup may initialize the model before claim, but a frozen controller must
/// never be mistaken for that initial state. A freeze also revokes old work if
/// a later successful admission grants a new control period.
struct WindowControlPermission: Sendable {
    private(set) var permitsWrites = false
    private(set) var permitsInitialization = true
    private(set) var generation = UUID()

    mutating func freeze() {
        permitsWrites = false
        permitsInitialization = false
        generation = UUID()
    }

    mutating func claim() {
        permitsWrites = true
        permitsInitialization = true
    }
}
