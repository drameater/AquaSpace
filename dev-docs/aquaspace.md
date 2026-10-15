# AquaSpace local fork

AquaSpace installs as `/Applications/AquaSpace.app` with bundle identifier `local.aquaspace`. AeroSpace remains `/Applications/AeroSpace.app` with its original `bobko.aerospace` identity. AquaSpace uses the same icon and assets, `~/.aerospace.toml` / `${XDG_CONFIG_HOME}/aerospace/aerospace.toml`, bindings, and `AEROSPACE_*` environment variables.

Open AquaSpace normally to switch from AeroSpace. To switch back, choose **Switch to AeroSpace** in AquaSpace's menu or run `aquaspace switch-to-aerospace`. This freezes and drains AquaSpace's window writes before opening AeroSpace. Opening AeroSpace directly also triggers a one-way exit through launch notifications and process detection, but an unmodified external application can start before that notification is delivered; use the explicit switch for ordered control. AquaSpace stops stock AeroSpace before taking over. Session transfer preserves workspace assignments, root layouts, floating/fullscreen settings, and focus. Nested tiling containers and their exact weights are rebuilt when changing managers.

The original `aerospace` CLI is restored unchanged. AquaSpace adds `/opt/homebrew/bin/aquaspace`, pointing to `AquaSpace.app/Contents/MacOS/aquaspace-cli`. The apps use separate command sockets: the stock CLI controls stock AeroSpace and the fork's CLI controls AquaSpace. AquaSpace prefixes its own callbacks' PATH with a bundled `aerospace` compatibility alias, so relative `aerospace` invocations in the existing config use the fork. The user's shell PATH and original executable remain unchanged; external scripts and absolute CLI paths must explicitly choose the appropriate manager. Only one manager should control windows.

## Validation on 2026-10-14

The user authorized rebuilding and testing. Release `0.21.3-aquaspace.20261014.2` is installed and running at `/Applications/AquaSpace.app`. Original AeroSpace and its Homebrew CLI remain restored alongside it. The installed executable matches the final-source build manifest.

All 26 headless regressions passed: 20 native-tab cases, four ownership/session cases, and two socket lifecycle cases. A scoped release candidate passed 11 background checks against real AppKit tabs, including cross-workspace group merging, selected/inactive detachment, and preservation of the destination workspace. The handoff target was a simulated IPC peer: it verified that ownership was yielding and window writes were disabled before target launch and received the restoration commands. Stock AeroSpace was not launched for this test. The current manager was paused during each scoped run and resumed afterward; workspace 3 and existing window assignments were preserved. The first fixture incorrectly prevented main-window status, exposing nonstandard AX subroles; restoring standard-window status while continuing to prevent keyboard focus resolved that harness issue.

The upgrade installer then incorrectly omitted processes with startup arguments from its process check. This triggered a rollback that briefly started an additional old AquaSpace process. That process was stopped, the verified new bundle was returned to Applications without another launch, and sole control plus all seven surviving window assignments and floating/fullscreen settings were verified. The throwaway installer is retired; the tracked replacement and its offline validation are described below. Runtime ownership checks and scoped test reports are saved locally under `.debug/aquaspace/`.

The revised source uses separate IPC endpoints, an atomic write gate, and a drain of pending AX work before an explicit handoff launch. Yielding is irreversible. A failed handoff cannot kill/restart the target, rebind its socket, or re-enable AquaSpace. Launch notifications close the write gate immediately; app-unhide callbacks also check ownership. Closed socket reads fail on EOF, and canceled connection setup resumes its waiter.

The distinct application identity can require a separate ordinary Accessibility / Device Control and Data Access grant. No root, SIP change, injection, or screen-recording permission is added.

## Build locally

```sh
python3 script/build-aquaspace.py \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
```

The SDK argument is optional when the default SDK builds the project. On this machine, the macOS 27 Command Line Tools SDK lacks the SwiftUI State macro plugin, so macOS 26.5 is used. The script builds an optimized arm64 app and matching CLI, copies the icon/resources from the installed AeroSpace app, signs locally, and stages `.debug/aquaspace/release/AquaSpace.app`. It installs and restarts only with the explicit `--install-and-restart` option. `--base-app`, `--scratch-path`, `--output`, and `--version` customize the build. `build.json` records the source and executable hashes. Full upstream XCTest requires Xcode; the local Swift Testing harness covers the tab-group and handoff session logic.

The Xcode project also uses AquaSpace's app name and bundle identities. Internal Swift module names and upstream command documentation retain their existing names to keep the fork small. The repository's original remote is unchanged, and no changes have been pushed.

## Updating AquaSpace

Use the local installer when you are ready for a brief restart:

```sh
./install-from-sources.sh \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
```

It builds AquaSpace, then calls `script/install-aquaspace.py --restart`. To install an already staged build, use `./install-from-sources.sh --dont-rebuild` or `python3 script/install-aquaspace.py --restart`. `python3 script/build-aquaspace.py` still builds only; its explicit `--install-and-restart` option performs the same update. Original AeroSpace, its Homebrew installation, CLI symlinks, and user configuration are left in place. The upstream Homebrew-uninstall workflow has been removed from `install-from-sources.sh`.

The installer uses public Darwin process APIs to identify the executable, bundle, PID, and process start time. Startup arguments and spaces in paths do not change that identity. Known unbundled/debug manager executables block an update too. A running process can be replaced only when it is the sole AquaSpace from the installation being updated, and its own CLI confirms ownership. Stock AeroSpace and other running copies cause the update to stop before signaling anything.

One persistent per-user file lock serializes updates across destinations. The app and CLI are staged and verified before stopping the old process. The updater saves session metadata, rechecks process identity, terminates the old AquaSpace without quit-time recentering, and waits for confirmed exit before replacing files or starting the new app. It retains the exact child handle and checks that child's ownership. There is one launch site and no automatic recovery launch, watchdog, pause, or resume path.

Each update keeps a backup, session metadata, and an atomic journal under `/Applications/.aquaspace-update-<id>/`. Launch intent is recorded before starting the child. An interrupted or uncertain update blocks another install attempt rather than guessing which copy should run. If startup or verification fails, the updater leaves the replacement alone and reports the journal path; it never launches an old copy alongside it.

Recovery restores files only, with all managers stopped:

```sh
python3 script/install-aquaspace.py \
  --recover /Applications/.aquaspace-update-<id>
```

The plain installer without `--restart` also requires stopped managers and never launches an application. Backups are retained for review. If a failure happened before any process was signaled, the unused staging transaction is marked aborted and can be retried normally.

The installer changes passed 47 offline tests before live validation. The transaction tests use fake kernel/process APIs and temporary bundles, and block real subprocess calls, signals, CLI calls, and native-library loading. Four shell-wrapper checks run Bash with a fake Python executable, including macOS Bash 3.2 empty-argument handling:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s script/tests -p 'test_aquaspace*.py'
```

## Live installer validation on 2026-10-14

After the user authorized live testing, the updater passed 38 integration assertions against the real installation and native process APIs. Two updates ran through `install-from-sources.sh --dont-rebuild`; a third successful update used the same native transaction with checkpoint recording. These replaced real application bundles and processes using the currently staged release, without recompiling Swift.

An independent observer recorded 102 process inventories during the run, including executable paths, kernel process start times, and parent PIDs. Every observed transition went from one manager to none to one. The peak was one, with no observer errors or overlap. Recorded native spawn boundaries also confirmed no manager was alive immediately before replacement launch.

The integration test reproduced the old installer failure by returning a falsely empty inventory after a real replacement was launched. The updater retained its child and failed closed; no rollback application started. A concurrent installer was refused by the real cross-process lock. Retrying the unfinished update and requesting file recovery with that child running both failed without changing its identity. After explicitly stopping that known child, file-only recovery left zero managers running. The harness then explicitly restored the saved session with no peer present and completed the final successful update. None of these launches occurred in an error or automatic recovery handler.

All seven baseline windows retained their workspace assignments throughout the checked updates. Final verification found AquaSpace alone controlling windows, a valid signed bundle matching the staged build, no unfinished update records, and original AeroSpace plus its CLI byte-for-byte equal to their backups. Evidence is saved locally in `.debug/aquaspace/installer-live-1792023855352846000/` (`results.json`, `monitor.json`, `samples.jsonl`, `events.jsonl`, and `final-verification.json`). This establishes the observed live outcomes; the single launch site and absence of an automatic recovery launch provide the corresponding transaction invariant.

To reproduce when ready for actual application restarts, first stage a build, then opt in explicitly:

```sh
python3 script/installer-smoke/live.py --run-live
```

The harness stops on unexpected errors and never starts a fallback manager. It checks the installed app and baseline workspace metadata; it does not exercise tab UI interactions.

## Local installation history

The original AeroSpace 0.21.3-Beta app and CLI are saved under `.debug/native-tabs/install-backup/`. Before separation, the native-tabs patch temporarily replaced that installation; the separation restores those original files and installs the patched fork under its own name. Native tab implementation and validation details are in [native-tabs.md](native-tabs.md).
