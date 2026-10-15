# Offline investigation of the AppKit fixture failures

Investigated on 2026-10-14 against local checkpoint `dc94868d`, then repaired offline as described below. No manager, AppKit fixture, or terminal app was launched. No live AX/CG observations, command sockets, installation changes, or network access were used. The installed applications remain unchanged; the repair, tests, and validation notes are recorded together in a local checkpoint after `dc94868d`.

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

These results establish offline recovery and model-binding behavior. They do not supply the missing AX trace for the historical live failures, nor replace a future explicitly authorized live AppKit/Ghostty validation. The repair is committed locally; no installation, app launch, or push was performed.
