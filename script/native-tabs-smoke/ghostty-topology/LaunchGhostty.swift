import AppKit
import Foundation
let args = Array(CommandLine.arguments.dropFirst())
if args == ["frontmost-pid"] {
 guard let app = NSWorkspace.shared.frontmostApplication,
       app.processIdentifier > 0, app.isActive else { exit(2) }
 print(app.processIdentifier)
} else if args.first == "list" {
 let rows = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == "com.mitchellh.ghostty" }.map { ["pid": String($0.processIdentifier), "path": $0.executableURL?.path ?? ""] }
 let data = try JSONSerialization.data(withJSONObject: rows)
 print(String(decoding: data, as: UTF8.self))
} else if args.first == "launch", args.count == 2 {
 let c = NSWorkspace.OpenConfiguration()
 c.createsNewApplicationInstance = true
 c.activates = false
 c.arguments = ["--config-default-files=false", "--config-file=" + args[1]]
 NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: "/Applications/Ghostty.app"), configuration: c) { app, error in
  if let app { print(app.processIdentifier); exit(0) }
  fputs("\(String(describing: error))\n", stderr); exit(2)
 }
 RunLoop.main.run()
} else { exit(2) }
