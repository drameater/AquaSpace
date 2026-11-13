# AquaSpace history

This folder keeps the dated validation, repair and diagnosis records of the fork.
The records moved here on 2026-11-12. Their text is unchanged; only link targets
changed so that the links still work. Do not edit a record. Add a new dated entry
to the end of the log that fits it.

The records use "current" and "now" for the date of each entry. Code changed
after many entries. For how the fork works today, read the
[AquaSpace guide](../aquaspace.md) and the [native tabs design](../native-tabs.md).
When a record and the code disagree, the code is correct.

"Result" repeats what the record states. "Offline" means that no app was started
and only in-memory or isolated tests ran. "Partial" means that some checks passed
and the full suite did not. Evidence paths such as `.debug/aquaspace/...` are on
the owner's machine; `.debug` is not in Git.

| File | Contents |
| --- | --- |
| [aquaspace-log.md](aquaspace-log.md) | Releases, installs, updates, handoffs and live Ghostty runs. Was `dev-docs/aquaspace.md`. |
| [native-tabs-log.md](native-tabs-log.md) | Native tab validation summaries. Was `dev-docs/native-tabs.md`. |
| [native-tabs-diagnosis.md](native-tabs-diagnosis.md) | Offline diagnoses and repairs with their reproductions. Same name as before, in `dev-docs/`. |
| [integration-2026-11-12.md](integration-2026-11-12.md) | The integration of 2026-11-12 and its review fixes up to 2026-11-13: changes, offline checks and the live checks still to do. |

## Integration records

| Date | Checkpoint | Entry | Outcome as recorded | Result |
| --- | --- | --- | --- | --- |
| 2026-11-12 to 2026-11-13 | `fix/assessment-2026-11` at `14fca076` | [Integration of 2026-11-12](integration-2026-11-12.md) | New builder and updater, faster peer watch, testable controller lifecycle, window-level cache fix, six upstream commits, one offline test entry point, two review rounds, current documents. All six offline suites passed (481 Python tests). Not installed. | Passed (offline); live checks pending |

## AquaSpace log

The file starts with a summary that describes release `.20261017.4` as current.
The entries then follow mostly in the order they were written.

| Date | Checkpoint | Entry | Outcome as recorded | Result |
| --- | --- | --- | --- | --- |
| 2026-10-17 | release `.20261017.4` | [Permission identity repair (2026-10-17)](aquaspace-log.md#permission-identity-repair-2026-10-17) | Builds sign with a persistent local certificate instead of ad hoc. An update that changed the executable kept the privacy grant. 251 Python offline checks passed. | Passed |
| 2026-10-14 | release `.20261014.2` | [Earlier validation on 2026-10-14](aquaspace-log.md#earlier-validation-on-2026-10-14) | 26 headless regressions and 11 AppKit background checks passed. The old throwaway installer then started an extra old AquaSpace during a rollback. | Passed; installer defect recorded |
| not stated | `140122de` to `e7b17a02` | [Offline ownership hardening after checkpoint 140122de](aquaspace-log.md#offline-ownership-hardening-after-checkpoint-140122de) | One controller lock for every build; peers rechecked before writes; pause/resume runners retired. 10 Swift and 50 Python offline tests passed. | Passed (offline) |
| release `.20261014.5` | after `e7b17a02` | [Ownership rebuild and live validation after e7b17a02](aquaspace-log.md#ownership-rebuild-and-live-validation-after-e7b17a02) | 45 ownership and 18 updater/Ghostty live assertions passed. Extra AppKit fixture checks were inconclusive and not counted. | Passed; fixture checks inconclusive |
| release `.20261014.6` | `5cf47d53` | [Recovery checkpoint installation and live validation](aquaspace-log.md#recovery-checkpoint-installation-and-live-validation) | 87 AppKit fixture assertions passed after two setup attempts failed. | Passed |
| not stated | after `339ed06f` | [Finder and Ghostty application checks after 339ed06f](aquaspace-log.md#finder-and-ghostty-application-checks-after-339ed06f) | Finder passed 14 assertions. Ghostty merge/detach was blocked. Ghostty window 21423 moved to a test workspace. | Partial: Finder passed, Ghostty blocked, preservation failed |
| release `.20261014.7` | `f13337a2` | [Ghostty fix installation and live validation after f13337a2](aquaspace-log.md#ghostty-fix-installation-and-live-validation-after-f13337a2) | 109 foreground Ghostty assertions passed. Window 21423 stayed on workspace 2. Two earlier attempts did not complete. | Passed |
| not dated | - | [Build locally](aquaspace-log.md#build-locally) | Build instructions as they stood then. Superseded by the [guide](../aquaspace.md#build). | Instructions, not a test |
| not dated | - | [Updating AquaSpace](aquaspace-log.md#updating-aquaspace) | Update and recovery instructions as they stood then; 47 installer offline tests passed. Superseded by the [guide](../aquaspace.md#install-and-update). | Instructions, not a test |
| 2026-10-14 | staged release | [Live installer validation on 2026-10-14](aquaspace-log.md#live-installer-validation-on-2026-10-14) | 38 updater integration assertions passed against the real installation. 102 inventories never saw two managers. | Passed |
| not dated | - | [Local installation history](aquaspace-log.md#local-installation-history) | Where the original AeroSpace backup is kept. | Record, not a test |
| 2026-10-14 (run ID) | `a243c7f0` | [Automated native Ghostty merge/detach validation after a243c7f0](aquaspace-log.md#automated-native-ghostty-mergedetach-validation-after-a243c7f0) | A System Events harness used Ghostty's own merge and detach menus. 31 assertions passed. | Passed |
| not stated | `4072970d` | [Offline Ghostty harness isolation repair after 4072970d](aquaspace-log.md#offline-ghostty-harness-isolation-repair-after-4072970d) | The harness creates windows only on test workspaces and no longer trusts System Events process indices. 80 Python tests passed. | Passed (offline) |
| 2026-10-15 | `4508f0e8` | [Fixed Ghostty harness live validation after 4508f0e8](aquaspace-log.md#fixed-ghostty-harness-live-validation-after-4508f0e8--2026-10-15) | 31 live assertions passed. A stricter final focus-equality assertion failed. | Passed, except final focus |
| not stated | `6e4ab9b2` | [Offline Ghostty focus-restoration repair after 6e4ab9b2](aquaspace-log.md#offline-ghostty-focus-restoration-repair-after-6e4ab9b2) | Cleanup restores focus before it closes the last test tab. 110 Python tests passed. | Passed (offline) |
| 2026-10-15 | `721214c1` | [Ghostty desktop-focus live validation after 721214c1](aquaspace-log.md#ghostty-desktop-focus-live-validation-after-721214c1--2026-10-15) | The first attempt stopped at focus capture. The second attempt passed 32 assertions with focus restoration verified. | Passed (second attempt) |
| not stated | `4492ad71` | [Offline execution-focus safety repair after 4492ad71](aquaspace-log.md#offline-execution-focus-safety-repair-after-4492ad71) | Any loss of foreground permanently stops harness actions. 139 Python tests passed. | Passed (offline) |
| 2026-10-15 | `eff15f35` | [Foreground-guard live attempts after eff15f35](aquaspace-log.md#foreground-guard-live-attempts-after-eff15f35--2026-10-15) | Three attempts. No attempt completed the full suite. | Failed (incomplete) |
| not stated | `ec38b622` | [Offline destination and retained-source repair after ec38b622](aquaspace-log.md#offline-destination-and-retained-source-repair-after-ec38b622) | Destination workspaces are checked for foreign windows; retained AX references reach the native helper. 162 Python tests passed. | Passed (offline) |
| 2026-10-15 | `c2a304e1` | [Diagnostic rebuild and blocked preflight after c2a304e1](aquaspace-log.md#diagnostic-rebuild-and-blocked-preflight-after-c2a304e1--2026-10-15) | The helper library built. Preflight stopped because the process scan failed on a Word widget path (errno 2). | Blocked |
| 2026-10-15 | release `.20261015.1` | [Lost-executable inventory repair and ordered handoff](aquaspace-log.md#lost-executable-inventory-repair-and-ordered-handoff--2026-10-15) | The scan accepted signed Word/Excel widgets with lost paths. The stock-to-fork switch kept one manager. Hidden Zen window 28122 was not managed afterward. | Partial: switch passed, all-window check failed |
| 2026-10-15 | - | [First Ghostty attempt after the inventory repair](aquaspace-log.md#first-ghostty-attempt-after-the-inventory-repair) | Stopped when focus returned to an Obsidian window. Not a merge/detach pass. | Failed |
| 2026-10-15 | not stated | [In-process foreground cache repair and idle attempts](aquaspace-log.md#in-process-foreground-cache-repair-and-idle-attempts--2026-10-15) | Foreground reader repaired: 13 native guard cases and 173 Python tests passed. The live attempts stopped early. | Offline passed; live incomplete |
| 2026-10-15 | - | [Extended native merge/detach attempt after renewed idle confirmation](aquaspace-log.md#extended-native-mergedetach-attempt-after-renewed-idle-confirmation) | 24 assertions, including the core merges and detaches, then a Raycast focus change stopped the run (`success=false`). | Failed (incomplete) |
| 2026-10-15 | not stated | [Completed fresh-reader Ghostty validation](aquaspace-log.md#completed-fresh-reader-ghostty-validation--2026-10-15) | All 32 assertions passed. | Passed |
| not stated | `734ebf62` | [Offline stale-library admission repair after 734ebf62](aquaspace-log.md#offline-stale-library-admission-repair-after-734ebf62) | Old helper libraries are refused before any action. 175 Python tests passed. | Passed (offline) |
| 2026-10-15 | `388b3117` | [v4 Ghostty live validation after 388b3117](aquaspace-log.md#v4-ghostty-live-validation-after-388b3117--2026-10-15) | All 32 assertions passed. | Passed |
| 2026-10-15 | `c56ce874` | [Offline cleanup termination repair after c56ce874](aquaspace-log.md#offline-cleanup-termination-repair-after-c56ce874--2026-10-15) | Termination of the test Ghostty needs its pinned birth identity. 183 Python tests passed. | Passed (offline) |
| 2026-10-15 | `3fd4ba73` | [v5 Ghostty live validation after 3fd4ba73](aquaspace-log.md#v5-ghostty-live-validation-after-3fd4ba73--2026-10-15) | All 32 assertions passed. | Passed |
| 2026-10-15 | `943558e3` | [Offline background-cleanup focus repair after 943558e3](aquaspace-log.md#offline-background-cleanup-focus-repair-after-943558e3--2026-10-15) | Cleanup no longer closes tabs while the test Ghostty is in the foreground. 190 Python tests passed. | Passed (offline) |
| 2026-10-15 | `b9aadb8f` | [v6 foreground-interrupted Ghostty attempt after b9aadb8f](aquaspace-log.md#v6-foreground-interrupted-ghostty-attempt-after-b9aadb8f--2026-10-15) | Stopped after 7 assertions on a foreground change (`success=false`). | Failed |
| 2026-10-15 | `b9aadb8f` | [v6 Ghostty live validation after b9aadb8f](aquaspace-log.md#v6-ghostty-live-validation-after-b9aadb8f--2026-10-15) | All 32 assertions passed. | Passed |
| 2026-10-15 | `44578401` | [Offline foreground-target and menu ownership repair after 44578401](aquaspace-log.md#offline-foreground-target-and-menu-ownership-repair-after-44578401--2026-10-15) | Menu actions need exact recorded membership. 105 native cases and 204 Python tests passed. | Passed (offline) |
| 2026-10-15 | `34e0f9af` | [V7 Ghostty live attempt and authorized recovery after 34e0f9af](aquaspace-log.md#v7-ghostty-live-attempt-and-authorized-recovery-after-34e0f9af--2026-10-15) | 12 preliminary checks passed, then the run stopped before the first merge: native windows were unreadable. | Failed |
| 2026-10-15 | `34e0f9af` | [Offline menu-tracking membership repair after 34e0f9af](aquaspace-log.md#offline-menu-tracking-membership-repair-after-34e0f9af--2026-10-15) | The harness presses the retained menu item without opening the menu first. 211 Python tests passed. | Passed (offline) |
| 2026-10-15 | `1789ab2d` | [Fixed v7 Ghostty live validation after 1789ab2d](aquaspace-log.md#fixed-v7-ghostty-live-validation-after-1789ab2d--2026-10-15) | The first attempt stopped on a focus change. The retry passed all 32 assertions. | Passed (retry) |
| 2026-10-15 | `fa59aeb4` | [Offline restoration and final menu focus repair after fa59aeb4](aquaspace-log.md#offline-restoration-and-final-menu-focus-repair-after-fa59aeb4--2026-10-15) | Restoration needs fresh exact ownership. 223 Python tests passed. | Passed (offline) |
| 2026-10-15 | `f6c7d2fd` | [Focus-fixed v7 Ghostty live validation after f6c7d2fd](aquaspace-log.md#focus-fixed-v7-ghostty-live-validation-after-f6c7d2fd--2026-10-15) | All 32 assertions passed on the first attempt. | Passed |
| 2026-10-15 | `7a81171c` | [Offline hidden Zen handoff repair after 7a81171c](aquaspace-log.md#offline-hidden-zen-handoff-repair-after-7a81171c--2026-10-15) | Startup discovery includes hidden windows. 7 new Swift tests, 49 native-tab tests and 223 Python tests passed. | Passed (offline) |
| 2026-10-15 | `fafd604a` | [Hidden Zen rebuild and ordered handoff validation after fafd604a](aquaspace-log.md#hidden-zen-rebuild-and-ordered-handoff-validation-after-fafd604a--2026-10-15) | The hidden-Zen and sole-manager checks passed. The all-window preservation check did not pass. | Partial |
| 2026-10-15 | `b0928c61` | [Offline seven-ID handoff diagnosis after b0928c61](aquaspace-log.md#offline-seven-id-handoff-diagnosis-after-b0928c61--2026-10-15) | 8 diagnostic tests reproduce how seven windows were omitted. The live handoff result stays failed. | Diagnosis (offline) |
| 2026-10-15 | not stated | [Offline identity-safe pending-restoration repair](aquaspace-log.md#offline-identity-safe-pending-restoration-repair--2026-10-15) | Unrestored assignments are kept with kernel-verified app identity and retried. 124 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-15 | `de5541d9` | [Deferred-restoration install and native-Space handoff validation after de5541d9](aquaspace-log.md#deferred-restoration-install-and-native-space-handoff-validation-after-de5541d9--2026-10-15) | Two attempts stopped before the handoff. The third run passed. | Passed (third run) |
| 2026-10-16 | `f60c1e5f` | [Offline deferred-restoration safety repair after f60c1e5f](aquaspace-log.md#offline-deferred-restoration-safety-repair-after-f60c1e5f--2026-10-16) | Three restoration races reproduced and repaired. 138 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-16 | `96ef60c5` | [Deferred-restoration safety install and handoff after 96ef60c5](aquaspace-log.md#deferred-restoration-safety-install-and-handoff-after-96ef60c5--2026-10-16) | Two setup attempts stopped. The continuation passed against the installed build. Vesktop 30682 was excluded, not counted. | Passed (continuation) |
| 2026-10-16 | `4396d3e4` | [Offline callback-ordering repair after 4396d3e4](aquaspace-log.md#offline-callback-ordering-repair-after-4396d3e4--2026-10-16) | Delayed rules cannot override newer user input. 155 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-17 | not stated | [Offline callback command continuation repair](aquaspace-log.md#offline-callback-command-continuation-repair--2026-10-17) | Commands that already started stop when newer input supersedes them. 170 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-17 | review of `76c70eaa` | [Offline freeze, container and focus repair](aquaspace-log.md#offline-freeze-container-and-focus-repair--2026-10-17) | A freeze also revokes automatic initialization. 184 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-17 | review of `00730108` | [Offline no-op intent repair](aquaspace-log.md#offline-no-op-intent-repair--2026-10-17) | Repeated (no-op) commands also record user intent. 191 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-17 | not stated | [Offline native intent and failure reporting](aquaspace-log.md#offline-native-intent-and-failure-reporting--2026-10-17) | Lifecycle failures write `~/Library/Logs/AquaSpace/last-failure.txt`. 198 Swift and 235 Python tests passed. | Passed (offline) |
| 2026-10-17 | not stated | [Cross-window callback chain cancellation](aquaspace-log.md#cross-window-callback-chain-cancellation--2026-10-17) | A failed command's fallback cannot override newer user intent. 204 Swift and 235 Python tests passed. | Passed (offline) |
| 2026-10-17 | not stated | [AquaSpace naming pass](aquaspace-log.md#aquaspace-naming-pass--2026-10-17) | App, bundle IDs and products renamed to AquaSpace. 237 Python and 33 Swift tests passed. | Passed (offline) |
| 2026-10-17 | `47be2afa`, release `.20261017.2` | [Daily-use continuation and renaming validation](aquaspace-log.md#daily-use-continuation-and-renaming-validation--2026-10-17) | 32 Ghostty assertions, an update restart and a stock/fork round trip passed. 240 Python and 212 Swift tests passed. | Passed |
| 2026-10-25 | not stated | [Process-scan resilience (2026-10-25)](aquaspace-log.md#process-scan-resilience-2026-10-25) | The scanner skips an unrelated process with an unreadable path; failed scans are retried. The Switch to AeroSpace action was removed. | No test result recorded |

## Native tabs log

The first part of this file is the design text as it stood. The maintained
design is in [../native-tabs.md](../native-tabs.md).

| Date | Checkpoint | Entry | Outcome as recorded | Result |
| --- | --- | --- | --- | --- |
| 2026-10-14 | `dc94868d`, `5cf47d53`, `f13337a2` | [Verification](native-tabs-log.md#verification) | Offline recovery tests, model binding tests, the AppKit harness, and Finder and Ghostty live checks (42 Ghostty assertions). Gives commands for the retired `ghostty-live.py` runner. | Passed, as recorded |
| 2026-10-14 | `0.21.3-native-tabs.20261014` | [Earlier local installation on 2026-10-14](native-tabs-log.md#earlier-local-installation-on-2026-10-14) | The patched app replaced AeroSpace for a time; 10 assertions passed. That replacement was later removed. | Passed |
| 2026-10-14 to 2026-10-17 | `a243c7f0` to `4396d3e4` and later | [Real Ghostty native-menu topology validation](native-tabs-log.md#real-ghostty-native-menu-topology-validation) | One summary paragraph per checkpoint. Each paragraph links to its full record in the AquaSpace log or the diagnosis log. | See each linked record |

## Native tabs diagnosis

| Date | Checkpoint | Entry | Outcome as recorded | Result |
| --- | --- | --- | --- | --- |
| 2026-10-14 | `dc94868d` to `5cf47d53` | [Finding](native-tabs-diagnosis.md#finding) | Start of the AppKit fixture investigation. The sections up to *Offline repair and validation* belong to it: six recovery defects reproduced offline and repaired; 44 offline tests passed. The cause of the saved live failures stays unresolved. | Passed (offline) |
| 2026-10-14 | release `.20261014.6` | [Subsequent authorized installation and live checks](native-tabs-diagnosis.md#subsequent-authorized-installation-and-live-checks) | 87 live AppKit fixture assertions passed. | Passed |
| not stated | `339ed06f` to `f13337a2` | [Independent Ghostty window moved during application checks](native-tabs-diagnosis.md#independent-ghostty-window-moved-during-application-checks) | False detachment attribution after an AX reset reproduced and repaired; 120 offline tests passed. A subsection records the installed Ghostty check. | Passed (offline); live check passed |
| 2026-10-15 | `7a81171c` | [Hidden Zen window omitted during stock-to-fork startup](native-tabs-diagnosis.md#hidden-zen-window-omitted-during-stock-to-fork-startup--2026-10-15) | An on-screen-only window query omitted hidden windows. 7 new tests pass with the fix. | Passed (offline) |
| 2026-10-15 | `b0928c61` | [Seven stock-only IDs: retained discovery state and one-shot restoration](native-tabs-diagnosis.md#seven-stock-only-ids-retained-discovery-state-and-one-shot-restoration--2026-10-15) | 8 diagnostic tests reproduce the omission. The live all-window comparison stays failed. | Diagnosis (offline) |
| 2026-10-15 | not stated | [Identity-safe deferred restoration repair](native-tabs-diagnosis.md#identity-safe-deferred-restoration-repair--2026-10-15) | `PendingWindowRestoration` keeps unrestored assignments with process identity. | Passed (offline) |
| 2026-10-15 | `de5541d9` | [Deferred-restoration live rediscovery proof after de5541d9](native-tabs-diagnosis.md#deferred-restoration-live-rediscovery-proof-after-de5541d9--2026-10-15) | A controlled live run shows the native-Space omission and its repair. It does not prove the cause of the older failure. | Passed (controlled run) |
| 2026-10-16 | `f60c1e5f` | [Deferred-restoration command and root-continuity safety repair](native-tabs-diagnosis.md#deferred-restoration-command-and-root-continuity-safety-repair--2026-10-16) | Three races reproduced (six assertion failures) and repaired. | Passed (offline) |
| 2026-10-16 | not stated | [Automatic discovery callbacks versus new user intent](native-tabs-diagnosis.md#automatic-discovery-callbacks-versus-new-user-intent--2026-10-16) | An automatic floating rule could undo a pending assignment. 141 Swift and 232 Python tests passed. | Passed (offline) |
| 2026-10-16 | `4396d3e4` | [Discovery initialization and superseded callback ordering](native-tabs-diagnosis.md#discovery-initialization-and-superseded-callback-ordering--2026-10-16) | Four ordering races reproduced (10 assertions) and repaired; 70 discovery methods passed. | Passed (offline) |
| 2026-10-17 | not stated | [Awaited callback command continuations](native-tabs-diagnosis.md#awaited-callback-command-continuations--2026-10-17) | 29 failing assertions reproduced, then repaired. | Passed (offline) |
| 2026-10-17 | `76c70eaa` | [Frozen admission, container choices and focus continuations](native-tabs-diagnosis.md#frozen-admission-container-choices-and-focus-continuations--2026-10-17) | 25 failing assertions reproduced, then repaired; 184 Swift methods passed. | Passed (offline) |
| 2026-10-17 | `00730108` | [Explicit no-op assignment and focus intent](native-tabs-diagnosis.md#explicit-no-op-assignment-and-focus-intent--2026-10-17) | 37 failing assertions reproduced, then repaired; 191 Swift methods passed. | Passed (offline) |
| 2026-10-17 | not stated | [Native intent before reads and readable lifecycle failures](native-tabs-diagnosis.md#native-intent-before-reads-and-readable-lifecycle-failures--2026-10-17) | 29 failing assertions reproduced, then repaired; failure reports added. 198 Swift and 235 Python tests passed. | Passed (offline) |
| 2026-10-17 | `8ff76fe3` | [Cross-window callback chains retain cancellation](native-tabs-diagnosis.md#cross-window-callback-chains-retain-cancellation--2026-10-17) | Fallback, sequence and pipeline commands keep cancellation after a failure. | Passed (offline) |
| 2026-10-17 | `258641a8` | [Required targets and nested closes](native-tabs-diagnosis.md#required-targets-and-nested-closes--2026-10-17) | 9 failing assertions reproduced, then repaired; 115 discovery methods passed. | Passed (offline) |
| 2026-10-17 | not stated | [macOS 27 native plist inventory compatibility](native-tabs-diagnosis.md#macos-27-native-plist-inventory-compatibility--2026-10-17) | The updater converts an Info.plist that Python cannot parse through `/usr/bin/plutil`. An orphaned crash helper was removed before a launch. 240 Python tests passed. | Passed (offline) |
| 2026-10-17 | not stated | [Nested wrappers and complete bulk-close plans](native-tabs-diagnosis.md#nested-wrappers-and-complete-bulk-close-plans--2026-10-17) | 6 failing assertions reproduced, then repaired; 119 discovery methods passed. | Passed (offline) |
| 2026-10-17 | `47be2afa`, release `.20261017.2` | [Installed continuation repairs and real daily-use checks](native-tabs-diagnosis.md#installed-continuation-repairs-and-real-daily-use-checks--2026-10-17) | The v7 Ghostty harness passed all 32 assertions against the installed build. | Passed |
