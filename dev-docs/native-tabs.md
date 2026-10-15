# Native macOS tabs

AppKit uses a different NSWindow/AX window ID for each native tab. Only the selected tab appears in the application's AXWindows list; the previous AX window remains valid. Keeping every previously seen ID in the tiling tree gives one physical window multiple slots and workspace owners.

The selected window's direct AXTabGroup and its AXTabButton elements retain their identities when AppKit moves the titlebar between tabs. AeroSpace follows that identity before adopting native focus, then transfers the predecessor's current binding, weight, floating size, fullscreen state, and cached layout. The predecessor leaves the managed tree without closed-window restoration or another on-window-detected callback.

The first tab has no predecessor tab bar. For this boundary, matching requires a recently created WindowServer ID, a native tab bar, the same frame, and a unique match in both directions. Public CG window metadata includes inactive native Spaces and background tabs, so an existing window becoming AX-visible is not evidence of creation. A brief creation allowance covers AppKit exposing a bare window before attaching its bar.

Closing one of two tabs removes the bar. This boundary requires a closed predecessor, its previously observed two-tab bar, an existing successor ID, and uniquely matching geometry. Unknown or ambiguous transitions retain their existing windows. Native Space changes invalidate the AX observation history. Settled independent bare-window evidence survives that reset, so another group's shrinking count or coincident geometry cannot claim an unrelated existing window. Confirmed joins consume that evidence; shared native-bar identity and the existing first-tab creation allowance remain authoritative.

The AX thread queues replacements until the main actor acknowledges transferring the slot. Cancellation cannot consume a handoff, and rapid selections can form chains or cycles. The main actor collapses these onto the exposed tab rather than replaying inactive intermediate tabs, including when a previous transfer committed before its acknowledgement was canceled. The binding is read when transferred because container normalization can change it between sessions.

## Verification

An offline investigation after checkpoint `dc94868d` reproduced recovery defects when tab counts, geometry, and AXWindows exposure settle across separate scans. The working source now retains a bounded pre-transition baseline and passes all six reproductions, additional ambiguity/cancellation checks, and model binding tests. A subsequent repair protects independent windows from false detachment attribution after AX observation resets. See [native-tabs-diagnosis.md](native-tabs-diagnosis.md) for the diagnoses, repairs, evidence, and limits on attributing the earlier live failures. `python3 script/test-native-tabs-offline.py` runs 49 tests using memory-only AX substitutes, including 16 existing matcher/queue regressions. No app is launched.

Recovered detachment can find a window already registered on a staging workspace. The registry now applies the inherited workspace to that existing model, preserving floating/fullscreen state and avoiding another new-window callback. Fresh registrations retain the existing callback path. Two tests run this binding helper against the real in-memory tree using `TestWindow`/`TestApp`:

```sh
python3 script/test-native-tab-model-offline.py \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  --scratch-path /private/tmp/aerospace-native-tab-validation-build
```

This builds the AppBundle library and a Swift Testing target, with no app entrypoint or window creation. Dependencies must already be cached; resolution and updates are disabled. The explicit scratch path above reuses this machine's existing dependency/build cache. The initial recovery release `0.21.3-aquaspace.20261014.6` from commit `5cf47d53` passed 87 live assertions with background and foreground AppKit fixtures; see [that installation record](aquaspace.md#recovery-checkpoint-installation-and-live-validation). The independent-window repair is now installed as `0.21.3-aquaspace.20261014.7` from `f13337a2`: 109 foreground Ghostty assertions passed, including two independently observed native Space changes and per-operation/background checks that the original Ghostty window remained on workspace 2. See [the latest live record](aquaspace.md#ghostty-fix-installation-and-live-validation-after-f13337a2) for ownership monitoring, setup limitations, cleanup, and subsequent desktop-state changes.

`Sources/AppBundleTests/tree/NativeTabReplacementTest.swift` exercises classification and actual tree/workspace transfers. Run it with the repository's normal Swift/Xcode test environment:

```sh
swift test --filter NativeTabReplacementTest
```

The standalone AppKit integration harness reads only its own AX tree. It requires no Accessibility/Device Control grant and uses the production AX state machine and tree-transfer helper. It creates two native tab groups, tests repeated simultaneous selections, canceled reads, unobservable snapshots, closing selected tabs, and exposing a bare window before attaching its first tab bar:

```sh
swift run --package-path script/native-tabs-smoke NativeTabSmoke
```

Use a toolchain/SDK that builds AeroSpace. On the development machine used for this investigation, the macOS 27 Command Line Tools SDK lacks the SwiftUI State macro plugin, and XCTest is absent. The application and smoke test compile with the installed macOS 26.5 SDK:

```sh
swift run --disable-sandbox \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  --package-path script/native-tabs-smoke NativeTabSmoke
```

This harness verifies real native AppKit/AX transitions plus the production matching and tree-transfer code. The separate live tests below exercise the running AeroSpace application's observer scheduling and app-specific titlebars. The merge/detach mode additionally covers merging existing groups, detaching selected and inactive tabs, and retrying committed transitions before acknowledgement. Tab overview, native Space transitions, and long sessions remain outside the automated coverage.

Live tests must keep the test windows' native macOS Space active. Background AX tab-button presses can change an application's focused-window attribute while its windows remain absent from AXWindows on an inactive Space, so those presses alone cannot validate normal foreground tab switching.

On macOS 27.0.1, a live Finder test verified first-tab creation, selecting both tabs after moving the group to another AeroSpace workspace, and closing the selected tab while the final sibling retained its workspace. A test-only build excluded all pre-existing WindowServer IDs and all applications except Finder/Ghostty to preserve the user's windows. The final implementation is covered by 20 regression tests and the AppKit harness's 50 selections, including committing a transfer without acknowledgement, switching back, and retrying the queued cycle.

Ghostty 1.3.1 also passed 42 automated live assertions against the final implementation. These cover first-tab creation, ten repeated selections after a workspace move, adding and closing a third tab, closing the selected sibling from two tabs to one, recreating the native bar, and twenty selections alternating between two independently owned groups. Cleanup verified that every test window disappeared, the user's personal terminal identities were unchanged, and existing window-to-workspace assignments were preserved. The installed AeroSpace was restored afterward.

The live harness uses Ghostty's [documented scripting API](https://ghostty.org/docs/features/applescript), not UI keystrokes. Fresh terminals execute an idle fixture through `/bin/sh` and `/bin/cat`; it sends no terminal input and reads no terminal contents. Every selection/closure verifies both a created tab ID and its terminal UUID. A test-only registration gate excludes all pre-existing WindowServer IDs, requires a unique test-title prefix, and admits only Ghostty. The harness verifies the scoped binary's checksum before launching it. These gates are applied to a source copy, leaving the production patch unchanged.

To reproduce, build the scoped app and run the harness on an active macOS desktop. It temporarily switches to dedicated AeroSpace test workspaces and restores the original workspace. The debug app needs AeroSpace's normal Accessibility/Device Control permission; Ghostty scripting uses the platform's normal Automation permission if not already granted.

```sh
python3 script/native-tabs-smoke/prepare-ghostty-live.py \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
python3 script/native-tabs-smoke/ghostty-live.py \
  --debug-app "$PWD/.debug/native-tabs/ghostty-isolated/AeroSpace Native Tabs.app"
```

The harness emits an assertions log and passive server events into its reported temporary output directory. The validated run is also saved locally under `.debug/native-tabs/ghostty-live/`.

Merging groups retains the destination group's workspace and retires the incoming group's old slot. An established native bar identifies the destination even when an incoming tab becomes selected. Incoming bar identities are destroyed by AppKit, so reconciliation also requires exact tab-count accounting, a still-valid incoming root, and matching fresh geometry. AppKit can resize an incoming tab while retaining AeroSpace's offscreen origin; this case additionally requires a destroyed incoming bar or a bare incoming window, matching dimensions, and no concurrent new WindowServer IDs. Ambiguous transitions are left alone.

Detaching a tab creates a separate slot on the group's current workspace. The surviving bar, an observed count decrease, and remembered membership identify the owner. The two-to-one boundary uses the remaining window's unchanged frame. Detached windows can then move independently; merging them back replaces obsolete membership.

A separate live run of the full observer/layout pipeline passed 30 assertions using `AppKitFixture.swift`. These include merging across AeroSpace workspaces into a bare destination, selecting each incoming tab, selected-tab detachment, independently moving the detached window, merging with the incoming tab selected, inactive-tab detachment after a merge, two-to-one detachment, bare-window merging, and repeated selections after merge/split cycles. Cleanup confirmed original terminal identities and workspace assignments were unchanged. This fixture invokes public NSWindow tab operations only on its own windows; it does not claim app-specific Ghostty merge/detach API coverage.

```sh
swiftc -sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  script/native-tabs-smoke/AppKitFixture.swift -o /path/to/Fixture.app/Contents/MacOS/Fixture
# Bundle ID: org.aerospace.NativeTabFixture; CFBundleExecutable: Fixture
python3 script/native-tabs-smoke/prepare-ghostty-live.py \
  --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  --application-id org.aerospace.NativeTabFixture \
  --output .debug/native-tabs/topology-isolated
python3 script/native-tabs-smoke/ghostty-live.py \
  --debug-app "$PWD/.debug/native-tabs/topology-isolated/AeroSpace Native Tabs.app" \
  --fixture-app "/path/to/Fixture.app" --topology-only
```

The implementation uses public AX/CG APIs and AeroSpace's existing AX-to-window-ID bridge. It adds no scripting addition, injection, root requirement, SIP change, or screen-recording permission.

## Earlier local installation on 2026-10-14

Before the fork was separated, the optimized arm64 app was installed at `/Applications/AeroSpace.app`, with its matching CLI at the existing Homebrew symlink target. Both report `0.21.3-native-tabs.20261014`. The original app and CLI are backed up under `.debug/native-tabs/install-backup/`. All 11 pre-existing window IDs, their workspace assignments, floating/fullscreen settings, and focus were restored. The existing user configuration passed dry-run validation. A further 10 assertions against the installed build verified live Ghostty creation, moving the group, six tab switches, final-sibling closure, and cleanup; original terminal identities and workspace assignments were unchanged. Test evidence is saved under `.debug/native-tabs/`. That temporary replacement has since been removed: original AeroSpace is restored and the patch is installed separately as AquaSpace. Current installation and handoff validation are documented in [aquaspace.md](aquaspace.md).


## Real Ghostty native-menu topology validation

The previously blocked Ghostty merge/detach checks now have a completed,
explicitly authorized isolated System Events run. **31 live assertions** passed
against installed AquaSpace `.7` and real Ghostty 1.3.1 native menus: cross-workspace
merging, selecting every merged member, detaching from three tabs and from two
tabs to bare windows, independent movement, bare-window merging and repeated
remerge cycles. Ghostty's own native membership API provides the independent
oracle. Original window 21423 stayed floating on workspace 2 with unchanged tab
and terminal identities. All 12 baseline windows retained workspace/fullscreen
state, all disposable windows/processes were removed, and 1,046 native inventories
observed the same sole AquaSpace process. No manager lifecycle action or rebuild
was needed. See the [full record](aquaspace.md#automated-native-ghostty-mergedetach-validation-after-a243c7f0)
and [opt-in harness](../script/native-tabs-smoke/ghostty-topology/README.md), including
setup failures, cleanup, evidence and the four offline entrypoint guard tests.


The subsequent offline harness review found that initial creation could occur on
the original workspace and that System Events process indices were not stable
mutation targets. Both are repaired with verified test-workspace focus before
creation and retained PID-bound AX menu elements. New offline regressions cover
the creation ordering and process-churn/error paths. The revised harness has not
been run live; see the [offline repair record](aquaspace.md#offline-ghostty-harness-isolation-repair-after-4072970d).
