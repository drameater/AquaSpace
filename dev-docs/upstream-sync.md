# Following upstream AeroSpace

This guide tells you how to check upstream AeroSpace for changes, which rule the
fork uses to take them, and what to expect at the next large merge. The facts
below were checked on 2026-11-12 against `upstream/main` at `6ed6af62`. Check
them again before you act on them.

## Where the fork stands

- The fork point is upstream commit `74a1bf17`. It is 21 commits after the tag
  `v0.21.3-Beta` (`d56e1637`). `git diff 74a1bf17..HEAD` shows the whole fork.
- Upstream publishes only pre-releases (`-Beta` tags). It has no stable channel.
- On 2026-11-12, `upstream/main` was 152 commits ahead of the fork point, and
  `v0.21.3-Beta` was still the latest upstream tag.
- The `upstream` remote has a disabled push URL (`disabled://upstream`), so a
  push to it fails.

## How to check for upstream movement

```sh
git fetch upstream --tags
gh release list -R nikitabobko/AeroSpace     # new releases and pre-releases
git log --oneline 74a1bf17..upstream/main    # commits since the fork point
git merge-tree --write-tree --name-only --no-messages HEAD upstream/main
```

The last command does a trial merge in memory. It changes no files and no
branch. Its output after the first line lists the files that conflict.

## Rule for taking upstream changes

1. Track upstream **releases**. Merge a release tag, not unreleased `main`.
2. Between releases, **cherry-pick isolated fixes** from `upstream/main` when the
   fork needs them. A fix is isolated when it applies without the rest of the
   in-progress work around it.
3. Do **not merge unreleased `upstream/main`**. It contains work in progress
   (see the next section) that is active with no feature flag.

## Upstream commits the fork already carries

These upstream commits were cherry-picked onto the fork on 2026-11-12. Each fork
commit names its source in a `(cherry picked from commit ...)` line. Do not
apply them again. At the next merge, Git sees them as changes on both sides, so
their files can conflict (see the list below); keep upstream's later version
unless the fork changed the same lines.

| Upstream | Fork | Change |
| --- | --- | --- |
| `80092f30` | `c520f452` | Fix `balance-sizes` and `resize` interop. |
| `bc6be84a` | `58a35a1f` | Show a warning icon after a config reload with warnings. |
| `0921fb81` | `3347d5e7` | Show the menu images of the Experimental UI Settings menu on macOS 27. |
| `be3e58df` | `27f4c2df` | `getWindowLevel`: do not block the main thread. |
| `0d498deb` | `e34cb3af` | `getWindowLevel`: signal the callers that wait for a fetch in progress. |
| `7a3f2e34` | `3355ae50` | `getWindowLevel`: return a cached value before the suspension point. |

To list them again:

```sh
git log --grep='cherry picked from commit' --format='%h %s' 74a1bf17..HEAD
```

## Upstream native tabs

Upstream is adding its own native tab support on `main`. The commit series is
named "N/n Native tabs WIP", from `45b2c5d9` (1/n, 2026-09-14) to `f5630de7`
(22/n, 2026-11-04). The code is active: nothing turns it off.

Its design, with the files on `upstream/main` that show it:

- **Window life status.** `WindowLifeStatus` has the values `dead`, `invisible`
  and `alive`. A window on an inactive macOS Space or a background tab is
  `invisible`, not dead. Containers have a matching `TilingContainerLifeStatus`.
  Source: `Sources/AppBundle/tree/tombstones.swift`.
- **Opaque window IDs.** `OpaqueWindowId` (commit `afbac18e`,
  `Sources/Common/model/OpaqueWindowId.swift`) is a counter that the running
  process assigns. It is new in each run. `MacApp` maps it to the macOS window
  ID. The CLI format variable `%{window-id}` now prints the opaque ID
  (`Sources/AppBundle/command/format.swift`). `%{window-id-native}` prints the
  macOS window ID (commit `f5630de7`).
- **Swap heuristic.** `nonidempotentNativeTabSwapHeuristic`
  (`Sources/AppBundle/tree/MacApp.swift`, about lines 386-398; commit `9c16cb96`,
  "20/n") runs on every refresh of each app. If exactly one window stopped being
  `alive` and exactly one window became `alive` since the previous refresh, it
  maps the old opaque ID to the new macOS window ID and schedules the old one
  for removal.

What this design does not have, as of `6ed6af62`:

- No tab-group model. Nothing reads the AX tab bar (`AXTabGroup`).
- No handling of merges or detaches.
- No protection against other one-out, one-in changes. A macOS Space switch can
  hide one window of an app and show another in the same refresh. The heuristic
  then treats the two windows as one tab.

The fork's detector is described in [native-tabs.md](native-tabs.md). It uses
AX tab-bar identity and requires unique evidence for every transition.

### Effect on the fork's CLI and sessions

After that change, `%{window-id}` no longer gives the macOS window ID, and the
value changes when the manager restarts. The fork's session transfer, the
updater and the live harnesses all pass `%{window-id}` values between processes
(see `WindowManagerSession.windowQuery` and `script/aquaspace_update.py`). A
merge must move them to a stable ID, for example `%{window-id-native}`. Stock
AeroSpace releases without that variable will not understand it, so the
handoff to stock needs a check of the peer's version.

## Plan for the merge that brings upstream native tabs

When upstream releases this work:

1. **Keep the fork's detector.** Keep `Sources/AppBundle/tree/nativeTabs.swift`,
   `Sources/AppBundle/tree/nativeTabTopology.swift` and
   `Sources/AppBundle/model/NativeTabReplacement.swift`. Replace or disable
   `nonidempotentNativeTabSwapHeuristic`; it conflicts with the detector and is
   wrong on Space switches.
2. **Port the detector onto upstream's identity model.** With opaque IDs, a tab
   switch can keep the same model window and point its opaque ID to the new
   macOS window. The fork's slot-transfer code then has no job, and can go:
   - `transferNativeTabState` (in `nativeTabs.swift`);
   - `replaceNativeTabFocusId` (in `Sources/AppBundle/focus.swift`);
   - the `replacingNativeTabWindowId` path in `MacWindow.getOrRegister`
     (`Sources/AppBundle/tree/MacWindow.swift`, called from `MacApp.swift`).
   Merges (absorptions) and detaches (inheritances) still need the fork's code.
3. **Move session IDs** to a stable window ID, as described above.
4. Run every offline suite in the [guide](aquaspace.md#tests), then the live
   harnesses with authorization.

### Files that conflict in a trial merge today

`git merge-tree` of `upstream/main` (`6ed6af62`) with the branch that merged the
2026-11-12 changes listed these conflicts:

- `Sources/AppBundle/tree/MacApp.swift`
- `Sources/AppBundle/tree/MacWindow.swift`
- `Sources/AppBundle/tree/Window.swift`
- `Sources/AppBundle/tree/TreeNode.swift`
- `Sources/AppBundle/tree/TilingContainer.swift`
- `Sources/AppBundle/layout/refresh.swift`
- `Sources/AppBundle/normalizeLayoutReason.swift`
- `Sources/AppBundle/windowLevelCache.swift`
- `Sources/AppBundle/command/impl/BalanceSizesCommand.swift` and
  `Sources/AppBundleTests/command/BalanceSizesCommandTest.swift`
- `Sources/AppBundle/command/impl/CloseAllWindowsButCurrentCommand.swift`
- `Sources/AppBundle/command/impl/DebugWindowsCommand.swift`
- `Sources/Common/util/NWConnectionEx.swift`
- `build-debug.sh`, `build-release.sh`, `script/setup.sh`
- `README.md`, `dev-docs/development.md`
- `.github/workflows/build.yml`

The app target, the Xcode project (`xcode/AeroSpace.xcodeproj`) and the
entitlements (`resources/AeroSpace.entitlements`) have upstream's names again,
so they no longer conflict.

Run the trial merge again before you plan the work; the list changes as both
sides move.

## Other upstream changes that matter at that merge

| Commit | Change | What to do in the fork |
| --- | --- | --- |
| `a84d5aef` | Config rejects an empty table for a nested option. Marked "BREAKING CHANGE". | Check the user config for empty nested tables before the merge. |
| `78346bcd` | The version and Git hash move into a Mach-O section instead of generated source files. | `script/build-aquaspace.py` writes `versionGenerated.swift` and `gitHashGenerated.swift`. Change it to the new method. |
| `8b3f8f52` | Build environment variables renamed from `AEROSPACE_*` to `SWIFTPM_*` (`Package.swift`, build scripts). | Check the fork's build scripts and the offline runners for the old names. The runtime callback variables (`AEROSPACE_*`) are a different thing. |
| `cd5b6f50` | Tests move from XCTest to Swift Testing. | The fork's own tests already use Swift Testing. Merge the test helpers with care. |
| `c707ad80` | Building on macOS 14 is no longer supported. | No action on macOS 26 or 27. |
| `587fc26a` | New workflow `.github/workflows/queue.yml`: a push to a `q/**` branch builds, then pushes to `main`. | Keep it disabled or delete it in the fork. Never let it push to the fork's `main`. |

## Upstream code that the fork removed on purpose

- `toggleReleaseServerIfDebug` (`Sources/AppBundle/server.swift` on upstream).
  It lets a debug build disable and later re-enable a running release manager.
  The fork removed it because it breaks the one-controller rule.
  `script/tests/test_aquaspace_lifecycle_source_guards.py` fails if it comes
  back in `initAppBundle.swift`, `server.swift` or `util/appBundleUtil.swift`.
- The Homebrew release and install jobs, the macOS build matrix, the nightly
  schedule and the issue/PR bots in `.github/`. The fork's only workflow runs by
  hand.
