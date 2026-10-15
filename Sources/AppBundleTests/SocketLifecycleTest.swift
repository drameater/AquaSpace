import Common
import Foundation
import Network
import Testing

@Suite(.serialized)
struct SocketLifecycleTest {
    @Test(.timeLimit(.minutes(1))) func connectionFailureOrCancellationCompletes() async {
        let connection = NWConnection(to: .unix(path: "/private/tmp/aquaspace-unused-\(UUID().uuidString).sock"), using: .tcp)
        async let pending = connection.initConnection()
        try? await Task.sleep(for: .milliseconds(10))
        connection.cancel()
        let result = await pending
        #expect(result.error != nil)
    }

    @Test(.timeLimit(.minutes(1))) func truncatedLengthPrefixStopsAtEOF() async throws {
        let path = "/private/tmp/aquaspace-eof-\(UUID().uuidString).sock"
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .unix(path: path)
        let listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { peer in
            peer.start(queue: .global())
            peer.send(content: Data([4, 0]), contentContext: .finalMessage, isComplete: true,
                      completion: .contentProcessed { _ in })
        }
        listener.start(queue: .global())
        defer {
            listener.cancel()
            try? FileManager.default.removeItem(atPath: path)
        }
        for _ in 0 ..< 100 {
            if case .ready = listener.state { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        guard case .ready = listener.state else { Issue.record("Local socket fixture did not start"); return }
        let connection = NWConnection(to: .unix(path: path), using: .tcp)
        connection.start(queue: .global())
        defer { connection.cancel() }
        switch await connection.readNonAtomic() {
            case .failure: break
            case .success: Issue.record("A truncated length prefix must fail rather than loop or decode")
        }
    }
}
