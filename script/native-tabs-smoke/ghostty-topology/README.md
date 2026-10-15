# Real Ghostty native merge/detach checks

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
those exact references for activation, menu presses and menu cancellation.
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

## Build the diagnostic helpers

From the repository root (these commands do not build or install a manager):

```sh
mkdir -p .debug/aquaspace/ghostty-topology
clang -fobjc-arc -framework Foundation -framework AppKit -framework ScriptingBridge \
  script/native-tabs-smoke/ghostty-topology/ScriptDriver.m \
  -o .debug/aquaspace/ghostty-topology/script-driver
swiftc -module-cache-path .debug/aquaspace/ghostty-topology/module-cache \
  script/native-tabs-smoke/ghostty-topology/LaunchGhostty.swift \
  -o .debug/aquaspace/ghostty-topology/launch-ghostty
```

Scripting Bridge's dynamically generated `new...` commands return autoreleased
objects: the Objective-C declarations explicitly disable ARC's inferred retained
return convention. Commands with a direct object (select/activate/close) belong
to that object's proxy, rather than the application proxy.

## Explicit live run

First inspect the current existing Ghostty window ID and workspace with the
installed AquaSpace CLI. Supply that actual baseline, for example:

```sh
python3 -B script/native-tabs-smoke/ghostty-topology/live.py \
  --run-live --expect-existing-window 21423:2
```

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

These focus changes have offline validation only; their native focus capture and
restoration order need a separately authorized live run. See the [diagnosis and
validation record](../../../dev-docs/aquaspace.md#offline-ghostty-focus-restoration-repair-after-6e4ab9b2).
