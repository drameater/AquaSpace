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

## Offline destination and retained-source repair after ec38b622

The user authorized fixes and offline regression tests for the two remaining
review findings, preserving all ownership checks and prohibiting any app/helper
launch, live test, installation or push. This repair stayed within that scope.

**Destination occupancy.** The old guard checked current foreground ownership
before selecting a workspace, then discovered a foreign destination occupant
only afterward. An offline reproduction ran the actual workspace-switch
function with an unrelated window already on its destination: it issued the
workspace command and focused that window before raising an error. Creation
and focus-following moves also lacked a new destination ownership read. The
saved Zen trace has owned test focus on B before a workspace switch and native
Zen focus on A afterward. It does not establish exactly when Zen arrived, so
it should not be read as proof that the foreground change was independent of
the harness's own workspace command.

The runner now reads both dedicated workspace inventories before any switch,
creation, selection/focus, move or menu dispatch. A pure policy compares exact
IDs, workspace, PID and title against the disposable model rows AND the native
selected tab memberships, already verified against recorded tab/terminal IDs.
Original IDs, foreign occupants, prefix/PID-only matches, duplicate native
titles/IDs, missing rows, malformed data and model/native disagreement refuse
the action and permanently revoke foreground permission. Creation repeats the
check after switching, and final native source validation occurs after the
destination reads. No foreign window is moved or closed to make a workspace
available. The existing manager/process/original-window checks remain.

**Source identity at admission.** The parent previously serialized only PID,
title and window presence. The child captured whichever native window was
focused when it started, so a different same-process, same-title window could
be accepted as a new source. Initial title uniqueness could become stale later.
Retaining the replacement in that child did not preserve the parent's identity.

`ScriptDriver.m` is now an in-process diagnostic library rather than a standalone
executable. The inert Python `script_driver.py` adapter passes the original
retained AX application/window references directly through typed pointer-width
C parameters, and retains the snapshot object across serialization, final source
confirmation and the native call. The native guard independently `CFRetain`s
those exact references, verifies their types/owner and compares current native
focus with the original window immediately before each mutation. It never
recaptures a replacement during initialization. The uniquely named Objective-C
guard avoids class-name collisions in the diagnostic process. Matching title
and PID cannot replace the original reference.

All scripting API operations and exact-ID background cleanup now use this
library; the native-menu process retains its existing no-activation policy.
Initial activation remains one-shot and guarded. Manager ownership, disposable
birth/executable identity, native tab/terminal IDs, original-window preservation,
prefix checks, ambiguity rejection and revocation behavior are unchanged or
stricter. No manager lifecycle, user-app recovery or action-retry path was added.

The new required capability is `ghostty-topology-retained-source-v3`. The runner
requires `script-driver.dylib`, validates its exports/capability before session
capture and refuses missing, old or incompatible libraries. It cannot fall back
to the earlier standalone helper or a metadata-only grant. The build instructions
now specify `-dynamiclib`; no library or helper binary was built in this repair.
Future explicitly authorized live validation must build that diagnostic library
first. Loading it in the runner does not inject code into either manager or
Ghostty or add macOS privileges. No library was loaded during these offline checks.

Offline validation added **19 test methods** and passed all **162 AquaSpace
Python tests**. The destination failures were observed against the old runner
before the guards were added, including a foreign occupant arriving between
switch and creation. The adapter tests execute the real ctypes boundary with
fake C entrypoints, check that the exact original full-width pointers cross it,
and simulate same-PID/same-title replacement for all four foreground operations.
They also cover windowless sources, missing/invalid pointers, final confirmation
failure, incompatible APIs and single-release error handling without retry.
Structural checks verify that native initialization retains the supplied roots
and contains no source-recapture API or executable entrypoint. These are offline
policy/adapter proofs, not a run of real AppKit or Scripting Bridge behavior.

`clang -fobjc-arc -fsyntax-only` accepted the native source without producing a
binary. Native loading, subprocesses, signals and relevant fixture writes are
blocked in the safety tests. No app/helper was launched, no live process/window
APIs were called, no installation occurred, and nothing was pushed. The fixes,
regressions and notes are included in a local checkpoint after `ec38b622`. No
additional tests were run when creating this checkpoint. Native library linkage,
Automation behavior and complete foreground restoration still need a separately
authorized live run; earlier live results must not be relabeled as this repair's
validation. Destination/focus checks remain bounded observations rather than an
atomic macOS lock against events arriving after the final check.

## Diagnostic rebuild and blocked preflight after c2a304e1 — 2026-10-15

The user authorized rebuilding the diagnostic library and running the fixed
Ghostty harness on the dedicated test workspace. The documented ARC dynamic
library build completed successfully, producing
`.debug/aquaspace/ghostty-topology/script-driver.dylib`. No manager or launcher
helper was rebuilt or installed, and the library was not loaded during preflight.

Read-only preflight at approximately 16:03 UTC could not establish the harness's
required sole installed AquaSpace controller. A process-list observation showed
stock AeroSpace PID 15583 and Ghostty PID 17528, with no AquaSpace process.
Independent kernel inspection verified PID 15583's executable as
`/Applications/AeroSpace.app/Contents/MacOS/AeroSpace`, bundle `bobko.aerospace`,
UID 501 and birth tuple `(1792077772, 930920)`. AquaSpace's installed CLI did not
respond and subsequently reported connection refused. Historical window 21423,
workspace 2 and the prior AquaSpace PID were not assumed to describe this session.

The complete process inventory also failed closed: `proc_pidpath(1071)` returned
zero with errno 2 for a live Word widget process. `ps` reported
`/Applications/Microsoft Word.app/Contents/PlugIns/WordWidget_mac.appex/Contents/MacOS/WordWidget_mac`,
and that file existed, but neither that display nor file existence was accepted
as a substitute for the required authoritative executable read. The same failure
occurred outside the command sandbox. No process was skipped, no scanner or
ownership check was weakened, and the partial process observations are not a
successful single-manager verification.

No disposable Ghostty process or windows were launched; the live runner and its
launcher were not invoked. Neither manager was started, stopped or switched,
and no workspace/focus mutation was requested. Existing-window preservation,
native merge/detach behavior and desktop focus restoration remain unvalidated
for this checkpoint. A future run first needs a complete verified process
inventory and a safe handoff to the installed AquaSpace controller, then a fresh
window/focus baseline. Nothing was pushed. These notes are local and uncommitted.

## Lost-executable inventory repair and ordered handoff — 2026-10-15

The process-inventory failure was reproducible outside the command sandbox.
Word widget PID 1071 was alive (BSD status 2) while `proc_pidpath` returned
ENOENT. Its kernel code-directory hash was
`ecf9e46032ffbc2c05e9d39383f57401c1ba6b71`; the installed arm64 Word widget
reported `a5b385e4f46afecee3374f59c713dddc351e97a1`. The running image and current
file were therefore different. This is consistent with a replaced executable;
it does not establish the precise filesystem operation responsible for the
lookup failure. The normal Security-framework process lookup also failed.
Excel widget PID 1072 had the same live-process/path failure.

Both the Swift controller scanner and Python installer/diagnostic scanner now
support a narrow alternative proof for ENOENT. They identify these two specific
nonmanager images from kernel code-signing data, rather than from `ps`, argv,
display names, an on-disk replacement, or a missing-file assumption. Acceptance
requires all of the following:

- Exact signed identifier `com.microsoft.Word.widgetextension` or
  `com.microsoft.Excel.widgetextension` and team `UBF8T346G9`.
- Kernel-validated Developer ID category 6, valid signed hardened code, enforced
  executable pages and library validation. Ad-hoc, debugged, task-access-enabled,
  killed and invalid-allowed processes are refused.
- A nonzero running code-directory hash, with two complete equal signing proofs.
- Equal before/after PID, kernel birth time, UID, main-image UUID, unique process
  ID and exec version. Every signing read uses `csops_audittoken`, binding it to
  the captured PID/exec version so a replacement image cannot supply the proof.

Unknown images, other Microsoft applications, unreadable proofs and permissions
errors still block. Unsupported kernel operations also block. No failed process
is skipped merely because its name looks unrelated, and no manager candidate's
path, bundle, birth or exclusivity checks were removed. This fallback establishes
an authenticated known nonmanager; it does not invent an executable path.
The read-only XNU ABI is declared locally and requires no root, entitlement,
SIP change or injection. The isolated Swift tests replace the added syscall with
an aborting stub, just like the existing process calls.

The ABI and trust-category semantics were checked against Apple's
[XNU code-signing implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_proc.c),
[signing flags/categories](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/cs_blobs.h)
and [exec-generation structure](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info_private.h).
The first two sources describe kernel-owned image signing data; the last defines
the 56-byte generation record. An installed replacement's static signature is
not used to authenticate the already running image.

The old committed Python scanner reproduced the lost-executable error against
the same injected kernel fixture. Ten new Python test methods cover acceptance,
unknown/manager identity refusal, wrong team, invalid trust/runtime flags, empty
hashes, denied/short/malformed reads, PID birth/exec/hash changes, token-bound
read failure, permission denial and process exit. Six new Swift tests exercise
the same acceptance/refusal policy. The native Swift reader was also compiled
alone into a read-only probe, which agreed with Python that stock AeroSpace
PID 15583 was the sole manager. No window-control code runs in that probe.

The first handoff attempt exposed a separate command-wrapper issue: `.strip()`
removed an empty final title field and title whitespace. A regression failed
for all three such output examples before the fix. `NativeSystem.run` now
removes only line endings. The focus/ambiguity checks themselves were unchanged.
All **173 AquaSpace Python tests** and **22 Swift ownership tests** pass.

The repaired source was built and signed as
`0.21.3-aquaspace.20261015.1`, using cached dependencies and the macOS 26.5 SDK.
Build output is `.debug/aquaspace/inventory-build.log`; the staged app and hashes
are in `.debug/aquaspace/release/`. The installed controller includes the Swift
inventory repair; the Python scanner is used by the updater and Ghostty harness.

Two pre-handoff attempts stopped before signaling or installation. The first
hit the empty-title parsing error. The second could not establish coherent
native/model focus: it observed loginwindow and later native Raycast focus while
stock metadata still had an empty title. No guard was bypassed. A later fresh
read had matching stock/native window identity and title; the third attempt
captured that current baseline and proceeded.

The ordered switch is recorded in
`.debug/aquaspace/inventory-switch/run-20261015-122020/`. It confirmed stock
AeroSpace PID 15583 exited, then used the guarded installer in file-only mode.
Every installation phase and the exact spawn boundary recorded zero managers.
Only then was installed AquaSpace launched once with the saved stock session.
Its sole PID is **42527**, kernel birth `(1792081221, 893887)`. The installation
transaction is `/Applications/.aquaspace-update-00559b0a15c6421da2c06f5d2914790b`.
The controller reports verified inventory, controlling state and no peers.
**218 independent inventories** observed a peak of one manager, including seven
empty inventories. There was no fallback, pause/resume, recovery or second launch.

Original Ghostty PID 17528 retained native tab/terminal identities, and its window
**28264 stayed on workspace 3**. Repeated read-only native/model focus observations
matched the exact original retained app/window and workspace after startup.
However, the broader all-window handoff comparison did not pass: hidden Zen
window 28122 was omitted from AquaSpace's managed inventory, although a later
read confirmed that exact window still existed natively in Zen. This observation
is retained as a handoff limitation, not relabeled a disappearing native window
or a complete all-window preservation pass. No foreign window was moved to
accommodate the comparison.

### First Ghostty attempt after the inventory repair

The same installed AquaSpace PID was reused on fresh empty workspaces
`native-tab-test-ghostty-c2a304e1-20261015-a` and `-b`. The v3 library loaded and
passed its retained-source checks through initial creation and activation.
During the first topology poll, both actual native and model focus returned to
the original Obsidian window/workspace. The execution gate permanently revoked
foreground permission and stopped. This is not a merge/detach or restoration pass.
Only the exact disposable tab/terminal was closed; disposable PID 43120 exited.
No restoration write overrode the departed foreground. All **124 manager
inventories** saw the same sole AquaSpace; window samples recorded no baseline
assignment drift during the attempt. Original Ghostty window 28264 stayed on
workspace 3 with unchanged native identities.

Evidence is `.debug/aquaspace/ghostty-topology/run-20261015-122117/`. Its failed
`results.json` is retained. A separate later read confirmed Ghostty preservation,
sole verified AquaSpace and disposable cleanup, but also found that original
Finder window 28450 was no longer managed. That later desktop change is not
rewritten as a full baseline preservation success. Further foreground testing
waits for the user to confirm an idle desktop. Nothing was pushed; source,
regressions and these notes remain local and uncommitted.

## In-process foreground cache repair and idle attempts — 2026-10-15

After the user confirmed an idle desktop, the first attempt reused sole installed
AquaSpace PID 42527 and stopped after initial creation/activation, before adding
the second tab. The runner's independent native/model observations still agreed
on the disposable window. Instrumenting the library's refusal distinguished
window/title validation from foreground PID: `window=1, title=1`, but AppKit
reported the preceding application's PID 3456 rather than test PID 51371.
The same refusal reproduced on the next isolated attempt. This was a diagnostic
reader defect, not evidence of an unauthorized source-window replacement.

`NSWorkspace.frontmostApplication` and `NSRunningApplication.isActive` can retain
stale state in the long-running Python host without an AppKit event loop. The
native guard now reads `AXFrontmost` from its exact retained source application
and queries the system foreground process directly with the read-only
`GetFrontProcess` / `GetProcessPID` APIs. It does not pump arbitrary pending
AppKit events or fall back to cached state. Both fresh reads must succeed and
agree on the captured PID. Retained application/window identity, window-owner
PID, exact native root equality and title checks remain in place, including
windowless-source rules. Every check still runs immediately before mutation.
Failure details expose only identity/check outcomes, not titles or terminal input.

A new native offline harness, `script/test-ghostty-foreground-offline.py`, compiles
the production Objective-C foreground guard against deterministic API stubs.
Real AX, AppKit workspace/application and process/focus APIs are never called;
Foundation/CoreFoundation objects stand in for independently retained roots.
Against the old implementation eight assertions failed: stale cache false
rejection, stale matching cache false acceptance, late foreground departure,
background AX source, nonboolean/unreadable AX data, and unreadable system
foreground/PID. All **13 native guard cases** pass with the repair, including
same-PID/same-title replacement refusal, changed-title refusal, foreign window
ownership and windowless-source checks. All **173 Python tests** also still pass.
No window manager was rebuilt, installed or relaunched during this follow-up;
only the diagnostic library was rebuilt.

Live evidence retained without converting interrupted checks into passes:

- `run-20261015-124027`: eight assertions before stale AppKit rejection; 159
  manager inventories and 48 original-window samples.
- `run-20261015-124139`: eight assertions and the instrumented stale-cache proof;
  160 manager inventories and 48 original-window samples.
- `run-20261015-124412`: fresh-reader build passed creation, two-tab single-slot
  behavior, moving the group to test workspace B and selecting tab A there. After
  tab B selection, an initial topology sample was correct, then **both** native
  and model foreground returned to the original Raycast window 28308 on workspace
  1. The execution gate stopped and permanently revoked foreground permission.
  This run reached 11 assertions, with 387 manager inventories and 206 window
  samples. It did not reach merging/detachment or establish restoration success.

All **706 manager inventories** across these three attempts observed the same
sole AquaSpace, and all **302 original-window samples** had no assignment drift.
Each disposable Ghostty process was cleaned up using exact recorded identities.
The final separate read confirmed all ten windows in the latest baseline retained
workspace/fullscreen state, original Ghostty window **28264 remained floating on
workspace 3** with unchanged tab/terminal identities, no disposable windows
remained, and only original Ghostty PID 17528 was running. Native/model focus then
agreed on Raycast window 28308/workspace 1. This separate read does not override
the revoked transaction or establish a successful guarded restoration.

The latest post-abort report records source/library hashes. Source
`ScriptDriver.m` is `d56047c7c0f5d5e3dd5f10849c162ef643b825cbb4ba4d3510e0d4c438286f0b`;
rebuilt library is `422bd2821971e8f39b1d4f3b1f309489423525eb152416e29de8263a87aaf115`.
The diagnostic repair took longer than the initially requested two-minute idle
window. Further foreground runs wait for clarification whether Raycast was used
or refocused itself. No ownership/ambiguity guard was relaxed, no terminal input
was sent, and nothing was pushed. Changes and notes remain local and uncommitted.

### Extended native merge/detach attempt after renewed idle confirmation

The user confirmed that they had used Raycast during the previous attempt and
that the desktop was idle again. Run
`.debug/aquaspace/ghostty-topology/run-20261015-124846/` reused the same sole
AquaSpace PID 42527 and the unchanged fresh-reader library. It created a new
owned disposable Ghostty process (PID 55417) on the empty workspace pair
`native-tab-test-ghostty-final-idle-20261015-a` / `-b`.

This attempt passed real Ghostty native-menu checks for merging a two-tab group
into the independent destination on A, selecting all three merged tabs without
workspace changes, detaching from three tabs while preserving A, moving that tab
independently to B, a two-to-one detachment on A, and merging the resulting three
bare windows into destination B. All three remerged selections retained B.
These core operations were reached and passed; the additional repeated detach,
move and remerge cycle was not reached.

About 100 seconds into the run, both native and model foreground changed to the
existing Raycast window 28308/workspace 1 between destination validation and the
final `before-script-select` check. The gate refused the next selection, revoked
foreground permission, and closed the three exact disposable tabs in the
background. PID 55417 exited. The run records **24 assertions**, including
cleanup, but `success=false`: it did not establish guarded focus restoration or
complete the full suite. Its failure record is not relabeled as a successful run.

All **1,593 manager inventories** observed the same sole AquaSpace. All **461
original-window samples** preserved workspace/fullscreen state. A separate
post-abort read confirmed all ten baseline assignments, original Ghostty window
28264 floating on workspace 3 with unchanged tab/terminal identities, no remaining
test windows, and only original Ghostty PID 17528. A later focus read was on a
personal ChatGPT window/workspace 3; the harness did not overwrite that state.
Across the four idle attempts, **2,299 process inventories** and **763 window
samples** record the same exclusive controller and no original assignment drift.

Further foreground testing waits for clarification about the latest Raycast
activation. Switching to a personal app, including this chat, during the native
menu test necessarily ends its foreground authorization. No guard is relaxed
for an incomplete test, and no native action is retried after an uncertain reply.

## Completed fresh-reader Ghostty validation — 2026-10-15

The user reported **no input during run 124846** and said they would close Raycast
before retrying. Its foreground change must therefore not be attributed to user
input. Read-only preflight then confirmed no Raycast window in the manager model,
original Ghostty window 28264 still on workspace 3 and native ChatGPT foreground.
The exact cause of Raycast's earlier activation was not isolated. This final run
succeeded after the user closed its window; that is the observed condition, not
proof of a particular Raycast or macOS bug.

The complete run is
`.debug/aquaspace/ghostty-topology/run-20261015-125356/`.
It reused the same sole installed AquaSpace PID **42527** and the unchanged
fresh-reader diagnostic library, with no manager rebuild, installation or launch.
Dedicated workspaces were
`native-tab-test-ghostty-no-raycast-20261015-a` and `-b`.
Disposable installed Ghostty PID **60331** was distinct from protected original
PID **17528**. No terminal input or process-name-based targeting was used.

**All 32 assertions passed, `success=true`, with no errors.** Native Ghostty
membership was independently checked through its scripting API while the actual
retained native menu elements performed three merges and three detachments.
The run covered initial two-tab single-slot behavior, cross-workspace group
movement, all selections, merging into a bare destination across workspaces,
three-tab detachment, independent detached-window movement, two-to-one detach,
merging three bare windows, a second detach/move/remerge cycle and cleanup.
All destination-ownership, native tab/terminal, retained source-root, foreground,
original-window, process-birth and ambiguity checks remained enabled.

Preservation and restoration evidence:

- Original Ghostty window **28264 remained floating on workspace 3** in all
  **895 original-window samples** and in the final model. Its original native
  tab/terminal identities were unchanged before and after cleanup.
- All **nine windows in this run's fresh baseline** retained their workspace and
  fullscreen state; no preservation sample recorded a mismatch. This baseline
  was captured after the user closed Raycast and does not relabel the earlier
  hidden-Zen handoff omission as repaired or preserved by that handoff.
- All **1,720 process inventories** saw exactly the same sole AquaSpace PID 42527,
  birth `(1792081221, 893887)`, executable `/Applications/AquaSpace.app/Contents/MacOS/AquaSpace`.
  The process-monitor peak was one. Runtime ownership/inventory checks also passed
  at every guarded operation and final verification.
- Before closing the test tabs, the harness restored starting workspace **3**,
  model window **28146**, and the exact retained native ChatGPT application/window
  (PID **15689**). Three matching samples spanned **1.218 seconds** before cleanup.
- After closing only exact recorded test tab/terminal pairs and terminating only
  the empty disposable process, three more matching native/model samples spanned
  **2.015 seconds**. The final focus report is `verified-after-cleanup`,
  `verified=true`, with no revocation. This is the completed guarded restoration
  result, rather than an independent later desktop snapshot.
- No disposable managed windows remained, and disposable PID 60331 was confirmed
  exited. Original Ghostty identities survived cleanup.

`results.json`, `events.jsonl`, `baseline.json`, `process-samples.jsonl`,
`window-samples.jsonl` and the derived `completion-summary.json` retain the proof.
The source/library hashes match the earlier fresh-reader build:
`d56047c7c0f5d5e3dd5f10849c162ef643b825cbb4ba4d3510e0d4c438286f0b` and
`422bd2821971e8f39b1d4f3b1f309489423525eb152416e29de8263a87aaf115`.
The installed app/CLI hashes recorded in the completion summary are
`854ae357aabd875a709c09fbab9f9a49896f3548359b959e82873aa7b5aad394` and
`04eb00cf789fd45b2e8ca9ae924921ac2260074cfb96f695d55d14ae78fdc2ea`.

The native reader repair remains covered by 13 offline guard cases; all 173
Python tests and 22 Swift ownership tests passed during this repair sequence.
The previously failed live runs remain failed in their own evidence. After the
completed run the user was told they could resume desktop use; only saved
artifacts and documentation were inspected afterward. No more live test was run,
no further manager/helper was launched, and nothing was pushed. The inventory and
reader repairs, offline regressions and updated notes are included in a local
checkpoint after `c2a304e1`. No additional tests, installations, or app/helper
launches were performed when creating this checkpoint.

## Offline stale-library admission repair after 734ebf62

Review found that the repaired fresh foreground reader still exported the same
`ghostty-topology-retained-source-v3` capability as the previous cached-reader
library. The Python adapter accepted that token, so an unrecompiled diagnostic
library could silently omit the native reader repair. The successful live run
used the correct fresh-reader binary; its saved hashes/results remain unchanged.

The native export and Python adapter now require
`ghostty-topology-retained-source-v4`. Old v3 libraries fail admission before
session capture, disposable launch or driver operations. No fallback, automatic
rebuild or action retry was added. No manager, window/root identity, foreground,
destination ownership, process-birth or ambiguity check changed.

Two added offline regressions first failed against the checkpoint: the real
adapter accepted an injected legacy-v3 library, and the native/consumer source
capabilities still declared v3. They now pass. The rejection test records one
fake load and zero run/free calls; native library loading, subprocesses and
signals are forbidden. A source check also requires the native export and Python
requirement to agree on v4. Existing retained-source tests use v4 fake libraries,
preserving current-version admission and all original refusal cases.

All **175 AquaSpace Python offline tests pass**. No compiler/native guard test,
installed CLI, process inventory, app, diagnostic helper or real native library
was run or loaded. No build, installation, live test or push occurred. The existing
on-disk v3 diagnostic binary was deliberately left untouched and will now be
rejected until separately authorized rebuilding. Earlier live evidence remains
historical; v4 admission has only this offline validation. The admission fix,
regressions and notes are included in a local checkpoint after `734ebf62`.
No additional tests, builds, installations or app/helper launches were performed
when creating this checkpoint, and nothing was pushed.

## v4 Ghostty live validation after 388b3117 — 2026-10-15

After explicit authorization, only the diagnostic library was rebuilt from
checkpoint `388b3117`. The native export and Python adapter both require
`ghostty-topology-retained-source-v4`; the full runner passed that admission
before capturing its session or launching a disposable process. No stale-library
compatibility path or ownership/focus check was changed.

Run `.debug/aquaspace/ghostty-topology/run-20261015-150139/` completed **all 32
assertions**, `success=true`, with no errors. It exercised three actual native
Ghostty merges and three detachments, including cross-workspace merging, both
three-tab and two-to-one detachments, independent detached-window movement,
repeated selection and the final detach/move/remerge cycle. Test windows alone
used `native-tab-test-ghostty-v4-20261015-a` / `-b`.

- Original Ghostty window **28264 remained floating on workspace 3** in every one
  of **626 original-window samples** and in the final model. Original PID 17528
  and its native tab/terminal identities were preserved through cleanup.
- All **nine windows in the fresh baseline** retained workspace/fullscreen state,
  with zero preservation mismatches. This does not revisit the separately
  recorded hidden-Zen omission during the earlier stock-to-fork handoff.
- All **1,812 manager inventories** observed the same sole AquaSpace PID **42527**,
  birth `(1792081221, 893887)`. Peak manager count was one. Ownership/inventory
  checks also passed before every guarded action and final verification. Neither
  window manager was rebuilt, installed, started, stopped or restarted.
- Starting workspace **1**, model window **28146**, and the exact retained native
  ChatGPT app/window (PID **15689**) were restored before cleanup: three matching
  observations spanned **1.543 seconds**. After cleanup another three matching
  observations spanned **1.115 seconds**. The report is `verified-after-cleanup`,
  `verified=true`, without revocation.
- All disposable tab/terminal pairs were closed, no disposable managed windows
  remained, and only the empty disposable Ghostty PID **7126** was terminated and
  confirmed exited. No terminal input was sent.

The run's `results.json`, `events.jsonl`, baseline, process/window samples and
`completion-summary.json` retain the evidence. Source `ScriptDriver.m` SHA-256 is
`5590a019cef6fc6aa52d3a84dceef8c07d27f4616418940cc59a332fed7994ab`;
Python adapter is `283ba08cf01bb8fde41d5c2839543f6712f65ee2661f44a6a12fca1582c606c8`;
rebuilt v4 library is `30bb9955adb1423af8806f0c131c930d6bbc349e1b1dae1304fa746537b6deea`.
Installed app/CLI hashes recorded in that summary match the prior installation.
The earlier v3 results remain historical; this is the new complete v4 result.

The user was told to resume desktop use after the completed run. Only saved
artifacts and documentation were inspected afterward; no further installed CLI,
app/helper or live test was invoked. These updated live notes form a local
documentation checkpoint after `388b3117`. No additional tests or launches were
performed while creating that checkpoint. Nothing was pushed.

## Offline cleanup termination repair after c56ce874 — 2026-10-15

The v4 checkpoint review reproduced a cleanup safety gap after its last empty-
membership and process-birth checks. `record_cleanup_focus('before-terminate')`
can block on model/native reads; afterward the Swift helper resolved a fresh
application using only PID and bundle ID. An unrecorded tab appearing during that
observation could be closed, and PID replacement could redirect termination.
Both reproductions exercised the runner's actual finalizer with fake APIs and
reached termination under the old code.

The runner now revalidates manager/original-window preservation, pinned process
birth/executable and empty native membership after that observation. It then
passes the expected birth tuple and protected original PID list into a new
in-process `terminate` operation. The receiver independently validates the proof,
UID, kernel birth and exact installed Ghostty executable, retains the verified
native application, reads its native empty window list twice and brackets those
reads with repeated kernel identity checks. An unreadable or malformed list is
never treated as empty. Kernel identity also brackets executable path reads.
The final termination request addresses the retained native application, without
intervening focus observations, logs or subprocess startup. Refusal/uncertain
responses produce an error without retry or fallback. The Swift PID-only
termination command has been removed.

The native library and adapter now require
`ghostty-topology-retained-source-v5`. Existing v3 and v4 libraries are refused
before session capture or disposable launch. All foreground, destination,
original-window, native tab/terminal and manager ownership checks remain in force;
the process inventory and window-manager implementations were not changed.

Offline validation:

- Both added final-observation regressions failed against the old finalizer and
  now prevent termination, leaving the unrecorded tab/replacement process alone.
- All **183 Python tests** pass, including manager uncertainty after observation,
  exact identity forwarding, protected/malformed identity refusal, stale-library
  rejection, native-error no-retry handling and retired Swift command checks.
  Native-library loading, subprocesses and signals are blocked in these tests.
- The actual native guard passes **26 deterministic cases** using fake kernel,
  Ghostty scripting and native application APIs. They cover unchanged empty
  cleanup, new tabs, process replacement at binding/enumeration/path/kernel
  boundaries, unknown window replies, identity errors and refusal without retry.
  Only a separate test binary was built/run; it executes no real desktop/process
  APIs. `ScriptDriver.m` also passes syntax-only compilation.

No diagnostic library or helper was rebuilt, loaded or launched; neither app,
installed CLI nor live process reader was invoked. Nothing was installed or
pushed. The earlier 32-assertion v4 live validation remains historical; this v5
cleanup repair has only offline validation and is recorded in a local checkpoint
after `c56ce874`. No additional tests, builds, installations or app/helper launches
were performed while creating the checkpoint. Nothing was pushed.

## v5 Ghostty live validation after 3fd4ba73 — 2026-10-15

After explicit authorization, only `script-driver.dylib` was rebuilt from
`3fd4ba73`. The harness admitted its required
`ghostty-topology-retained-source-v5` capability and completed the full real
Ghostty native-menu suite. No manager was rebuilt, installed, launched, stopped
or restarted. The Swift helper was not rebuilt; its existing binary supplied
read-only observations and the disposable launch. Cleanup invoked the new
in-process termination operation.

Run `.debug/aquaspace/ghostty-topology/run-20261015-173153/` passed **all 32
assertions**, `success=true`, with no errors. Disposable windows alone used
`native-tab-test-ghostty-v5-20261015-a` / `-b`. The suite performed three actual
native merges and three detachments, including cross-workspace merging,
three-tab and two-to-one detachment, independent movement, merging three bare
windows, repeated selections and the final detach/move/remerge cycle.

- The fresh preflight found original Ghostty window **28264 on workspace 1**;
  that current assignment was the preservation baseline. The test did not return
  it to workspace 3 from the older v4 record. It stayed floating on workspace 1
  in all **565 original-window samples** and the final model. Original PID
  **17528** and its native tab/terminal identities remained unchanged.
- All **nine baseline windows** retained workspace/fullscreen state, with zero
  recorded mismatches. This fresh baseline does not retest the earlier
  stock-to-fork hidden-Zen handoff limitation.
- All **1,900 manager samples** observed the same sole AquaSpace PID **42527**,
  birth `(1792081221, 893887)`, at the installed path. Peak count was one.
  Control/inventory ownership checks also passed throughout the guarded actions
  and final verification. No window-manager lifecycle action occurred.
- Original workspace **1**, model window **28122**, and its exact retained native
  app/window (PID **15654**) were restored before cleanup. Three matching samples
  spanned **0.591 seconds**; after cleanup three matching samples spanned
  **0.564 seconds**. Focus finished `verified-after-cleanup`, `verified=true`,
  with no revocation and only one restoration request.
- Exact recorded tabs were closed before cleanup requested native v5 termination
  of the empty, pinned disposable PID **53112**, birth
  `(1792099915, 410460)`. `cleanup-terminate` records
  `{"terminationRequested":true}` from the library, and the disposable PID was
  confirmed exited. No disposable managed windows remained. The removed Swift
  PID-only termination command was not invoked; no terminal input was sent.

Saved `results.json`, `events.jsonl`, baseline, process/window samples and
`completion-summary.json` retain the evidence. SHA-256 values:

- Native source: `929bfee4b6da367d66939d80fc0b989bb0bb16cc1e1f4955a01c1d2c745bf355`.
- Python adapter: `48eddf7b644aa3a1acb9510969f43eb3535e9782dbfaa038b4217ac9f107e58b`.
- Rebuilt v5 library: `f42d643d084ce9d27012c444e6997c7e0469ac308dcf46e1ea0a564c357da769`.

Installed app/CLI hashes in that summary match the previous installation. The
earlier v4 result remains historical evidence for its exact binary; this is the
new v5 result. The unsafe-arrival/replacement cases were validated with offline
regressions, not injected into this live run.

The user was told to resume desktop use after completion. Only saved artifacts,
source/binary files and documentation were inspected afterward; no further live
process/focus query, installed CLI, app/helper or live test was invoked. Nothing
was pushed. These live validation notes form a local documentation checkpoint
after `3fd4ba73`. No additional tests, builds, installations or app/helper launches
were performed while creating the checkpoint.

## Offline background-cleanup focus repair after 943558e3 — 2026-10-15

Source review found that restoration failure only marked the run failed; cleanup
still closed recorded tabs even if disposable Ghostty remained foreground.
Its last foreground close could hand focus to an unrelated original window or
workspace. Best-effort cleanup observations logged focus-read errors but did not
establish background admission. The earlier ignored-focus regression modeled
that handoff and checked failure reporting without preventing the close.

The runner now independently requires confirmed native and model background
focus before every close and termination. Admission uses the actual read-only
focus observer, including its retained-root/final foreground confirmation;
unreadable focus, stale model focus on the disposable process, or foreground
Ghostty refuses the mutation. Confirmed background cleanup still works after a
user switches elsewhere, without regaining focus or retrying restoration. On
refusal it records `cleanup-incomplete` with the disposable PID and exact recorded
tab identities and leaves remaining disposable tabs/process untouched.

The native receiver independently checks the target's typed AX foreground and
window attributes plus the system's fresh foreground PID after blocking target
enumeration. It never uses cached AppKit foreground state. Kernel birth/UID and
exact executable checks bracket that final background read. Exact tab/terminal
resolution is unchanged; closes additionally require the pinned birth/protected
PID proof already required by termination. Termination keeps both Scripting
Bridge empty-window reads and also requires an empty AX window list in its final
background snapshot, catching a new window during that read. Neither cleanup
operation activates an app or retries a failed action. The manager inventory,
destination ownership, original-window and execution-focus guards are unchanged.

Native export and Python admission now require
`ghostty-topology-retained-source-v6`, refusing stale v5 libraries before capture
or launch. The existing diagnostic library was not rebuilt or loaded.

Offline validation:

- Five failing cases reproduced the old finalizer: ignored restoration,
  unreadable focus, foreground return before the first close, foreground return
  between closes, and an empty disposable process becoming foreground before
  termination. All now leave the unsafe remainder untouched, with no extra focus
  write or successful-run claim.
- All **190 Python tests** pass. Additional coverage exercises the real focus
  observer through cleanup admission, stale model/native disagreement, late
  focus changes during observation logging, pinned exact-close identity
  forwarding, protected-process refusal and stale-v5 admission rejection.
  Native loads, subprocesses and signals are blocked in these tests.
- All **52 native cases** pass using fake process, focus, Ghostty scripting and
  application APIs against the actual close/termination guard source. Coverage
  includes foreground and unreadable/malformed snapshots, disagreement between
  AX and fresh system foreground, late focus acquisition, process replacement
  during background reads, and new windows during termination admission. The
  earlier identity/empty-membership refusal cases still pass. Only an isolated
  test binary was built/run; no real desktop/process API is executed by it.
- `ScriptDriver.m` passed syntax-only compilation; the diff passes whitespace
  checks.

No app or diagnostic helper/library was launched or rebuilt, no installed CLI or
live process/focus reader was invoked, and no live test or installation occurred.
Nothing was pushed. The v5 live validation remains historical evidence for its
exact binary; the v6 background-cleanup repair has offline validation only and
is recorded in a local checkpoint after `943558e3`. No additional tests, builds,
installations or app/helper launches were performed while creating the checkpoint.
Nothing was pushed.

## v6 foreground-interrupted Ghostty attempt after b9aadb8f — 2026-10-15

After explicit authorization, only the diagnostic library was rebuilt from
`b9aadb8f`. The v6 harness admitted the new capability and ran on
`native-tab-test-ghostty-v6-20261015-a` / `-b`. Neither manager was rebuilt,
installed, launched, stopped or restarted. The existing Swift helper supplied
read-only observations and the disposable launch; it was not rebuilt.

Run `.debug/aquaspace/ghostty-topology/run-20261015-180001/` stopped after **seven
assertions**, before the two-tab grouping settled or any merge/detach operation.
The independent native foreground reads disagreed, followed by actual native/model
focus on the baseline Helium window on workspace 5. The saved evidence cannot
attribute that change to user input or autonomous application behavior. Execution
permission was revoked; no restoration write or action retry occurred. The result
is `success=false`, with focus restoration unverified, not a completed v6 pass.

All **206 manager samples** observed the same sole AquaSpace PID **42527**, birth
`(1792081221, 893887)`. All **63 original-window samples** preserved the seven
fresh-baseline workspace/fullscreen assignments. Existing Ghostty window
**28264 stayed on workspace 1**; its harness baseline/final layout was
`macos_native_window_of_hidden_app`. The earlier preflight saw it tiled, before
the harness captured that fresh hidden-window baseline. No original-workspace
movement was observed.

Both exact disposable tab/terminal pairs passed the v6 background-admission and
native close guards. The failed-run error latch then blocked its normal final
preservation check before termination, leaving an empty disposable PID **99908**,
birth `(1792101603, 79799)`. A supplemental cleanup independently reverified the
same sole manager/control/inventory, every original workspace/fullscreen state,
original Ghostty native identities, exact disposable process birth/executable,
absence of disposable managed/native windows, and native/model background focus.
Only then did the same v6 native termination guard request that empty process's
exit; its exit and the original identities/assignments were checked afterward.
No ownership check or source guard was relaxed. Supplemental cleanup made **zero
focus writes** and left its observed native/model desktop focus unchanged.

The original run remains failed. `supplemental-cleanup.json` records the separate
successful cleanup; `completion-summary.json` records both outcomes and hashes.
Native source SHA-256 is
`e7c0ce9c05699141bf30cdfa2e907af99ae7627dc75b5d7fc09faf65aad03349`;
adapter is `35516873e61ddf6292652c4f18219210a3a7bcf5d0ad9b5555d1c7e4907fcf48`;
rebuilt v6 library is `44075dc36adc3ffb95070b1e1470a11112db31f4263bc97d108dfa8d549b0dc5`.
Installed app/CLI hashes remain unchanged. The user subsequently confirmed the
desktop was idle and authorized the separate retry below. Nothing was installed
or pushed. These attempt notes remain local and uncommitted.

## v6 Ghostty live validation after b9aadb8f — 2026-10-15

After the user confirmed the desktop was idle and authorized retry, the unchanged
v6 library built from `b9aadb8f` completed the full real Ghostty native-menu suite
on the fresh pair `native-tab-test-ghostty-v6-retry-20261015-a` / `-b`.
No manager or Swift helper was rebuilt or installed, no manager was launched,
stopped or restarted, and no ownership or focus guard was changed for the retry.

Run `.debug/aquaspace/ghostty-topology/run-20261015-180417/` passed **all 32
assertions**, `success=true`, with no errors. It exercised three native merges
and three detachments, cross-workspace merging, three-tab and two-to-one
detachment, independent movement, merging three bare windows, repeated tab
selections and the final detach/move/remerge cycle.

- All **seven fresh-baseline windows** retained workspace/fullscreen state in
  **571 original-window samples**, with zero mismatches. Existing Ghostty window
  **28264 stayed on workspace 1**, with the same hidden-window layout in every
  sample and the final model. Original Ghostty PID **17528** and native tab/
  terminal identities were preserved before and after cleanup. This fresh
  baseline does not retest the older stock-to-fork hidden-Zen handoff limitation.
- All **1,893 manager samples** observed the same sole installed AquaSpace PID
  **42527**, birth `(1792081221, 893887)`. Peak count was one; verified control/
  inventory and ownership checks also passed throughout the guarded actions.
- Original workspace **1**, model window **28146**, and the exact retained native
  ChatGPT app/window (PID **15689**) were restored with a single focus request.
  Three matching observations spanned **0.584 seconds** before cleanup, and
  three matching observations spanned **0.545 seconds** afterward. Focus finished
  `verified-after-cleanup`, `verified=true`, with no revocation.
- Three strict `admit-close-*` observations preceded the exact recorded tab/
  terminal closes; each passed the independent v6 native background/process
  guards. `admit-terminate` preceded native guarded termination of only the empty
  disposable PID **2282**, birth `(1792101858, 946940)`. Its exit was confirmed,
  no disposable managed windows remained, and no `cleanup-incomplete` event
  occurred. No terminal input or additional focus-restoration request was sent.

Saved results, events, baseline, process/window samples and
`completion-summary.json` retain the evidence. Native source SHA-256 is
`e7c0ce9c05699141bf30cdfa2e907af99ae7627dc75b5d7fc09faf65aad03349`;
adapter is `35516873e61ddf6292652c4f18219210a3a7bcf5d0ad9b5555d1c7e4907fcf48`;
v6 library is `44075dc36adc3ffb95070b1e1470a11112db31f4263bc97d108dfa8d549b0dc5`.
Installed app/CLI hashes match the preceding installation. The first interrupted
attempt remains failed; this independent complete result does not relabel it.
Unsafe foreground/read-error cases retain their offline regression evidence;
they were not artificially injected into this live run.

The user was told to resume desktop use after completion. Only saved artifacts,
source/binary files and documentation were inspected afterward; no further live
process/focus query, installed CLI, app/helper or live test was invoked. Nothing
was installed or pushed. These updated live validation notes form a local
documentation checkpoint after `b9aadb8f`. No additional tests, builds,
installations or app/helper launches were performed while creating the checkpoint.

## Offline foreground-target and menu ownership repair after 44578401 — 2026-10-15

The v6 checkpoint review found two older ownership gaps. First, the native
foreground driver checked its source and then bound the target by PID/bundle ID
without requiring the disposable's recorded birth/UID proof. The original source
can remain valid during first creation while the target exits and its PID is
reused by another Ghostty. Second, exact native membership was checked only in
the parent before starting the menu child. An unrecorded background window could
inherit the disposable configuration's valid prefixed initial title and pass the
child's prefix-only checks, allowing Merge All Windows to include it. The
protected original Ghostty PID remained excluded, but neither conditional gap
met the recorded-ownership contract.

Foreground create/add/select/activate now require the same pinned process proof
as cleanup. The native receiver rechecks birth/UID/executable after potentially
blocking source capture and immediately after final source validation, before
mutation. Exact-anchor enumeration remains required for add/select/activate.
Creation rechecks lifetime before its subsequent title action. The runner also
uses a new proof-required read-only `owned-status` operation for every disposable
membership snapshot; it rechecks target lifetime after enumeration. Personal
status reads remain read-only. Unreadable native window/tab collections now fail
instead of silently becoming an empty inventory.

Menu dispatch now runs inside the runner, using its recorded expected topology
and the same pinned native reader. It compares exact group IDs, selections, tab/
terminal identities and titles before menu opening and before the action, with
further validation around AX enumeration. All exposed AX roots must map to exact
recorded member titles, every selected group must be represented, and duplicate
titles/roots and changed retained roots are refused. Recorded inactive tab roots
may be exposed without admitting unrecorded content. Prefix matching remains a
necessary check, never sufficient ownership. The reader also rechecks execution
focus; departure/read errors revoke permission, cancel only the retained menu and
stop without retry. The old standalone prefix-only menu CLI is inert/retired.
Its help and invalid-argument paths still perform no native work.

Both native export and adapter now require
`ghostty-topology-retained-source-v7`. Stale v6 libraries are refused before
session capture/disposable launch. Existing source-root, ambiguity, destination,
original-window, background-only cleanup and manager inventory guards remain in
force; no manager lifecycle or privilege mechanism was added.

Offline validation:

- Two prefixed-unrecorded-window tests first failed against the old menu policy,
  including arrival after menu opening. They now prevent menu/action presses.
- A missing-target-proof test failed for all four foreground operations before
  the fix. The adapter now refuses missing, mismatched or protected proof before
  native entry, and runner wiring passes the original proof for mutations and
  owned snapshots.
- All **204 Python tests** pass, including hidden unknown tabs, changed terminal/
  group/selection identity, duplicates, unreadable membership, same-title AX root
  replacement, recorded inactive-root variants, late user focus and retired CLI
  behavior. Native loads, subprocesses and signals are blocked in these tests.
- All **105 native cases** pass using fake process, focus, scripting and app APIs:
  the actual native operation body has 40 target-admission cases; the existing
  52 cleanup and 13 retained-foreground cases still pass. The new suite was also
  replayed against saved `44578401` native source and reproduced mutations despite
  replacement/missing/protected proof while the original source stayed valid.
  Current-source cases reject those mutations, including target churn during
  source capture, native binding and anchor enumeration. The follow-up title
  write is refused if target identity changes after creation. Owned reads reject
  replacement and unreadable inventories.
- Native source passes syntax-only compilation and the diff passes whitespace
  checks. Only isolated test binaries were built/run; none execute real desktop,
  process, focus or Ghostty APIs.

No app or diagnostic helper/library was launched, rebuilt or loaded, no installed
CLI/live reader was invoked, and no live test or installation occurred. Nothing
was pushed. The completed v6 live run remains historical; v7 has offline
validation only. These fixes, regressions and notes form the local checkpoint
after `44578401`. Creating this checkpoint involved no additional tests, builds,
installations, app or helper launches, or live checks. Nothing was pushed.

## V7 Ghostty live attempt and authorized recovery after 34e0f9af — 2026-10-15

The user authorized rebuilding the diagnostic library and running the v7 harness
on test workspaces, preserving existing Ghostty/focus and sole-manager ownership.
Only `script-driver.dylib` was rebuilt; the installed manager/CLI and existing
launcher helper were reused. No manager lifecycle action or installation occurred.

Run `.debug/aquaspace/ghostty-topology/run-20261015-190454/` used the fresh empty
pair `native-tab-test-ghostty-v7-20261015-{a,b}` and passed **12 preliminary checks**:
separate disposable creation, no restored windows, native two-tab grouping,
group movement, both tab selections and an independent destination. It then
stopped **before the first merge action** with
`DriverError('Ownership: Unreadable native windows')`. This is a failed run;
no v7 merge/detach or desktop-restoration pass is claimed.

The last successful exact membership/focus observation preceded opening the
retained Window menu. The next exact membership read, while the menu was open,
failed roughly ten seconds later, consistent with the native driver's ten-second
Apple-event timeout. The retained menu cancellation then returned. A separate
read-only diagnostic, bound to the same birth/UID/executable, successfully read
the unchanged complete three-tab membership after cancellation. Read-only AX
enumeration also exposed the Window menu and its Merge/Detach items while closed.
This points to a menu-tracking/Scripting Bridge interaction in the new harness
path; the evidence does not establish a window-manager topology failure or a
definitive Apple-event error code. No guard was bypassed, read failure converted
to an empty inventory, or action retried. Harness changes remain future work.

Preservation evidence:

- **800 manager samples** all observed the same sole installed AquaSpace PID
  **42527**, birth `(1792081221, 893887)`, peak count **1**.
- **237 window samples** preserved all **eight fresh baseline** workspace and
  fullscreen assignments with zero drift. Original Ghostty window **28264**
  remained on workspace **1**. The failed run's final verification was blocked
  by its error latch; later independent recovery reads confirmed the original
  Ghostty native tab/terminal identities and baseline assignments again.
- The initial native/model desktop focus was Helium PID **15590**, window
  **28101**, workspace **5**. The membership error permanently revoked this
  run's foreground authorization. Restoration issued **zero focus requests**;
  background cleanup correctly refused while disposable Ghostty was foreground.
  Its three tabs were left recorded, with `cleanup-incomplete` evidence.

The user then explicitly authorized one guarded original-focus return and exact
disposable cleanup. By recovery entry, native/model focus instead belonged to
ChatGPT PID **15689**, window **28146**, workspace **1**. The fresh recovery
preserved that new focus and issued **zero focus writes**. Independent manager,
baseline, native membership and pinned-process reads preceded cleanup; the v7
receiver's background/birth/executable guards admitted three exact tab/terminal
closes and termination of only disposable PID **34940**, birth
`(1792105496, 29756)`. Its exit was confirmed. A final read-only observation
confirmed the same sole AquaSpace, all eight original assignments unchanged,
disposable exit and matching ChatGPT native/model focus. This does not establish
restoration to the original Helium focus. Evidence is saved separately in
`supplemental-recovery.json` and `post-recovery-observation.json`; the original
`results.json` remains unsuccessful.

Source SHA-256:
`dc3537027531c9825c6efd05fbc8f1e2451f71e6455b48fd674164c2bf7ebd3b`;
adapter `cddd83296f81f7a24f75c61d3f8da30fd577583ad1f10cc3346364c43ffed472`;
built v7 library `73861d20dd39c95baa264c81428e73d4229ee7d56efc5dbd70b32993c8568735`.
Installed manager/CLI hashes still matched the preceding v6 record. No manager
was launched, paused or stopped, no terminal input was sent, and nothing was
installed or pushed. These attempt/recovery notes are included in the subsequent
local menu-tracking repair checkpoint.

## Offline menu-tracking membership repair after 34e0f9af — 2026-10-15

The user authorized fixing the v7 menu-open reader failure and adding offline
regressions, with app/helper launch, live testing, installation and pushing
explicitly prohibited. This repair changes only the Python menu dispatch/tests
and documentation; the native library, its v7 admission capability, proof checks,
and all manager/window/focus policies remain unchanged. No helper/library rebuild
is needed for this Python dispatch change.

The saved live trace points to menu tracking blocking Scripting Bridge replies:
exact membership reads succeeded before opening Window, failed after the open
with the driver's roughly ten-second timeout, and succeeded read-only again
after retained-menu cancellation. The prior read-only probe also confirmed that
Ghostty exposes the complete native Window menu/action tree while closed. A
transient unrelated native-read failure and AX-root churn were considered as
alternatives; neither explains that ordering as directly. No new live probe was
performed, so the underlying native timeout mechanism is an inference rather
than a newly observed Apple-event error code.

The offline reproduction runs the actual `run_menu` policy against a fake AX
backend and a native membership callback that refuses replies while the menu is
tracking. Both merge and detach failed before the fix with the same
`Ownership: Unreadable native windows` error at the post-open membership read.
It now passes by removing the preliminary Window-menu press, not by relaxing
the read. The policy retains the exact closed-tree menu/item references,
rechecks the full native group/tab/terminal/title/selection inventory and pinned
AX roots around enumeration, checks target PID and foreground, and issues one
direct `AXPress` to the requested item. There is no menu-opening fallback,
compatibility reader, cached-membership admission, activation or action retry.
Unavailable, unreadable, disabled or ambiguous closed menu trees stop before
any mutation. An attempted item press gets one best-effort cancellation against
the same retained, PID-checked menu, including on an uncertain action result;
pre-press failures need no cancellation because no menu was opened.

All **211 AquaSpace Python offline tests** pass, including **41 menu/live-path
safety tests**. Seven added test methods cover the modal reader reproduction
for both operations, topology changes at the action without stale post-action
admission reads, unavailable closed trees without fallback, exact late native
membership changes, hidden-tab arrival during final AX enumeration, foreign menu
elements, and foreign/unreadable cancellation without retry. Existing late
arrival, same-title-root replacement, read-error, process-churn and user-focus
tests now inject at closed-tree action discovery rather than the removed
menu-opening press. The pinned reader still runs after those injections; it
refuses hidden unknown tabs, changed groups/terminals/selections/titles,
duplicates and unreadable membership. The uncertain-action test covers both
merge and detach and requires exactly one retained-menu cancellation attempt.

Tests block native-library loading, subprocesses and signals. No native test
binary was compiled/run, no diagnostic library was rebuilt/loaded, no installed
CLI or native process/focus reader was invoked, and no app/helper was launched.
The diff passes whitespace checks. No installation, live test or push occurred.
The direct closed-menu action still requires separately authorized live
validation; the prior failed v7 run and its recovery remain historical and are
not relabeled as passing. The repair, regressions and notes form the local
checkpoint after `34e0f9af`. Creating this checkpoint involved no additional
tests, builds, installations, app/helper launches or live checks. Nothing was
pushed.

## Fixed v7 Ghostty live validation after 1789ab2d — 2026-10-15

The user authorized running the fixed harness on dedicated test workspaces,
verifying native merge/detach, focus restoration, existing Ghostty preservation
and sole-manager ownership. The committed Python menu fix and already built v7
library were used unchanged. No manager or diagnostic helper/library was rebuilt,
no installation or manager lifecycle action occurred, and nothing was pushed.

The first fixed-path attempt,
`.debug/aquaspace/ghostty-topology/run-20261015-191915/`, passed ten preliminary
checks before native/model focus changed from the test to existing Helium window
28101 on workspace 5. The source of that foreground change was not attributed.
The execution guard stopped before any merge/detach/menu dispatch, issued zero
restoration requests, and closed its two exact recorded tabs in the background.
The error latch then prevented normal final termination. A separate fresh
sole-manager/baseline/process-birth/executable/empty-membership/background check
admitted the same v7 guarded termination of only disposable PID **67545**, birth
`(1792106356, 867849)`. Exit and unchanged native/model focus were confirmed;
`supplemental-cleanup.json` records zero focus writes. All **339 manager samples**
saw the same sole AquaSpace; **105 window samples** preserved all eight fresh
baseline assignments. This run remains failed, with no menu or restoration pass.

After the user explicitly confirmed "Desktop is idle—retry", the unchanged
harness completed `.debug/aquaspace/ghostty-topology/run-20261015-192022/` on the
fresh empty pair `native-tab-test-ghostty-v7-retry-20261015-{a,b}`:

- **All 32 assertions passed**, `success: true`, `errors: []`. Six actual native
  menu events record three merges and three detachments. The cross-workspace
  merge onto destination A, three-tab detachment, two-to-one detachment, merge
  of three bare windows onto B, further selection/detachment/movement and final
  remerge onto A all passed using direct retained closed-menu item presses.
  The menu-open membership-read failure did not recur. Fresh exact membership,
  target proof, retained AX roots, foreground and ambiguity guards stayed enabled.
- **2,392 process inventories** all observed the same sole installed AquaSpace
  PID **42527**, birth `(1792081221, 893887)`, executable
  `/Applications/AquaSpace.app/Contents/MacOS/AquaSpace`, bundle `local.aquaspace`.
  Peak manager count was **1**; no manager was launched, paused or stopped.
- **699 original-window samples** preserved all **eight fresh-baseline** workspace
  and fullscreen assignments with zero drift. Existing Ghostty **window 28264**
  remained on **workspace 1**; original process **17528** retained its native
  selected-tab, tab and terminal identities before and after cleanup.
- The exact original focus returned to **workspace 1**, model window **28146**,
  and retained native **ChatGPT PID 15689** window. Three matching pre-cleanup
  observations spanned **0.517396250 seconds**; three matching post-cleanup
  observations spanned **0.502698250 seconds**. The report is
  `verified-after-cleanup`, `verified: true`, `revoked: null`, with one guarded
  focus-window restoration request and no retry.
- All three closes and final termination passed strict background admissions
  with native/model focus on the restored ChatGPT window. The native v7 receiver
  independently validated process lifetime, exact tab/terminal pairs, background
  state and final empty native/AX window lists. Only disposable PID **68465**,
  birth `(1792106423, 909609)`, was terminated, and its exit was confirmed.
  No disposable managed windows or `cleanup-incomplete` events remained.

Results, native membership/menu events, baseline, process/window samples and
`completion-summary.json` retain the evidence. Native source SHA-256 remains
`dc3537027531c9825c6efd05fbc8f1e2451f71e6455b48fd674164c2bf7ebd3b`;
adapter `cddd83296f81f7a24f75c61d3f8da30fd577583ad1f10cc3346364c43ffed472`;
menu policy `fe6c85399cf507706e5ccfe2b15ebb4dfa86c19124057f8c0c6892543ebc04e4`;
v7 library `73861d20dd39c95baa264c81428e73d4229ee7d56efc5dbd70b32993c8568735`.
Installed manager/CLI hashes matched the preceding record. The prior failed
v7 menu-open run and the fixed-path foreground-interrupted attempt remain failed;
this complete retry does not relabel them. Existing unsafe-case offline regression
evidence remains separate; those failures were not injected into the live run.

The user was told to resume desktop use after completion. Only saved artifacts,
source/binary files and documentation were inspected afterward; no additional
live query, installed CLI, app/helper or live test was invoked. Nothing was
installed or pushed. These live validation notes form the local documentation
checkpoint after `1789ab2d`. Creating this checkpoint involved no additional
tests, builds, installations, app/helper launches or live checks. Nothing was
pushed.

## Offline restoration and final menu focus repair after fa59aeb4 — 2026-10-15

The user authorized fixing both focus gaps found in the v7 checkpoint review,
adding offline regressions without weakening ownership, and prohibited all app/
helper launches, live tests, installations and pushes. Only Python runner/menu/
focus policy code, offline tests and documentation were changed. The native
library, v7 capability admission, manager inventory, pinned process proof and
native background cleanup guards are unchanged; no diagnostic rebuild is needed
for these Python changes.

First, `native()` rejected unknown tabs and read/schema failures without always
revoking foreground permission. Cleanup then built a restoration grant from
`own_rows()`, which only filtered the disposable PID, baseline IDs and title
prefix. A new foreground window inheriting `prefix + initial` could fail exact
native ownership but still qualify for restoration. The offline reproduction
executed the actual native reader, row reader, teardown restoration call and
focus policy; it issued a focus command after ownership rejection before repair.

Native reads now validate collection/group/member/selection structure and
uniqueness, compare every tab/terminal/title triple with its original creation
record, and permanently revoke on any read/ownership failure. Restoration accepts
a fresh validator rather than a set of model IDs. Before granting a focus write,
the runner validates pinned process/manager/original-window ownership, the full
recorded native membership, exact selected model rows and dedicated workspace
inventories. The source snapshot must match one exact row with coherent native/
model PID/title and unique model focus. The focus policy rechecks the retained
source after these potentially blocking reads. It requires the same source
window/model/title across original-target validation; another known group or
same-title replacement cannot inherit an earlier grant. Failures permanently
revoke restoration, including if the user subsequently returns to a known tab.
The empty-workspace path now carries only that exact source through workspace
selection and validates its retained reference/title before the existing native
activation. Background exact-ID cleanup remains independently guarded and never
acquires foreground restoration permission from this validation.

Second, native-menu selection was checked before the last potentially blocking
membership/source callback, followed only by application foreground validation.
The real execution gate correctly allowed another recorded group, but that was
insufficient to authorize the specifically requested menu selection. Fake-API
reproductions using the actual menu policy and actual `ExecutionFocus.check`
switched from requested group A to recorded B during that callback, or replaced
the requested root with a same-title root, and still reached the action before
repair. Menu dispatch now pins the initial requested root and rechecks that exact
root/title plus the complete retained AX inventory after blocking callbacks and
before the item press. The callback cannot substitute a new source. The closed-
menu dispatch, fresh exact membership, PID checks, ambiguity refusal, no-retry
behavior and retained-only cancellation remain intact. No menu-opening fallback
or activation path was added.

Both reviewed reproductions failed before their repairs. Further exact-source
restoration cases also first failed when a known group or same-title root changed
during target validation. All **223 AquaSpace Python offline tests** now pass,
including **12 new regression methods** covering prior rejection and fresh
restoration admission, malformed/unreadable/duplicate/changed membership and
permanent revocation, late arrivals and model ambiguity, baseline/manager refusal,
source changes during membership and target reads, empty-workspace transitions,
both merge/detach operations after final callback root changes, and runner-level
revocation/local backend release. An unchanged exact source still restores once
and verifies after cleanup. Legacy prefix-ID sets and truthy substitutes for an
explicit successful fresh validator are refused.

All process/native boundaries are fakes in these tests. Native library loads,
subprocesses and signals are blocked; the tests also prevent live-path file
creation. No native test binary or diagnostic helper/library was built, run or
loaded, no installed CLI or live process/focus reader was invoked, and no app
was launched. The diff passes whitespace checks. No installation, live test or
push occurred. The successful v7 run remains historical for its exact source;
these additional fixes have offline validation only. Both fixes, regressions and
diagnosis notes form the local checkpoint after `fa59aeb4`. Creating this
checkpoint involved no additional tests, builds, installations, app/helper
launches or live checks. Nothing was pushed.

## Focus-fixed v7 Ghostty live validation after f6c7d2fd — 2026-10-15

The user authorized running the committed focus-fixed harness on dedicated test
workspaces, verifying native merging/detaching, desktop restoration, existing
Ghostty preservation and sole-manager ownership. The unchanged installed manager/
CLI, existing launcher helper and previously built v7 native library were reused.
No app or diagnostic helper/library was rebuilt, no manager lifecycle action or
installation occurred, and nothing was pushed.

Run `.debug/aquaspace/ghostty-topology/run-20261015-194416/` used the fresh empty
pair `native-tab-test-ghostty-focus-fixed-20261015-{a,b}` and completed on its
first attempt with **32 assertions passed**, `success: true`, `errors: []`:

- Six real native menu events record **three merges and three detachments**.
  Cross-workspace destination merging, three-tab and two-to-one detachments,
  independent movement, merging three bare windows, repeated selections and
  final detach/move/remerge all passed. Fresh native membership, exact retained
  requested-window selection after blocking callbacks, pinned target proof and
  no-retry/cancellation policies remained enabled.
- **2,240 process inventories** all observed the same sole installed AquaSpace
  PID **42527**, birth `(1792081221, 893887)`, executable
  `/Applications/AquaSpace.app/Contents/MacOS/AquaSpace`, bundle `local.aquaspace`.
  Peak manager count was **1**; no manager was launched, paused or stopped.
- **935 original-window samples** preserved all **eight fresh-baseline** workspace
  and fullscreen assignments with zero drift. Existing Ghostty **window 28264**
  remained on **workspace 1**. Its original process **17528** retained selected
  tab, tab and terminal identities before and after cleanup.
- The exact original desktop focus returned to **workspace 1**, model window
  **28146**, and the retained native **ChatGPT PID 15689** window. Three matching
  pre-cleanup samples spanned **0.815997917 seconds**; three matching post-cleanup
  samples spanned **0.706928666 seconds**. Status is `verified-after-cleanup`,
  `verified: true`, `revoked: null`, with one guarded focus-window restoration
  request. Fresh exact native/model restoration-source validation and retained
  source checks across target validation were enabled; no focus retry occurred.
- Three exact tab/terminal closes and final termination passed strict native/
  model background admission with focus on the restored ChatGPT window. Only
  disposable PID **81303**, birth `(1792107859, 507692)`, was terminated and its
  exit was confirmed. No disposable managed windows or `cleanup-incomplete`
  events remained. Native process-lifetime/background/empty-membership guards
  were unchanged.

Results, events, baseline, process/window samples and `completion-summary.json`
retain the evidence. Python source SHA-256:
runner `e5398fb52352bb30220a146ce59df4b4169ecb51344a6c06bad88f29d58ee9ba`;
menu `fd14afc7fa35a2e4cbe32196fa42dfc80a37a0306bc9e29a0c832b63660162b4`;
focus policy `fbd1c5a4ce3f946d8bb1083c667949ea9ca04bcef6c6d0a8db761de94ad7bd85`.
Native source/adapter/library and installed app/CLI hashes match the preceding
record; the v7 library remains
`73861d20dd39c95baa264c81428e73d4229ee7d56efc5dbd70b32993c8568735`.
The 223-test offline evidence covers unsafe ownership/source-change cases;
those cases were not injected into this live run. Earlier runs retain their
individual results and exact source/binary identities.

The user was told to resume desktop use after completion. Only saved artifacts,
source/binary files and documentation were inspected afterward; no additional
live query, installed CLI, app/helper or live test was invoked. Nothing was
installed or pushed. These live validation notes form the local documentation
checkpoint after `f6c7d2fd`. Creating this checkpoint involved no additional
tests, builds, installations, app/helper launches or live checks. Nothing was
pushed.

## Offline hidden Zen handoff repair after 7a81171c — 2026-10-15

The user authorized investigating the historical hidden-Zen omission and fixing
it with offline regressions, with app/helper launches, live tests, installation
and pushing prohibited. The saved failed handoff still shows Zen **28122** on
workspace **1** before startup and present natively but absent from the managed
inventory afterward; its full preservation result remains failed.

The actual layer cache and browser classifier reproduce a direct cause: the
on-screen-only WindowServer query omits hidden windows, and a cold-start Zen
window consequently receives no known layer and is classified as a popup. That
leaves it outside any workspace and prevents the saved workspace move from
resolving. The source now obtains actual layers from `.optionAll` with desktop
elements excluded. No classifier/ownership checks were relaxed, no unknown
metadata is treated as a normal window, and no unhide, activation, focus write
or manager lifecycle path was added. This read-only metadata correction also
covers off-screen windows on inactive native Spaces.

Seven new isolated Swift regressions pass after four test methods reproduced
the old behavior; the existing 49 native-tab replay tests and 223 Python tests
also pass. The new replay runs actual cache/classification/session-planning
source against fake WindowServer/AX objects, with no app executable or native
desktop API. Only isolated test binaries were built/run; no app, diagnostic
helper/library, installed CLI or live reader was invoked. Nothing was installed
or pushed. The repair and offline validation form this local checkpoint and are
not in the installed application; a separately authorized rebuild/install and
live handoff remain pending. Creating the checkpoint involved no additional
tests, builds, installations, app/helper launches or live checks.
See the [full diagnosis and reproduction](native-tabs-diagnosis.md#hidden-zen-window-omitted-during-stock-to-fork-startup--2026-10-15).

## Hidden Zen rebuild and ordered handoff validation after fafd604a — 2026-10-15

The user authorized rebuilding/installing the hidden-Zen repair and testing the
stock-to-fork handoff, with no push. Release
`0.21.3-aquaspace.20261015.2` was built from
`fafd604ae0dbf319d650052493321c9da2d0ccde` using the macOS 26.5 SDK and two
workers. The build log is `.debug/aquaspace/hidden-zen-build.log`; the staged
manifest is `.debug/aquaspace/release/build.json`. The signed application was
installed at `/Applications/AquaSpace.app` in file-only mode, with no managers
running at every installation checkpoint. Transaction:
`/Applications/.aquaspace-update-4d863314b4374681bb0bf70b80d04e3d`.

Installed release hashes:

- App: `e3a444817cb26b68ac20a50a26f2f289b467e9c23dd1c285e7c3834cbae7b171`.
- CLI: `d5293d058e8c0358a5d6b3bfd2a5ecca2e071596e284d7c601f81be3090e4524`.

Initially neither manager was running. Read-only AX observation found original
Zen PID **15654**, window **28122**, unhidden with standard-window subrole. The
test therefore required hiding that background application before capturing the
stock session; it did not move Zen to a different workspace or activate it.

The first attempt, `.debug/aquaspace/hidden-zen-handoff/run-20261015-204839/`,
installed the release and started stock AeroSpace alone, PID **37676**, birth
`(1792111721, 605889)`. It stopped during strict native/model baseline capture
when foreground changed between existing ChatGPT, Zen and Helium windows.
No Zen hide, stock termination, AquaSpace launch or restoration write occurred.
All **89 independent inventories** observed a peak of one manager, including
**10 empty inventories**. The failed result remains recorded and is not a
handoff pass. Stock remained the sole manager until the user confirmed that the
desktop was idle for a retry. A resume-runner argument-parser import error was
corrected before any native or process action; it did not launch another copy.

The resumed run is
`.debug/aquaspace/hidden-zen-handoff/run-20261015-205646/`. It revalidated and
reused the exact recorded stock process and installed release without another
installation or stock launch. It hid only background Zen after a coherent
native/model focus capture. Stock then reported original Zen **28122** on
workspace **2** with layout `macos_native_window_of_hidden_app`. The runner
saved that session, confirmed stock PID **37676** exited, recorded an empty
inventory at the spawn boundary, and launched installed AquaSpace exactly once
with the saved session. There was no recovery launch, pause/resume watchdog,
test workspace reassignment, window selection or harness focus write.

The requested hidden-Zen and exclusive-manager checks passed:

- Zen PID **15654** and its kernel birth identity are unchanged. Native AX
  continues to expose exact window **28122**, now with hidden `AXDialog`
  subrole. AquaSpace reports it on the same workspace **2** with the same hidden
  layout/fullscreen fields. Five additional read-only observations confirmed
  that exact native/model state. Zen was left hidden as stated before testing.
- All **264 independent manager inventories** in the resumed run observed a
  peak of one manager, including **two empty inventories** around the switch.
  The only final manager is installed AquaSpace PID **45262**, kernel birth
  `(1792112215, 825354)`. Its status reports `controlling`, `controlsWindows=true`,
  `inventoryVerified=true`, `readOnly=false`, and no original peers.
- All **31 post-handoff native/model focus observations**, spanning **14.54
  seconds**, matched retained original ChatGPT window **28146**, PID **15689**,
  workspace **1**. The runner issued no focus request.
- Installed app/CLI hashes match the staged build. Original stock executable,
  Homebrew CLI and user configuration hashes remain unchanged.

The broader all-window preservation check did **not** pass. Seven other stock
IDs were absent from AquaSpace's workspace inventory: **30583**, **29724**,
**30197**, **29754**, **29598**, **30290** and **30682**. Read-only follow-up
found every ID still in WindowServer metadata, but not in the owning apps'
current AX window lists. Finder PID **684** exposes only its current window
**30881** plus its desktop element; System Settings PID **738**, Obsidian PID
**22595**, Vesktop PID **15177** and VSCodium PID **93525** expose no AX windows.
The pre-handoff run did not record those apps' complete native AX memberships,
so the cause and timing of that discrepancy remain unresolved. It is not
classified as a verified tab collapse, window closure or preserved assignment.

The original `failure.json` is retained. `targeted-verification.json` explicitly
records `targeted_checks_passed=true` and
`full_handoff_preservation_passed=false`; it does not replace or weaken the
all-window check. `post-handoff-read-only.json` and
`other-baseline-native-read-only.json` retain the follow-up observations. Thus
this run validates the hidden-Zen fix and sole-controller handoff, while leaving
broader window-inventory reconciliation for a separate investigation. No
additional manager cycle, window mutation or live test followed those read-only
checks. AquaSpace was left running. These notes form the local live-validation
checkpoint after `fafd604a`, including the seven unresolved IDs and the failed
broader comparison. Creating this documentation checkpoint involved no additional
tests, builds, installations, app/helper launches or live checks. Nothing was
pushed.

## Offline seven-ID handoff diagnosis after b0928c61 — 2026-10-15

Offline source comparison and a differential replay explain a mechanism for the
stock/fork disagreement: both versions retain previously acquired AX roots that
are absent from current AXWindows, but a fresh manager cannot discover those
roots from WindowServer IDs alone. Stock with retained roots and a cold fork
reproduces exactly **30583**, **29724**, **30197**, **29754**, **29598**, **30290**
and **30682** as stock-only; equal state or reversed retained state removes or
reverses the difference. No native-tab transfer is needed in that reproduction.
Inactive native macOS Space visibility during the recorded fullscreen ChatGPT
foreground is the leading trigger hypothesis, not a proved historical event.

The investigation also reproduces a definite gap in the actual one-shot session
restore: saved assignments for undiscovered windows are discarded and never
retried when their AX roots become available. No production repair was made.
All **8 offline diagnostic test methods** pass. The full live handoff result
remains failed, and the earlier hidden-Zen/sole-manager passes are unchanged.
See the [evidence, per-app IDs, limits and repair direction](native-tabs-diagnosis.md#seven-stock-only-ids-retained-discovery-state-and-one-shot-restoration--2026-10-15).

Only memory-only tests were built/run. No app/helper, native desktop reader or
installed CLI was launched; no live tests, installation or push occurred. The
diagnosis and replay are included in the local deferred-restoration checkpoint
below.

## Offline identity-safe pending-restoration repair — 2026-10-15

The user authorized fixing deferred restoration with offline regressions only.
The fork now retains undiscovered/failed assignments with kernel-verified
per-window application identity and retries them through ordinary discovery.
Native ownership, birth/exec identity, exact registered AX roots, controller
admission and user focus/model state are rechecked before a synchronous
background model update. No delayed focus, unhide or workspace activation is
queued. Proven native-tab ownership supersedes stale stock per-tab assignments.

Identity-session export preserves unresolved metadata through future updates;
the updater verifies the same controller and schema before stopping anything.
Old snapshots without owner identity remain immediate-only and cannot authorize
a future move. A separately authorized live handoff must capture fresh identity
metadata; the old failed seven-ID record is not a substitute. Native hidden and
multi-tab windows are not forcibly promoted into eligible conventional windows.

All **124 offline Swift test methods** and **232 Python tests** pass. The full
library and CLI target compile checks passed without running their products.
No apps/helpers, native readers or live tests were launched, nothing was installed
or pushed, and the installed release remains unchanged. This repair, its tests
and these notes form the local checkpoint after `b0928c61`. Creating the checkpoint
involved no additional tests, builds, installations, app/helper launches or live
checks. Nothing was pushed. See the [full implementation, safety rules and
validation record](native-tabs-diagnosis.md#identity-safe-deferred-restoration-repair--2026-10-15).

## Deferred-restoration install and native-Space handoff validation after de5541d9 — 2026-10-15

The user authorized rebuilding/installing the deferred-restoration repair and
testing stock-to-AquaSpace handoff, without pushing. Installed
`/Applications/AquaSpace.app` is now **0.21.3-aquaspace.20261015.3**, built from
`de5541d95c3806b558a34b06c51dd7fd0fafd07f`. The staged production-source hashes
match the checkout; installation verifies the app/CLI signatures and hashes:

- App: `0ec8f4fb4b6c1b00d85c8ff66293fc444eead84a0ef8951cc8a3aff49cdf1e83`.
- CLI: `e830ab9efbe454858fd267a13118a81e4b9b2425589f81ef1d6dcf590ea0844b`.

The release build completed after retrying debug-symbol generation outside the
filesystem sandbox. A disposable AppKit fixture was separately compiled with the
Command Line Tools compiler and an explicit macOS deployment target. It accepts
commands only for its own single window, using a per-process random token;
the runner verifies its process generation, protocol identity, exact native AX
membership and WindowServer owner before mutations. No existing application's
fullscreen state was changed for the successful test.

The initial update attempt, `run-20261015-221000`, stopped after replacing files
but before spawning the new manager because foreground focus changed from
Ghostty **31814** to ChatGPT **28146**. The update journal retained launch intent;
no replacement or recovery manager was started. A fresh read confirmed an empty
manager inventory. After the user confirmed the desktop was idle, the runner
used the installer's recovery operation with all managers stopped, then installed
the same staged release with `restart=False`. The committed installation record is
`/Applications/.aquaspace-update-cc1adbee2eb2415990c27cee8c884fe6`; the interrupted
record `.aquaspace-update-f096a5c543a64678aa610ed79c222732` is recovered.

Stock AeroSpace **8139**, birth `(1792116951, 884665)`, was then launched alone
and all 12 saved window assignments were restored. The first fixture run,
`run-20261015-221548`, rejected changed AX membership during its fullscreen
transition, before a stock-to-fork handoff. The disposable fixture closed after
its command pipe ended; stock remained the sole manager. A subsequent read-only
baseline attempt, `run-20261015-221728`, stopped on changing session metadata,
before launching a fixture or changing any window. The successful runner waits
for exact fixture membership and fresh identity/session capture to stabilize;
it does not admit changed membership or use a cached ownership result.

The successful live record is
`.debug/aquaspace/deferred-handoff/run-20261015-221802/`. It reused that exact
stock process and installed release. A new disposable fixture entered native
fullscreen on `deferred-handoff-test-run-20261015-221802`. With stock's AX roots
already acquired, **all six surviving historical IDs disappeared from fresh
AXWindows while remaining in stock's model**. The runner captured a fresh
identity-backed stock session, verified unchanged application birth/exec/native
ownership, stopped stock and observed empty inventories before starting the
installed AquaSpace once with that session. No two manager processes overlapped.

The requested deferred restoration is now live-validated for every surviving ID:

| Window ID | Application | Saved and restored workspace | Final layout |
| --- | --- | --- | --- |
| 30583 | Finder | 1 | floating |
| 29724 | Finder | 1 | floating |
| 29598 | System Settings | 1 | floating |
| 30197 | Obsidian | 3 | h_tiles |
| 29754 | Obsidian | 3 | h_tiles |
| 30290 | VSCodium | 3 | h_tiles |

These are fresh saved assignments, including the user's current workspace 3
placements; the historical workspace 1 snapshot was not imposed. All six were
absent from AquaSpace's cold model, but its guarded `handoff-session` export
retained their exact desired rows and matching owner identities. After the
fixture exited fullscreen, fresh AX reads exposed all six. The first exposed
observation still included staging-workspace rows; the next observation, about
**0.79 seconds later**, showed all six on their saved workspaces. No manual
move command was used after the stock-to-fork handoff. All 12 original rows,
including layout and fullscreen fields, then matched the baseline exactly.

The seventh historical ID, Vesktop **30682**, is unavailable: its original PID
**15177** had exited before this test and the ID is absent from the all-Spaces
WindowServer inventory. It was not added to the fresh session or resurrected.
This is **six surviving assignments restored, one retired/unavailable**, not
seven live restorations. The earlier failed run is preserved as historical
evidence; this new controlled run demonstrates the repair and the native-Space
omission mechanism, without claiming to prove the cause of the old recording.

Final checks confirmed:

- Original Ghostty process **7190** / window **31814** regained native/model
  focus on workspace **1** after its background assignment had restored. Its
  retained native selection and process identity were checked before activation.
- The fixture exited; all **12** original window rows remain exactly equal to
  their baseline, and the final export contains matching identities for all 12.
- All **122** independent manager samples in the successful run observed at most
  one process, including **two empty samples** at handoff. Across the monitored
  installation/fixture attempts, all **256** samples likewise peaked at one.
- The sole final manager is installed AquaSpace PID **9465**, birth
  `(1792117092, 152606)`, reporting `controlling`, `controlsWindows=true`,
  `inventoryVerified=true`, `readOnly=false`, `originalPids=[]`, `sessionSchema=2`.
- Original stock executable, Homebrew CLI and user configuration hashes are
  unchanged; installed app/CLI hashes match the staged release. No remote was
  contacted and nothing was pushed.

AquaSpace was left running. No production source change or additional live test
followed the successful result. These notes form the local live-validation
checkpoint after `de5541d9`. Creating this documentation checkpoint involved no
additional tests, builds, installations, app/helper launches or live checks.
Nothing was pushed.

## Offline deferred-restoration safety repair after f60c1e5f — 2026-10-16

The user authorized fixing all three gaps found in the offline checkpoint review,
with no apps/helpers, live tests, installation or push. All three were reproduced
against the checkpoint before the fix: an explicit background move before the
first anchor was undone; a failed second AX read forgot the first root; and an
update export carried stale pending intent after a root replacement.

The shell now tags explicit command execution with task-local provenance. Tree
binding and fullscreen changes revoke affected windows' pending intent before
mutation, even without an anchor, after checking the exact registered model and
application PID. Internal startup restoration has a separate task-local scope;
it cannot suppress a separate user input task while awaiting work. Automatic
native normalization continues to preserve retryable assignments.

Each successful AX proof now records root continuity immediately. Failure of a
later read cannot erase it, and a different token revokes the assignment. Export
also verifies established roots twice, before carrying pending metadata. Proven
replacement preserves the current model row instead of the stale desired row;
an unavailable proof rejects export without discarding retryable pending state.
Never-discovered assignments still survive using their native/process continuity
checks. Controller admission is rechecked after export awaits. No ownership,
ambiguity, native-tab or controller-lock check was weakened.

All **138 offline Swift tests** pass, including **53 discovery/deferred tests**
(14 added), **27 ownership/process**, **49 native-tab** and **9 hidden-window**
tests. The three original regressions were observed failing against the old
source. The replay now includes actual command dispatch/movement, pre-bind
policy, fullscreen mutation, owner capture and guarded export/reload, rather than
testing only isolated row merging. All **232 Python tests** pass, and the complete
changed `AppBundle` library passes a compile-only check. Whitespace checks pass.

The repair, regressions and notes form the local checkpoint after `f60c1e5f`.
Creating this checkpoint involved no additional tests, builds, installations,
app/helper launches or live checks. No app/helper,
installed CLI, native desktop reader or live harness was launched, nothing was
installed or pushed, and the installed release is unchanged. The last installed
`0.21.3-aquaspace.20261015.3` does not yet include this repair. See the
[diagnosis, implementation and offline evidence](native-tabs-diagnosis.md#deferred-restoration-command-and-root-continuity-safety-repair--2026-10-16).


## Deferred-restoration safety install and handoff after 96ef60c5 — 2026-10-16

The user authorized rebuilding/installing all three safety repairs and an idle
stock-to-AquaSpace handoff, without pushing. Installed
`/Applications/AquaSpace.app` is now **0.21.3-aquaspace.20261016.2**, built from
checkpoint `96ef60c5dcaecc9952445efe184837b94b1d8392` plus the local automatic
registration-callback follow-up described below. This is a build of a dirty
working tree, not an unmodified checkpoint. The manifest records exact
production-source hashes, which matched the checkout before installation.

- App SHA-256: `77748777fe23e0b447038b28055997416c0de64949a9dc052ec907d98939a251`.
- CLI SHA-256: `a0e76611d454ee4a57f6c6cebf074ba733abf94c973297cb0a2e6ff6bc47cb74`.
- Installation transaction: `/Applications/.aquaspace-update-8ada41f676cf48b593cdd7bb38e55714`.

Preflight found that normal `on-window-detected` floating rules would be tagged
as fresh user commands and discard deferred intent during rediscovery. An
additional offline reproduction failed against the checkpoint before repair.
Normal registration now scopes its callbacks as automatic discovery; independent
user commands and explicitly requested callbacks still supersede pending intent.
The three new regressions cover each case without relaxing identity, native-root,
ambiguity or controller admission. All **141 offline Swift methods** and **232
Python tests** pass. See the [callback diagnosis and evidence](native-tabs-diagnosis.md#automatic-discovery-callbacks-versus-new-user-intent--2026-10-16).
The first staged release `.20261016.1` was never installed/launched; the corrected
release `.20261016.2` was rebuilt and signed before installation.

The installer ran with all managers stopped and `restart=False`; every phase
checkpoint required an empty kernel inventory. Stock AeroSpace PID **43345**,
birth `(1792128166, 156414)`, was then launched alone. All 15 fresh baseline
assignments were established and verified before the disposable fixture was
created. The original stock executable, Homebrew CLI and user configuration
hashes remained unchanged.

Two setup attempts, `run-20261016-012231` and `run-20261016-012509`, stopped before
handoff because adding a conventional tiled fixture cleared stock's fullscreen
flag on original ChatGPT **28146**. The recorded mismatch was only that flag;
workspace/layout assignments did not differ. Both fixtures exited after their
drivers stopped; stock remained the sole manager. A subsequent preflight
`run-20261016-012601` rejected the remaining flag difference before any fixture
launch or window mutation. The successful continuation restored that one recorded
flag only after rechecking the exact original foreground/process/window identity
and every original owner. Its fixture advertises the native dialog subrole, so
stock initially places it floating and does not disturb the original tiled
fullscreen window. This changes fixture classification, not ownership checks or
the production manager. The final exact-state assertions were retained.

Successful evidence is in
`.debug/aquaspace/deferred-safety-handoff/run-20261016-012615/`. The single-window
AppKit fixture was **32446** on
`deferred-safety-test-run-20261016-012615`. Its executable hash, process birth/exec
generation, random protocol token, exact native AX membership and all-Spaces
WindowServer owner were checked before its operations. Retained native foreground
checks reject unrelated focus rather than activating over it.

While the disposable fixture was in native fullscreen, fresh AXWindows omitted
all six surviving historical target IDs even though stock retained their model
rows. A fresh stock session was captured with complete native/process identity
proof; every original row still matched the baseline. Stock then exited, empty
inventory was verified, and installed AquaSpace **44913**, birth
`(1792128389, 220998)`, was started once with that session. All six targets were
absent from its cold model, while guarded export retained all six saved rows and
matching owners. After exiting only the fixture's fullscreen, all six returned
through normal discovery and restored automatically:

| Window ID | Application | Saved/restored workspace | Layout |
| --- | --- | --- | --- |
| 30583 | Finder | 3 | floating |
| 29724 | Finder | 1 | floating |
| 29598 | System Settings | 1 | floating |
| 30197 | Obsidian | 3 | h_tiles |
| 29754 | Obsidian | 3 | h_tiles |
| 30290 | VSCodium | 3 | h_tiles |

No manual move was issued after the stock-to-fork handoff. The initial
rediscovery observations still contained missing/staging rows; about **1.57
seconds** after the first observation, all 15 original rows matched. That state
remained stable for more than two seconds before focus restoration. Historical
Vesktop **30682**, former PID **15177**, remains unavailable in the fresh native
inventory and was excluded, not recreated or counted as a live pass.

Final checks, made within that same run, confirmed:

- All **15** original IDs retained their saved workspaces, model layout and
  fullscreen fields; final identity-backed export matched every original owner.
  Existing Ghostty **31814** remained on workspace **1**, Zen **28122** on
  workspace **1**, and Helium **28101** on workspace **2**. Current assignments
  were used rather than the previous day's saved placements.
- Native/model focus returned to original ChatGPT **28146**, PID **15689**,
  workspace **1**, with its retained selected native window and process identity
  checked before activation. Its model fullscreen flag is again `true`.
- The fixture exited, no test window remained, and workspace root layouts matched
  the baseline. This is workspace/model-state preservation, not pixel-identical
  geometry: handoff rebuilds tiling trees, and saved native metadata shows bounds
  changes for Code **32232**, VMware **32116**, cmux **31967**, Messages **32139**
  and Zen **28122**. None changed its workspace. No additional corrective window
  mutations were made after the successful final verification.
- All **138** independent kernel manager samples in the successful run peaked at
  one, with **two empty samples**. Across the three monitored attempts, all
  **356** samples peaked at one (16 empty). Empty launch-boundary checks also
  enforced sequential launches; sampling alone is not proof of every instant.
- Sole final AquaSpace **44913** reports `phase=controlling`,
  `controlsWindows=true`, `inventoryVerified=true`, `readOnly=false`,
  `originalPids=[]`, `sessionSchema=2`. Original stock/config hashes and installed
  app/CLI hashes still match their preflight/staged values.

The three reviewed safety failure paths remain covered by offline fault-injection
regressions; no destructive owner/root replacement or second-manager race was
injected on the user's live desktop. The actual deferred lifecycle and automatic
floating-rule interaction passed against the installed production build. Live
testing ended after the successful result; AquaSpace was left running. The
callback fix, its offline regressions and updated notes are included in this
local checkpoint. Creating the checkpoint involved no additional tests, builds,
installations, app/helper launches or live checks. No remote was contacted and
nothing was pushed.


## Offline callback-ordering repair after 4396d3e4 — 2026-10-16

The user authorized repairing both callback-ordering gaps with offline
regressions only. The working source now tracks initialization for each exact
window model from before registration publication through its awaited callbacks.
Startup/deferred restoration waits for that initialization to end and rechecks
after suspension. Explicit user mutations invalidate outstanding automatic
callbacks, including when no saved assignment exists; delayed matchers, later
commands in a compound callback, and deferred next-window layouts stop instead
of overriding newer input. Exact-model checks reject stale continuations after
replacement. Existing owner/process/native-root/controller checks remain intact.

Four new reproductions failed against the checkpoint with 10 assertions before
the repair. All **155 offline Swift tests** now pass: **70 discovery/deferred**
(including 14 new ordering regressions), **27 ownership/process**, **49 native-tab**
and **9 hidden-window**. All **232 Python tests** pass, and the exact changed
production library compiles without executing an app entrypoint. Whitespace
checks pass. See the [diagnosis, implementation and offline evidence](native-tabs-diagnosis.md#discovery-initialization-and-superseded-callback-ordering--2026-10-16).

No app/helper, installed CLI, native reader or live test was launched; nothing
was installed, committed or pushed. Installed AquaSpace remains
`0.21.3-aquaspace.20261016.2`; these working-tree repairs have not been installed
or live-validated. The preceding live handoff record remains historical evidence
for that installed build, not a validation of these additional ordering guards.


## Offline callback command continuation repair — 2026-10-17

The user authorized repairing awaited callback-command continuations with offline
regressions, without app/helper launches, live checks, installation or pushing.
The working source now captures each automatic command's exact affected model,
initialization and user-mutation token and rechecks after native reads. An
already-dispatched layout/relayout, native fullscreen/minimize or close operation
therefore stops before its later mutation when newer user input, model replacement
or loss of controller ownership supersedes it. Separately targeted windows get
their own checks; ordinary explicit commands and unrelated input remain supported.
Other mutating command continuations receive matching guards. Existing owner,
native-root, ambiguity and exclusive-controller checks remain intact.

Floating-as-tiling focus preparation now performs all reads before temporarily
unbinding candidates, verifies current destinations, and cleans up only unchanged
temporary bindings. It preserves newer choices even when they use the same parent.
The real command/helper replay reproduced 29 failing assertions before repair.
All **170 offline Swift methods** now pass, including **85 discovery/deferred**
with **15 new methods / 36 new case invocations**; all **232 Python tests** pass.
The full changed production library compiles without executing an app entrypoint.
See the [continuation diagnosis and evidence](native-tabs-diagnosis.md#awaited-callback-command-continuations--2026-10-17)
for the executed cases and audit/compile-only coverage limits.

No app/helper or live test was launched during this repair; nothing was installed
or pushed. This continuation repair and the preceding callback-ordering repairs
are included in this local checkpoint, together with their offline regressions
and diagnosis notes. Creating the checkpoint involved no additional tests,
builds, installations, app/helper launches or live checks. Installed
`0.21.3-aquaspace.20261016.2` is unchanged and does not contain these repairs.

## Offline freeze, container and focus repair — 2026-10-17

The working source fixes the three gaps reviewed at `76c70eaa`. A synchronous
controller freeze now revokes automatic initialization as well as physical
writes, with a generation that prevents old callbacks from reviving after a
later claim. Close requires actual native-write authority before quitting or
closing an application; initial model preparation cannot grant it. Container
layout/orientation choices, including no-ops and ancestor normalization,
supersede affected callbacks. A separate focus-intent token preserves newer
window/workspace focus without canceling valid background assignments.

The new in-memory cases reproduced 25 failing assertions before repair. All
**184 offline Swift methods** (including two real-model recovery tests) and
**232 Python tests** now pass; the production
library compiles without an app launch. Existing owner, process-identity,
ambiguity, native-root and exclusive-controller checks remain intact. See the
[diagnosis and coverage limits](native-tabs-diagnosis.md#frozen-admission-container-choices-and-focus-continuations--2026-10-17)
for the executed command/setter races, startup boundary checks and evidence.

No app or diagnostic helper was launched, and there was no live testing,
installation or push. These fixes, offline regressions and diagnosis notes are
included in this local checkpoint. Creating it involved no additional tests,
builds, installations, app/helper launches or live checks. Installed AquaSpace remains
`0.21.3-aquaspace.20261016.2` and does not yet contain this repair.

## Offline no-op intent repair — 2026-10-17

The working source fixes the two no-op intent gaps reviewed at `00730108`.
Repeating floating layout, a current workspace assignment or a fullscreen state
now supersedes stale initialization through the existing exact-model/provenance
guard. Floating no-ops affect only the targeted window. Focus records explicit
intent after target resolution and before reads or boundary no-ops, so a waiting
automatic focus command cannot override it. Automatic/session no-ops and unrelated
floating siblings retain their previous behavior.

The new memory-only cases reproduced **37 failed assertions** before repair.
All **191 offline Swift methods** and **232 Python tests** pass, and the production
library compiles without an app launch. Existing ownership, identity, ambiguity
and controller checks remain intact. See the
[diagnosis and executed coverage](native-tabs-diagnosis.md#explicit-no-op-assignment-and-focus-intent--2026-10-17)
for the seven new methods / 28 case invocations and evidence.

No app or diagnostic helper was launched, and there was no live testing,
installation or push. These fixes, offline regressions and diagnosis notes are
included in this local checkpoint. Creating it involved no additional tests,
builds, installations, app/helper launches or live checks.
Installed AquaSpace remains `0.21.3-aquaspace.20261016.2`.

## Offline native intent and failure reporting — 2026-10-17

The working source fixes the native-fullscreen no-op gap and records explicit
assignment intent before native reads. The same entry helper covers tiling,
native fullscreen/minimize, move, close and relayout without changing automatic
or session provenance, exact-model validation, or controller authority.

Lifecycle failures now leave a readable, timestamped report at
`~/Library/Logs/AquaSpace/last-failure.txt` and write the reason to stderr and
public Console output. The report includes phase/version/executable/PID and
nested error details; missing-executable proof failures retain PID/errno/stage.
File-write failures retain both output fallbacks. Reporting is nonmodal and
cannot retry, grant control, activate a window or start another manager. Existing
failure paths still exit; freeze/pause precede reporting, and reporting precedes
shutdown waits. The final startup session's thrown errors are reported too.

The callback regressions reproduced 29 failed assertions before repair. All
**198 offline Swift methods** and **235 Python tests** pass; the production
library compiles using cached dependencies. See the
[diagnosis and coverage limits](native-tabs-diagnosis.md#native-intent-before-reads-and-readable-lifecycle-failures--2026-10-17)
for the executed cases and source-only lifecycle wiring checks.

The installed `.20261016.2` bundle matches the saved release and does not contain
these changes. Its recent startup logs show a rapid exit but redact the final
reason, so the current blocker has not been confirmed or repaired by installing
anything. Both intent fixes, failure reporting, offline regression tests and
diagnosis notes are included in this local checkpoint. Creating the checkpoint
involved no additional tests, builds, installations, app/helper launches or live
checks. Nothing was pushed.

## Cross-window callback chain cancellation — 2026-10-17

An awaited automatic command targeting another window could reject newer user
intent correctly, then let a fallback, sequence or pipeline command recapture
that window and override the user's choice. Failed commands now retain their
continuations through model refresh and supersede the original initialization
when those continuations become stale. Later branches and callbacks remain
blocked; ordinary failures and successful optional probes retain their behavior.
Ownership and physical-write authority checks are unchanged.

Six new offline methods / 41 cases exercise all three operators, throwing reads,
nested movement/close, refresh suspension, independent initialization and valid
fallback/pipeline behavior. All **204 offline Swift methods** and **235 Python
tests** pass, and the production library compiles with cached dependencies. See
the [diagnosis and validation limits](native-tabs-diagnosis.md#cross-window-callback-chains-retain-cancellation--2026-10-17).
The continuation fix, offline regressions and diagnosis notes are included in
this local checkpoint and are not installed. Creating the checkpoint involved
no additional tests, builds, app/helper launches, installations or live checks.
Nothing was pushed.
