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
app/helper launches or live checks. The installed AquaSpace still has the old
layer query until a separately authorized rebuild/install; live verification of
hidden-window handoff remains pending.
