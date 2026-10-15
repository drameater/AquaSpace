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

## Finder and Ghostty application checks after 339ed06f

The user subsequently authorized application-specific merge/detach checks on dedicated test workspaces. The installed recovery release and sole controlling AquaSpace PID 52732 were reused throughout; no manager was launched, stopped, rebuilt, or installed, and nothing was pushed.

Finder passed 14 stable workspace assertions on `native-tab-test-apps-a` and `native-tab-test-apps-b`. Its native tab bar and Window/context menus independently established the actual groups. Checks covered a two-tab group move and selection, merging that group across workspaces into a bare destination, selecting the merged members, selected-tab detachment, independently moving the detached window, detaching an inactive tab from the remaining two-tab group, moving it independently, remerging the three bare windows onto the other destination workspace, selecting the remerged members, and cleanup. No pre-existing Finder windows were open. All eight original managed windows retained their workspace assignments and fullscreen flags after Finder cleanup.

Ghostty merge/detach validation remains **blocked**, not passed: the computer-use tool refused Ghostty access for safety reasons, and the installed Ghostty scripting dictionary and action documentation expose no merge/detach operation. No alternative UI automation was used to bypass that restriction. Its supported scripting API did pass 16 checks using uniquely identified disposable idle terminals: native membership, three-tab single-slot behavior, moving the group, six selections, inactive/selected closes, cleanup, and preservation of pre-existing terminal/tab identities. These checks do not establish Ghostty merging or detachment.

The final workspace-preservation check **failed** for the existing Ghostty window 21423: it had moved from workspace 2 to test workspace B despite retaining its original terminal identity. The cause was not isolated, so this application run is not counted as a complete preservation success. That window was explicitly restored to workspace 2; final checks then confirmed all eight original window assignments/fullscreen flags, original workspace 3 and focused window 11584, no Finder test windows, and only the original Ghostty window remaining. All disposable Ghostty terminals were closed.

The independent monitor recorded 2,108 inventories during the application tests, always with the same single AquaSpace process and no inventory errors. Final ownership status after restoration again reported PID 52732 controlling windows with verified empty peer inventory. The monitor had ended before the explicit workspace restoration; restoration commands separately rechecked process identity and exclusive ownership. Local evidence: `.debug/aquaspace/native-app-live-1792030218578891000/` (`baseline.json`, `checks.jsonl`, `ghostty-baseline.json`, `ghostty-results.json`, `samples.jsonl`, `monitor.json`, and `summary.json`). These application-specific notes are included with the subsequent offline repair in a local checkpoint after `339ed06f`.

The subsequently authorized offline investigation reproduced false detachment attribution after an AX observation reset: an unrelated existing window could inherit a shrinking group's workspace. The source now retains settled independent-window evidence across those resets and rejects that attribution while permitting genuine joins/detachments. All 120 offline tab/model/ownership/installer tests passed; manager ownership checks are unchanged. Exact attribution of the earlier live event remains limited by its missing AX trace. See [the diagnosis and regression record](native-tabs-diagnosis.md#independent-ghostty-window-moved-during-application-checks). The repair was committed as `f13337a2` before the separately authorized installation and live checks below.

## Ghostty fix installation and live validation after f13337a2

After explicit authorization, commit `f13337a2226fe6ace14f50cbdd6a6433612692d7` was built and signed as `0.21.3-aquaspace.20261014.7`, source digest `54de93f11723`, then installed at `/Applications/AquaSpace.app`. The build used the cached dependencies, macOS 26.5 SDK, and two workers. The default scratch directory lacked dependencies, and a sandboxed cached build could not finish debug-symbol generation; the authorized unrestricted cached build succeeded. No source repair or ownership-check changes were made during live validation.

The production guarded updater replaced sole AquaSpace PID 52732 with PID 68598 (kernel birth `[1792033211, 996731]`). Every stopped/replacement phase and the exact spawn boundary recorded zero managers. The transaction committed at `/Applications/.aquaspace-update-88a3df7a0fa74af9ba5aa768b425206b`. Subsequent test retries reused PID 68598 without another installation or manager launch. Stock AeroSpace was never launched.

The completed foreground Ghostty suite passed **109 assertions** on `native-tab-test-ghostty-fix-a` and `native-tab-test-ghostty-fix-b`. It created three disposable tabs through Ghostty's scripting API, checked native membership and one managed slot, moved the group, selected all members twice, entered/exited native fullscreen on the test window, closed an inactive tab, closed the selected tab to a bare survivor, and closed the remaining test terminal. An independent AppKit notification observer recorded two actual native Space changes during the fullscreen cycle. This establishes foreground Space-boundary/tab-close coverage; it does not establish Ghostty merge/detach behavior or replay the exact synthetic AX timings.

The original Ghostty window **21423 remained floating on workspace 2** after every operation, throughout 108 background window samples in the completed run, and in subsequent final reads. Existing Ghostty tab/terminal identities were unchanged. All 12 windows in that run's fresh baseline retained their workspace assignments and fullscreen flags through test cleanup. The observer stops on any baseline change instead of checking only disposable titles. All disposable terminals were closed, and cleanup restored that run's starting workspace 2 and focused window 21423.

Two preceding attempts are retained honestly: an empty-test-workspace switch timed out before installation, so that unnecessary pre-update focus step was removed; the first installed-build sequence passed the tab/preservation checks but did not observe the required Space notifications. Its helper used a plain run loop and selected a window without explicitly activating the application. The corrected helper runs an AppKit event loop, activates Ghostty through its scripting API, verifies it is foreground, and retains the requirement for two independently observed Space changes. No UI keystrokes, terminal input, or second manager were used. The corrected rerun reused the installed process.

Native process monitors recorded **678 inventories** across these attempts (82, 299, and 297), with a peak of one manager and no scan errors. Final status reports PID 68598 controlling windows with verified empty peer inventory. Installed app/CLI hashes match the release manifest, both signatures verify, stock app/CLI and configuration hashes are unchanged, and no unfinished update remains.

Baselines were captured separately: other windows/layout state changed during the build, before installation or tab operations. After successful cleanup, later reads also observed changed focus and the Codex fullscreen flag. Their cause is not attributed to this test, and newer desktop state was left alone. The first `final-verification.json` retains failed comparisons against the earlier cleanup focus/fullscreen snapshot. `completion-verification.json` separately confirms every baseline workspace assignment, original Ghostty workspace 2, the same exclusive controller, matching installation, closed disposable windows, and unchanged stock files/configuration. The checks were not rewritten to claim byte-identical post-cleanup desktop state.

Local evidence:

- `.debug/aquaspace/ghostty-fix-live/`: build logs, authorized harness, scripting driver, and notification observer source.
- `.debug/aquaspace/ghostty-fix-live-1792032795411555000/`: pre-update workspace timeout and 82 inventories.
- `.debug/aquaspace/ghostty-fix-live-1792033210234429000/`: guarded installation, spawn boundaries, first notification-observer failure, cleanup, and 299 inventories.
- `.debug/aquaspace/ghostty-fix-live-1792033298740641000/`: completed `results.json`, `monitor.json`, `samples.jsonl`, `window-samples.jsonl`, `events.jsonl`, `native-space-events.jsonl`, `cleanup-verification.json`, `final-verification.json`, and `completion-verification.json`.

These updated live notes are included in a local documentation checkpoint after `f13337a2`; nothing was pushed.

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


## Automated native Ghostty merge/detach validation after a243c7f0

The user explicitly authorized an isolated AppleScript/System Events harness after
the computer-use tool refused Ghostty. Ghostty 1.3.1's installed scripting API and
actions do not expose native merge/detach commands; sending a scripted key goes
to the terminal surface. The completed harness instead invokes Ghostty's actual
**Window → Merge All Windows** and **Move Tab to New Window** menus. It sends no
terminal keystrokes and verifies actual native tab groups through Ghostty's own
scripting API, independently of AquaSpace's window/workspace model.

The test launches a separate process of the installed Ghostty application using
`NSWorkspace`'s new-instance option. Its throwaway configuration suppresses user
configuration, saved-state restoration and initial windows, and runs idle
`/bin/cat` terminals. The original Ghostty PID 60264 is excluded from mutations.
Every created tab is identified by its tab ID and focused terminal UUID. Before
menu operations, all native members must be recorded test tabs and every exposed
AX window must have the unique test prefix. System Events addresses the process
by index from a single snapshot of UNIX PIDs, then rechecks that PID before
activation and each menu click. This avoids ambiguous name-based references to
the two identically named application processes. A changed index fails closed.

The completed run passed **31 assertions** on
`native-tab-test-ghostty-topology-a` and `native-tab-test-ghostty-topology-b`:

- A two-tab native group moves and retains its workspace through selections.
- Native merging of that group into a bare destination across workspaces produces
  one three-tab group on the destination workspace; selecting each member keeps it there.
- Detaching from three tabs preserves the source workspace, and the detached tab
  moves independently without moving the remaining two-tab group.
- Detaching from the remaining two-tab group produces two independent bare windows.
- Merging all three bare windows chooses the other destination workspace, and
  selecting each member preserves it.
- A further detach, independent move and remerge uses the moved destination.
- Cleanup removes all disposable tabs and exits only the isolated Ghostty process.

The same installed AquaSpace PID **68598**, kernel birth
`[1792033211, 996731]`, was reused throughout. No manager was launched, stopped,
paused, resumed, rebuilt or installed. In the completed suite, **1,046 independent
native process inventories** observed exactly that one manager, with no scanner
errors. **367 background window snapshots**, plus per-operation checks, preserved
all **12 original windows' workspace assignments and fullscreen flags**. Original
Ghostty window **21423 stayed floating on workspace 2**, with the same native tab
and terminal UUID. Final read-only verification confirmed only original Ghostty
PID 60264 remained, no disposable process/windows remained, and AquaSpace still
reported exclusive control with a verified empty peer inventory. Installed app
and CLI hashes still match release `.7` from `f13337a2`.

Earlier attempts are retained separately, rather than counted as completed
successes. A helper ARC return-ownership declaration caused a crash after creating
its first tab; its exact isolated PID, birth time, title and tab/terminal identity
were verified before cleanup. Direct-object scripting commands also needed their
object proxy rather than the application proxy. Subsequent attempts stopped
before the first merge on foreground/name-resolution guards; another stopped
when baseline window 23718 disappeared (its application/cause was not captured).
One later attempt passed the first merge and both detach boundaries, then stopped
before remerging because processes exited during a per-process System Events
scan. A single primitive-ID snapshot replaced that scan. No ambiguity or baseline
preservation guard was relaxed; no production AquaSpace fix was needed.

Local evidence: `.debug/aquaspace/ghostty-topology/run-20261014-233015/`
(`baseline.json`, `baseline-app-identities.json`, `isolated-process.json`,
`created.json`, `events.jsonl`, `process-samples.jsonl`, `window-samples.jsonl`,
`results.json`, and `final-verification.json`). Interrupted attempts are sibling
`run-*` directories; the initial helper failure has a separate verified cleanup
record. The executed working harness/helpers are in the parent directory.

The reusable source is saved under
[`script/native-tabs-smoke/ghostty-topology/`](../script/native-tabs-smoke/ghostty-topology/README.md).
Its runner retains the tested core and adds repository-relative paths, an explicit
`--run-live` gate and a required `--expect-existing-window ID:WORKSPACE` baseline.
Those entrypoint changes were checked offline; the live suite used the working
copy described above. Four offline tests block subprocesses, signals, native
library loading and fixture writes, and verify that help, missing opt-in, missing
baseline and malformed baseline cannot reach live actions. The existing retired
lifecycle runners remain retired. These sources, offline guard tests and
validation notes are included in a local checkpoint after `a243c7f0`; nothing was
pushed. No additional live tests were run while preparing that checkpoint.


## Offline Ghostty harness isolation repair after 4072970d

An offline review found two gaps in the reusable harness: its first standalone
window was created before entering a dedicated test workspace, and System Events
process indices could shift between a PID check and a subsequent UI mutation.
The earlier claim that a changed index always failed closed was too strong: a
check after activation could report the problem after foregrounding another app.
The completed live record above remains evidence for the observed run, rather
than proof against these unexercised failure schedules.

The creation path now selects a dedicated test workspace and verifies focused
workspace readback before every new window/tab request. Unsupported workspaces,
failed switches and incorrect readback abort before creation. Existing tab-group
membership, window-ID, terminal-UUID, manager-identity and baseline-preservation
checks remain in place.

The positional AppleScript driver has been removed. `native_menu.py` uses public
accessibility APIs to bind one application element to the isolated Ghostty PID,
retains its copied window/menu references, and acts directly on those references.
Every element must belong to that PID, every exposed window must retain the test
prefix, the focused title must match, and the requested menu item must be unique
and enabled. Process-list changes cannot redirect a retained target. Stale/native
read failures abort; uncertain presses are never retried. Error cleanup cancels
only the retained menu, without global keyboard input. The direct AX caller uses
normal Accessibility/Device Control access, independently of System Events'
Automation permission. No new manager lifecycle or fallback launch path was added.

Offline regressions first reproduced creation on a personal workspace and rejected
the old positional-menu wiring. The repaired suite adds 23 safety tests: actual
runner creation functions run against fake workspace state, the menu policy runs
against retained fake elements under process churn/exit, and the actual native
adapter's C boundary is replaced by fake functions. Its tests confirm reference
retention, direct-reference mutations and native failure handling. Existing opt-in
and retired-runner guards are also retained. Native libraries, subprocesses and
signals are blocked throughout the new safety tests. The final offline run passed
all **80 Python AquaSpace tests**, and `git diff --check` passed.

This repair was performed entirely offline. No AquaSpace or Ghostty launch, real
process/window inspection, live test, installation or push occurred. The new AX
path and creation order have not yet been validated live. The prior 31 live
assertions must not be relabeled as a live run of these changes.


## Fixed Ghostty harness live validation after 4508f0e8 — 2026-10-15

The user authorized running the repaired harness on the dedicated test workspaces.
The exact committed runner and native AX driver from `4508f0e8` completed on the
first attempt, passing **31 live assertions**. The diagnostic binaries matched
the hashes saved by the earlier completed run, and their source matched the
committed helper sources. Accessibility trust was checked without prompting
before starting; no permission change was needed.

All six actual native-menu operations used retained AX targets in isolated
Ghostty PID 9973: three merges and three detachments. The suite verified initial
creation on test workspace A, tab creation, a two-tab group move to B, member
selections, cross-workspace merge into a bare destination on A, three-to-two and
two-to-one detachment, independent movement, merging three bare windows onto B,
repeated selections, and a final detach/move/remerge onto A. Native membership
was read through Ghostty's scripting API independently of AquaSpace's model.
No positional System Events targeting or terminal keystrokes were used.

**913 independent process inventories** observed the same sole AquaSpace PID
68598, kernel birth `[1792033211, 996731]`, with no scanner errors. Background
window monitoring recorded **419 snapshots**. Per-operation checks and final
read-only verification preserved all **11 original windows' workspace and
fullscreen states**; the final rows also match their original layout fields.
Original Ghostty window **21423 remained floating on workspace 2**, with unchanged
native tab/terminal identities. All disposable tabs were closed, only isolated
Ghostty PID 9973 exited, and only original Ghostty PID 60264 remained afterward.
Installed AquaSpace and CLI hashes still match release `.7` from `f13337a2`.
The installed manager was reused without launch, stop, pause, resume, rebuild or
installation. Nothing was pushed.

The initial desktop snapshot reported workspace 1/focused window 11344. Final
read-only verification reported workspace 2/focused window 11584. All original
window assignments/layout/fullscreen fields still matched. The exact timing and
cause of the focus difference were not established; the harness conditionally
restores only when a test workspace remains focused. A stricter separate final
focus-equality assertion consequently failed, while all preservation, process,
cleanup, native menu and installed-hash assertions had passed. Verification was
repeated without treating focus equality as an invariant or issuing corrective
focus/workspace commands. This run establishes the tab/workspace and ownership
checks above, and does **not** claim complete restoration of desktop focus.

Evidence: `.debug/aquaspace/ghostty-topology/run-20261015-004359/`
(`baseline.json`, `baseline-app-identities.json`, `isolated-process.json`,
`created.json`, `events.jsonl`, `process-samples.jsonl`, `window-samples.jsonl`,
`results.json`, `final-verification.json`). The final verification includes the
commit, source/helper hashes, six retained-target responses, original/final focus
metadata, installed-hash confirmation and cleanup checks. Preflight is saved in
`.debug/aquaspace/ghostty-topology/preflight-20261015-1792039433040629000.json`.
These live validation notes are included in a local documentation checkpoint
after `4508f0e8`. No further live tests were run while preparing the checkpoint,
and nothing was pushed.


## Offline Ghostty focus-restoration repair after 6e4ab9b2

The saved run started from manager workspace 1/window 11344 and its later
read-only check found workspace 2/window 11584. The trace contains **no focus
events**, and the final focus check was about 60 seconds after its last event
(about 60 seconds after `results.json` was saved). It does not establish whether
focus changed during cleanup, during asynchronous restoration, or afterward.
It also lacks an initial native foreground snapshot, so the initial model focus
cannot independently be confirmed as the actual foreground window.

Two harness defects are confirmed independently of that historical ambiguity:

1. The runner closed its tabs and quit the disposable app **before** deciding
   whether to restore. It restored only if the subsequently focused workspace
   was still a test workspace. A normal foreground-app close/quit handoff onto
   another original workspace could therefore skip restoration entirely.
2. Neither the saved model workspace/window nor actual native foreground focus
   was checked after restoration. A skipped, ignored or later overwritten focus
   request could still produce `success: true`.

Four offline reproductions execute the runner's actual teardown block with fake
process/CLI/native boundaries. Before the repair they reproduced foreground-close
handoff, a silent success after external focus, late cleanup drift, and a focus
command that returned success without restoring focus. All four are now green.

The repaired lifecycle captures a stable, coherent **model and native** baseline
before launching the disposable app. The original application/window AX elements
are retained. Inconsistent or ambiguous model/native snapshots are rejected;
a failed capture records `focus-capture-failure.json` and starts no application.
The runner restores **before the first tab close**, while the actual foreground
PID/title and managed window ID still identify an owned test window. It rechecks
that ownership after validating the original target. Detected external focus is
left untouched and marks the run unsuccessful, rather than silently passing.

For a normal window, one explicit focus command restores the original window and
its workspace; there is no intermediate MRU workspace hop. An originally empty
workspace uses the saved workspace and retained original application, refusing
activation if that application has gained a window. Manager/peer identity and
original-window preservation guards still apply. No manager launch/restart or
fallback activation by application name/process index was added.

Both model workspace/window and the exact retained native application/window
must agree for at least three snapshots and half a second before background
cleanup. Focus is recorded immediately before and after each tab close and app
termination, with restoration request metadata. After cleanup another bounded
stability check is required. It makes no corrective focus writes: an unexpected
handoff or new user focus fails the run without taking focus back. `results.json`
includes the focus report, and success/exit status require final verification.
These records distinguish focus during teardown from later changes after the
harness ends; they do not assert indefinite control over subsequent user focus.

The repair adds **30 offline tests** covering actual teardown ordering/verdicts,
manager/native disagreement, delayed or ignored focus, late drift, detected user
input (including during target validation), stale targets, manager uncertainty,
empty workspaces (including new windows from another application), ambiguous
baselines and the real AX adapter's read boundary.
All **110 Python AquaSpace offline tests** pass. Native libraries, subprocesses,
signals and fixture writes are blocked in the new tests; source extraction runs
only the relevant runner code against fakes.

No application was launched, no real process/window APIs were invoked, no live
test or installation was performed, and nothing was pushed during this repair.
The revised focus lifecycle is **not yet live-validated**; the earlier 31-assertion
run must not be presented as a run of this new lifecycle. The repair, offline
regression tests, and diagnosis are included in a local checkpoint after
`6e4ab9b2`.

## Ghostty desktop-focus live validation after 721214c1 — 2026-10-15

The user authorized the fixed harness on the dedicated test workspaces, with
original Ghostty window **21423 on workspace 2** protected and only one manager.
The first attempt (`run-20261015-011011`) stopped during initial focus capture,
before launching any disposable app or changing focus. The system-wide AX
`AXFocusedApplication` attribute returned `-25204`. Three read-only Python
queries reproduced it with Accessibility trust true. A separate native AppKit/C
probe reproduced the same system-wide failure while `NSWorkspace` identified
foreground PID 52214 and direct application AX reads successfully returned its
`ChatGPT` window. This was a native-reader limitation, not a restoration failure
or lack of permission. The underlying macOS failure was not otherwise attributed.

The harness now supplies a read-only AppKit `frontmost-pid` query from its existing
diagnostic helper, following the route used by AquaSpace's production native
focus reader. `NativeAX` validates the positive integer PID, binds and retains the
application AX element, verifies its PID, and independently rereads AppKit's
foreground PID before returning it. Errors and foreground churn stop capture;
there is no fallback to manager focus. Focused native window reads, retained
window comparisons, initial ambiguity checks, foreground ownership checks and
stable pre/post-cleanup verification are unchanged. Only the diagnostic helper
was rebuilt. Three new offline adapter tests passed, bringing the AquaSpace
Python suite to **113 passing tests** before the second live attempt.

The second attempt (`run-20261015-011154`) completed with **32 passing live
assertions**, no errors, and `focus_restoration.verified: true`. All six native
menu operations ran in disposable Ghostty PID **17112**: three merges and three
detachments, with tab selections, independent moves and repeated remerges on
`native-tab-test-ghostty-topology-a` and `native-tab-test-ghostty-topology-b`.
Original Ghostty PID **60264**, tab IDs and terminal identities were unchanged.
Window **21423 remained floating on workspace 2**. Every original window's
workspace and fullscreen flags survived **564 background window snapshots**;
all 11 original rows, including layout fields, matched at completion.

Initial focus was **workspace 2, window 11584, native PID 52214 (`ChatGPT`)**.
The runner issued exactly one restoration request, focusing window 11584 while
the owned test window was still foreground. Native and manager focus matched
the retained initial references for three stable samples before cleanup, around
every tab close and isolated-process termination, and for three stable samples
after cleanup. No cleanup-triggered focus handoff or corrective retry occurred.
All disposable windows were removed and only the isolated Ghostty process exited.

All **875 process inventories** observed the same sole AquaSpace PID **68598**,
birth `[1792033211, 996731]`, with no monitor errors. The manager was reused
without launch, pause, termination, rebuild or installation. Nothing was pushed.

A later read-only verification initially saw an additional transient Zen window
24999 after the suite had completed; an exact-window-set assertion stopped that
extra check. No action was taken on the window or desktop focus. Subsequent
read-only checks found only the original windows and the original model/native
focus, unchanged Ghostty identities, and the same sole manager. The transient
window's cause was not established and is not counted as a harness test window.

Evidence is local under
`.debug/aquaspace/ghostty-topology/run-20261015-011154/`: `baseline.json`,
`results.json`, `events.jsonl`, process/window samples and
`final-verification.json` (including source/helper/installed hashes). The failed
preflight has `focus-capture-failure.json` in `run-20261015-011011/`. The live
reader repair, its regressions and these notes are included in a local checkpoint
after `721214c1`; the run must not be described as an unchanged run of that earlier
checkpoint. No additional live tests were performed when creating this checkpoint.

## Offline execution-focus safety repair after 4492ad71

The user authorized repairing both focus-stealing gaps found in the offline
review, with regression tests and unchanged ownership protections. No live test,
app launch, installation or push was authorized or performed.

**Restoration snapshot gap.** The AppKit foreground PID check preceded later AX
window/title reads, so a new user foreground app during those reads could leave
a stale test-app snapshot authorizing restoration. The offline reproductions
used the real `NativeAX.focused_application` implementation and real restoration
policy, substituting only process/AX boundaries. They changed foreground during
the window or title read and observed an unwanted restoration request against
`4492ad71`. Additional failing cases covered user focus during restoration-intent
logging, late empty-workspace application/workspace validation, and a different
window in the same native app during final title validation.

Snapshots now recheck the retained window title, the application's current
focused-window reference/owner, and finally actual foreground app identity after
the initial native reads and observation logging. Title validation precedes the
final window read so a title callback cannot hide a same-app window switch.
Restoration intent is recorded before the last foreground validation. The empty
workspace activation path performs another complete snapshot after all its late
validation reads and logging, and refuses changed model/native focus. Existing
retained roots, ambiguity rejection and stable cleanup verification remain.

**Execution-wide gap.** Previously only baseline capture and teardown examined
native foreground ownership. The actual selection, creation and move functions
overrode simulated user focus in the offline runner-function reproductions.
`ExecutionFocus` now checks coherent model/native ownership before foreground
actions and during topology polling. It permits the verified original baseline
only for the initial transition; subsequent focus must belong to a uniquely
identified disposable test window on a dedicated test workspace. A deliberate
switch to an empty test workspace can carry only the unchanged preceding native
source (or that same app with its window hidden) and only on the explicitly
requested workspace. Personal model windows and different native apps/windows
do not qualify. Initial ambiguity or model/native identity changes also refuse
the first action.

Detected departures, unreadable ownership or uncertain action replies revoke
foreground permission permanently for that run. Returning to a test window
cannot reauthorize more actions or cleanup restoration. Background cleanup still
uses only recorded tab/terminal pairs and the pinned disposable process, with
the existing manager/process checks. Existing windows are not moved or activated
to recover from a failure.

The script driver requires the caller's native foreground grant for every
create/add/select, retains source application/window AX references across target
enumeration and rechecks them before mutation. Redundant window/application
activation was removed from selection; the parent performs its separately guarded
focus command. Native menu automation now refuses a background target, never
activates it, and checks foreground immediately before both menu presses. All
PID, executable/birth, prefix, original-window, manager and native tab/terminal
ownership checks remain. The runner requires the new helper's read-only
`ghostty-topology-foreground-v1` capability before capturing a session or launching
Ghostty, so a stale helper cannot bypass the new guard. The helper build command
now explicitly links ApplicationServices for these AX reads.

Offline validation added **26 test methods** (with additional boundary subcases)
and passed all **139 AquaSpace Python tests**. The new cases exercise actual
runner functions, the real focus policy/adapter with fakes, native-menu policy,
initial/empty workspace transitions, late read/logging changes, revocation,
successful owned operations, guarded exact targets and stale-helper admission.
Native loading, subprocesses and signals are blocked in these safety tests.
The Objective-C helper passed `clang -fobjc-arc -fsyntax-only`; this parses and
type-checks its source without building or executing a helper binary. No
window-manager or diagnostic helper was rebuilt, installed or launched.

These changes are included in a local checkpoint after `4492ad71`. No additional
tests were run when creating this checkpoint. They have **offline validation
only**; the earlier 32-assertion live run predates this repair and
must not be relabeled as its validation. A future explicitly authorized live
run must first rebuild the diagnostic script driver. The foreground checks are
bounded observations around mutations; they do not constitute an atomic macOS
lock against an input event arriving after the final check.

## Foreground-guard live attempts after eff15f35 — 2026-10-15

The user authorized rebuilding the diagnostic helper and running the harness on
test workspaces, checking desktop focus, original Ghostty placement and sole
manager ownership. Only `script-driver` was rebuilt. Neither window manager was
rebuilt, installed, restarted, paused or launched. Nothing was pushed.

The first attempt ran the exact `eff15f35` source with the rebuilt v1 helper:
`run-20261015-014619`, disposable Ghostty PID **29203**. It passed nine assertions
then refused a workspace transition. Original Ghostty PID **60264**, window
**21423**, remained native foreground while the manager model selected the test
windows. Removing redundant activation had also removed the initial native app
handoff needed for a separately launched instance. The gate correctly refused
when the group left the bootstrap workspace; it did not issue a restoration
request after revocation. All disposable tabs/processes were removed. Its
`abort-verification.json` confirms all 11 original rows and original Ghostty
native tab/terminal identities were unchanged, with the same sole AquaSpace.

The follow-up adds exactly one explicit initial native activation after the
first owned tab/terminal pair exists. The runner's execution gate validates the
unchanged source before dispatch; the helper retains its source application and
window and checks them again immediately before normal app activation. The
runner waits read-only for the owned native/model focus and never retries the
activation. A new user foreground app still revokes permission. Selection and
native-menu paths retain their foreground checks and do not activate apps.
The helper capability was advanced to `ghostty-topology-foreground-v2` so a stale
v1 helper cannot bypass this interface requirement. Three added offline tests
cover the exact one-shot handoff, refusal after a user switch, and an ignored
activation without retries. They failed before the repair and now pass.

A Zen window **25795**, PID **51309**, appeared on the old test workspace B
between attempts. A guarded preparation check stopped without any focus write
when it observed this extra window. The runner now accepts a validated
`--test-workspace-prefix` so an occupied test pair can be left untouched. One
additional offline test covers invalid/personal prefixes, before native APIs or
file writes. All **143 AquaSpace Python offline tests** passed before continuing.
The v2 Objective-C helper built successfully with ApplicationServices linked and
normal activation options. No ownership or ambiguity check was relaxed.

The two v2 attempts used
`native-tab-test-ghostty-focus-20261015-a` and `-b`:

- `run-20261015-015041`, disposable PID **30257**: passed **21 assertions**,
  including real merges and detachments. It included all 12 existing windows in
  its preservation baseline. Zen row **25795** disappeared during the run;
  background monitoring stopped the suite, and the unchanged preservation gate
  refused restoration. Cleanup removed only the disposable Ghostty process.
  The final cleanup observation matched original native/model focus without a
  restoration request, but this failed run is not a focus-restoration pass.
- `run-20261015-015240`, disposable PID **33310**: captured a fresh baseline of
  the 11 remaining original windows and passed **12 assertions**. A new Zen
  window **25984** appeared on test workspace A and was its actual native/model
  foreground when the suite next selected A. The execution guard revoked
  permission immediately. Cleanup left Zen focus alone, made no restoration
  request, and removed only disposable Ghostty PID 33310. The cause of Zen's
  transient window activity was not established; no helper command targeted Zen.

Live testing stopped after those interruptions. **No attempt completed** the
full topology/restoration suite. The last attempt demonstrates refusal to
overwrite foreign foreground focus; it does not establish successful normal
restoration after the new handoff. Counts from failed attempts must not be
combined into a passing suite.

Across the three attempts, **1,636 native process inventories** observed the same
sole AquaSpace PID **68598**, birth `[1792033211, 996731]`, with no process-monitor
errors. The attempts recorded 65, 398 and 194 window snapshots respectively.
Original Ghostty window **21423 stayed floating on workspace 2**, with unchanged
native tab/terminal identities. Final read-only verification found all 11
long-lived original rows unchanged, only original Ghostty PID 60264 remaining,
and all three disposable PIDs exited. At that later snapshot, actual native and
model focus both again matched **workspace 2, window 11584, ChatGPT PID 52214**.
No manual focus reset was issued to produce that snapshot.

Evidence is saved in each run directory under
`.debug/aquaspace/ghostty-topology/`; the last run's `final-verification.json`
contains the cross-run checks, installed/source/helper hashes, current focus and
the explicit incomplete result. The initial-activation repair, workspace-prefix
support, four added offline tests and these notes are included in a local
checkpoint after `eff15f35`. No additional tests were run when creating this
checkpoint. A complete live restoration run remains pending a desktop
without the interfering Zen window activity.
