# Integration of 2026-11-12

**Status: not installed, not validated live.** No commit in this change set has
been installed in `/Applications` or run against the live window manager. All
checks below are offline. The [live checks](#live-checks-after-installation)
at the end are still to be done.

## What changed

Four workstreams were developed in parallel and merged on the branch
`fix/assessment-2026-11`. A fifth pass made the documents match the merged code
and joined the tools. The change set is `79e53f21..b826a4c7` on that work, plus
the commit that adds this record.

| Area | Change | Commits |
| --- | --- | --- |
| Builder | Builds `AquaSpace.app` from repository files only: `Info.plist` from a template, `AppIcon.icns` from the asset catalog image, no copy of another app. Derives the version from Git. Assembles in a staging folder, validates the bundle and the signature, and keeps the previous build on failure. | `f105b148` |
| Builder | SwiftPM's manifest sandbox is on by default; `--disable-sandbox` is an explicit option. | `58c1c292` |
| Build names | The app target, the Xcode project and the entitlements have upstream's names again. The installed app keeps AquaSpace's names. | `bffaf3b5` |
| Debug and tests | `build-debug.sh` and `swift-test.sh` stop at once when XCTest is missing. | `2f70ab60` |
| Updater | `--resume`, `--confirm`, `--allow-identity-change` and `--ready-timeout`; exit statuses 0, 1, 2, 3 and 130; atomic bundle exchange; a per-user lock; only the 5 newest completed update records stay. | `a5cc25f9`, `c730944f`, `0170a8f8` |
| Signing | Secrets on standard input, command timeouts, an interrupted setup can be done again. | `be81ac2e`, `7a997268` |
| App | The 200 ms peer watch reads only new or changed processes. | `75102516` |
| App | The WindowServer window list is read once per refresh, off the main actor. | `3d0da086`, `3b4d52a6` |
| Upstream | Six upstream commits, listed in [upstream-sync.md](../upstream-sync.md#upstream-commits-the-fork-already-carries). | `c520f452`, `58a35a1f`, `3347d5e7`, `27f4c2df`, `e34cb3af`, `3355ae50` |
| Documents | A current guide, a native tabs design and an upstream sync guide; the dated logs moved to this folder. | `05a444c4`, `49925d73`, `e521f392` |
| Tests | Retired harness stubs removed; source-text guards ignore formatting. | `57c6be3c`, `1255f134` |
| Joining pass | The builder passes `--allow-identity-change` and `--ready-timeout` to the installer and hands its process to the installer. A test feeds the builder's manifest to the updater. The installer live harness follows the new updater. A test checks every relative Markdown link. The documents describe the merged code. | `7488826e`, `1a0f9205`, `c10caa6a`, `e4a59749`, `5f583c9b`, `b826a4c7` |

Merge commits: `430b8b35`, `aad54e3d`, `1b52b88a`.

## What was verified offline

- The offline Python suite passed: 321 tests on the merged branch (`1b52b88a`)
  and 333 tests after the joining pass. It runs the real builder and updater
  code with fake tools and processes. No app started.
- `script/install-aquaspace.py --help` and `script/build-aquaspace.py --help`
  list the options that the [guide](../aquaspace.md) describes.
- Commit `75102516` records a standalone measurement over the production
  sources: about 0.11 ms CPU for each periodic scan in a release build. Before
  the change, a scan took about 8-10 ms. An assessment on the same day measured
  about 12% of one core at idle for the installed AquaSpace.
- Commit `3b4d52a6` records that the offline replays were adapted to the
  asynchronous window-level cache, with a new case for two concurrent readers.
- Commit `58c1c292` records that a release build with the SwiftPM sandbox
  succeeded on the owner's machine. That build was not installed.

This record does not repeat Swift test results; each commit message states what
its author ran.

## Live checks after installation

Make these checks after the first installation of this change set:

1. The first update with the new updater deletes the oldest completed update
   records in `/Applications`. Before it, there were 20; afterward, at most 6
   (the 5 newest older records and the new one).
2. Idle CPU of AquaSpace falls from about 12% of one core to well under 1%.
3. An unbundled stock manager binary that starts while AquaSpace runs still
   makes AquaSpace yield within about 0.2 seconds.
4. New native tabs and new windows are detected as before.
5. After a config reload with warnings, the warning icon appears in the menu
   bar.
6. The app icon looks right in Finder and System Settings. It now comes from
   `AppIcon.icns` only.
7. The installer live harness passes:
   `python3 script/installer-smoke/live.py --run-live`. It was changed for the
   new updater and has not run since.
