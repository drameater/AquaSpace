# Integration of 2026-11-12

**Status: not installed, not validated live.** Nothing in this change set has
been installed in `/Applications` or run against the live window manager. All
checks below are offline. The [live checks](#live-checks-after-installation)
at the end are still to be done.

## Scope

The work is on the branch `fix/assessment-2026-11`, from `79e53f21` to
`87b2c589`, plus the documentation commits that bring this record up to date.
It came in three stages:

1. **First integration (2026-11-12).** Four workstreams, merged in `430b8b35`,
   `aad54e3d` and `1b52b88a`, and a joining pass, merged in `c8e6e9b4`.
2. **Review fixes (2026-11-12 and 2026-11-13).** Fixes for the findings of the
   first review round, merged in `c858ed14` (offline harness), `03818580`
   (updater, builder, signing) and `1c307163` (Swift app), with the direct
   commits `74740866`, `16473ad0` and `87b2c589`.
3. **Documentation (2026-11-13).** The documents describe the code at
   `87b2c589`.

`git log --oneline 79e53f21..87b2c589` lists every commit.

## What changed

### Swift app

| Change | Commits |
| --- | --- |
| The 200 ms peer watch reads only new or changed processes (cache keyed by PID and kernel exec generation). | `75102516`, `f0db697e` |
| An unreadable bundle `Info.plist` is read again after 1, 2, 4, 8 and 16 scans, then every 25 scans. | `d424cd9b` |
| 25 failed watch scans in a row write one entry to the `Lifecycle` log category (subsystem `local.aquaspace`); a working scan writes one more. | `bc9dfa7a` |
| The controller lifecycle (admission, claim, watch, launch freeze, yield, handoff, stop) moved into `WindowManagerController` with injected effects. The session, the write permission and the tab slot transfer moved to their own files. Swift behavior tests replace most source-text guards. | `9b928a29`, `c62b29bd`, `8eee6239`, `34b2febd`, `5ac72286` |
| A launch or activation notification during a handoff or a stop is ignored, so a failed scan cannot end a handoff. A second stop during the first does nothing. | `87b2c589` |
| The WindowServer window list is read once per refresh, off the main actor. | `3d0da086`, `3b4d52a6` |
| An app that this list does not show reads its own tab baseline on its AX thread. | `b72fed2d`, `bc94822a` |
| `getWindowLevel`: a miss that joined an older read waits for, or starts, a newer read; a failed read keeps the known levels. This deviates from upstream; see [upstream-sync.md](../upstream-sync.md#deviation-to-keep-getwindowlevel). | `3abce5b1`, `531fb9fe`, `cc484271` |
| Smaller fork changes in upstream files: focus admission in one call, shell command wrapping moved out of `Shell.run`, one cancellation of pending intent for an orientation change and for a no-op layout command. | `87b99b16`, `d80503c0`, `8bd8058c`, `5a2f2a44`, `0c15c5b7` |
| Six upstream commits, listed in [upstream-sync.md](../upstream-sync.md#upstream-commits-the-fork-already-carries). | `c520f452`, `58a35a1f`, `3347d5e7`, `27f4c2df`, `e34cb3af`, `3355ae50` |

### Builder

| Change | Commits |
| --- | --- |
| Builds `AquaSpace.app` from repository files only: `Info.plist` from a template, `AppIcon.icns` from the asset catalog image. Derives the version from Git. Validates the bundle and the signature. | `f105b148` |
| SwiftPM's manifest sandbox is on by default; `--disable-sandbox` is an explicit option. | `58c1c292` |
| The app target, the Xcode project and the entitlements have upstream's names again. | `bffaf3b5` |
| `build-debug.sh` and `swift-test.sh` stop at once when XCTest is missing. | `2f70ab60` |
| `--install-and-restart` passes `--allow-identity-change` and `--ready-timeout` on, waits for the installer, never stops it, and returns its exit status. | `1a0f9205`, `f4351af0` |
| Checks the icon source, every rendered size and the final `.icns`. | `d4c5d3f4` |
| Two build locks; publish by atomic exchange; `build.json` before the app. | `6c1ad519`, `16473ad0` |
| `--version` accepts only plain characters. | `d2a60e7b` |
| Tests: the updater accepts the builder's manifest and verifies a fake build. | `c10caa6a`, `2a305b3c` |

### Updater

| Change | Commits |
| --- | --- |
| `--resume`, `--confirm`, `--allow-identity-change` and `--ready-timeout`; exit statuses 0, 1, 2, 3 and 130; atomic bundle exchange; a per-user lock; only the 5 newest completed records stay. | `a5cc25f9`, `c730944f`, `0170a8f8` |
| Records of older updaters: bundles found by content, inodes written before the first file change; `--recover` checks by content that the previous bundle is in place. | `6f3c86ab` |
| The legacy lock `/tmp/.aquaspace-install-<uid>.lock` is created and held. | `29603922` |
| `--confirm` accepts an instance of the installed app that started after the new files were in place. A file-only update stays file-only in `--resume` and `--recover`. | `0fc4bd4d`, `cf53bc79` |
| A sent stop signal is recorded and reported. | `ede865ef` |
| The identity-change retry command keeps `--ready-timeout`. | `746ccc05` |
| `--set-aside` for unreadable records; records of other installations no longer break tidying. | `20b7360a` |
| Journals and archives are flushed with `F_FULLFSYNC`. | `1705abcb` |
| `--help` describes the new rules. | `fbb7d241` |

### Signing

| Change | Commits |
| --- | --- |
| Secrets on standard input, command timeouts, an interrupted setup can be done again. | `be81ac2e`, `7a997268` |
| Every `security -i` word is quoted; a path with a double quote, a backslash or a line break is refused. | `419c6d68` |
| Setup removes the `setup-*` leftovers of an interrupted setup and runs only once at a time. | `3d6917e7` |

### Tests and harnesses

| Change | Commits |
| --- | --- |
| One entry point, `script/test-offline.py`, for the Python suite and the five Swift suites. Shared runner code in `script/offline_harness.py` and shared stubs in `script/offline-shared/`. Runners find the Swift Testing macro plugin, extract declarations by name, and need no Git tag. | `474eb66a`, `7b78181a`, `b186928c`, `a83a6dec` |
| A test checks every relative Markdown link and heading anchor. | `7488826e` |
| Retired harness stubs removed; source-text guards ignore formatting. | `57c6be3c`, `1255f134` |
| The installer live harness follows the new updater. Both live harnesses refuse abbreviated forms of `--run-live`. | `e4a59749`, `74740866` |
| The unused Ghostty preparer builds the `AeroSpaceApp` product. | `0f157f1a` |

### Documents

A current guide, a native tabs design and an upstream sync guide; the dated logs
moved to this folder; this record. Commits: `05a444c4`, `49925d73`, `e521f392`,
`5f583c9b`, `b826a4c7`, `81a2c8fa`, `efe76840`, and the documentation commits
after `87b2c589`.

## What was verified offline

- **Offline suites.** `python3 script/test-offline.py --sdk
  /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk` runs the Python
  suite and the five Swift suites. See the [guide](../aquaspace.md#offline-suites).
  At `87b2c589` the suites have 444 (Python), 60 (ownership), 53 (native tabs),
  15 (hidden handoff), 121 (window discovery) and 2 (native tab model) tests.
  The Python suite passed with 444 tests on the documentation branch on
  2026-11-13. The Swift counts are the declared tests; the integrator reports
  that all suites passed.
- **Compiles.** The integrator reports a debug and a release compile of the
  app. No compiled app was installed or started.
- **Help output.** `script/install-aquaspace.py --help`,
  `script/build-aquaspace.py --help` and `script/test-offline.py --help` match
  the commands that the [guide](../aquaspace.md) describes.
- **Independent reviews, two rounds.**
  - Round one found one medium Swift defect, inherited from upstream. In the
    updater it found one high, four medium and seven low defects. The review
    fixes above address them.
  - Round two found no high or medium Swift defect. In the updater it found
    one medium and several low defects. A final fix pass for these findings
    followed; this record does not list its commits.
- **Earlier measurements.** Commit `75102516` records about 0.11 ms CPU for
  each periodic scan in a release build of the production sources, against
  about 8-10 ms before. An assessment on 2026-11-12 measured about 12% of one
  core at idle for the installed AquaSpace. Commit `58c1c292` records a release
  build with the SwiftPM sandbox on the owner's machine.

### What offline checks cannot cover

- The real `security` and `codesign` runs in signing setup and in the build.
  The tests use fakes.
- The atomic exchange on `/Applications` and on the build output volume.
- The XCTest cases of the upstream test target. They need Xcode.
- Release-configuration behavior beyond a successful compile.
- Everything in the next section.

## Live checks after installation

Make these checks after the first installation of this change set:

1. **First install flow.** The installer waits for the Accessibility
   permission. If the wait ends first, it exits with status 3, and the
   `--confirm` command it prints completes the update. See
   [First installation](../aquaspace.md#first-installation).
2. **Record pruning.** The first update with the new updater deletes the oldest
   completed update records in `/Applications`. Before it, there were 20;
   afterward, at most 6 (the 5 newest older records and the new one).
3. **Idle CPU.** Idle CPU of AquaSpace falls from about 12% of one core to well
   under 1%: `ps -o %cpu,cputime,etime -p <pid>`.
4. **Unbundled stock manager.** An unbundled stock manager binary that starts
   while AquaSpace runs still makes AquaSpace yield within about 0.2 seconds.
5. **Browser windows.** New Chrome, Zen and Firefox windows, opened quickly and
   also right after a workspace switch, tile at once on the expected workspace
   and never float as popups.
6. **App launch and tabs.** Launch an app while AquaSpace runs, then make a
   native tab in it. Its existing windows are not treated as new.
7. **Native tabs.** Tab switch, merge and detach behave as before.
8. **Lifecycle log.** The log stays silent in normal use:
   `log stream --predicate 'subsystem == "local.aquaspace" AND category == "Lifecycle"'`.
9. **Warning icon.** After a config reload with warnings, the warning icon
   appears in the menu bar.
10. **App icon.** The icon looks right in Finder and System Settings. It now
    comes from `AppIcon.icns` only.
11. **Handoff.** A handoff to stock AeroSpace and back still keeps the
    workspaces.
12. **Installer live harness.** With authorization:
    `python3 script/installer-smoke/live.py --run-live`. It was changed for the
    new updater and has not run since.
