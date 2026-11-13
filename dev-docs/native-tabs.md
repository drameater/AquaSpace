# Native macOS tabs: design and limits

This document describes how AquaSpace keeps a native macOS tab group in one
workspace slot. It describes the code as it is now. The dated test records are
in [history/](history/README.md).

A *native tab group* is a set of windows that AppKit shows as tabs in one
titlebar, for example Finder or Ghostty tabs. It is not a tab strip that an app
draws inside its own content, such as browser tabs.

## The problem

AppKit gives each native tab its own window. Each tab has its own window ID
(the macOS `CGWindowID`, also used as the Accessibility (AX) window ID). Only
the selected tab is in the app's AX window list (`AXWindows`). The windows of
the other tabs stay valid but hidden.

Stock AeroSpace treats each window ID as a separate window. When the user
selects another tab, AeroSpace sees a new window and lays it out as a new tile.
The old tab keeps its own tile and workspace. One physical window then has
several slots and several workspace owners.

## The approach

AquaSpace keeps one model window (one *slot*) for each tab group. When the
selected tab changes, the slot moves to the new window ID. The slot keeps its
workspace, its place in the tiling tree, its weight, its floating size, its
fullscreen state and its cached layout. The old window ID leaves the model
without closed-window handling and without another `on-window-detected`
callback.

The detector uses AX identity, not window titles or app-specific APIs:

- The tab bar is an `AXTabGroup` that is a direct child of the selected window
  and contains `AXTabButton` elements. When AppKit moves the titlebar to another
  tab, the tab group keeps its AX identity. A shared tab group is the main
  evidence that two window IDs belong to one group.
- Tab bars that are deeper in the AX tree (content tab strips) are ignored.

## Transitions

| Transition | Evidence that AquaSpace requires | Result |
| --- | --- | --- |
| Select another tab | The new window has the tab bar of the old one. Matching must work in both directions, so two groups that switch at the same time cannot take each other's slot. | The slot moves to the new window ID. |
| First tab is created | The old window had no tab bar. The new window ID is recent, has a tab bar with two or more tabs, the same frame (within 1 point), and the match is unique. | The slot moves to the window that now shows the tab bar. |
| Last sibling closes (two tabs to one) | The old window closed, it had a two-tab bar, the survivor already existed, and the frames match. | The slot moves to the survivor. |
| Merge two groups | The destination group has an established tab bar. The tab count grows by exactly the number of incoming tabs. The incoming root is still valid and its fresh frame matches. | The incoming group's slot is retired. The group keeps the destination's workspace. |
| Detach a tab | The remaining tab bar, a count decrease, and remembered membership identify the old group. For two tabs to one, the remaining window keeps the group's frame. | The detached window gets a new slot on the group's current workspace. It can then move on its own. |
| Anything else | No unique match. | Nothing changes. Ambiguous transitions keep their existing windows. |

AppKit can expose a new window before it attaches the tab bar. A short creation
allowance covers this. Public window metadata also lists windows on inactive
macOS Spaces and background tabs, so a window that becomes AX-visible is not
taken as proof of a new window.

For a merge, AppKit destroys the incoming group's tab bar and can resize an
incoming tab while it keeps an offscreen origin. In that case AquaSpace also
requires a destroyed incoming bar or a bare incoming window, the same size, and
no other new window IDs in the same refresh.

## State and threading

- Each app has an `AxNativeTabState` on its AX thread
  ([nativeTabs.swift](../Sources/AppBundle/tree/nativeTabs.swift)). It keeps the
  last observations, a short pre-transition baseline (AX lists, tab bars and
  frames can settle in different reads), pending replacements, and the known
  independent windows.
- When an app registers, its window IDs in the WindowServer list are the
  *known windows*: none of them counts as a new window. A refresh reads that
  list once, off the main actor. If the list of that refresh does not have the
  app (it launched after the read, or had no window then), the app reads its
  own list on its AX thread.
- [nativeTabTopology.swift](../Sources/AppBundle/tree/nativeTabTopology.swift)
  computes replacements, absorptions (merges), inheritances (detaches) and
  continuations from those observations.
- [NativeTabReplacement.swift](../Sources/AppBundle/model/NativeTabReplacement.swift)
  holds the snapshot types and the single-switch matcher.
- The AX thread keeps a replacement queued until the main actor confirms that it
  moved the slot. Cancellation cannot drop a switch. Fast selections can form a
  chain or a cycle; the main actor collapses them onto the tab that is exposed
  now (`nativeTabTransferPlan`), not onto an inactive tab in between.
- The main actor moves the slot in `MacWindow.getOrRegister` with
  `transferNativeTabState`, `absorbNativeTabState` and
  `inheritNativeTabWorkspace`
  ([nativeTabSlotTransfer.swift](../Sources/AppBundle/tree/nativeTabSlotTransfer.swift)).
  The first two also move focus with `replaceNativeTabFocusId`.
  The binding is read at the moment of transfer, because tree normalization can
  change it between refreshes.
- A macOS Space change (`activeSpaceDidChangeNotification`) clears the AX
  observations of every app. Known independent windows survive this reset. So
  after a Space change, another group's shrinking tab count or equal geometry
  cannot claim an unrelated existing window. A confirmed join removes the
  independent mark.
- A confirmed tab transfer cancels any pending session restoration for the
  windows involved
  ([WindowManagerHandoff.cancelPendingNativeTabAssignments](../Sources/AppBundle/WindowManagerHandoff.swift)).
  An old per-tab assignment from stock AeroSpace cannot then move the group.

The feature uses public AX and CoreGraphics APIs and the existing AX-to-window-ID
bridge. It adds no scripting addition, code injection, root requirement, SIP
change or screen-recording permission.

## Tests

| Test | What it covers | Needs |
| --- | --- | --- |
| `python3 script/test-native-tabs-offline.py` | The production state machine and matchers, replayed with in-memory AX elements. Includes the recovery, ambiguity and Space-reset cases, and the known-window baseline. | Swift toolchain. No app. |
| `python3 script/test-native-tab-model-offline.py --sdk <SDK>` | Recovered workspace bindings against the real in-memory tree (`TestWindow`, `TestApp`). | Swift toolchain, an SDK that builds the app, cached dependencies. No app. |
| [NativeTabReplacementTest.swift](../Sources/AppBundleTests/tree/NativeTabReplacementTest.swift), [NativeTabWorkspaceRecoveryTest.swift](../Sources/AppBundleTests/tree/NativeTabWorkspaceRecoveryTest.swift) | Classification and real tree/workspace transfers. | The full Swift test target (`./swift-test.sh`). Upstream's XCTest cases need Xcode. |
| `swift run --package-path script/native-tabs-smoke NativeTabSmoke` | Real AppKit tab groups in a test process that reads only its own AX tree. | An active desktop. No Accessibility grant. |
| [ghostty-topology](../script/native-tabs-smoke/ghostty-topology/README.md) `live.py --run-live` | Real Ghostty merges and detaches through the installed AquaSpace. | Explicit authorization, an idle desktop, dedicated test workspaces. |

`python3 script/test-offline.py` runs both offline suites with the others. The
[AquaSpace guide](aquaspace.md#tests) lists every suite and its safety rules.

## Known limits

- Only native titlebar tab groups are handled. Tabs that an app draws in its
  content are separate concerns.
- When a transition is ambiguous, AquaSpace changes nothing. The windows can then
  keep separate slots until a later transition is unambiguous.
- No automated test covers the macOS tab overview, native Space transitions in
  general, or long sessions. The Ghostty live harness covered two native Space
  changes in one run; see the [history](history/README.md).
- The offline replays model AppKit timing that was seen in live runs. They do not
  prove every possible AppKit or app-specific timing.
- Stock AeroSpace knows nothing about tab groups. It keeps a separate slot for
  each tab window that it has seen. A session that comes from stock can therefore
  hold per-tab rows. See the
  [handoff limits](aquaspace.md#known-limits).
- Upstream AeroSpace is building its own native tab support. See
  [upstream-sync.md](upstream-sync.md#upstream-native-tabs) for how the two
  designs differ and the plan for a merge.
