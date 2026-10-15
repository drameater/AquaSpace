# AquaSpace local fork

AquaSpace installs as `/Applications/AquaSpace.app` with bundle identifier `local.aquaspace`. AeroSpace remains `/Applications/AeroSpace.app` with its original `bobko.aerospace` identity. AquaSpace uses the same icon and assets, `~/.aerospace.toml` / `${XDG_CONFIG_HOME}/aerospace/aerospace.toml`, bindings, and `AEROSPACE_*` environment variables.

Open AquaSpace normally to switch from AeroSpace. To switch back, choose **Switch to AeroSpace** in AquaSpace's menu or run `aquaspace switch-to-aerospace`. This freezes and drains AquaSpace's window writes before opening AeroSpace. Opening AeroSpace directly also triggers a one-way exit through launch notifications and process detection, but an unmodified external application can start before that notification is delivered; use the explicit switch for ordered control. AquaSpace stops stock AeroSpace before taking over. Session transfer preserves workspace assignments, root layouts, floating/fullscreen settings, and focus. Nested tiling containers and their exact weights are rebuilt when changing managers.

The original `aerospace` CLI is restored unchanged. AquaSpace adds `/opt/homebrew/bin/aquaspace`, pointing to `AquaSpace.app/Contents/MacOS/aquaspace-cli`. The apps use separate command sockets: the stock CLI controls stock AeroSpace and the fork's CLI controls AquaSpace. AquaSpace prefixes its own callbacks' PATH with a bundled `aerospace` compatibility alias, so relative `aerospace` invocations in the existing config use the fork. The user's shell PATH and original executable remain unchanged; external scripts and absolute CLI paths must explicitly choose the appropriate manager. Only one manager should control windows.

## Earlier validation on 2026-10-14

The user authorized rebuilding and testing. Release `0.21.3-aquaspace.20261014.2` was installed at this stage; the later installation is recorded below. Original AeroSpace and its Homebrew CLI remained restored alongside it. The installed executable matched that build manifest.

All 26 headless regressions passed: 20 native-tab cases, four ownership/session cases, and two socket lifecycle cases. A scoped release candidate passed 11 background checks against real AppKit tabs, including cross-workspace group merging, selected/inactive detachment, and preservation of the destination workspace. The handoff target was a simulated IPC peer: it verified that ownership was yielding and window writes were disabled before target launch and received the restoration commands. Stock AeroSpace was not launched for this test. The current manager was paused during each scoped run and resumed afterward; workspace 3 and existing window assignments were preserved. The first fixture incorrectly prevented main-window status, exposing nonstandard AX subroles; restoring standard-window status while continuing to prevent keyboard focus resolved that harness issue.

The upgrade installer then incorrectly omitted processes with startup arguments from its process check. This triggered a rollback that briefly started an additional old AquaSpace process. That process was stopped, the verified new bundle was returned to Applications without another launch, and sole control plus all seven surviving window assignments and floating/fullscreen settings were verified. The throwaway installer is retired; the tracked replacement and its offline validation are described below. Runtime ownership checks and scoped test reports are saved locally under `.debug/aquaspace/`.

The revised source uses separate IPC endpoints, an atomic write gate, and a drain of pending AX work before an explicit handoff launch. Yielding is irreversible. A failed handoff cannot kill/restart the target, rebind its socket, or re-enable AquaSpace. Launch notifications close the write gate immediately; app-unhide callbacks also check ownership. Closed socket reads fail on EOF, and canceled connection setup resumes its waiter.

The distinct application identity can require a separate ordinary Accessibility / Device Control and Data Access grant. No root, SIP change, injection, or screen-recording permission is added.

## Offline ownership hardening after checkpoint 140122de

The working source now uses one persistent per-user controller lock for every writable AquaSpace build, including debug and unbundled builds. Debug startup refuses active peers instead of disabling and later re-enabling another manager. Only release startup can perform the existing ordered stock-release takeover, and it verifies kernel process birth identities after saving the session. Every build rechecks peers immediately before enabling window writes and freezes on peer arrival or an uncertain inventory.

Runtime detection now uses the same public libproc identity approach as the installer. It recognizes release/debug AquaSpace and AeroSpace bundle identities and known unbundled manager executable names, independent of location or startup arguments. Embedded CLIs are excluded. Launch notifications freeze known bundled peers immediately; polling also covers unbundled processes. Explicit switching still freezes and drains writes before launching stock AeroSpace. An independently launched, unmodified stock process cannot participate in AquaSpace's lock; the documented notification/polling interval still applies to that external launch.

The historical `handoff-qa.py` and `ghostty-live.py` lifecycle runners are retired and return an error without process actions. The former paused a manager and resumed it from watchdog/cleanup paths; the latter depended on debug disable/re-enable. The scope preparer's old `--handoff-qa` option now fails before file creation or compilation, so it cannot produce a candidate that bypasses admission. Fixture and Ghostty driver sources remain for future live tests with explicit ownership transitions.

Offline checks (no application launch or live process inspection):

```sh
python3 script/test-ownership-offline.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s script/tests -p 'test_aquaspace*.py'
```

The isolated Swift package compiles the production admission and process-reader sources without the app entrypoint or SwiftUI. Native process functions are replaced with aborting stubs; behavioral tests inject inventories and use temporary lock files. They cover both build configurations, late peers, unreadable inventories, debug/unbundled classification, CLI exclusion, irreversible yielding, lock contention, and symlink refusal. Python regressions verify that retired runners cannot start processes, signal managers, or write files, and that runtime paths have no debug escape hatch. At checkpoint `e7b17a02`, validation passed 10 Swift ownership regressions, all 50 Python offline tests, and compilation of the AppBundle library target. Neither application was launched or stopped during that offline validation. Subsequent installation and live validation are recorded below.

## Ownership rebuild and live validation after e7b17a02

Release `0.21.3-aquaspace.20261014.5` was installed for this validation. It includes two followups discovered during the authorized rebuild and live tests. These followup source changes, offline regressions, and this validation record are included together in a local checkpoint after `e7b17a02`; nothing has been pushed. The subsequent recovery release is recorded below.

The initial scanner requested BSD process identity before classifying each executable. On macOS 27, an ordinary unrelated process denied that request, causing AquaSpace to fail closed and exit. The corrected Swift runtime scanner and Python installer first classify the authoritative executable path and bundle metadata. Unrelated executables and embedded CLIs require no BSD identity query. Actual manager candidates still require matching before/after kernel birth identities and executable paths; an uncertain candidate or unreadable executable inventory still blocks control. An injected scanner regression reproduced the denied-helper failure before the correction, then passed with it. The updater also now accepts the precise `No window is focused` response when capturing an empty workspace; other capture failures still abort. Its regression likewise failed before the correction and passed afterward.

The final offline run passed 16 isolated Swift tests and 53 Python tests. The optimized app and CLI were rebuilt and locally signed. The final live suites passed 45 ownership assertions and 18 updater/Ghostty assertions on dedicated test workspaces. Their independent monitors recorded 188 inventories in total, with a peak of one actual manager and no scan errors. Before every explicit restoration launch, the native inventory confirmed that no manager remained. The final saved status reports AquaSpace PID 31402 controlling windows with a verified empty peer inventory.

Ownership tests exercised production debug/release admission against the controller lock, embedded CLI exclusion, retired-runner refusal, and runtime yielding plus startup refusal for stock-debug, fork-debug, and unbundled peer identities. Peer identity fixtures contained only C output/sleep code and could not control windows; debug admission probes used the production scanner and admission code without AX/window-control code. Stock AeroSpace was not launched. Its original app executable and CLI hashes remained unchanged.

The public guarded updater succeeded from an empty test workspace, with the predecessor confirmed exited before replacement control. Real Ghostty automation created two native tabs, verified one managed slot, moved the group between test workspaces, switched tabs six times, and closed the selected tab while preserving the survivor's workspace. All disposable Ghostty tabs were then closed; existing terminal identities and all eight original window workspace assignments were preserved. The test restored workspace 1 and the original focused window. Dynamic hidden-app layout labels differed during a later metadata inspection, so this run does not establish byte-identical layout/visibility state for every original window.

Evidence:

- `.debug/aquaspace/ownership-live-1792026402881915000/results.json` and `monitor.json` (45 assertions, 84 final monitor samples).
- `.debug/aquaspace/ghostty-ownership-1792026485819586000/results.json` and `monitor.json` (18 assertions, 104 final monitor samples).
- `.debug/aquaspace/release/build.json` (installed version and source/executable hashes).

Additional AppKit fixture checks were inconclusive: one background merge check retained separate workspace assignments, and a foreground fixture failed its initial single-slot expectation. These checks are not counted as passing and do not revalidate merging/detachment for this build. A further fixture retest was not executed because automatic approval review reached its usage limit. This was a review-service failure, not an unsafe-action determination. No further live test was attempted.

## Recovery checkpoint installation and live validation

After explicit authorization to rebuild, install, and test, release `0.21.3-aquaspace.20261014.6` was built from local commit `5cf47d5356c1735090d5355b277c87e1772034a0` with the macOS 26.5 SDK and two compilation workers. The signed app and CLI are installed at `/Applications/AquaSpace.app`; final hashes match `.debug/aquaspace/release/build.json`. The runtime version includes source digest `c71a6481c794`. No source changes beyond that commit were needed for this live run, and nothing was pushed.

The guarded production installer replaced sole predecessor PID 31402 with AquaSpace PID 52732. Instrumented phase boundaries confirmed zero managers after stopping, during replacement, and immediately before the single replacement launch. The transaction committed at `/Applications/.aquaspace-update-f7cfd7c27522438aab97683bb2b2bcfe`. The final status reports AquaSpace controlling windows with a verified empty peer inventory. Stock AeroSpace was never launched.

The completed live suite passed **87 assertions** against the installed build, running both background and foreground variants of the AppKit fixture on `native-tab-test-recovery-a` and `native-tab-test-recovery-b`. It covered first-tab replacement, moving a group across workspaces, merging into a bare destination, selecting every merged member, selected-tab detachment, independently moving the detached window, merging with the incoming tab selected, inactive-tab detachment, two-to-one detachment, bare-window merging, cross-workspace remerging, and repeated selections after those cycles. Native NSWindow group membership was verified independently of the managed workspace model. The fixture also recorded only its own AX roots, native bars, frames, and WindowServer IDs: 404 background and 173 foreground trace samples.

Two setup attempts preceded the completed suite. The first installed the release successfully, then the new fixture logger crashed because a remembered-bar dictionary used numeric JSON keys. That logger was corrected to use string keys and validate serialization; no manager crashed. The next attempt stopped before tab operations because initial app activation placed the standalone fixture on the previously focused workspace. A readiness handshake and an explicit move of only that uniquely identified disposable window established the test workspace before topology assertions. Both retries reused the installed process without reinstalling or launching any manager.

Independent native process monitors recorded **380 inventories** across installation and the three fixture attempts (43, 45, and 292). Peak manager count was one throughout the observed samples, with no observer errors. The spawn-boundary check supplies direct confirmation of empty inventory before replacement, in addition to sampled observations.

The completed suite and final read-only verification preserved all eight windows in that suite's baseline, including workspace assignments and fullscreen flags. Its initial workspace 3 and focused window 23278 were restored; all fixture windows were closed. The original AeroSpace app executable, original CLI, and user configuration match their preflight hashes. Signatures are valid and no unfinished installer transaction remains. Baselines were captured separately for each attempt, because the Messages window identity and Codex fullscreen state changed between the early attempts; tab operations targeted only the disposable fixture windows.

Evidence is saved locally:

- `.debug/aquaspace/recovery-live/preflight.json`, `build.log`, and `live.py` (preflight, build, and test harness).
- `.debug/aquaspace/recovery-live-1792029411213869000/` (guarded installation, launch-boundary events, first fixture failure, 43 process inventories).
- `.debug/aquaspace/recovery-live-1792029525519926000/` (startup-workspace failure before tab operations, 45 process inventories).
- `.debug/aquaspace/recovery-live-1792029683997452000/` (`results.json`, `monitor.json`, `samples.jsonl`, `events.jsonl`, both AX traces, and `final-verification.json`: completed 87 assertions and 292 process inventories).

These tests establish real AppKit behavior through the installed observer/layout pipeline. They do not claim a live replay of each synthetic timing sequence, or app-specific Ghostty merging API coverage. The six synthetic recovery cases remain covered by the 44 offline tests documented in [native-tabs-diagnosis.md](native-tabs-diagnosis.md). This validation record is included in a local documentation checkpoint after `5cf47d53`.

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
