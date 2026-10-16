# Offline investigation of the AppKit fixture failures

Investigated on 2026-10-14 against local checkpoint `dc94868d`, then repaired offline as described below. During that offline phase, no manager, AppKit fixture, or terminal app was launched, and no live AX/CG observations, command sockets, installation changes, or network access were used. The installed applications were unchanged during that phase; the repair, tests, and validation notes were committed as `5cf47d53`. Subsequent authorized installation and live validation are recorded at the end of this document.

## Finding

At the checkpoint, production native-tab reconciliation had reproducible recovery gaps. A transitional snapshot could become the next comparison baseline before a merge, first-tab replacement, or detachment had been recognized. A later consistent snapshot then lacked the delta or eligibility needed to finish the transition. This could leave a source slot alive after a merge, retain both slots after first-tab creation, or omit a detached window's workspace inheritance.

The harness also has an evidence gap: the failed runs recorded managed window titles/workspaces, but did not save the relevant AX observations or verified AppKit group membership. The exact trigger in those particular live runs cannot be established from their artifacts. The offline cases below are synthetic sequences exercising production code, not captured traces of those failures. They establish code defects consistent with the reported symptoms; they do not prove which one occurred in the live fixture.

## Saved live evidence

- `.debug/aquaspace/ownership-live-1792025473646147000/failure.json`: background run expected only `c` on test workspace A after merging the two-tab source into a bare destination. It instead retained `b` on test workspace B and `c` on A. Its independent monitor recorded 114 inventories over approximately 7.78 seconds, with one manager throughout and no errors. This failure cannot be dismissed as a second manager controlling the windows.
- `.debug/aquaspace/ownership-live-1792025571262093000/failure.json`: foreground run stopped at initial two-tab grouping, reporting both `a` and `b`. Its independent observer also encountered the BSD process-identity permission error fixed in `dc94868d`; its 34 completed inventories still show the same manager PID. These records neither prove nor rule out a management interruption after the last successful inventory.
- Neither run reached the fixture's detach operation. There was no failed live detachment assertion in these runs.
- Earlier passing merge/detach logs remain relevant but tested settled observations. For example, `.debug/aquaspace/validation-20261014/scoped-candidate.log` records successful 2+2-to-4 merging followed by selected/inactive splits. Those successes do not cover the multi-snapshot recovery cases below.

The current ignored live harness contains a later membership-save addition, but neither failed output directory contains `fixture-merge-membership.json`. Its current source cannot substitute for evidence missing from those runs.

## Offline feedback loop

Run:

```sh
python3 script/test-native-tabs-offline.py
```

Before the repair, this exited **1** with **six failures and five passing controls**. Those failures were retained as ordinary regression tests, without expected-failure annotations. The repaired source now passes all six, plus the additional checks described below.

The runner builds a separate dependency-free Swift test package under `.debug/native-tabs-offline/`. It copies the production `NativeTabReplacement.swift`, `nativeTabTopology.swift`, and `nativeTabs.swift`. Matching, AX observation decoding, state reconciliation, acknowledgements, and representative tracking run unchanged. Only AppKit imports and the model-tree transfer adapters are excluded. In-memory AX elements supply attributes and containing-window identities; a synthetic server inventory and task-local clock supply creation history. No AX/CG implementation or app entrypoint is included. This tests the emitted ownership updates, not the main-actor model/AX write pipeline. It now also copies the 16 existing matcher/queue regressions and supplies memory-only AX identities for those tests.

The runner preserves unchanged generated files so subsequent runs are incremental. The original diagnostic run and an unchanged repeat both produced the same six failures. Logs are retained locally under `.debug/native-tabs-offline/`. The table records that pre-repair result:

| Case | Expected ownership update | Current result |
| --- | --- | --- |
| Merge source into bare destination, source geometry settles one scan late | Retire source into destination | Missing permanently |
| Same delayed geometry with an established two-tab destination | Retire source into destination | Missing permanently |
| Merge with a concurrent auxiliary server ID, followed by a stable inventory | Recover the deferred merge | Missing permanently |
| First tab's geometry settles one scan late | Transfer predecessor's slot | Missing permanently |
| AXWindows briefly includes both first-tab siblings, then exposes only the selection | Transfer predecessor's slot | Missing permanently |
| Inactive detachment exposes the window before its group's count decreases | Inherit the group's workspace | Missing permanently |
| Bare-destination merge with immediately consistent observations, at ages 0 and 3 seconds | Retire source into destination | Pass |
| First tab with immediately matching geometry | Transfer predecessor's slot | Pass |
| Selected detachment with a surviving native bar | Inherit the group's workspace | Pass |
| Inactive detachment with immediate count decrease | Inherit the group's workspace | Pass |
| Independent windows with identical frames | Keep independent ownership | Pass |

## Causes in production code

### Merge history is consumed before matching succeeds

`AxNativeTabState.reconcile` calls topology matching, then unconditionally merges current observations into its history (`tree/nativeTabs.swift`, around lines 60–68). `nativeTabTopology` requires a positive destination tab-count increase relative to that history (`tree/nativeTabTopology.swift`, around lines 80–106).

A minimal sequence is:

1. Source `b` has two tabs and width 400 at its offscreen origin. Bare destination `c` has width 800.
2. The destination reports three tabs, but refreshing the source still returns width 400. The source's old bar is destroyed; the source root remains valid. Matching correctly waits because geometry does not yet agree.
3. The source now reports width 800. The old bar is still destroyed and the inventory is stable, but the stored destination already has three tabs. Growth is zero, so matching never retries the source absorption.

An auxiliary server ID can cause the same loss through the intentional concurrent-creation guard. Refusing the first ambiguous snapshot is conservative; discarding the event's history makes that refusal permanent even when the synthetic observations subsequently satisfy the original matching requirements. Removing the ambiguity guard would be an unsafe shortcut.

### First-tab replacement becomes ineligible after one unsuccessful observation

`nativeTabReplacements` skips an already observed successor unless its previous observation was bare and the ID remains recently created (`tree/nativeTabs.swift`, around line 234). If the first snapshot with its tab bar has mismatched geometry, or still lists both siblings as exposed, no replacement is emitted. That snapshot is nevertheless stored. On the next scan, the successor already has a stored bar and is skipped, even after geometry or AXWindows settles.

### Detached windows become ineligible before the shrinking group is observed

`nativeTabTopology` considers newly exposed detached windows only when `previous[newId] == nil` (`tree/nativeTabTopology.swift`, around line 63). For a previously inactive tab without remembered representative membership, exposing its separate window while the source still reports its old count emits no inheritance. Once the source's count catches up, the detached window already exists in `previous` and is no longer considered. The test expires creation grace using synthetic time, so this failure is independent of the two-second birth allowance.

## Hypotheses resolved and remaining limits

The newly created bare destination is not by itself the cause: the same merge succeeds both inside and outside creation grace when observations agree. A simple 2.4-second fixture delay would not fix the confirmed loss of transition history. Nor does disabling foreground activation establish correctness; both classes of replay fail without any activation code at all.

The earlier claim that merge/detach checks were merely inconclusive is now narrower: actual reconciliation bugs are demonstrated offline, while exact attribution of the saved live failures remains unresolved. The main-actor tree-transfer path and app-specific behavior have not been revalidated in this investigation.

Future authorized live runs should save fixture member IDs/window numbers plus the AX snapshot sequence at the failure, and stop labeling unreached detach steps as failed detach checks.

## Offline repair and validation

The repair retains one pre-transition observation baseline for at most two seconds, separate from the latest AX observation. It preserves unmatched count/bar changes and newly exposed possible tabs, then sends that baseline and freshly read geometry through the existing matcher. The geometry, exact tab-count accounting, bar/root validity, concurrent-creation, and uniqueness checks in `nativeTabTopology` and `nativeTabReplacements` are unchanged.

Confirmed replacements, absorptions, and inheritances settle their affected IDs immediately. Unrelated confirmed transitions can proceed without replaying them or extending the original recovery deadline. Other newly observed independent windows enter the baseline so they remain competing candidates. If an unclassified provisional group disappears, the partial transition loses its attribution: recovery starts a fresh observation epoch rather than silently removing a competing source. The added refusal tests caught this gap in the first repair attempt and passed after that correction.

Recovery history expires at the two-second boundary and is discarded on hidden/unobservable snapshots, mouse-drag suppression, and native Space invalidation. Closed roots cannot be used as merge sources. Cancellation during AX reads leaves the baseline and queued ownership updates unconsumed; acknowledgements retain their existing semantics.

Two downstream cases also needed repair:

- A detached window can already have a managed model when delayed count evidence identifies its source. Both existing-model return paths in `MacWindow.getOrRegister` now apply a supplied native-tab workspace through the normal workspace move helper. This keeps floating/fullscreen state and avoids new-window callbacks. The fresh-registration callback path is unchanged.
- A recovered first-tab transition can form a queued switch-back cycle before its main-actor commit. If the exposed original slot is still managed alongside a staging sibling, the transfer plan keeps the original binding. It still transfers from the sibling when the first transfer already committed and the original model no longer exists. A state-machine regression reproduced the erroneous staging-workspace transfer before this correction.

Final validation passed **44 offline tests**:

- `python3 script/test-native-tabs-offline.py`: 26 state-machine tests plus 16 existing matcher/queue tests. This includes all six original reproductions, competing sources/destinations/predecessors, competing shrinking groups, exact-count refusal, real concurrent-window ambiguity, provisional-group disappearance, expiry without deadline renewal, source closure, hidden/unobservable/Space invalidation, cancellation, acknowledgement retries, independent confirmed transitions, and switch-back before commit.
- `python3 script/test-native-tab-model-offline.py --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk --scratch-path /private/tmp/aerospace-native-tab-validation-build`: two tests against the real model tree with `TestWindow`/`TestApp`, covering tiled/fullscreen and floating pre-registered splits, focus preservation, and idempotent repeated inheritance. The production AppBundle library compiled with the project's Swift settings. No manager executable or AppKit fixture is launched; dependencies use the existing cache with automatic resolution and updates disabled.

Final logs: `.debug/native-tabs-offline/recovery-final.log` and `recovery-model-final.log`. Earlier red runs include `diagnosis.log`, `recovery-ambiguity-red.log`, and `recovery-switchback-red.log`.

These results establish offline recovery and model-binding behavior. They do not supply the missing AX trace for the historical live failures, nor replace live AppKit/Ghostty validation. The repair was committed locally; no installation, app launch, or push was performed during the offline phase.

## Subsequent authorized installation and live checks

Commit `5cf47d53` was subsequently built, signed, and installed as AquaSpace `0.21.3-aquaspace.20261014.6`. The guarded updater confirmed empty manager inventory before launching its one replacement. Final verification found only AquaSpace PID 52732 controlling windows, with verified empty peer inventory and installed app/CLI hashes matching the release build manifest.

Both background and foreground AppKit fixture sequences passed: **87 live assertions** including cross-workspace merges into bare destinations, selected and inactive detachment, independent movement, incoming-selected merges, two-to-one splits, bare merges, remerging, and repeated selections. The fixture now verifies NSWindow membership and saves its own AX snapshot sequence. Two fixture setup failures (JSON dictionary keys and initial workspace placement) were corrected without changing or reinstalling AquaSpace. The complete suite preserved its eight existing baseline windows and restored workspace/focus; all disposable windows were closed.

Across installation and the three fixture attempts, independent monitors recorded 380 process inventories, with peak one manager and no scan errors. No stock AeroSpace process was launched, and nothing was pushed. The [full live validation record](aquaspace.md#recovery-checkpoint-installation-and-live-validation) includes report paths, baseline details, limitations, and the final verification file. This subsequent live validation note is recorded in a local documentation checkpoint after `5cf47d53`.

## Independent Ghostty window moved during application checks

The later application-specific run preserved Ghostty's original tab/terminal identities but failed workspace preservation: existing floating window 21423 moved from workspace 2 to `native-tab-test-apps-b`. It was restored before that run ended. The saved `checks.jsonl` establishes that all eight baseline assignments were correct after Finder cleanup and that 21423 was wrong after Ghostty cleanup. The Ghostty driver validates created tab IDs and terminal UUIDs before selection/closure; the harness's explicit group move used test window 23530, not 21423. It did not record per-action baseline assignments, AX snapshots, or native Space notifications, so the exact live event remains unproven.

An offline reproduction identifies a production path that produces the same unintended move. `GlobalObserver` invalidates native-tab observations when the native macOS Space changes. If an existing independent window is absent from the next AXWindows read, then reappears while another group's count decreases, `nativeTabTopology` treats it as a newly exposed detached tab. The shrinking-group path does not require matching geometry or prior membership. `MacWindow.getOrRegister` then applies the inferred workspace even to the existing registered model. That existing-model behavior is required for genuine delayed detach recovery; the invalid attribution must be stopped before it reaches the model.

The new regression first observes the independent window and a separate group, invalidates observations, observes only the group, then closes a tab while exposing the independent window again. With the previous source it failed in all four combinations of a two/three-tab group and coincident/offscreen independent-window frames, emitting inheritance for 21423. These are synthetic observations exercising the production state machine, not a reconstruction of the missing live AX trace. A second reproduction covers the geometry-only final-tab replacement boundary, where the independent window must remain excluded while the real survivor retains its group's slot.

The repair retains independent bare-window evidence separately from invalidatable AX observations. Only accepted settled bare observations establish that evidence; provisional windows awaiting recovery do not. The shrinking-group and two-to-one geometry paths cannot claim an established independent window, and the geometry-only replacement path cannot transfer its slot. Confirmed joins consume that evidence, while actual shared native-bar identity still permits an incoming selected window to join a different group. Recently created bare staging windows remain eligible for first-tab recognition during the existing creation allowance. Cancellation commits none of this evidence before the existing final cancellation check.

This adds negative membership evidence without changing manager admission, process scanning, controller locks, AX write gates, existing exact-count/geometry/uniqueness guards, or recovery deadlines. The original six recovery reproductions and all existing ambiguity tests remain passing. Seven added tests cover the four failing variants, simultaneous real detachment with an unrelated window present, selected closing with coincident geometry, genuine merge/select/detach cycles, first-tab staging, positive incoming-selection bar identity, and cancellation.

Offline validation passed **120 tests** as reported by the runners:

- Native-tab replay: 49 tests, including the previous 42 and all four variants of the primary regression.
- Real model tree and production AppBundle library: 2 tests.
- Isolated ownership admission/process-reader tests: 16 tests with aborting native API stubs.
- Python installer/entrypoint/retired-harness regressions: 53 tests.

Logs are saved under `.debug/native-tabs-offline/`: `ghostty-independent-red.log`, `ghostty-independent-final.log`, `ghostty-independent-model.log`, `ghostty-independent-ownership.log`, and `ghostty-independent-python.log`. No application bundle was rebuilt or installed, no app or live test was launched, no live process/AX inventory was queried, and nothing was pushed during that offline investigation. The source changes, regressions, and notes were committed together as `f13337a2` before the separately authorized installation and live validation below.

### Subsequent installed Ghostty validation

The fix is now installed as AquaSpace `0.21.3-aquaspace.20261014.7`, with app/CLI hashes matching the `f13337a2` build manifest and valid signatures. The guarded updater recorded empty manager inventory before its single replacement launch. AquaSpace PID 68598 remained the sole controller through the completed Ghostty tests; independent monitors recorded 678 inventories across all attempts, peak one and no scan errors.

The completed suite passed 109 assertions using disposable Ghostty terminals on dedicated test workspaces. It exercised three-tab creation, group movement and repeated selections, two independently observed active native Space changes, inactive/selected closes, and cleanup. Existing window 21423 stayed on workspace 2 after every operation and across 108 background window samples; original terminal identities and all 12 baseline workspace assignments were preserved. Two earlier setup/observer limitations were corrected in the harness, with no source changes or repeated installation. This supplies foreground application/Space-boundary coverage, not a captured replay of the earlier missing AX trace or Ghostty merge/detach coverage.

The [full installation and live record](aquaspace.md#ghostty-fix-installation-and-live-validation-after-f13337a2) includes evidence paths and the later focus/fullscreen changes observed after successful cleanup. Final read-only checks confirm the original Ghostty workspace, all baseline workspace assignments, exclusive ownership, installation hashes, and closed disposable windows without overwriting newer desktop state. These live validation notes are included in a local documentation checkpoint after `f13337a2`; nothing was pushed.


The subsequent explicitly authorized isolated Ghostty native-menu suite completed
31 live assertions against the installed `f13337a2` release. Actual Ghostty merges,
three-to-two and two-to-one detach boundaries, independent moves and remerges all
passed while existing window 21423 stayed on workspace 2. No production changes
were needed. The [validation record](aquaspace.md#automated-native-ghostty-mergedetach-validation-after-a243c7f0)
distinguishes the completed suite from earlier harness setup/lookup failures and
records independent native membership and single-manager monitoring.

## Hidden Zen window omitted during stock-to-fork startup — 2026-10-15

The saved handoff at
`.debug/aquaspace/inventory-switch/run-20261015-122020/` captured Zen window
**28122** on workspace **1**, with layout `macos_native_window_of_hidden_app`.
After takeover, the managed-window listing omitted it while a separate saved
read-only native observation still found window **28122** in Zen PID **15654**.
The earlier all-window preservation check remains failed; it was not a native
window closure or a passing handoff.

The investigation identified a deterministic classification failure in
`windowLevelCache.swift`. `getWindowLevel` queried WindowServer with
`.optionOnScreenOnly`, so a cold cache had no layer for a hidden application
window. Zen belongs to the Firefox family, whose strict classifier rejects a
window unless its layer is positively known to be `.normalWindow`. Thus the
hidden window could be registered as a popup instead of a workspace window.
Popups have no visual workspace, so `toLiveFocusOrNil` cannot resolve them for
the session's `move-node-to-workspace` command. Startup restoration ignores that
command error, leaving the window outside `list-windows --all`. Repeated
on-screen-only refreshes cannot repair it while the app remains hidden. Visible
windows already cached before hiding do not exhibit this cold-start problem.

Three alternatives were considered: hidden-window filtering, late AX discovery
after one-shot session restoration, and native-tab suppression. The isolated
replay supplies an ordinary hidden Zen AX window immediately, without any tab
state or asynchronous discovery, and still reproduces the popup result using
the actual cache and classifier. This establishes a source-level causal path
consistent with the saved omission. The old live evidence did not capture Zen's
full AX/classification trace, so it cannot exclude another contributing native
API failure in that historical run.

The fix changes the layer query to `.optionAll` while retaining
`.excludeDesktopElements`, just as the existing WindowServer ID reader already
includes off-screen windows. Hidden/minimized windows and windows on inactive
native Spaces now retain their actual layer metadata. No unknown layer is
assumed normal, and no hidden app is unhidden or activated. The strict browser
classifier, popup/tooltip exclusions, native-tab attribution, ownership admission,
session command planning and focus behavior are unchanged.

`script/test-hidden-handoff-offline.py` compiles the production layer cache,
classifier, bundle-ID rules and session command planner in an isolated package.
WindowServer and AX types/functions are replaced by memory-only fixtures;
AppKit/application entrypoints and live CG/AX/process calls are absent. Its
seven tests cover hidden Zen, the AXDialog hidden-window variant, a cache first
populated by a visible window, actual non-normal layer rejection, unreadable or
absent metadata, buttonless tooltip rejection, and visibility-independent
classification across Zen/Firefox variants/Chrome. Session assertions retain
the saved workspace command and original focused window rather than focusing
Zen. Four test methods initially failed with the old on-screen-only query;
all seven pass with the fix.

Validation completed offline:

- New hidden-handoff replay: **7 tests passed**.
- Existing native-tab reconciliation replay: **49 tests passed**.
- AquaSpace Python regressions: **223 tests passed**.
- Whitespace checks passed.

Only isolated test packages/binaries were built and run. Neither manager, any
diagnostic helper, installed CLI, or live reader was launched; no live process,
window or focus query was made. No application bundle was rebuilt, nothing was
installed, no live handoff/test occurred, and nothing was pushed. The source fix,
offline regressions and these notes form the local hidden-Zen repair checkpoint.
Creating the checkpoint involved no additional tests, builds, installations,
app/helper launches or live checks. At that offline checkpoint the installed
AquaSpace still had the old layer query, pending a separately authorized
rebuild/install and live verification.

The subsequent authorized build/install and handoff run installed release
`0.21.3-aquaspace.20261015.2` from `fafd604a`. Exact Zen window **28122** remained
hidden on its captured stock workspace **2**, with the same native process
identity. All **264 manager inventories** in the resumed run observed at most
one manager, with confirmed stock exit and an empty spawn boundary before the
sole AquaSpace launch. The requested Zen/ownership checks passed, but the
broader all-window comparison failed for seven other stock IDs that remain in
WindowServer metadata and are absent from the apps' current AX window lists.
That separate discrepancy remains unresolved and the full result remains
failed. See the [scoped live validation and limitations](aquaspace.md#hidden-zen-rebuild-and-ordered-handoff-validation-after-fafd604a--2026-10-15).

## Seven stock-only IDs: retained discovery state and one-shot restoration — 2026-10-15

The user authorized offline investigation only: no app/helper launch, live test,
desktop query, installation or push. This investigation reads the saved
`run-20261015-205646` evidence and local source; the earlier live failure remains
unchanged. No production code was changed.

The stock snapshot minus the AquaSpace workspace inventory is exactly these
seven IDs. Every one had saved stock workspace **1**, fullscreen flag `false`,
and a layer-zero WindowServer record after the handoff:

| App | PID | Missing IDs | Saved later AXWindows observation |
| --- | --- | --- | --- |
| Finder | 684 | 30583, 29724 | Only window 30881 and the desktop element |
| System Settings | 738 | 29598 | Empty |
| Obsidian | 22595 | 30197, 29754 | Empty |
| VSCodium | 93525 | 30290 | Empty |
| Vesktop | 15177 | 30682 | Empty |

Those reads succeeded and reported the applications unhidden. They did not
report seven accessibility errors. A layer-zero WindowServer record establishes
that the ID was still present in that native inventory; it does not provide an
AX window root or prove that a fresh client can manage the window. No saved
trace proves that these were closed windows or inactive members of native tab
groups.

The important source behavior is shared by stock tag `v0.21.3-Beta` (local
commit `d56e1637c3a1ed660d0cadd7534e94fb3218d1c3`) and AquaSpace:

1. `Ax.windowsAttr` explicitly documents that windows on inactive native macOS
   Spaces may be absent from AXWindows. Those Spaces are separate from the
   numbered AeroSpace workspaces in the session file.
2. `MacApp.refreshAndGetAliveWindowIds` starts from already retained AX windows.
   A retained root survives an empty AXWindows list while
   `containingWindowId()` still succeeds. A fresh manager has no such roots and
   can discover only the roots returned by AXWindows/focused-window discovery.
3. AquaSpace's all-Spaces WindowServer inventory supplies ID/layer and native-tab
   evidence. It does not manufacture AX roots for every ID. The hidden-Zen
   layer fix therefore cannot discover a different app's absent AX root.

The captured foreground was ChatGPT **28146**, whose recorded layout was
`macos_native_fullscreen`. The first attempt also recorded native foreground
changes between ChatGPT, Zen and Helium before the later stock-to-fork switch.
Stock retaining windows observed before a native Space transition, followed by
an AquaSpace cold start on ChatGPT's fullscreen Space, is the leading explanation
for this cross-application pattern. This is an inference: no native Space
membership/visibility map or pre-handoff retained-root validation was captured.
Stale-but-still-resolvable references, or a discovery transition between the
snapshots, cannot be excluded. The records do not prove that AquaSpace closed,
reclassified or merged these seven windows.

`script/test-window-discovery-offline.py` now compiles the actual stock and fork
refresh methods, actual AquaSpace tab reconciliation and actual session
restoration method against memory-only AX/application/thread objects. The small
registration seam models successful subscriptions; no native registration or
app lifecycle code is used. The sanitized captured inventory records evidence
file hashes and explicitly labels the retained-root premise as a hypothesis.

The differential replay establishes:

- Stock with retained roots versus a cold AquaSpace reproduces **exactly the
  seven-ID difference**, without any tab replacement, absorption or inheritance.
- Equal retained state makes both versions retain the same IDs across all five
  apps. Reversing which manager retains the roots reverses the difference.
- WindowServer IDs alone discover no AX windows. Exposing the same roots again
  lets both implementations discover them; confirmed invalid roots are collected
  by both. An unavailable AXWindows attribute has the same cold-start limit.

This establishes a discovery-history mechanism consistent with the saved
disagreement; it is not a retrospective capture of the missing pre-handoff AX
state. It rules out a necessary native-tab or classification regression in the
reproduced path, rather than claiming to rule out every possible historical API
failure.

There is also a definite restoration defect independent of the exact visibility
trigger. `WindowManagerSession.restorationCommands` filters out undiscovered
IDs, while `restoreStartupSession` clears the saved session before executing
commands and ignores command failures. The replay invokes the actual restoration
method with the seven IDs absent, then exposes them and invokes it again: **no
saved move for those IDs is ever issued**. A later discovery cannot recover the
discarded workspace/layout/fullscreen intent. The historical trace did not save
the startup all-windows map or individual command results, so it cannot show
which IDs were filtered versus attempted and rejected during that particular
startup; neither path currently has deferred restoration.

The repair direction is to retain unresolved per-window metadata and apply it
when a legitimate AX root reappears, after validating the source application's
identity and window ownership. The current four-field window session has no
per-window PID/birth identity, so an identity-safe deferred repair also needs
source identity captured before the handoff; a numeric window ID alone must not
authorize a later move. It must not create windows from CG IDs, focus or
unhide other Spaces to force enumeration, relax popup/native-tab admission, or
replay stale desktop focus when a user has moved on. That repair is **not
implemented** in this investigation. The full live all-window comparison
remains failed; the earlier hidden-Zen and single-manager results remain scoped
passes.

All **8 diagnostic test methods** pass, including the five-app parameterized
case. Only isolated memory-only test binaries were compiled and run. No manager,
diagnostic helper/library, installed CLI or native desktop reader was invoked;
nothing was installed or pushed. Replay output and extracted-source hashes are
under `.debug/aquaspace/seven-window-diagnosis/` and
`.debug/window-discovery-offline/`. The diagnostic tests and these notes are
included in the local deferred-restoration repair checkpoint below.

## Identity-safe deferred restoration repair — 2026-10-15

The user authorized the source repair and offline regressions, without app/helper
launches, live tests, installation or pushing. This supersedes the earlier
source-only finding that deferred restoration was not implemented; it does not
change either historical live result.

`WindowManagerSession` now carries optional per-window owner metadata: PID, UID,
kernel start seconds/microseconds, executable path, unique process identifier,
exec version and image UUID. The reader brackets path/generation reads with
kernel identity checks and refuses missing, changed or foreign-user identity.
Capture also matches the source's window/PID attribution against strict native
owner inventories before and after capture. A stock capture must retain the same
window rows through its final exchange before the old manager is stopped.

The session remains compatible with existing four-field JSON. Legacy snapshots
without authenticated owners retain immediate startup restoration, but cannot
authorize a delayed numeric-ID move. In particular, the old saved seven-ID live
session must not be retrofitted with guessed ownership and relabeled verified.
A future live test needs fresh identity capture from the updated path.

The new `PendingWindowRestoration` policy retains workspace, floating/tiling mode,
AeroSpace fullscreen flag and saved root layout for undiscovered windows and
failed startup assignments. It rejects duplicate/invalid window rows and
unvalidated owners. A window that appears while another startup command awaits
is not incorrectly marked completed: completion is limited to IDs in the actual
original command plan. Successful restoration is consumed once; retryable AX
reads or an unsuccessful model commit do not silently consume the assignment.

Before ordinary discovery and again before a delayed commit, the adapter checks
native window ownership and the exact current process identity. An observed
native disappearance, ambiguous owner, unreadable native inventory, process exit,
PID reuse, exec change or missing process identity revokes pending intent.
There is no timer that launches an app or forces AX discovery.

A delayed candidate needs a normal registered model window plus fresh AXWindows
membership for that same retained root and PID. Duplicate IDs/roots, missing
children/titlebar information and popup classification cannot authorize it. Two
AX reads must agree on the registered root's subscription token; a replacement
root cannot inherit the previous root's pending assignment. Model object,
parent/workspace/fullscreen state and token changes across reads are checked.
After the reads the adapter rechecks controller admission and native ownership,
then commits synchronously without another await.

The commit changes only that background window's model parent and fullscreen
flag. It runs no CLI, focus/workspace activation, unhide, native fullscreen,
menu or AX write command. A new empty target workspace can regain its saved root
layout; an existing workspace's layout is never overwritten by delayed intent.
Native hidden/minimized/fullscreen containers are left alone until ordinary
conventional discovery makes restoration eligible. The user-change anchor is
pinned only at that point, so automatic native-container normalization does not
consume an unrestored assignment. Dialogs remain floating.
A model-focused window, native-focused window, foreground application, or a
window whose observed model state was changed by the user loses stale pending
intent. An unreadable foreground prevents a commit. Desktop focus commands are
confined to the existing initial session restore and are never queued for later.

Native-tab ownership takes precedence over stock's per-tab metadata. A native
bar with multiple tabs is ineligible for delayed reassignment. Proven replacement,
merge absorption and split inheritance cancel pending assignments for their
participants, scoped to the matching application PID. Existing tab matching,
ambiguity and ownership checks are unchanged.

For updates, `management-status` advertises identity-session schema 2 and
`handoff-session` exports the source manager's metadata under the existing
exclusive-controller admission checks. Unresolved, untouched assignments survive
that export, including absent target workspace metadata. An existing row can be
overridden only by the same captured owner. The Python updater validates the
schema, field types/ranges, unique rows and owner associations, and verifies the
same controller and schema afterward, before any stop. Once schema 2 is
advertised, a malformed or failed export cannot fall back to weaker legacy
capture. Older installed releases without that capability use their existing
immediate-only capture; no owner identity is invented for their old files.
Stock AeroSpace itself remains unmodified and does not acquire a deferred queue.

Offline validation:

- **39 discovery/deferred-restoration tests**, using actual stock/fork discovery,
  actual startup/deferral methods, actual AX proof logic and the pure policy
  against memory-only backends. Includes the seven-ID replay, startup discovery
  race, failed commands, later rediscovery, legacy decoding, export preservation,
  root layout, PID/exec/root reuse, native ambiguity, foreground/model changes,
  popup/dialog/native-tab exclusions, and peer arrival or inventory failure.
- **27 ownership/process tests**, including five new injected-kernel identity
  methods. Native process calls in this package abort if invoked accidentally.
- **9 hidden-window tests**, including strict native ownership ambiguity and
  malformed/unavailable inventory checks.
- **49 existing native-tab tests**.
- **232 Python tests**, including nine new identity-export transport methods;
  those tests block native library loading, subprocesses and signals.
- Full `AppBundle` library and CLI target compilation with the macOS 26.5 SDK;
  neither compiled product was executed. CLI debug-symbol generation required
  retrying outside the filesystem sandbox after an `Operation not permitted`
  error. No application launch or installation was part of that retry.
- Whitespace checks passed.

The earlier regression asserting that undiscovered assignments were discarded
was replaced by assertions requiring identity-backed pending state; legacy
snapshots still have an explicit rejection test. The startup-discovery race was
observed failing before its fix. No ambiguity or controller-admission test was
removed or relaxed. Only isolated memory-only test executables ran; no manager,
diagnostic helper/library, installed CLI, native process/window/focus reader or
live harness was invoked. No app bundle was installed and nothing was pushed.
The repair, offline regressions and diagnosis notes form the local checkpoint
after `b0928c61`; creating it involved no additional tests, builds, installations,
app/helper launches or live checks. The installed release and the failed broader
live handoff result are unchanged. Logs/source hashes are under
`.debug/aquaspace/seven-window-diagnosis/deferred-*`.

## Deferred-restoration live rediscovery proof after de5541d9 — 2026-10-15

The repair is now installed as `0.21.3-aquaspace.20261015.3` and tested with an
exclusive, serialized stock-to-fork handoff. A disposable AppKit window entered
native fullscreen on a dedicated test workspace after stock acquired existing
AX roots. All six surviving historical IDs then disappeared from fresh owning
applications' AXWindows while stock continued reporting them. A fresh session
captured their current workspace/layout/fullscreen rows and bracketed native
owner/process-generation identities. Stock exited completely before the new
AquaSpace was launched with that session.

Cold AquaSpace did not discover any of the six. Its guarded session export
nevertheless retained every saved row and identity. Leaving fullscreen on only
the disposable fixture made the six ordinary background windows discoverable.
The saved rediscovery observations show staging-workspace placements followed
by successful automatic pending restoration: Finder **30583**, **29724**, and
System Settings **29598** on workspace **1**; Obsidian **30197**, **29754**, and
VSCodium **30290** on workspace **3**. All six had their correct workspace by
the observation about 0.79 seconds after the first AX exposure. No external move
command repaired these rows after handoff. All 12 original baseline rows later
matched exactly, including layout/fullscreen, and original Ghostty **31814**
regained native/model focus on workspace **1** before owned fixture cleanup.

Vesktop PID **15177** had already exited and window **30682** was absent from
the all-Spaces native inventory. It was excluded from fresh metadata and was
not recreated. Thus the live result covers all six surviving historical windows;
the seventh cannot be tested as a surviving window. This controlled run proves
the native-Space AX omission mechanism, not the unrecorded cause of the older
seven-ID failure.

The successful run recorded **122** independent inventories, peak **one** manager
and two empty samples at handoff. Across the installation and fixture attempts,
all **256** monitored inventories peaked at one. AquaSpace **9465**, birth
`(1792117092, 152606)`, remained the sole controller with verified inventory and
schema 2. The earlier guarded stops and the interrupted update's explicit
recovery are retained, rather than relabeled as successes. The final fixture
exited and no production safety check was relaxed. No source repair was needed
during this validation; stock/config hashes are unchanged and nothing was pushed.

Evidence is in `.debug/aquaspace/deferred-handoff/run-20261015-221802/`, especially
`fullscreen-omission.json`, `stock-handoff-session.json`, `cold-discovery.json`,
`cold-pending-export.json`, `rediscovery-observations.json`, `final-export.json`,
`manager-samples.jsonl` and `results.json`. See the [installation, guarded stops,
identities and full validation record](aquaspace.md#deferred-restoration-install-and-native-space-handoff-validation-after-de5541d9--2026-10-15).
