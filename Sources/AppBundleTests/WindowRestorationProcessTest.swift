@testable import AppBundle
import Testing

private final class RestorationProcessStub: WindowRestorationProcessAPI {
    var userId: UInt32 = 501
    var birth = WindowManagerProcessBirth(pid: 9, uid: 501, seconds: 100, microseconds: 2)
    var image = WindowManagerExecGeneration(uuid: Array(repeating: 1, count: 16), uniqueId: 7, version: 1)
    var path: String? = "/fixture/app"
    var identities = 0
    var paths = 0
    var generations = 0
    var mutation: ((String, Int, RestorationProcessStub) -> Void)?
    func identity(_ pid: Int32) throws -> WindowManagerProcessBirth? {
        identities += 1; mutation?("identity", identities, self); return birth
    }
    func executable(_ pid: Int32) throws -> String? {
        paths += 1; mutation?("path", paths, self); return path
    }
    func generation(_ pid: Int32) throws -> WindowManagerExecGeneration {
        generations += 1; mutation?("exec", generations, self); return image
    }
}

struct WindowRestorationProcessTest {
    @Test func kernelIdentityIsBracketedAndCarriesBirthExecutableAndExecGeneration() {
        let api = RestorationProcessStub()
        let owner = WindowManagerProcesses.restorationOwner(9, using: api)
        #expect(owner?.pid == 9)
        #expect(owner?.startedSeconds == 100)
        #expect(owner?.executable == "/fixture/app")
        #expect(owner?.uniqueId == 7)
        #expect(owner?.execVersion == 1)
        #expect(api.identities == 2 && api.paths == 2 && api.generations == 2)
    }
    @Test func PIDReuseDuringReadIsRejected() {
        let api = RestorationProcessStub()
        api.mutation = { phase, count, state in
            if phase == "identity" && count == 2 { state.birth = .init(pid: 9, uid: 501, seconds: 101, microseconds: 2) }
        }
        #expect(WindowManagerProcesses.restorationOwner(9, using: api) == nil)
    }
    @Test func execChangeAndExecutableReplacementDuringReadAreRejected() {
        for phase in ["exec", "path"] {
            let api = RestorationProcessStub()
            api.mutation = { which, count, state in
                if which == phase && count == 2 {
                    if phase == "exec" { state.image = .init(uuid: Array(repeating: 1, count: 16), uniqueId: 7, version: 2) }
                    else { state.path = "/fixture/replacement" }
                }
            }
            #expect(WindowManagerProcesses.restorationOwner(9, using: api) == nil)
        }
    }
    @Test func foreignUIDAndWrongPIDAreRejected() {
        for birth in [WindowManagerProcessBirth(pid: 9, uid: 502, seconds: 100, microseconds: 2),
                      WindowManagerProcessBirth(pid: 10, uid: 501, seconds: 100, microseconds: 2)] {
            let api = RestorationProcessStub(); api.birth = birth
            #expect(WindowManagerProcesses.restorationOwner(9, using: api) == nil)
        }
    }
    @Test func invalidExecIdentityAndMissingExecutableAreRejected() {
        for image in [WindowManagerExecGeneration(uuid: [], uniqueId: 7, version: 1),
                      WindowManagerExecGeneration(uuid: Array(repeating: 0, count: 16), uniqueId: 7, version: 1),
                      WindowManagerExecGeneration(uuid: Array(repeating: 1, count: 16), uniqueId: 0, version: 1),
                      WindowManagerExecGeneration(uuid: Array(repeating: 1, count: 16), uniqueId: 7, version: 0)] {
            let api = RestorationProcessStub(); api.image = image
            #expect(WindowManagerProcesses.restorationOwner(9, using: api) == nil)
        }
        let api = RestorationProcessStub(); api.path = nil
        #expect(WindowManagerProcesses.restorationOwner(9, using: api) == nil)
    }
}
