# Real Ghostty native merge/detach checks

Latest authorized **v4** validation on **2026-10-15** passed **32 assertions**, including
all native merge/detach cycles and stable native/model desktop focus restoration.
All nine baseline assignments were preserved; original Ghostty window 28264
stayed on workspace 3, disposable tabs/processes were removed, and 1,812 manager
inventories observed the same sole AquaSpace. See the
[completed v4 run](../../../dev-docs/aquaspace.md#v4-ghostty-live-validation-after-388b3117--2026-10-15).

Current source admission requires **`ghostty-topology-retained-source-v5`** for
guarded cleanup termination. This repair has only offline validation; the
diagnostic library has not been rebuilt. The earlier v4 live result above remains
historical. Both v3 and v4 libraries are now refused before capture or launch.

This opt-in harness reuses the **already running, sole installed AquaSpace**.
It has no installer, manager launch, stop, pause, resume, or recovery path.
Run it only when foreground interaction on dedicated test workspaces is authorized.
The operator must explicitly authorize native accessibility menu automation of Ghostty.

It launches a second instance of the installed `/Applications/Ghostty.app` using
`NSWorkspace` with `createsNewApplicationInstance`. That instance reads only its
throwaway configuration, restores no sessions, creates no initial window, and
runs `/bin/cat` rather than shell startup scripts. No terminal input is sent.
The original Ghostty PID is protected. Scripting Bridge addresses the disposable
PID directly; all subsequent tab operations re-find both the created tab ID and
terminal UUID. The native-menu driver requires the unique test title prefix for
**every** exposed window, checks the selected title, and checks the PID of every
accessibility element. `native_menu.py` binds an application accessibility element
to the disposable PID once, retains the copied window/menu elements, and uses
those exact references for menu presses and menu cancellation. The menu driver
never activates a background app.
It never enumerates System Events processes or resolves a target by display name
or list position. Process-list changes cannot redirect those retained references;
a stale element produces an error without selecting another process. Ambiguous
action replies stop the run without retrying the action. Native menu errors cancel
only the retained target menu; no global Escape key or terminal input is sent.

Before each mutation and during polling, the harness checks sole manager
ownership and every original window's workspace/fullscreen flags. Independent
background monitors sample manager identity and original windows throughout.
Before **every** window/tab creation it selects the requested dedicated test
workspace and reads back the focused workspace. A failed switch, a success reply
without actual focus, or a request to create on any other workspace refuses
creation. This happens before the first standalone window exists, avoiding
registration/tiling on the original workspace. The later explicit window moves
remain independently guarded.

It stops if the baseline changes, including a window disappearing. Do not weaken
this check to accommodate another app's activity: inspect the evidence, clean up,
and establish a fresh baseline. Test windows alone are moved between
`native-tab-test-ghostty-topology-a` and `native-tab-test-ghostty-topology-b`.

Native topology comes from Ghostty's own window/tab API, independently of the
AquaSpace workspace model. The suite exercises merging a two-tab group into a
bare destination across workspaces, selecting every incoming tab, selected-tab
detachment from three tabs, independently moving it, two-to-one detachment,
merging three bare windows, further selection/detach/move/remerge cycles, and
cleanup. The existing Ghostty native tab/terminal identities must also survive.

Cleanup closes only exact recorded tab/terminal pairs. It terminates the
pinned disposable process only after its native window list is empty, with its
kernel birth identity and executable still matching. An unrecognized remaining
tab is left alone and reported. It never relaunches a manager after an error.
After the final cleanup focus observation, manager/original-window preservation,
process identity and empty membership are checked again. Termination then uses
the in-process library, not a PID-only subprocess command. The receiver validates
the supplied birth/UID and protected PIDs, retains the matching native application,
and repeats independent kernel identity/executable and native empty-window reads
immediately before its single termination request. Missing or malformed native
window replies refuse termination. No focus write, logging or subprocess startup
intervenes between those final checks and the request.

## Build the diagnostic helpers

From the repository root (these commands do not build or install a manager):

```sh
mkdir -p .debug/aquaspace/ghostty-topology
clang -fobjc-arc -dynamiclib -framework Foundation -framework AppKit -framework ScriptingBridge \
  -framework ApplicationServices \
  script/native-tabs-smoke/ghostty-topology/ScriptDriver.m \
  -o .debug/aquaspace/ghostty-topology/script-driver.dylib
swiftc -module-cache-path .debug/aquaspace/ghostty-topology/module-cache \
  script/native-tabs-smoke/ghostty-topology/LaunchGhostty.swift \
  -o .debug/aquaspace/ghostty-topology/launch-ghostty
```

Scripting Bridge's dynamically generated `new...` commands return autoreleased
objects: the Objective-C declarations explicitly disable ARC's inferred retained
return convention. Commands with a direct object (select/close) belong
to that object's proxy, rather than the application proxy.
The script driver is now a library loaded by the runner, so retained native
window references stay in the same process through every guarded mutation.
It has no executable entrypoint or fallback to the old `script-driver` binary.

## Explicit live run

First inspect the current existing Ghostty window ID and workspace with the
installed AquaSpace CLI. Supply that actual baseline, for example:

```sh
python3 -B script/native-tabs-smoke/ghostty-topology/live.py \
  --run-live --expect-existing-window 21423:2
```

If another window occupies the default test pair, leave it untouched and select
a fresh empty pair with `--test-workspace-prefix native-tab-test-ghostty-focus-20261015`.
The prefix must start with `native-tab-test-` and contain only lowercase ASCII
letters, digits and hyphens. All original windows, including windows on the old
test pair, remain in the new preservation baseline.

Without `--run-live` and an explicit baseline, the runner exits before writing
files, compiling anything, inspecting native processes, or launching applications.
The suite currently requires exactly one pre-existing Ghostty process and an
otherwise idle desktop. Normal Automation permission for the Ghostty scripting
API and Accessibility/Device Control access for the native AX caller must already
be available. The menu driver now calls public AX APIs directly; System Events'
Automation permission alone does not supply AX access for that caller. No sudo, SIP changes, injection, manager
rebuild, installation, or remote access is needed.

Evidence is saved under `.debug/aquaspace/ghostty-topology/run-*/`; `current`
points to the latest run. `results.json` distinguishes a completed success from
an interrupted attempt. `events.jsonl` records native memberships, actual menu
invocations, workspace rows, and cleanup. Separate process/window sample files
retain the preservation observations. Do not count unreached checks as passes.


## Offline safety regressions after 4072970d

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s script/tests -p 'test_aquaspace_ghostty_topology*.py'
```

The creation tests evaluate the runner's actual creation functions with a fake
workspace service, checking the workspace at the instant of creation. They cover
initial/destination windows, anchored tabs, failed switches, incorrect focus
readback and refusal of personal workspaces. The menu tests inject process churn
at activation and both presses, including after the final PID check, so the
personal application occupies the old process-list position. They also cover
target exit, foreign-PID elements, unowned windows, selected-title changes,
disabled/ambiguous menus, and uncertain action completion without retries.
Tests of the actual ctypes adapter replace only its C functions, verifying direct
handle use, independent CF retention, stale-element errors and failed window reads.
All these tests block native-library loading, subprocesses and signals. They
execute no application, installed CLI, native process reader or real AX operation.

The repair checkpoint was validated offline without app launch or installation.
A separately authorized live run on **2026-10-15** then passed **31 assertions**
against the exact committed direct-AX driver and reordered creation path. It
preserved all 11 baseline assignments/fullscreen states and original Ghostty
window 21423 on workspace 2; 913 process inventories observed the same sole
AquaSpace. All disposable windows/processes were removed. Final desktop focus
differed from the initial snapshot, so full focus restoration is not claimed.
See the [complete live record](../../../dev-docs/aquaspace.md#fixed-ghostty-harness-live-validation-after-4508f0e8--2026-10-15)
for evidence and the final read-only focus check.


## Desktop focus cleanup (offline repair after 6e4ab9b2)

The earlier lifecycle restored only if a test workspace remained focused **after**
closing/quitting Ghostty, and did not verify actual desktop focus. That could
silently skip a normal app-close handoff or report success on an ignored/overwritten
focus request. The old saved trace cannot establish which focus event actually
occurred; it has no focus samples across teardown.

The current runner first requires a stable manager/native baseline: workspace,
window ID, foreground PID and native window title must agree unambiguously. It
retains the native application/window references and rejects an incoherent or
ambiguous baseline before launching the disposable process. During teardown it
restores the original focus **while the test still owns foreground focus**, then
closes its tabs and quits in the background. Ownership is checked again just
before requesting focus. Detected user focus changes are preserved and fail the
run; a workspace name alone never authorizes taking focus back.

Restoration and post-cleanup verification each require matching manager and
native focus for at least three samples and half a second. Post-cleanup checks
never retry focus writes. Original window disappearance, manager changes, unreadable
native focus, ignored commands and late focus drift cannot become passing runs.
An originally empty workspace is supported only with a confirmed empty native
window baseline and a confirmed empty workspace; newly appeared windows in
that workspace or application prevent restoration.

`events.jsonl` now records native/model focus around every close/quit and focus
request metadata. `results.json` contains `focus_restoration`, whose `verified`
field is mandatory for success. A failed initial capture writes
`focus-capture-failure.json`. No new manager lifecycle or installation path exists.

Run the offline focus regressions with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s script/tests -p 'test_aquaspace_ghostty_focus_restore.py'
```

The separately authorized live run on **2026-10-15** passed **32 assertions**,
including stable native/model focus restoration before and after cleanup. The
first attempt stopped before launching a disposable process because macOS's
system-wide `AXFocusedApplication` query returned `-25204`, despite Accessibility
trust and successful direct application reads. The runner now supplies the
diagnostic helper's read-only AppKit `frontmost-pid` command to `NativeAX`.
It binds that actual foreground PID to a retained AX application and rechecks
the foreground PID during capture. Native focused-window/reference validation
and all ownership/ambiguity checks remain in force; manager focus is never a
substitute for native foreground identity.

Rebuild `launch-ghostty` with the command above before using this reader.
Three additional offline adapter tests cover binding, foreground churn, invalid
PIDs, read failures and mismatched AX ownership. All **113** AquaSpace Python
offline tests pass. The live run preserved all 11 original windows and observed
the same sole manager in 875 inventories. See the [live validation record](../../../dev-docs/aquaspace.md#ghostty-desktop-focus-live-validation-after-721214c1--2026-10-15)
and [earlier diagnosis](../../../dev-docs/aquaspace.md#offline-ghostty-focus-restoration-repair-after-6e4ab9b2).

## Offline focus-safety repair after 4492ad71

The subsequent review reproduced two remaining focus-stealing gaps. Native focus
snapshots are now revalidated after window/title reads and observation logging;
restoration intent is logged before the final foreground check. The empty
workspace path checks foreground again after its last application/workspace
validation reads and before activation.

An execution-wide, read-only foreground gate now guards creation, selection,
workspace switching, focus-following moves and topology polling. It permits the
captured baseline for the initial transition, then only owned test focus. A
deliberate switch to an empty test workspace may carry the unchanged preceding
native foreground source until the next owned window is available. This exception
is tied to that requested workspace and cannot authorize a personal model window.
Any detected departure or uncertain action reply permanently revokes foreground
permission for the run, including cleanup restoration. Returning to a test window
does not clear that failure. Exact-ID background cleanup remains available.

The script driver now requires a native foreground grant for create/add/select,
retains its source AX application/window across tab enumeration, and rechecks
that source immediately before mutation. Redundant app/window activation was
removed from selection. The native-menu driver also never activates an app:
it requires the target to remain foreground immediately before each menu press.
All existing process-birth, manager, original-window, native tab/terminal identity,
prefix and ambiguity checks remain in place.

**Rebuild `script-driver` before a future authorized live run.** At this offline
checkpoint the runner required the read-only `ghostty-topology-foreground-v1`
capability and refused old helpers before session capture or disposable launch.
The subsequent v2 attempt and current v3 library are described below.
Neither helper nor either window manager was rebuilt or launched during this
offline repair; the Objective-C helper received a syntax-only compiler check.
These changes are not yet live-validated. The earlier 32-assertion run predates
this repair. See the [offline repair record](../../../dev-docs/aquaspace.md#offline-execution-focus-safety-repair-after-4492ad71).

## Initial activation follow-up and interrupted live runs after eff15f35

The live check found that creation and CLI movement can update model focus while
the original Ghostty instance remains native foreground. The runner now performs
one explicit initial activation after its first exact tab/terminal pair exists,
while the preceding native source still passes the execution gate. The helper
retains and rechecks that granted source immediately before activation. Native
activation is awaited read-only, with no action retry; any different user focus
revokes permission. Selection and menu operations do not regain activation paths.
That checkpoint used **`ghostty-topology-foreground-v2`**. It is superseded by the
retained-source v3 library described below.

All **143 offline tests** pass, including initial activation, no-retry and user
focus refusal cases plus inert rejection of invalid workspace prefixes. The
helper was rebuilt and the v2 path reached real native merges/detachments, but
the live attempts did **not** complete: a baseline Zen window disappeared in
one attempt and a new Zen window took foreground on a test workspace in another.
The guards refused restoration rather than overriding those changes. All
disposable processes were cleaned up; original Ghostty window 21423 stayed on
workspace 2, and 1,636 process samples saw the same sole AquaSpace. A later
read-only snapshot matched the original desktop focus, but this is not a passing
restoration result. See the [attempt record](../../../dev-docs/aquaspace.md#foreground-guard-live-attempts-after-eff15f35--2026-10-15).

## Offline destination and source-identity repair after ec38b622

Destination occupancy is now revalidated before workspace switches, creation,
selection/focus, focus-following moves and native-menu dispatch. Both test
workspaces must contain only exact disposable model rows that agree with the
recorded native selected tabs. Foreign windows, original baseline IDs, unknown
IDs, duplicate titles/IDs, missing rows, malformed reads and native/model
disagreement revoke foreground permission before any mutation. Matching the
Ghostty PID or title prefix alone never authorizes an occupant. Creation checks
again after a workspace switch, so a newly arrived window cannot bypass the
earlier switch check. Final source-focus validation still follows these reads.

The source window is no longer reconstructed in a child process from PID/title
metadata. `script_driver.py` calls `ScriptDriver.m` **in process**, passing the
runner's original retained AX application/window pointers through typed,
pointer-width C arguments. The native guard independently retains those exact
references and compares current focus against them immediately before mutation.
A same-PID, same-title replacement cannot become a new authorized source.
Existing target tab/terminal, process birth, manager, prefix, ambiguity and
original-window protections remain in force. Status and exact-ID cleanup use
the same library without introducing foreground activation or recovery retries.

At that checkpoint the library capability was **`ghostty-topology-retained-source-v3`**.
The stale-library admission repair below now requires v4, including when an old
v3 library happens to contain the fresh reader.
Before a future authorized live run, build `script-driver.dylib` with the updated
command above. Old executables or mismatched libraries are refused; no executable
fallback exists. The library is loaded into the diagnostic runner, not either
window manager or Ghostty. This repair did not build, load or run it.

All **162 AquaSpace offline tests** pass, including 19 added methods covering
destination arrivals, membership/ambiguity refusal, exact retained references,
same-title replacement, windowless sources, pointer width, failed confirmation,
stale-library rejection and native-response cleanup without retries. The native
source passed a syntax-only compiler check. No apps, diagnostic helpers or
diagnostic native libraries were launched/loaded; no live test or installation
was performed. Live validation remains pending. See the [repair record](../../../dev-docs/aquaspace.md#offline-destination-and-retained-source-repair-after-ec38b622).

## Lost-executable process inventory and first v3 live attempt

The controller and Python inventory readers can now authenticate the specific
Word/Excel widgets whose original executables have been replaced while they
remain running. This is a kernel signing proof, not an exception for process
names: exact vendor/identifier, Developer ID validation, hardened runtime,
enforcement/library validation, stable code hash and before/after kernel process
and exec generation must all agree. Every signing read is bound to that exec
generation. Only an ENOENT path failure can use the fallback; unknown or
unverifiable processes continue to block. No additional privilege is required.

The command wrapper also preserves empty tab-separated fields and whitespace
in window titles, removing only line endings. All **173 Python tests** and
**22 Swift ownership tests** pass, including the new inventory and parsing
regressions. See the [diagnosis and handoff record](../../../dev-docs/aquaspace.md#lost-executable-inventory-repair-and-ordered-handoff--2026-10-15).

The first v3 live attempt after that repair reached initial creation/activation,
then stopped when native/model focus returned to the original desktop window.
The execution gate refused further foreground work and cleanup closed only the
disposable process. This attempt is not a merge/detach or restoration pass.
Its 124 process inventories saw one AquaSpace; original Ghostty window 28264
stayed on workspace 3 with unchanged native identities. A complete run requires
an idle desktop. Always inspect the current window/workspace baseline rather
than reusing IDs from earlier examples or validation records.

## Fresh foreground reader in the in-process library

An idle live attempt exposed stale AppKit foreground state inside the Python
host. The native guard now checks the retained application's AX foreground flag
and the system's fresh foreground PID directly, while retaining all original
application/window/root/title/PID checks. Neither an unreadable live query nor
matching cached state authorizes a mutation. The helper does not run an AppKit
event loop or activate a window to refresh its observation.

Run the actual native guard against offline API stubs with:

```sh
python3 -B script/test-ghostty-foreground-offline.py
```

All 13 cases pass, including stale-cache false rejection and false acceptance,
late focus changes, same-title root replacement, unreadable queries and
windowless-source handling. No real desktop APIs are executed by those tests.
All 173 Python tests remain passing. Rebuild `script-driver.dylib` with the command
above to include the fresh reader; no manager rebuild is needed for this change.

The fresh-reader live attempt passed initial tab grouping, movement and selection,
then stopped on a real native/model foreground change to Raycast. Disposable
cleanup, sole-manager observations and existing Ghostty preservation passed their
separate checks, but complete merge/detach and guarded focus restoration remain
pending. See the [attempt and regression record](../../../dev-docs/aquaspace.md#in-process-foreground-cache-repair-and-idle-attempts--2026-10-15).

A later uninterrupted portion of the next run did pass the core real native
merge/detach cases, including a cross-workspace destination merge, both three-tab
and two-to-one detachments, independent movement, merging three bare windows and
all subsequent selections on destination B. Another real Raycast foreground
change stopped it before the final repeated cycle/restoration. That interrupted
run did not establish restoration success; see the
[extended attempt](../../../dev-docs/aquaspace.md#extended-native-mergedetach-attempt-after-renewed-idle-confirmation).
Switching back to this chat during the test also ends foreground authorization;
leave the desktop untouched until completion is reported.

## Offline stale-library admission repair after 734ebf62

The fresh native reader initially retained the older library's v3 capability.
That allowed an unrecompiled library containing the stale AppKit reader to pass
admission. Both the native export and Python adapter now require
**`ghostty-topology-retained-source-v4`**. A v3 library is refused during adapter
initialization, before session capture, disposable launch or any driver operation.
There is no compatibility fallback, retry or automatic rebuild.

Two new offline regressions reject an injected old-v3 library without invoking
its run/free functions and require the source's exported capability to match the
adapter's v4 requirement. Existing pointer, exact-root, ownership and focus tests
now use a v4 fake library. All **175 Python tests pass**. Native loading,
subprocesses and signals are blocked in the adapter tests. No diagnostic library
was rebuilt or loaded, no app/helper was launched, and no live test or installation
was performed for this repair. The earlier 32-assertion result remains a record
of its exact v3 binary; it is not a new v4 live-validation result.

## Offline cleanup termination repair after c56ce874

Review reproduced two ways cleanup could reach termination after its earlier
checks: an unrecorded tab could appear during the final focus observation, or
the recorded PID could refer to a different process. The runner now revalidates
after that observation and passes the expected identity into the native receiver.
`LaunchGhostty.swift` no longer exposes a `terminate` command. Receiver-side
checks live in `ScriptDriver.m`; no executable fallback or action retry exists.
The required library capability is now **`ghostty-topology-retained-source-v5`**,
so an unrecompiled v4 library cannot admit this run. All existing focus, exact tab/
terminal, destination, original-window and manager checks remain in place.

All **183 Python offline tests** pass. The two new reproductions first failed
against the old runner and now reject termination. Further cases verify manager
uncertainty, identity proof forwarding, malformed/protected identities, v4
rejection, receiver errors without retry and removal of the PID-only entrypoint.
The actual native termination guard also passes **26 cases** against fake process,
Scripting Bridge and native application APIs:

```sh
python3 -B script/test-ghostty-termination-offline.py
```

That command builds/runs only an isolated test binary using Foundation objects;
it executes no real process, Ghostty, focus or termination APIs. The native source
also passes syntax-only checking. No diagnostic helper/library was built, loaded
or launched, no app or installed CLI was invoked, and no live test, installation
or push was performed. The new cleanup path awaits separately authorized live
validation; the earlier v4 result is not a validation of v5.
