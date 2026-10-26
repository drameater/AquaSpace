# Development Notes

For current local build/install and safety instructions, see [aquaspace.md](aquaspace.md).
The optional upstream toolchain setup follows:
1. Install dependencies
2. Create codesign certificate in `Keychain Access.app`
3. Run one of the entry point scripts to build/install from sources

For this fork's checks, see [AquaSpace Actions](https://github.com/drameater/AquaSpace/actions).
They run only when manually requested and cover the offline Python regressions.
Swift builds and model regressions remain local checks; hosted CI does not build,
install or launch a window manager.

## Definitions

**SPM.** Swift package manager and Swift build tool. In other words, `swift` CLI tool

## 1. Install dependencies

1.  Install Xcode from App Store https://apps.apple.com/us/app/xcode/id497799835
2.  Install [swiftly](https://github.com/swiftlang/swiftly).
    Swiftly is a Swift toolchain manager that will make sure that you use the same swift version as written in `.swift-version` file.
    `brew install swiftly`
3.  If you want to build shell completion, install rust, bash and fish
    -   Install Rust using rustup. https://www.rust-lang.org/tools/install
    -   `brew install bash fish`
4.  If you want to build man pages, install Ruby >= 3.0. I recommend using [rbenv](https://github.com/rbenv/rbenv).
    -   `rbenv install 3.3.4` (or whatever 3.x version)
    -   Install asciidoctor using Ruby `bundler`. `cd AeroSpace && bundler install`
5.  Install optional `xcbeautify` to make Xcode build logs readable. `brew install xcbeautify`

## 2. Local signing

Run `python3 script/aquaspace_signing.py --setup` once before building. The local
builder reuses that certificate for the app and CLI, preserving the
`local.aquaspace` identity across rebuilds. Ad-hoc signing pins permissions to an
executable hash and is refused. The private key is non-exportable in a dedicated
keychain under `~/Library/Application Support/AquaSpace/Signing`; its password
file and containing directory are accessible only to the current user. Keep this
directory when cleaning build caches. Setup does not alter system trust settings
or grant privacy permissions. An existing certificate can instead be selected
with `--signing-identity` and optionally `--signing-keychain`.
The resource donor defaults to /Applications/AeroSpace.app, retaining the same
logo; pass --base-app to use another verified local resource copy.

## 3. Entry point scripts

- build-release.sh stages a signed bundle at .debug/aquaspace/release/AquaSpace.app.
  It forwards builder options, including --sdk, --version and --base-app;
  --build-version remains an alias for --version. It does not install or launch.
- install-from-sources.sh builds and invokes the serialized AquaSpace updater.
  --dont-rebuild uses the existing staged build. The updater preserves the
  separate stock AeroSpace installation and never launches a rollback manager.
- script/install-aquaspace.py installs a staged bundle. Add --restart to capture
  the sole installed AquaSpace's identity-backed session, stop it, verify exit,
  and start the replacement exactly once. Without --restart it launches nothing.
  Restarts use Launch Services so privacy requests belong to AquaSpace rather
  than the terminal or agent running the installer. Completed backups are
  archives rather than discoverable `.app` bundles. `--archive-backups` cleans
  up older completed transactions without starting or stopping an app.
- build-debug.sh stages AquaSpaceApp and aquaspace in .debug/aquaspace-debug;
  run-debug.sh and run-cli.sh use those products. Debug admission refuses another
  manager rather than taking over stock. Existing .debug evidence is preserved.
- test.sh runs the upstream model suite. The script/test-*-offline.py entry
  points exercise deterministic in-memory regressions without controlling windows.
- generate.sh regenerates command metadata and xcode/AquaSpace.xcodeproj.
  Existing config/command compatibility identifiers intentionally remain.

The inherited Homebrew release/install job, four-runner macOS matrix, nightly
schedule and upstream issue/PR bots are retired in this fork. The sole workflow
uses workflow_dispatch; pushes, pull requests and issue events start no jobs.
It runs the Python suite on one Linux runner with a five-minute limit and cancels
superseded runs on the same ref. Run the same checks locally without installing
dependencies or starting an app:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -v
```

## IDE

-   You can obviously [open the project in Xcode](#xcode).
-   You can use your editor of choice (Neovim, Vim, Emacs, Sublime, VS Code) by using [sourcekit-lsp LSP](https://github.com/apple/sourcekit-lsp).
    I only tested it in Neovim
-   AppCode. The initial codebase was written in AppCode and the IDE was pretty solid.
    But AppCode was unfortunately sunsetted, and it started falling apart.
    Last time I checked it, it didn't support Swift 5.9 features, and I couldn't make it reliably import the project.
    RIP

## Xcode

Even if you use LSP and another text editor, Xcode is still useful to attach debugger (though you can use `lldb` in CLI).

1.  To open the project in Xcode: File -> Open -> Choose `Package.swift` file instead of `xcode/AquaSpace.xcodeproj`.
    It's better to open `Package.swift`, because SPM project is more lightweight.
    `xcode/AquaSpace.xcodeproj` is only used in `*release*.sh` build scripts.
2.  After you opened the project in Xcode.
    Edit Scheme... -> Options -> Console -> Choose `Terminal`.
    This way Accessibility permission will be requested from Terminal.
    If you don't change Console to `Terminal`, Accessibility permission will be requested on every rebuild, because the debug binary is unsigned.

## Tips

- Use built-in "Accessibility Inspector.app" to inspect accessibility properties of windows
- Use [DeskPad](https://github.com/Stengo/DeskPad) or [BetterDisplay 2](https://github.com/waydabber/BetterDisplay) to emulate several monitors
- You can use `script/clean-project.sh` to clean the project when something goes wrong.
