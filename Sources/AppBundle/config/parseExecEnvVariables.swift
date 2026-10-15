import AppKit
import Common

let testEnv = ["PATH": "AEROSPACE_TEST_PATH", "AEROSPACE_INHERITED_TEST_ENV": "inherited"]
private var env: [String: String] {
    isUnitTest ? testEnv : ProcessInfo.processInfo.environment
}

private let rawExecConfigParser: [String: any ParserProtocol<RawExecConfig>] = [
    "inherit-env-vars": Parser(\.inheritEnvVariables, parseBool),
    "env-vars": Parser(\.overriddenVars, parseEnvVariables),
]

let defaultOverriddenEnvVars = ["PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:\(env["PATH"] ?? "")"]

struct ExecConfig: Equatable {
    var envVariables: [String: String] = env + defaultOverriddenEnvVars

    /// Legacy config callbacks resolve `aerospace` to this fork's CLI, without
    /// changing the user's PATH or the original Homebrew executable.
    var launchEnvironment: [String: String] {
        var result = envVariables
        if !isUnitTest, let path = result["PATH"],
           let helpers = Bundle.main.resourceURL?.deletingLastPathComponent().appending(path: "Helpers"),
           FileManager.default.fileExists(atPath: helpers.appending(path: "aerospace").path)
        {
            result["PATH"] = helpers.path + ":" + path
        }
        return result
    }
}

struct RawExecConfig: ConvenienceMutable, Equatable {
    var inheritEnvVariables = true
    // Already interpolated value of overridden vars
    var overriddenVars: [String: String] = [:]

    func expand() -> ExecConfig {
        let base: [String: String] = inheritEnvVariables ? env : [:]
        return ExecConfig(envVariables: base + overriddenVars)
    }
}

func parseExecConfig(_ raw: OrderedJson, _ backtrace: ConfigBacktrace, _ c: inout ConfigParserContext) -> ExecConfig {
    parseTable(raw, RawExecConfig(), rawExecConfigParser, backtrace, &c).expand()
}

private func parseEnvVariables(_ raw: OrderedJson, _ backtrace: ConfigBacktrace, _ c: inout ConfigParserContext) -> [String: String] {
    guard let table = raw.asDictOrNil else {
        c.errors.append(expectedActualTypeDiagnostic(expected: .array, actual: raw.tomlType, backtrace))
        return [:]
    }
    let mutated = table.keys
    let fullEnv: [String: String] = env
    let baseEnv: [String: String] = fullEnv.filter { (key, _) -> Bool in !mutated.contains(key) }
    var result: [String: String] = [:]
    for (key, value) in table {
        let backtrace = backtrace + .key(key)
        if key == "PWD" { c.errors.append(.init(backtrace, "Changing 'PWD' is not allowed")) }
        guard let rawStr = parseString(value, backtrace).getOrNil(appendErrorTo: &c.errors) else { continue }
        var env = baseEnv
        if let add: String = fullEnv[key] {
            env[key] = add
        }
        switch rawStr.interpolate(with: env) {
            case .success(let interpolated): result[key] = interpolated
            case .failure(let _errros): c.errors += _errros.map { .init(backtrace, $0) }
        }
    }
    return result
}
