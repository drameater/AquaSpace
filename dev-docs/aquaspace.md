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

The rule: at most one manager moves windows. When AquaSpace is not sure, it
stops controlling windows and exits. It never starts another manager to recover.

| Code | Job |
| --- | --- |
| [WindowManagerController.swift](../Sources/AppBundle/model/WindowManagerController.swift) | The lifecycle: startup admission, claim, peer watch, launch notifications, yield, handoff and stop. It also holds the write gate, `WindowWriteGate`. Every outside effect (exit, report, pause, drain, take over, hand off) is an injected closure, so tests run the real policy without a desktop. |
| [WindowManagerHandoff.swift](../Sources/AppBundle/WindowManagerHandoff.swift) | Builds the one production controller and supplies its effects. Captures, sends and restores sessions. Keeps pending restoration. |
| [WindowManagerAdmission.swift](../Sources/AppBundle/model/WindowManagerAdmission.swift), [WindowControlPermission.swift](../Sources/AppBundle/model/WindowControlPermission.swift) | The controller lock, the ownership phase and the state inside the write gate. |
| [windowManagerProcesses.swift](../Sources/AppBundle/util/windowManagerProcesses.swift), [WindowManagerWatchHealth.swift](../Sources/AppBundle/model/WindowManagerWatchHealth.swift) | The process scan, its cache for the watch, and the log of failed watch scans. |
| [WindowManagerSession.swift](../Sources/AppBundle/model/WindowManagerSession.swift), [PendingWindowRestoration.swift](../Sources/AppBundle/model/PendingWindowRestoration.swift), [WindowDiscoveryInitialization.swift](../Sources/AppBundle/model/WindowDiscoveryInitialization.swift) | The session format, delayed restoration and automatic window setup. |

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
- A process whose path cannot be read is skipped. If its `Info.plist` cannot be
  read, only the executable name decides. This rule stops one unrelated process
  (for example, an app that was replaced on disk while it runs) from stopping
  AquaSpace.
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

The *write gate* (`WindowWriteGate`) holds a `WindowControlPermission` with
three parts: permission to write window positions, permission for automatic
window setup, and a generation ID. A freeze closes both permissions and starts a
new generation, so work that began before the freeze cannot continue after a
later claim. If the last startup step fails, before or after the claim,
AquaSpace stops as in [Any other peer or doubt](#any-other-peer-or-doubt).

### Monitoring

- Every 200 ms AquaSpace scans for peers. A failed scan is retried on the next
  tick. Only a confirmed peer stops control.
- This periodic scan reads only new or changed processes. It remembers which
  processes are not managers, by PID and kernel exec generation (unique process
  ID, exec version and executable image UUID). Any `exec` changes the
  generation, so a process that becomes a manager is read again on the next
  scan. Startup, launch notifications, session export, pending restoration and
  status use a full scan.
- If the `Info.plist` of a process cannot be read, the periodic scan reads it
  again after 1, 2, 4, 8 and 16 scans, then every 25 scans.
- After 25 failed scans in a row (about 5 seconds), AquaSpace writes one entry
  to the system log, and one more when a scan works again. AquaSpace keeps
  control during the failures. The entries use subsystem `local.aquaspace`,
  category `Lifecycle`, as the failure reports do.
- When macOS reports that another app with a manager bundle ID will launch,
  launched or became active, AquaSpace freezes writes at once and scans. If the
  scan confirms a peer, AquaSpace yields. If not, it resumes control. If a
  handoff or a stop already runs, AquaSpace ignores the notification, so a
  failed scan cannot end a handoff.
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
finishes pending AX work and exits with a failure status. The report is
written before the first wait, so it is saved even if a wait never ends. A
second stop while the first one runs does nothing.

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

You need:

- macOS, Python 3 and Git;
- a Swift toolchain that matches [Package.swift](../Package.swift). The Command
  Line Tools are enough. Xcode is not necessary;
- `/usr/bin/sips`, `/usr/bin/iconutil`, `/usr/bin/lipo` and `/usr/bin/codesign`;
- the Swift package dependencies at the versions in `Package.resolved`. The
  builder does not resolve or update dependencies.

The builder uses no other installed app. It checks every tool and the signing
identity before it compiles.

Create the signing identity once:

```sh
python3 script/aquaspace_signing.py --setup
```

This creates a persistent local code-signing certificate in a dedicated
keychain under `~/Library/Application Support/AquaSpace/Signing`. macOS ties
privacy permissions (Accessibility) to the signing identity. A stable identity
lets a grant survive rebuilds. Keep that folder when you clean caches. Setup
does not change system trust settings, and passes the keychain passwords on
standard input, not on the command line.

- A complete identity is reused unchanged.
- An interrupted setup has no certificate yet, so no build used it. Setup moves
  its files to an `incomplete-<date>-<time>` folder and starts again.
- If the certificate is present but the keychain or its password file is
  missing, setup stops. It prints the commands to make a new identity. A new
  identity needs a new Accessibility grant.

Build:

```sh
./build-release.sh            # or: python3 script/build-aquaspace.py
```

The builder:

1. Derives the version from Git. The short version is
   `<base>-aquaspace.<commits>.g<commit>`, for example
   `0.21.3-aquaspace.71.g79e53f21`. `<base>` comes from the nearest tag
   `v<major>.<minor>.<patch>...` before `HEAD`, and `<commits>` is the number of
   commits since that tag. Without a tag, the version is
   `0.21.3-aquaspace.g<commit>`. If a build input has uncommitted changes or
   untracked files, the version ends with `.dirty`. Build inputs are `Package.swift`,
   `Package.resolved`, `Sources/`, `script/aquaspace-Info.plist`, the icon image
   and `docs/config-examples/default-config.toml`.
2. Sets `CFBundleVersion` to the commit time in UTC (`YYYYMMDDhhmmss`). A dirty
   build uses the build time instead. So a newer commit or a later dirty build
   always gets a higher number.
3. Copies `Package.swift`, `Package.resolved` and `Sources/` to
   `.debug/aquaspace/source` and writes the version and Git hash files there.
4. Runs `swift build -c release` for the products `AeroSpaceApp` and
   `aerospace`, with 2 jobs and with dependency resolution and updates
   disabled. The build cache is `.debug/aquaspace/build`.
5. Assembles `AquaSpace.app` in a temporary `.staging-*` folder in the output
   folder:
   - `Contents/MacOS/AquaSpace` (the `AeroSpaceApp` product) and
     `Contents/MacOS/aquaspace-cli` (the `aerospace` product);
   - the alias `Contents/Helpers/aerospace`, a symlink to `../MacOS/aquaspace-cli`;
   - `Contents/Info.plist` from `script/aquaspace-Info.plist`, with bundle ID
     `local.aquaspace`;
   - `Contents/Resources/AppIcon.icns`, made with `sips` and `iconutil` from
     `resources/Assets.xcassets/AppIcon.appiconset/icon.png`. The bundle has no
     `Assets.car`; `Info.plist` names only this icon file;
   - `Contents/Resources/default-config.toml`.
6. Validates the bundle before and after signing: the exact file list, the
   bundle ID, executable and version in `Info.plist`, the helper symlink, the
   architecture of this Mac, and the version and Git hash inside both
   executables.
7. Signs the CLI and the app with the persistent identity and refuses ad-hoc
   signing. The designated requirement must name `local.aquaspace` and a
   certificate. Both signatures must pass `codesign --verify --strict`.
8. Publishes the result. It replaces `.debug/aquaspace/release/AquaSpace.app`
   with the new bundle, then writes `build.json` next to it: version, bundle
   version, Git revision, dirty flag, source hashes, app and CLI hashes, and
   designated requirement. If any step fails, the previous build stays.

If the builder stops between the app and `build.json`, the old manifest no
longer matches the app. The installer then refuses the build; build again.

It does not install or start anything unless you pass `--install-and-restart`.

| Option | Use |
| --- | --- |
| `--sdk <path>` | Build with this macOS SDK. |
| `--scratch-path <path>`, `--output <path>` | Other build cache and output folders. |
| `--version <text>` | Replace the short version. `CFBundleVersion` stays derived from Git. |
| `--jobs <n>` | Compiler jobs (default 2). |
| `--disable-sandbox` | See below. |
| `--signing-identity <id>`, `--signing-keychain <path>` | Sign with another persistent identity. `-` (ad hoc) is refused. |
| `--install-and-restart` | After the build, run the installer with `--restart`. The installer takes over the process, so its [exit status](#exit-status) is the builder's. |
| `--allow-identity-change`, `--ready-timeout <seconds>` | Installer options. They need `--install-and-restart`, and the builder checks them before it compiles. |

`build-release.sh` passes all options on and accepts `--build-version` as a
name for `--version`.

SwiftPM evaluates `Package.swift` inside a macOS sandbox. Some sandboxes do not
allow a nested sandbox. If you run the builder inside such a sandbox, for
example the command sandbox of a coding agent, `swift build` fails. Only then,
pass `--disable-sandbox`.

On the owner's machine the macOS 27 Command Line Tools SDK lacks the SwiftUI
macro plugin, so builds use
`--sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`.

Debug build: `./build-debug.sh` needs Xcode (for XCTest), swiftly and Bash 5.
Without XCTest it stops at once with a message. It copies the products
`AeroSpaceApp` and `aerospace` to `.debug/`. A debug build refuses to run while
any other manager runs.

## Install and update

`script/install-aquaspace.py` installs a staged build. The code is in
[aquaspace_update.py](../script/aquaspace_update.py). Run
`python3 script/install-aquaspace.py --help` for the full list of options.

| Command | What it does |
| --- | --- |
| `./install-from-sources.sh` | Build, then install with `--restart`. Accepts build options and the two installer options below. |
| `./install-from-sources.sh --dont-rebuild` | Install the staged build with `--restart`. Accepts only the two installer options below. |
| `python3 script/install-aquaspace.py --restart` | Replace the running AquaSpace, or install when no manager runs. Starts the new AquaSpace once. |
| `python3 script/install-aquaspace.py` | Install files only. All managers must be stopped. Starts nothing. |
| `... --allow-running-stock` | File-only install while the one stock AeroSpace (`/Applications/AeroSpace.app`) keeps running. The installer checks that stock's identity does not change and never signals it. Not with another mode. |
| `... --allow-identity-change` | Accept a build signed with another identity. See [Signing identity change](#signing-identity-change). |
| `... --ready-timeout <seconds>` | How long to wait for the new AquaSpace to take control: more than 0, at most 3600, default 30. With `--restart`, `--resume` or `--confirm`. |
| `... --resume <record>`, `--confirm <record>`, `--recover <record>` | Finish an unfinished update. See [Recovery](#recovery). |
| `... --archive-backups` | Tidy the update records (see below). Starts and stops nothing. |

`--source` (default `.debug/aquaspace/release/AquaSpace.app`) and `--target`
(default `/Applications/AquaSpace.app`) select other bundles.

### First installation

1. Quit every window manager, including stock AeroSpace. The installer refuses
   to start while a manager other than the installed AquaSpace runs.
2. Run `./install-from-sources.sh`, or `./build-release.sh` and then
   `./install-from-sources.sh --dont-rebuild`.
3. The installer starts AquaSpace once. AquaSpace waits for the Accessibility
   permission before it controls windows. Allow AquaSpace in System Settings >
   Privacy & Security > Accessibility (called *Device Control and Data Access*
   on some macOS versions). The installer waits up to the ready timeout
   (30 seconds by default) and tells you when it starts to wait.
4. If you allow it in time, the installer finishes with exit status 0.
   Otherwise it stops with exit status 3, leaves AquaSpace running and prints a
   `--confirm` command. Allow AquaSpace, then run that command. `--confirm`
   stops and starts nothing. It waits again for AquaSpace to take control, then
   completes the update.

### Updates

`./install-from-sources.sh` builds and replaces the running AquaSpace. The
installer:

1. Takes the [install lock](#install-lock). A second installer stops.
2. [Tidies the update records](#update-records). It refuses if an earlier
   update is unfinished and prints the next command for it.
3. Verifies the staged app and CLI signatures and refuses a hash-bound (ad-hoc)
   identity. Refuses if any manager other than the installed AquaSpace runs.
4. Compares the signing identity of the build with the installed app. See
   [Signing identity change](#signing-identity-change).
5. Creates the update record `/Applications/.aquaspace-update-<id>/` with a
   journal. Each phase is written to the journal before it starts. Copies the
   build into the record and verifies the copy again. If the build has a
   `build.json`, the copy's hashes, version and designated requirement must
   match it.
6. Asks the running AquaSpace for its status and session through the installed
   CLI, and checks that the CLI belongs to the running process. Saves the
   session in the record.
7. Stops the old AquaSpace with `SIGKILL` after a fresh identity check. This
   skips the normal quit path, which moves windows. It waits up to 5 seconds
   for the process to exit.
8. Exchanges the new and the old bundle in one atomic step. The old bundle
   goes into the record as `previous.app`. A volume without atomic exchange
   uses two renames; `--resume` can finish them.
9. Starts the new app once through Launch Services (`open -n -g ...
   --args --restore-session <file>`), so macOS gives the privacy request to
   AquaSpace and not to the terminal. Its output goes to `app.log` in the
   record. The installer checks that exactly one new manager runs from the
   installed path, then waits up to the ready timeout for it to report control.
10. Marks the update complete and archives the old app as
    `previous.app.tar.gz`.

Only `--restart` and `--resume` start an app, and each starts the new
AquaSpace at most once. No step starts an old copy, a rollback app or a
watchdog. If a step fails, the installer names the record, says what state
remains, and prints the next command. If you press Ctrl-C before the old
AquaSpace is stopped, the installer closes the record as aborted.

### Signing identity change

If the build is signed with another identity than the installed app, the
installer stops before it changes anything. It prints both designated
requirements and an installer command with `--allow-identity-change` added.
`./install-from-sources.sh` also accepts that option. macOS ties
the Accessibility permission to the identity, so after that change AquaSpace
waits for a new grant: allow it, then run the `--confirm` command that the
installer prints. If the change is not intended, rebuild with the usual
identity. `python3 script/aquaspace_signing.py` shows it.

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Stopped. The message says what state remains and which command comes next. |
| 2 | Invalid arguments. |
| 3 | The new AquaSpace runs but did not take control in time. Run the `--confirm` command shown. |
| 130 | Interrupted. The message says which command comes next. |

### Install lock

Every installer run takes an exclusive lock on
`~/Library/Application Support/AquaSpace/Updater/.aquaspace-install-<uid>.lock`.
Updaters before 2026-11 locked `/tmp/.aquaspace-install-<uid>.lock` or
`/Applications/.aquaspace-install-<uid>.lock`. If you own one of these files,
the installer locks it too, so an older updater that still runs cannot overlap.
The installer never deletes a lock file.

### Update records

Each install keeps a record folder `/Applications/.aquaspace-update-<id>/`.
Before each install, and with `--archive-backups`, the installer tidies the
records:

- A record in the phase `staging` or `prepared` is closed as aborted. In these
  phases no process was stopped and no file was replaced, and the installer
  that wrote the record has ended.
- In completed records (committed, recovered or aborted), the unused copy of
  the new build is deleted, and `previous.app` and `unconfirmed.app` become
  `.tar.gz` archives. Live `.app` backups can show up in Privacy settings.
- Only the 5 newest completed records stay. **Older completed records are
  deleted, with their archived backups.** Unfinished records are never deleted.

## Recovery

- **AquaSpace stopped or will not start.** Read
  `~/Library/Logs/AquaSpace/last-failure.txt`. It names the refusal: a
  competing manager, an unreadable process scan, or the controller lock. Resolve
  that cause first. Resetting the Accessibility permission does not fix a scan
  or lock failure.
- **An update stopped.** An unfinished update blocks every later install. The
  installer prints the commands for the state of the record, with full paths.
  Copy them from its message. The table explains them.

| State in the message | Next command |
| --- | --- |
| No process was stopped and no file was replaced. | `--recover <record>` closes the record. Then run the update again. |
| The updater asked the old AquaSpace to stop. | If a manager still runs, nothing was replaced: `--recover <record>` closes the record. If no manager runs: `--resume <record>`. |
| The new AquaSpace was not started. | `--resume <record>`: completes the file exchange and starts the new AquaSpace once. No manager may run. |
| The new AquaSpace may or may not have started. | If it runs: `--confirm <record>`. If no manager runs: wait 30 seconds after the start attempt, then `--resume <record>`. `--resume` refuses earlier. |
| The new AquaSpace was started (exit status 3). | If it runs: allow it in Accessibility settings, then `--confirm <record>`. If no manager runs: `--resume <record>`. |

`--confirm` stops and starts nothing. It completes the update only if the one
running manager is the AquaSpace that this update started, from the installed
bundle of this update. `--resume` and `--recover` close a record in which no
process was stopped. Otherwise they refuse while a manager runs. If that
manager is the AquaSpace from this update, they print the `--confirm` command.

**Restore the previous version** instead of finishing an update: quit all
managers, then run

```sh
python3 script/install-aquaspace.py --recover /Applications/.aquaspace-update-<id>
```

`--recover` restores files only and starts nothing. The new bundle stays in the
record as `unconfirmed.app`. It refuses if the installation changed after that
update. Then start AquaSpace by hand (`open /Applications/AquaSpace.app`). After
a first installation there is no previous version: `--recover` closes the record
and leaves the new files in place.

## Tests

Run the checks that match your change. Read a test before you run it for the
first time.

### Offline Python suite

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -v
```

This is the suite that CI runs. It needs only Python 3 and Git. It covers the
builder, the updater and its transaction, signing checks, session identity
checks, the shell wrappers, the opt-in guards of the live harnesses, the
Ghostty harness safety guards, some source-text guards, and the relative links
and heading anchors in every tracked Markdown file. The tests use fakes for
`swift`, `codesign`, the macOS tools and the process scan. The updater tests
block `subprocess`, `os.kill` and native library loading. The shell-wrapper
tests run Bash with a fake `python3` in a temporary folder. The link test runs
`git ls-files`.

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
Testing files in `Sources/AppBundleTests`. It needs Xcode for XCTest, swiftly
and Bash 5, and stops at once with a message when XCTest is missing.
`./test.sh` also builds, lints and regenerates files.

### Live harnesses

These act on the real desktop. Run them only with explicit authorization, on an
idle desktop, and on dedicated test workspaces. Each one refuses to start
without its opt-in flag.

| Harness | What it does |
| --- | --- |
| `script/installer-smoke/live.py --run-live` | Real updates, `--confirm`, `--resume` and `--recover` cases against `/Applications/AquaSpace.app`. Restarts AquaSpace and prunes old update records. Not yet run against the updater of 2026-11. |
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
- **Build machine.** The builder uses the dependency versions in
  `Package.resolved` and does not resolve or update them.
- **CI.** CI runs only the Python suite. Swift builds and tests run only on a
  local Mac.
