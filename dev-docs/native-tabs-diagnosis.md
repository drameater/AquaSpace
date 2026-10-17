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

## Deferred-restoration command and root-continuity safety repair — 2026-10-16

The offline review of checkpoint `f60c1e5f` found three additional paths that
the successful six-window live run did not exercise. The user authorized fixing
them and adding offline regressions, with no app/helper launches, live tests,
installation or push. The earlier installed/live result remains historical;
this repair forms a local source-only checkpoint after `f60c1e5f`.

### Reproduced failures

1. A newly registered background window is published before registration awaits
   callbacks and before the deferred coordinator records its first model anchor.
   An explicit background move during that interval, or before the first retry
   of a failed startup assignment, was accepted as the untouched baseline. The
   later restore overwrote the user's workspace with stale saved intent.
2. A successful first AX proof established root A, but only the final proof's
   token was retained. If the second read became unavailable, the coordinator
   forgot A. Replacement root B in the same process and under the same numeric
   ID could then pass B/B checks on a later attempt and inherit A's assignment.
3. Session export checked native/process identity and model state but omitted
   registered-root continuity. Export after A was replaced with B could substitute
   A's pending desired row for B's current row. Restart removed the remembered
   token and turned this into a newly admitted stale assignment.

All three new regression methods reproduced their specific failure against the
checkpoint: the 42-test replay failed in those three methods with six assertion
failures. Its export reproduction exercises the actual `captureCurrentModel`
method and an encode/decode/reload, rather than only the pure row-merging helper.

### Source changes

`WindowRestorationMutation` supplies task-local command/initial-restore provenance.
The real shell dispatcher marks explicit command execution. A tree bind under
that provenance revokes pending assignments for the affected registered windows,
including descendants of moved containers, before the model changes. The
fullscreen command does the same before changing its flag. Revocation checks the
exact registered model object and its matching application PID, so an unrelated
window or an impostor sharing a numeric ID cannot cancel another assignment.
It works before any anchor exists and while a candidate is not yet conventional.

Internal initial restoration commands have a separate task-local scope and do
not revoke their own retryable intent. That scope cannot suppress an independent
user input task while the initial command awaits. Automatic discovery/native
normalization outside command execution retains its existing eligibility and
anchor behavior. No global suppression flag or ownership bypass was added.

The coordinator now remembers each successful root proof immediately, before
another read can suspend. A failed second read leaves the original token and
pending assignment retryable. A different token revokes intent; it is never
written over the established token as a new baseline. The existing final
process/native-owner/model/focus/controller checks remain in place.

Session capture now verifies any established pending root with two fresh AX
proofs before carrying its saved intent. A changed root revokes the old pending
row; export preserves the current model row instead. An unavailable proof blocks
the export and preserves retryable pending state. The updater consequently
receives an error before stopping the running manager, without a legacy fallback.
Assignments never discovered or proved still survive export using their original
native/process continuity checks; the repair does not invent AX roots for them.

`sessionForUpdate` retains exclusive-controller checks both before and after the
new awaits. The switch-to-stock path also uses the validated capture, after
freezing writes; failure exits without starting another manager. Controller locks,
peer classification, admission policy, native-tab precedence and ambiguity
rejection are unchanged.

### Offline verification

The replay now extracts the production shell dispatcher, movement helper,
fullscreen command, pre-bind mutation policy, owner capture, complete deferred
coordinator and guarded update-export method. AX, processes, model storage and
controller admission use memory-only backends. New coverage includes the three
original failures, explicit fullscreen before the first anchor, unrelated and
impostor windows, initial-restore versus independent user tasks, automatic native
normalization, unchanged-root retries, first/second export-read failures, a root
replacement during export, untouched undiscovered export and peer/inventory
changes across export awaits.

All **138 offline Swift test methods** pass: **53** discovery/deferred tests,
**27** ownership/process tests, **49** native-tab tests and **9** hidden-window
tests. All **232 Python tests** also pass. The complete `AppBundle` library
compile check uses the changed production sources without executing the library
or building/launching an app or helper. An initial compile attempt hit a denied
global module-cache path; retrying with caches inside the repository resolved it.
Whitespace checks pass. No existing ownership or ambiguity regression was removed
or relaxed.

Evidence and source hashes are under
`.debug/aquaspace/seven-window-diagnosis/safety-*`, including the original failing
`safety-red.log`, final `safety-final-replay.log`, `safety-ownership.log`,
`safety-tabs.log`, `safety-hidden.log`, `safety-python.log` and
`safety-library-exact-source.log`. No app/helper, installed CLI, native desktop
reader or live harness was launched; nothing was installed or pushed. The
previously installed `0.21.3-aquaspace.20261015.3` does not contain this source-only
repair, and no new live-validation claim is made. Creating this local checkpoint
involved no additional tests, builds, installations, app/helper launches or live
checks. Nothing was pushed.

## Automatic discovery callbacks versus new user intent — 2026-10-16

During preflight for the authorized rebuild/install/handoff after `96ef60c5`,
the user's existing floating-window discovery rules exposed an additional
interaction. The new command provenance treated a normal registration callback's
`layout floating` as fresh user intent. If a saved window returned through cold
discovery, its floating rule could therefore discard pending restoration before
the coordinator restored its saved workspace. The staged first build was not
installed or launched before checking this interaction offline.

The replay now extracts the actual `runOnWindowDetected` registration entrypoint
as well as the real shell and pre-bind policy. An automatic floating rule
reproduced the failure against the checkpoint: the 55-test run failed in that
method with three assertions (lost pending assignment, wrong final workspace,
and missing restoration mutation).

The follow-up adds a task-local automatic-discovery scope only around normal
registration callbacks. Shell dispatch in that scope does not claim new user
intent. Independent input tasks still revoke pending assignments; an explicitly
requested `run-callback` uses the separate direct entrypoint and also retains
that protection. There is no global suppression flag and no change to native,
process, root-token, ambiguity or exclusive-controller admission checks.

All 56 discovery/deferred replay methods pass, including the new automatic-rule,
independent-input and explicit-callback cases. The production app/CLI rebuild
includes all three committed repairs and this follow-up; its source hashes are
recorded in the staged build manifest. Build logs are
`.debug/aquaspace/deferred-safety-build.log` (first, uninstalled build) and
`.debug/aquaspace/deferred-safety-followup-build.log` (callback-corrected build).
The failing and passing offline records are
`.debug/aquaspace/seven-window-diagnosis/automatic-rule-{red,green}.log`.
All **141 offline Swift methods** pass across discovery/deferred (56), ownership
(27), native tabs (49) and hidden-window handoff (9); all **232 Python tests**
also pass. The corrected signed release is installed as
`0.21.3-aquaspace.20261016.2`. Its guarded native-Space handoff restored all six
surviving deferred assignments, preserved all 15 original final model rows and
restored original ChatGPT focus/fullscreen state. The automatic-callback source,
regressions and these notes are included in this local checkpoint. Creating it
involved no additional tests, builds, installations, app/helper launches or live
checks; nothing was pushed.
See the [installation, setup failures and successful live evidence](aquaspace.md#deferred-restoration-safety-install-and-handoff-after-96ef60c5--2026-10-16).


## Discovery initialization and superseded callback ordering — 2026-10-16

Offline review of `4396d3e4` found two scheduling gaps left by the automatic
callback provenance repair. First, registration published its model before
awaiting diagnostic/cache work and detection rules. A second refresh could
consume pending restoration while the first task's matcher was still awaiting;
the late rule then overwrote the restored workspace/layout. Second, an explicit
user move discarded pending intent but did not invalidate that outstanding
matcher, so its automatic rule could still move the window afterward. The earlier
concurrent-input test performed no automatic mutation after the input completed.

Four new deterministic memory-only reproductions failed against the checkpoint,
with **10 assertions**: restoration during an awaited matcher, a rule move after
an intervening user move, a later command in the same callback, and the deferred
next-window layout reapplying after the user's tiling choice. The replay now
extracts the actual production callback iteration and sequential shell dispatch,
instead of substituting the callback loop. Its matcher/AX/process/tree storage
remain memory substitutes; gates establish ordering without desktop input or
sleep-dependent races.

The working source introduces `WindowDiscoveryInitialization`, a token for one
exact model object's initialization. Registration installs it synchronously
before publishing the window, and finishes it in `defer`, including error exits.
The scope covers the first awaited reads and closed-window-cache path, not only
the callback function. Popup promotion and direct detection entrypoints establish
the same scope when registration does not already own it. A second invocation
cannot run the same initialization concurrently. Finished tokens remain attached
to their window so an awaiting restoration can detect a newer initialization;
the token's window reference is weak.

Both immediate startup restoration and deferred restoration exclude initializing
windows. The deferred adapter checks again after native-proof/controller awaits,
including initialization that started and finished during those awaits. It keeps
saved assignments retryable rather than consuming them before late rules. Startup
also rechecks each planned window command, so initialization beginning after the
plan was captured cannot cause a skipped assignment to be marked completed.

An explicit binding/fullscreen mutation now supersedes the exact registered
window's initialization even if no pending assignment exists. The original PID
check still gates discarding a pending row. Automatic matchers recheck their
scope before and after awaiting; shell dispatch checks before every subsequent
command, including commands inside a compound callback. The deferred next-window
layout also checks before reapplying. A stale scope fails when its registered
model is replaced or becomes unbound. Explicit callbacks outside the automatic
scope and independent user tasks retain their ordinary command semantics;
mutating another window does not invalidate this window's initialization.

All **70 discovery/deferred methods** pass, including **14 new regressions**.
Beyond the four original reproductions, these cover registration before its first
callback read, error cleanup, user input before rule dispatch, no-pending user
input, model replacement, unrelated-window input, initialization starting or
finishing during native proofs, and both startup-planning interleavings. All
**27 ownership/process**, **49 native-tab**, **9 hidden-window**, and **232 Python**
tests pass: **155 offline Swift methods** in total. No existing owner/PID/native
root, ambiguity, foreground or controller admission regression was relaxed.

The exact changed production `AppBundle` library compiled successfully with
cached dependencies and the macOS 26.5 SDK, without building or executing an app
entrypoint. Evidence is under
`.debug/aquaspace/seven-window-diagnosis/callback-order-*`: `red.log` preserves
the four original failures, `final.log` the 70-method run, `ownership.log`,
`tabs.log`, `hidden.log` and `python.log` the remaining suites, and `library.log`
plus `source-record.json` the compile and production-source hashes. Whitespace
checks pass. No app/helper, installed CLI, native desktop reader or live harness
was launched; nothing was installed, committed or pushed. The installed release
remains `0.21.3-aquaspace.20261016.2` and does not contain this source-only repair.


## Awaited callback command continuations — 2026-10-17

The readiness check after the uncommitted ordering repair found a further gap
inside a dispatched command. `layout tiling` captures its floating container,
awaits `getAxSize(.nonCancellable)`, then updates cached size and rebinds using
the captured workspace. An explicit user move during that read invalidated the
initialization, but there was no continuation check inside the command. Shell
entry checks cannot stop a command that is already past dispatch. Similar waits
precede native fullscreen/minimize writes and the close command's quit/close
choice; relayout also awaits native window classification before unbinding.

The memory replay now extracts the real complete layout, close and native-state
command bodies, the relayout adapter/classification helper, and floating-focus
preparation/cleanup. Its native reads suspend on deterministic memory gates and
its native-write adapters only append to memory logs; even the close/terminate
cases never interact with an application. Against the prior working source,
user moves and exact-model replacements during the five command/read paths
produced **29 failing assertions** across ten parameterized cases. The other
existing regressions and valid command cases passed. This failing run is retained
as `command-continuation-red.log`.

The repair adds `WindowCommandContinuation`, which captures automatic-discovery
provenance, the initialization token, the exact affected model, its latest user
mutation token and whether window control was held at dispatch. After native
reads, the command verifies that its original initialization is still current,
its affected model is still registered/bound, and no explicit mutation of that
model superseded it. It also rejects a yielding manager or loss of previously
held control. A per-window mutation token matters because callback commands can
target another window whose user changes must also win. Explicit command tasks
outside automatic discovery keep their ordinary command semantics. Existing
pending-owner PID checks, native-root/WindowServer/process continuity, peer
admission, controller lock and physical AX-write gates remain intact.

Layout checks before touching cached size, and relayout checks after native
classification before any unbind. Native fullscreen/minimize and close check
before their native effect; the close-all loop rechecks its retained subject
between awaited closes. Movement/focus, mouse positioning, enable size capture
and post-callback configuration synchronization also revalidate their continuations
before later effects. These are continuation guards, not attempts to undo an
operation already completed before newer input arrived.

Floating-as-tiling focus preparation completes all native center reads before
unbinding any candidate. It rechecks each candidate, geometry target and still
attached destination/index before the synchronous temporary binding phase.
Cleanup restores only an exact, unchanged temporary binding, comparing its user
mutation token even when newer input reused the same parent. Thus canceling a
later read cannot strand earlier floating windows unbound, and cleanup cannot
undo a user's intervening tiling choice. Untouched temporary bindings still get
restored. MRU restoration also skips a superseded automatic continuation.

All **85 discovery/deferred test methods** pass: the prior 70 and **15 new
methods**, including **36 new case invocations**. The cases cover five actual
command/read paths for newer user moves, model replacement, loss of control and
valid execution; separately targeted windows; unrelated user input; failed-read
fallbacks; ordinary explicit tiling; and seven floating-focus preparation/cleanup
sequences. All **27 ownership/process**, **49 native-tab**, **9 hidden-window**
and **232 Python tests** also pass: **170 offline Swift methods** in total.
The complete changed production `AppBundle` library compiles successfully with
cached dependencies and the macOS 26.5 SDK. The final source-hash record matches
the checkout, and whitespace checks pass.

Evidence is under `.debug/aquaspace/seven-window-diagnosis/command-continuation-*`:
`red.log`, `final.log`, `ownership.log`, `tabs.log`, `hidden.log`, `python.log`,
`library-final.log` and `source-record.json`. Some additional guarded command
paths are audited/compile-checked rather than independently fault-injected; the
read/write and focus-helper cases above are the executed memory-only coverage.
No app/helper, installed CLI, process inventory, native desktop reader or live
harness was launched. Nothing was installed or pushed. Both ordering repairs,
this continuation follow-up, their offline regressions and diagnosis notes are
included in this local checkpoint. Creating it involved no additional tests,
builds, installations, app/helper launches or live checks. Installed AquaSpace
is still `0.21.3-aquaspace.20261016.2` and does not include these repairs.

## Frozen admission, container choices and focus continuations — 2026-10-17

Offline review of `76c70eaa` found three remaining gaps. A callback dispatched
after peer detection froze writes captured `hadWindowControl = false`, which
looked like initial startup to the continuation guard. Before the watcher marked
ownership yielding, `close --quit-if-last-window` could therefore call
`NSRunningApplication.terminate()` outside the physical AX adapter. An inventory
error exposed the same interval. Second, explicit container layout/orientation
changes did not invoke the window-mutation hook, allowing a waiting automatic
rule to overwrite them. Third, newer focus/workspace choices did not invalidate
an awaiting automatic focus command.

The replay now extracts the complete production focus command, its fullscreen
check, the central focus setter and the container orientation method, alongside
the existing real callback/shell/layout/close bodies. All native reads, writes,
application termination, process inventory and tree storage in this replay are
memory substitutes. The initial eight new regression methods reproduced **25
failed assertions** against the checkpoint, covering both freeze causes,
container choices, and focus changes during matching and command reads.

`WindowControlPermission` distinguishes initial model preparation from frozen
control. Each freeze disables initialization and physical writes synchronously
and changes a generation captured by discovery lifetimes and continuations.
Even a subsequent successful claim cannot revive a lifetime from before the
freeze. The only runtime grant remains after successful admission/lock/peer
checks. Close also checks current native-write authority immediately before its
quit/close branch, including explicit commands and pre-claim startup callbacks.
Read-only model preparation remains possible without acquiring native-write
authority. No process, owner, ambiguity, native-root or controller-lock check was
relaxed.

Explicit layout choices invalidate the exact registered descendants of their
container, including no-op tiling/layout choices. Orientation normalization also
invalidates the descendants of every ancestor whose orientation it assigns.
Automatic rules and session restoration retain their existing provenance and do
not become user mutations.

Focus has a separate intent token captured at the start of initialization.
Explicit focus choices, including repeated window focus, and changed focus from
non-command paths supersede automatic focus. Session restore and automatic focus
do not advance that token. The central setter rejects stale automatic focus;
the focus command and floating preparation recheck it after reads, before tree
preparation, and before restoring MRU. Background workspace/layout assignment
can still complete after unrelated focus input. The workspace command's no-op
fast path records explicit focus intent too; that small path is compile/audit
checked, while the setter/command races are executed by the replay.

The final replay has **95 discovery/deferred methods**, including **10 new
methods / 19 case invocations**. All pass, together with **29 ownership/process**,
**49 native-tab**, **9 hidden-window** and **232 Python tests**: **182 offline
Swift methods** across those suites. Two additional real-model workspace recovery
tests also pass using simulated applications/monitors: **184 Swift methods** in
total. The production `AppBundle` library also
passes a compile-only check using cached dependencies and the macOS 26.5 SDK.
The Python wiring assertion now checks that the permission object's grant follows
successful admission, replacing its former literal-Boolean assignment check.

Evidence is under `.debug/window-discovery-offline/callback-safety-*`: `red.log`,
`green.log`, `ownership.log`, `tabs.log`, `hidden.log`, `python.log`, `library.log`
and `source-record.json`, plus `tab-model-cached.log`. The first optional real-model run had an empty package
cache; dependency fetch attempts failed under the network restriction. It did
not execute tests or download dependencies. The successful rerun used the
existing local cache. No app entrypoint, diagnostic helper, installed CLI,
native desktop reader, live inventory or live harness was launched. Nothing was
installed or pushed. These fixes, offline regressions and diagnosis notes are
included in this local checkpoint. Creating it involved no additional tests,
builds, installations, app/helper launches or live checks. Installed AquaSpace remains
`0.21.3-aquaspace.20261016.2`.

## Explicit no-op assignment and focus intent — 2026-10-17

Offline review of `00730108` found two early-return gaps in the preceding repair.
Repeating `layout floating` for an already floating window, assigning its current
workspace, or repeating its fullscreen state returned before invalidating a
waiting automatic initialization. The delayed rule could therefore override the
newer explicit choice. Focus at a workspace, DFS or monitor boundary could also
succeed with `stop` without calling the central focus setter; its intent token
never advanced. An explicit focus command's initial native read exposed the same
gap before it reached that setter.

The in-memory replay now includes the actual production workspace/monitor
boundary and wrapping functions, rather than its former unused boundary stub.
Monitors, tree storage, native reads and writes remain memory substitutes. Seven
new test methods (28 case invocations) exercise the real layout/fullscreen/focus
commands and movement helper through the production shell/callback loop. Before
repair they reproduced **37 failed assertions**: stale assignments/focus won,
tokens stayed unchanged and saved pending assignments remained eligible.

The floating-layout no-op now invalidates only its exact targeted window,
preserving other floating windows in the same container. Same-workspace movement
records the targeted window's intent before returning; this shared helper also
covers moves to a monitor's already-current workspace. Fullscreen records intent
before comparing its requested state. Existing success/failure results, including
`--fail-if-noop`, are unchanged. These explicit requests invalidate stale intent
even when that flag selects a failure result, matching the existing tiling-layout
policy. Every invalidation still uses the exact registered-model check and the
existing user/session provenance guard.

Focus records accepted explicit intent immediately after resolving its target,
before any fullscreen/geometry read or boundary return. Invalid targets do not
advance intent. Automatic and session commands retain their previous provenance;
their no-ops neither cancel the initialization nor claim user focus. The tests
also resume an older callback while a newer explicit focus command is still
awaiting its native read, asserting that focus never changes temporarily. A newer
focus no-op blocks old automatic focus while allowing background placement and
preserving unrelated pending assignments.

All **102 discovery/deferred**, **29 ownership/process**, **49 native-tab** and
**9 hidden-window** methods pass. Two additional simulated real-model recovery
tests pass as well: **191 offline Swift methods** in total. All **232 Python
tests** pass. The full production `AppBundle` library compiles against cached
dependencies and the macOS 26.5 SDK; its source hashes match the checkout and
staging. Existing compiler warnings are unchanged. Controller admission,
physical-write authority, native ownership, process identity, root continuity
and ambiguity checks were not weakened.

Evidence is under `.debug/window-discovery-offline/no-op-intent-*`: `red.log`,
`green.log`, `ownership.log`, `tabs.log`, `hidden.log`, `python.log`, `library.log`,
`tab-model.log` and `source-record.json`. No app entrypoint, diagnostic helper,
installed CLI, native desktop reader, live inventory or live harness was launched.
Nothing was installed or pushed. These fixes, regressions and diagnosis notes are
included in this local checkpoint. Creating it involved no additional tests,
builds, installations, app/helper launches or live checks. Installed AquaSpace
remains `0.21.3-aquaspace.20261016.2`.

## Native intent before reads and readable lifecycle failures — 2026-10-17

Review of `7317f7b5` found a native counterpart to the model-fullscreen no-op gap:
an explicit native-fullscreen request returned without invalidating discovery
when AX already reported that state. Floating-to-tiling also awaited native size
before recording user intent. An older detection callback could remain authorized
while the newer explicit assignment awaited its read. The sibling native-state,
close, move and relayout entries had the same timing boundary.

The replay adds three methods / eleven case invocations using the production
callback/shell loop, layout/native-state/close commands and relayout adapter.
Native reads suspend on memory gates and all native effects remain memory logs.
Before repair these cases produced **29 failed assertions**: native no-ops left
pending intent active and older automatic closes dispatched during newer reads.
The repaired entry helper records exact-window user mutation before constructing
its continuation and before the read. Layout tiling, native fullscreen/minimize,
close, move and relayout use it; focus and mouse reads retain their separate
semantics. Automatic/session provenance still prevents these entries from
acquiring user intent. All existing generation, exact-model, ownership and
physical-write authority checks remain. Move's complete body is compile/audit
checked; the five executed read paths are tiling, relayout, native fullscreen,
minimize and close.

The launch investigation separately verified all nine installed bundle files
against the saved `.20261016.2` release (revision `96ef60c5`). Saved October 17
logs show successful process startup followed by a rapid orderly exit, with the
final Foundation message redacted. An earlier saved October 16 log reports an
uncertain process inventory. This supports an admission-failure hypothesis but
does not establish the current exact blocker; neither app nor any reader/helper
was launched to reproduce it. These recent checkpoints are not installed.

`WindowManagerFailureReport` now records lifecycle phase, timestamp, version,
executable, own PID and a bounded NSError cause chain. Reports go to
`~/Library/Logs/AquaSpace/last-failure.txt`, with a private directory (0700) and
file (0600), and to stderr plus explicitly public Console logging. Persistence
failure leaves the original cause in both fallback sinks. The process scanner's
missing-path proof wrapper retains its PID, errno and stage alongside the
underlying proof error, rather than losing those details in a generic refusal.
Its trust/signature/identity policy is unchanged; this wrapper is compile/audit
checked, while report-chain rendering is exercised with fixture errors.

Admission and handoff catches retain their fail-closed exits. The final startup
session also catches thrown errors and uses the same failure path. That path
yields, freezes writes and pauses management before reporting, then disables
bindings/drains writes before exiting. Reporting precedes shutdown awaits so a
slow drain cannot hide the reason. Reporting has no UI, activation, process,
retry or control-grant capability and cannot launch a replacement manager. The
last report is timestamped historical evidence; it is not cleared on success.

Four new behavioral report tests write only to isolated temporary directories
and inject both output sinks. They cover all three admission reasons, metadata,
permissions, overwrite, nested causes, bounded chains and persistence failure.
Three Python boundary checks reject the old log-only checkpoint and protect
public logging, phase wiring, freeze-before-report and report-before-await.
They do not execute the app lifecycle or native process functions.

All **105 discovery/deferred**, **33 ownership/report/process**, **49 native-tab**,
**9 hidden-window** and two simulated real-model tests pass: **198 offline Swift
methods**. All **235 Python tests** pass. The production library compiles with
cached dependencies and the macOS 26.5 SDK; source hashes match the checkout.
No app entrypoint, diagnostic helper, installed CLI, native process inventory,
desktop reader or live harness was launched. Nothing was installed or pushed.
The installed app's current startup blocker remains unconfirmed until a future
authorized run can produce the new report.

Evidence is under `.debug/window-discovery-offline/`: `mutation-entry-red.log`,
`mutation-entry-green.log`, `startup-report-ownership.log`,
`startup-report-wiring-red.log`, `startup-report-python.log`,
`mutation-entry-tabs.log`, `mutation-entry-hidden.log`,
`mutation-entry-tab-model.log`, `startup-report-library.log` and
`mutation-entry-source-record.json`. Both intent fixes, failure reporting,
offline regression tests and diagnosis notes are included in this local
checkpoint. Creating the checkpoint involved no additional tests, builds,
installations, app/helper launches or live checks. Installed AquaSpace remains
`0.21.3-aquaspace.20261016.2`.

## Cross-window callback chains retain cancellation — 2026-10-17

Review of `8ff76fe3` found that a rejected continuation was only a command
failure. If window A's automatic callback explicitly targeted B, newer user
intent for B correctly stopped that awaited command without superseding A's
initialization. An `||` fallback, subsequent sequence command or pipeline stage
could then capture B's new mutation token and move or close it anyway. A later
callback in A's same initialization could also continue.

The shell now gives each leaf command a task-local collection of its automatic
window continuations. After a failed command, stale continuations supersede
their captured initialization before refresh and again after refresh returns.
This preserves cancellation across every compound operator and later callback,
including reads that throw before reaching their continuation guard and newer
target intent arriving during the command's awaited refresh. The existing
initialization gate rejects subsequent dispatch; no branch acquires replacement
authority. Ordinary failures with valid targets still allow fallbacks. Successful
optional probes can skip changed candidates without canceling unrelated
placement. The scope is local to one command and cannot suppress another
window's independent initialization. All exact-model, user-intent, provenance,
controller generation, native ownership and physical-write checks remain.

The offline replay now extracts all four production compound shell functions;
fallback and pipeline no longer use fail-if-invoked stubs. I/O storage and model
refresh remain memory substitutes. Six new methods / 41 case invocations cover:

- Fallback, sequence and pipeline after five real awaited command paths:
  tiling, relayout, native fullscreen, minimize and close. Each is exercised with
  a successful native read and a throwing read while newer user input moves B.
- Nested follow-up movement and close commands, later callback dispatch, and
  an independent initialization after the rejected source callback ends.
- Newer user intent during the failed command's refresh, for all three operators.
- Ordinary read-failure fallbacks, unrelated user input, successful optional
  geometry probes, and pipeline input/output/error/exit-status propagation.

The original 30 stale-target cases reproduced **150 failed assertions** before
repair. Three additional refresh cases reproduced **six failed assertions**
against the initial repair, proving the need for the second cancellation check.
All **111 discovery/deferred**, **33 ownership/report/process**, **49 native-tab**,
**9 hidden-window** and two simulated real-model tests pass: **204 offline Swift
methods**. All **235 Python tests** pass. The full production `AppBundle` library
compiled through the simulated real-model test package using cached dependencies
and the macOS 26.5 SDK. An initial attempt used a scratch directory without
dependencies; SwiftPM attempted fetches that failed under the network restriction.
No dependency was fetched. The successful rerun used the existing AquaSpace build
cache. A separate relative scratch-path invocation was rejected before building.

Evidence is under `.debug/window-discovery-offline/cross-window-chain-*`:
`red.log`, `refresh-red.log`, `green.log`, `ownership.log`, `tabs.log`,
`hidden.log`, `python.log`, `model.log`, `model-cached.log` and `source-record.json`.
No app entrypoint, diagnostic helper, installed CLI, native desktop reader, live
inventory or live harness was launched. Nothing was installed or pushed. The
continuation fix, offline regressions and diagnosis notes are included in this
local checkpoint. Creating it involved no additional tests, builds, app/helper
launches, installations or live checks. Installed AquaSpace remains
`0.21.3-aquaspace.20261016.2`.

## Required targets and nested closes — 2026-10-17

Checkpoint `258641a8` only latched failed shell leaves. A successful native
fullscreen no-op targeting B could suspend in refresh, miss newer B intent, and
let a later command recapture B. Bulk-close also called individual closes
outside Shell, allowing the next close before the outer collector ran.

Required command targets now revoke stale initialization regardless of exit
status, both before and after refresh. Optional focus geometry/MRU candidates
retain their snapshot checks but do not register as required command targets.
Bulk-close checks the collector immediately after each nested close. A dispatched
close can permit its own model's expected absence for chain bookkeeping only;
this never permits another native write, reused ID, changed user token, or changed
controller generation. The physical-effect continuation checks remain unchanged.

Four new methods (six cases) reproduced nine failed assertions before repair:
successful refresh across AND/sequence/pipeline, nested bulk-close, and reused ID
after a completed close. Normal multi-close and optional probe behavior pass.
The discovery suite now passes 115 methods; the real-model library build and two
simulated recovery tests also pass. Evidence is in
`.debug/window-discovery-offline/required-target-{red,green,model}.log`.

## macOS 27 native plist inventory compatibility — 2026-10-17

Live preflight found two environmental launch blockers before any manager launch.
An orphaned browser_crashpad_handler (PID 15691, parent 1) retained a deleted Codex
Sparkle-cache executable. Its running signature did not satisfy the immutable
widget exception; no exception was added. Read-only kernel mappings, process
birth/exec identity, exact running code-signature proof and a native empty-window
inventory pinned the orphan. One guarded SIGTERM removed it. No manager or user
window was stopped, and the normal full process scan remained mandatory.

The next full scan exposed NeptuneOneWallpaper.appex's macOS 27 Info.plist:
NSHumanReadableCopyright contains XML control byte 0x03. Apple's plutil accepts
it, but Python Expat raises outside the updater's previous error wrapper.
The updater now canonicalizes the exact captured XML bytes through /usr/bin/plutil
on ExpatError, parses the binary result, and requires dictionary metadata with
string identity fields. It never strips invalid bytes or guesses identities.
Conversion errors retain PID/executable context and refuse admission. Foundation's
native controller parser already uses Apple's plist semantics.

Three new offline methods reproduced the failures using injected converter
results and blocked native APIs. All 240 Python tests pass. Live read-only full
inventory now succeeds and reports zero window managers; none was launched during
this diagnosis. Evidence: .debug/aquaspace/daily-validation/plist-{red,green}.log
and the pinned orphan cleanup script. A manager may launch only after this full
inventory succeeds through the ordinary unchanged controller admission path.
