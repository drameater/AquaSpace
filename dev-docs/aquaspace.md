# AquaSpace guide

AquaSpace is a personal fork of [AeroSpace](https://github.com/nikitabobko/AeroSpace),
a tiling window manager for macOS. This guide describes the fork as the code
works now. It covers what the fork changes, how it keeps one window manager in
control, and how to build, install, update, recover and test it.

Other documents:

- [native-tabs.md](native-tabs.md): design and limits of native tab support.
- [upstream-sync.md](upstream-sync.md): how to follow upstream AeroSpace.
- [development.md](development.md): editor and toolchain notes.
- [history/](history/README.md): dated validation and diagnosis records. Read
  these for evidence. They describe the code as it was on each date.

Terms used in this guide:

- *Stock AeroSpace*: the upstream app, `/Applications/AeroSpace.app`, bundle ID
  `bobko.aerospace`.
- *Manager*: any process that controls windows. AquaSpace and stock AeroSpace
  are both managers. Their debug and unbundled builds are managers too.
- *Controller*: the one manager that may move windows now.
- *Session*: the workspace assignment, layout, floating and fullscreen state of
  each window, the root layout of each workspace, and the focused workspace and
  window. Managers pass a session to each other.

## What the fork changes

| Area | Change | Main code |
| --- | --- | --- |
| Native tabs | A native macOS tab group keeps one tiling slot. Selecting a tab keeps the group on its workspace. Merges and detaches are handled. | [native-tabs.md](native-tabs.md) |
| One controller | A lock, a process scan and a write gate make sure that only one manager controls windows. | [Controller ownership](#controller-ownership-and-handoff) |
| Session transfer | AquaSpace takes the session from stock AeroSpace at startup and gives it back if stock AeroSpace starts later. | `WindowManagerHandoff.swift` |
| Delayed restoration | Session rows for windows that are not visible yet are kept, with proof of the owning process, and applied when the window appears. | `PendingWindowRestoration.swift` |
| Failure reports | A refusal or failure writes a readable report before AquaSpace exits. | `WindowManagerFailureReport.swift` |
| Build, sign, install | Local Python scripts build, sign with a persistent certificate, and replace the running app in one ordered transaction. | `script/build-aquaspace.py`, `script/aquaspace_update.py` |
| Identity | App `AquaSpace.app`, bundle ID `local.aquaspace` (debug: `local.aquaspace.debug`), CLI `aquaspace-cli`. | `Sources/Common/appMetadata.swift` |
| CI | GitHub Actions runs only when started by hand. It runs the Python suite on Linux. | `.github/workflows/build.yml` |

What stays the same as stock AeroSpace:

- The config file: `~/.aerospace.toml` or
  `$XDG_CONFIG_HOME/aerospace/aerospace.toml`. Commands and key bindings.
- The callback environment variables (`AEROSPACE_*`).
- The icon and the internal Swift module names (for example
  `Sources/AppBundle`).

Each manager has its own command socket: `/tmp/local.aquaspace-<user>.sock` for
AquaSpace and `/tmp/bobko.aerospace-<user>.sock` for stock AeroSpace. The
`aerospace` CLI controls stock AeroSpace. The `aquaspace-cli` in the app bundle
controls AquaSpace. The builder puts an `aerospace` alias for `aquaspace-cli` in
`AquaSpace.app/Contents/Helpers`. AquaSpace adds that folder to the start of
`PATH` for its own config callbacks only, so `aerospace` commands in the config
reach AquaSpace. Scripts outside the config must call the CLI of the manager
that they mean. Nothing adds `aquaspace` to your shell `PATH`.

> An older record says that AquaSpace adds `/opt/homebrew/bin/aquaspace`. The
> current builder and installer create no such link.

## Controller ownership and handoff

This section describes the safety model in
[WindowManagerHandoff.swift](../Sources/AppBundle/WindowManagerHandoff.swift),
[WindowManagerAdmission.swift](../Sources/AppBundle/model/WindowManagerAdmission.swift),
[windowManagerProcesses.swift](../Sources/AppBundle/util/windowManagerProcesses.swift),
[PendingWindowRestoration.swift](../Sources/AppBundle/model/PendingWindowRestoration.swift)
and [WindowDiscoveryInitialization.swift](../Sources/AppBundle/model/WindowDiscoveryInitialization.swift).

The rule: at most one manager moves windows. When AquaSpace is not sure, it
stops controlling windows and exits. It never starts another manager to recover.

### Identifying managers

The process scan reads the kernel's executable path and the bundle `Info.plist`
of each process. It does not trust process names or startup arguments.

- A process is a manager if its executable is named `AquaSpace`,
  `AquaSpaceApp`, `AeroSpace` or `AeroSpaceApp`, or if it is the main executable
  of a bundle with ID `local.aquaspace`, `local.aquaspace.debug`,
  `bobko.aerospace` or `bobko.aerospace.debug`. A CLI inside a manager bundle is
  not a manager.
- For a manager candidate, the scan reads the process birth time (PID, user,
  start time) before and after it reads the path. Both reads must agree, so a
  reused PID cannot pass.
- A process whose path or `Info.plist` cannot be read is skipped. It cannot be a
  known manager. This rule stops one unrelated process (for example, an app that
  was replaced on disk while it runs) from stopping AquaSpace.
- A scan that fails as a whole is an error, not an empty result.

### Startup

1. **Lock.** Every writable build (release, debug, unbundled) takes the
   controller lock `/tmp/.aquaspace-controller-<uid>.lock` with a non-blocking
   exclusive `flock`. The lock refuses symlinks, files of other users and files
   with extra hard links. A second AquaSpace cannot take the lock. Read-only
   runs take no lock.
2. **Scan for peers.** A debug build refuses any other manager. A release build
   accepts only stock AeroSpace release processes, and only to take over from
   them.
3. **Take over from stock (release only).** AquaSpace reads the session from
   stock AeroSpace through stock's socket, together with the process identity
   of each window's app. It reads the window list again at the end and refuses
   if it changed. It scans again and requires the same stock processes, then
   stops them with `SIGKILL` and waits up to 2 seconds for an empty scan.
4. **Claim.** AquaSpace claims ownership only when it holds the lock and the scan
   is empty. Then it opens the write gate and starts monitoring.
5. **Restore.** AquaSpace applies the session: workspace moves, root layouts,
   floating and fullscreen flags, then the focused workspace and window. Rows
   for windows that are not visible yet stay pending (see below).

The *write gate* (`WindowControlPermission`) has three parts: permission to
write window positions, permission for automatic window setup, and a generation
ID. A freeze closes both permissions and starts a new generation, so work that
began before the freeze cannot continue after a later claim.

### Monitoring

- Every 200 ms AquaSpace scans for peers. A failed scan is retried on the next
  tick. Only a confirmed peer stops control.
- When macOS reports that another app with a manager bundle ID will launch,
  launched or became active, AquaSpace freezes writes at once and scans. If the
  scan confirms a peer, AquaSpace yields. If not, it resumes control.
- Yielding cannot be undone. A yielded AquaSpace never takes control again; a
  new AquaSpace process must start.

### Handing off to stock AeroSpace

If the only peer is one stock AeroSpace release process, AquaSpace hands its
session to it:

1. Yield, freeze writes, and pause window management. The pause stops the
   normal layout path, which would unhide every window.
2. Save the current session and finish pending AX work. Wait up to 8 seconds
   for stock AeroSpace to answer on its socket and list all saved windows.
   After 8 seconds, continue with the windows that stock lists.
3. Send the restoration commands to stock AeroSpace and exit without the normal
   quit path, which would move windows again.

If any step fails, AquaSpace writes a failure report and exits. It never stops
or restarts stock AeroSpace, takes over its socket, or enables itself again.

There is no command or menu item to switch from AquaSpace to stock AeroSpace.
Start stock AeroSpace to switch; the switch happens through the steps above.
Open AquaSpace normally to switch back.

### Any other peer or doubt

A debug build, an unbundled build, another AquaSpace, several peers, or a
failed admission check stops control: AquaSpace yields, clears pending
restoration, freezes writes, pauses management, writes the failure report,
finishes pending AX work and exits with a failure status.

### Pending restoration

When a session arrives, some windows can be hidden, minimized, on another macOS
Space, or not yet seen. `PendingWindowRestoration` keeps their rows. Each row
must name the exact process that owned the window when the session was saved:
PID, user, start time, executable path, exec generation and image UUID.

- A row is dropped when the window ID now belongs to another process, when the
  process identity changed, or when the user changed or focused the window, or
  brought its app to the front, before the row was applied.
- A row is applied only while AquaSpace controls windows and after a fresh AX
  read proves the window root. AquaSpace scans for peers again after each read
  that can wait.
- A confirmed native tab transfer and every explicit user command cancel the
  rows of the windows involved.
- An update carries the remaining rows into the next session.

### Automatic window setup

A newly registered window gets an automatic setup: restoration from the
closed-window cache or the `on-window-detected` callbacks.
`WindowDiscoveryInitialization` ties each setup to one exact window object, the
control generation and a focus token. The setup stops when the user changes that
window or focus, when the window object is replaced, or when the write gate
freezes. A later automatic step cannot undo newer user input. Pending
restoration waits until the setup of its window has finished.

### Failure reports

AquaSpace writes `~/Library/Logs/AquaSpace/last-failure.txt` (folder mode 700,
file mode 600) before it exits on a failure. The report gives the phase
(`startup`, `monitoring` or `handoff`), version, executable, PID and the chain
of error reasons. AquaSpace also writes the text to stderr and to the system
log without redaction. Writing the report has no other effect: it does not
retry, show a dialog, change focus or start a manager.

### Status

`aquaspace-cli management-status` prints JSON: PID, phase (`preparing`,
`controlling` or `yielding`), `controlsWindows`, `readOnly`, peer PIDs,
`inventoryVerified` and `sessionSchema`. `aquaspace-cli handoff-session` prints
the session with owner identities; it works only while AquaSpace has exclusive
control. The updater uses both.

## Build

<!-- UPDATE-AFTER-INTEGRATION: build -->

You need macOS, Python 3, Git, a Swift toolchain that matches
[Package.swift](../Package.swift), and a macOS SDK that builds the app.
Keep `/Applications/AeroSpace.app` installed: the builder copies its icon and
bundle resources.

Create the signing identity once:

```sh
python3 script/aquaspace_signing.py --setup
```

This creates a persistent local code-signing certificate in a dedicated
keychain under `~/Library/Application Support/AquaSpace/Signing`. macOS ties
privacy permissions (Accessibility) to the signing identity. A stable identity
lets a grant survive rebuilds. Keep that folder when you clean caches. Setup
reuses an existing identity unchanged and does not change system trust
settings.

Build:

```sh
./build-release.sh            # or: python3 script/build-aquaspace.py
```

The builder:

- copies `Package.swift`, `Package.resolved` and `Sources/` to
  `.debug/aquaspace/source` and writes the version and Git hash files there;
- runs `swift build -c release` with dependency resolution and updates
  disabled (dependencies must already be cached) and 2 jobs by default;
- copies `/Applications/AeroSpace.app` as the base, replaces its executables
  with `AquaSpace` and `aquaspace-cli`, adds the `Contents/Helpers/aerospace`
  alias, and sets the bundle ID `local.aquaspace`;
- signs with the persistent identity and refuses ad-hoc signing;
- writes the app to `.debug/aquaspace/release/AquaSpace.app` and a manifest
  `build.json` (version, Git revision, source and executable hashes, designated
  requirement).

It does not install or start anything unless you pass `--install-and-restart`.

Options: `--sdk <path>`, `--scratch-path`, `--output`, `--base-app` (another
resource donor), `--version` (default `0.21.3-aquaspace.<date>.<time>`),
`--jobs`, `--signing-identity` and `--signing-keychain`. `build-release.sh`
passes all options on and accepts `--build-version` as a name for `--version`.

On the owner's machine the macOS 27 Command Line Tools SDK lacks the SwiftUI
macro plugin, so builds use
`--sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`.

Debug build: `./build-debug.sh` writes `AquaSpaceApp` and `aquaspace` to
`.debug/aquaspace-debug`. A debug build refuses to run while any other manager
runs.

## Install and update

<!-- UPDATE-AFTER-INTEGRATION: install-and-update -->

`script/install-aquaspace.py` installs a staged build. The code is in
[aquaspace_update.py](../script/aquaspace_update.py).

| Command | What it does |
| --- | --- |
| `./install-from-sources.sh` | Build, then install with `--restart`. Build options pass through. |
| `./install-from-sources.sh --dont-rebuild` | Install the staged build with `--restart`. Refuses build options. |
| `python3 script/install-aquaspace.py --restart` | Replace the sole running AquaSpace and start the new one once. |
| `python3 script/install-aquaspace.py` | Install files only. All managers must be stopped. Starts nothing. |
| `... --allow-running-stock` | File-only install while the one stock AeroSpace keeps running. The installer checks that stock's identity does not change and never signals it. Not with `--restart` or `--recover`. |
| `... --archive-backups` | Turn bundles from completed updates into `.app.tar.gz` archives. Starts and stops nothing. |
| `... --recover <dir>` | See [Recovery](#recovery). |

First installation: stop any running manager, then run
`./install-from-sources.sh --dont-rebuild` after a build. Grant Accessibility
(called *Device Control and Data Access* on some macOS versions) to AquaSpace
once.

An update with `--restart`:

1. Takes the install lock `/Applications/.aquaspace-install-<uid>.lock`. A
   second installer stops.
2. Refuses if an earlier update is unfinished. Archives the backups of
   completed updates.
3. Verifies the staged app and CLI signatures and refuses a hash-bound (ad-hoc)
   identity. Refuses if any manager other than the sole installed AquaSpace
   runs.
4. Creates `/Applications/.aquaspace-update-<id>/` with a journal. Each phase is
   written to the journal before it starts. Copies the build into the folder,
   verifies the copy again, and checks its hashes against `build.json`.
5. Asks the running AquaSpace for its status and session, and checks that the
   CLI belongs to the running process. Saves the session in the folder.
6. Stops the old AquaSpace with `SIGKILL` after a fresh identity check. This
   skips the normal quit path, which moves windows. It waits for the process to
   exit.
7. Moves the old app into the update folder and moves the new app into place.
8. Starts the new app once through Launch Services (`open -n -g ...
   --restore-session <file>`), so macOS gives the privacy request to AquaSpace
   and not to the terminal. It then checks that exactly one new manager runs
   from the installed path and that it reports control.
9. Marks the update complete and archives the old app as `previous.app.tar.gz`.

There is one launch site. No step starts an old copy, a rollback app or a
watchdog. If a step fails, the installer reports the update folder and stops.

## Recovery

<!-- UPDATE-AFTER-INTEGRATION: recovery -->

- **AquaSpace stopped or will not start.** Read
  `~/Library/Logs/AquaSpace/last-failure.txt`. It names the refusal: a
  competing manager, an unreadable process scan, or the controller lock. Resolve
  that cause first. Resetting the Accessibility permission does not fix a scan
  or lock failure.
- **An update failed.** The error names the update folder. An unfinished update
  blocks every later install until you deal with it. If the failure happened
  before any process was signaled, the update is marked aborted and you can
  retry.
- **Restore the previous files.** Stop all managers, then run:

  ```sh
  python3 script/install-aquaspace.py --recover /Applications/.aquaspace-update-<id>
  ```

  Recovery restores files only and starts nothing. It refuses if the
  installation changed after that update. Then start the manager you want by
  hand.
- **Old backups.** Run `--archive-backups` to turn completed backups into
  archives. Live `.app` backups can show up in Privacy settings.

## Tests

Run the checks that match your change. Read a test before you run it for the
first time.

### Offline Python suite

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -v
```

This is the suite that CI runs. It needs only Python 3. It covers the updater
and its transaction, signing checks, session identity checks, the shell
wrappers, the Ghostty harness safety guards, and some source-text guards. The
tests use fakes and block `subprocess`, `os.kill` and native library loading.
The shell-wrapper tests run Bash with a fake `python3` in a temporary folder.

`test_aquaspace_lifecycle_source_guards.py` reads Swift source instead of
running it. Each of its tests names the Swift behavior test that should replace
it.

### Offline Swift replays

Each runner copies the production sources it needs into an isolated Swift
package under `.debug/<name>/` and runs `swift test`. No app starts. They need a
Swift toolchain, and the `swift test` runners need cached package dependencies
(resolution is disabled).

| Runner | Covers |
| --- | --- |
| `script/test-ownership-offline.py` | Admission, controller lock, process scan, failure reports. Native process calls are replaced by stubs that abort. |
| `script/test-native-tabs-offline.py` | The native tab state machine and matchers with in-memory AX. |
| `script/test-native-tab-model-offline.py [--sdk <path>]` | Native tab bindings against the real in-memory tree. |
| `script/test-hidden-handoff-offline.py` | Hidden-window discovery and session commands with in-memory WindowServer and AX data. |
| `script/test-window-discovery-offline.py` | Stock versus fork discovery, deferred restoration and callback ordering. Needs the local tag `v0.21.3-Beta`. |
| `script/test-ghostty-{foreground,target,termination}-offline.py` | The Ghostty harness native helper against fake APIs. Uses `clang`. |

### Upstream test target

`./swift-test.sh` runs `swift test` on the package, including the fork's Swift
Testing files in `Sources/AppBundleTests`. Upstream's XCTest cases need Xcode.
`./test.sh` also builds, lints and regenerates files.

### Live harnesses

These act on the real desktop. Run them only with explicit authorization, on an
idle desktop, and on dedicated test workspaces. Each one refuses to start
without its opt-in flag.

| Harness | What it does |
| --- | --- |
| `script/installer-smoke/live.py --run-live` | Real updates and failure/recovery cases against `/Applications/AquaSpace.app`. Restarts AquaSpace. |
| `script/native-tabs-smoke/ghostty-topology/live.py --run-live` | Real Ghostty merges and detaches through the running AquaSpace. See its [README](../script/native-tabs-smoke/ghostty-topology/README.md). |
| `swift run --package-path script/native-tabs-smoke NativeTabSmoke` | AppKit tab groups in a test process that reads only its own AX tree. |

`script/native-tabs-smoke/prepare-ghostty-live.py`, `AppKitFixture.swift` and
`ghostty-driver.applescript` are kept from earlier harnesses. No current
harness uses them.

## Known limits

- **Nested layout across managers.** A session holds each window's workspace,
  its own layout value, floating and fullscreen flags, and each workspace's
  root layout. Nested containers and their weights are rebuilt, not copied.
- **Stock AeroSpace started from outside.** An unmodified stock AeroSpace does
  not take AquaSpace's lock. AquaSpace detects it through the launch
  notification (at once) or the 200 ms poll (for processes without a bundle).
  Between its start and that detection, both can act.
- **Windows not seen in time.** Windows on another macOS Space or hidden at
  startup are restored only when AquaSpace sees them later and can prove their
  process. Rows that lose that proof are dropped, not guessed.
- **Native tabs.** See [native-tabs.md](native-tabs.md#known-limits).
- **Build machine.** The builder needs the stock app as a resource donor and
  cached Swift package dependencies.
- **CI.** CI runs only the Python suite. Swift builds and tests run only on a
  local Mac.
