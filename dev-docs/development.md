# Development notes

This file holds editor and toolchain notes. Some notes come from upstream
AeroSpace, and some of those do not apply to this fork. Each section says which.

For build, install, update, recovery and tests of the fork, use the
[AquaSpace guide](aquaspace.md). Do not copy those instructions here.

## What applies to the fork

| Tool or step | Status in the fork |
| --- | --- |
| Swift toolchain and a macOS SDK | **Required.** The Command Line Tools are enough for the release build. See [Build](aquaspace.md#build). |
| Python 3 | **Required** for the builder, installer and tests. |
| `script/aquaspace_signing.py --setup` | **Required once.** It replaces upstream's manual certificate step in Keychain Access. |
| Xcode | **Required** for XCTest by `build-debug.sh`, `run-debug.sh`, `run-cli.sh`, `swift-test.sh` and `test.sh`; they stop at once without it. **Not used** by the release build, which uses `swift build`. |
| [swiftly](https://github.com/swiftlang/swiftly) | **Required** by the scripts that source `script/setup.sh` (for example `build-debug.sh`, `swift-test.sh`, `test.sh`, `generate.sh`); `setup.sh` stops without it. **Not used** by `build-release.sh` and the Python builder, which use the `swift` on your `PATH`. |
| Bash 5 | **Required** by the scripts that source `script/setup.sh`. macOS ships Bash 3.2. `install-from-sources.sh` and `build-release.sh` work with Bash 3.2. |
| `xcbeautify` | **Not used.** No fork script calls it. |
| Rust, `bash`, `fish` (shell completion); Ruby and `asciidoctor` (man pages) | **Not used** by the fork's build. Upstream's release used them through `build-shell-completion.sh` and `build-docs.sh`. |
| Upstream Homebrew release and install jobs, CI matrix, bots | **Removed.** The fork's one workflow runs the Python suite by hand. |

`script/install-dep.sh` downloads tools (for example XcodeGen for
`generate.sh`, and `complgen` through `cargo`). Vet a tool before you let a
script download it.

## Entry point scripts

| Script | Use |
| --- | --- |
| `build-release.sh` | Build and sign a release app. See [Build](aquaspace.md#build). |
| `install-from-sources.sh` | Build and install. See [Install and update](aquaspace.md#install-and-update). |
| `build-debug.sh` | Debug build. Copies the products `AeroSpaceApp` and `aerospace` to `.debug/`. A debug build refuses to run while another manager runs. |
| `run-debug.sh`, `run-cli.sh` | Build debug, then run the debug app or CLI. |
| `swift-test.sh`, `test.sh` | Upstream test entry points. See [Tests](aquaspace.md#tests). |
| `generate.sh` | Regenerate command metadata and `xcode/AeroSpace.xcodeproj` (XcodeGen). No fork build uses the Xcode project. |
| `script/clean-project.sh` | Clean the project when a build gets into a bad state. |

## Definitions

**SPM.** Swift Package Manager and Swift build tool, the `swift` CLI.

## IDE (from upstream, applies)

- You can [open the project in Xcode](#xcode-from-upstream-partly-applies).
- You can use another editor (Neovim, Vim, Emacs, Sublime, VS Code) with
  [sourcekit-lsp](https://github.com/apple/sourcekit-lsp).

## Xcode (from upstream, partly applies)

Xcode is useful to attach a debugger (you can also use `lldb` in a terminal).

1. Open `Package.swift` (File → Open), not the generated Xcode project. The
   package is lighter.
2. In Edit Scheme → Options → Console, choose `Terminal`. Then macOS asks for
   the Accessibility permission for Terminal. Otherwise it asks again after
   every rebuild, because the debug binary is not signed.

A debug build takes the same controller lock as the release app and refuses to
start while AquaSpace or stock AeroSpace runs. See
[Controller ownership](aquaspace.md#controller-ownership-and-handoff).

## Tips (from upstream, applies)

- Use the built-in Accessibility Inspector app to inspect the accessibility
  properties of windows.
- Use [DeskPad](https://github.com/Stengo/DeskPad) or
  [BetterDisplay 2](https://github.com/waydabber/BetterDisplay) to emulate
  several monitors.
