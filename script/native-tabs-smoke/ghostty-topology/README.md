# Real Ghostty native merge/detach checks

This opt-in harness reuses the **already running, sole installed AquaSpace**.
It has no installer, manager launch, stop, pause, resume, or recovery path.
Run it only when foreground interaction on dedicated test workspaces is authorized.
The operator must explicitly authorize AppleScript/System Events access to Ghostty.

It launches a second instance of the installed `/Applications/Ghostty.app` using
`NSWorkspace` with `createsNewApplicationInstance`. That instance reads only its
throwaway configuration, restores no sessions, creates no initial window, and
runs `/bin/cat` rather than shell startup scripts. No terminal input is sent.
The original Ghostty PID is protected. Scripting Bridge addresses the disposable
PID directly; all subsequent tab operations re-find both the created tab ID and
terminal UUID. The native-menu driver requires the unique test title prefix for
**every** exposed window, checks the selected title, and rechecks the target PID
before menu clicks. System Events uses a primitive process-ID snapshot and an
index because resolving two apps by their identical display name is ambiguous.
A changed process index fails closed; it never retries a possibly executed action.

Before each mutation and during polling, the harness checks sole manager
ownership and every original window's workspace/fullscreen flags. Independent
background monitors sample manager identity and original windows throughout.
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
otherwise idle desktop. Normal Automation and Accessibility/Device Control
permission must already be available. No sudo, SIP changes, injection, manager
rebuild, installation, or remote access is needed.

Evidence is saved under `.debug/aquaspace/ghostty-topology/run-*/`; `current`
points to the latest run. `results.json` distinguishes a completed success from
an interrupted attempt. `events.jsonl` records native memberships, actual menu
invocations, workspace rows, and cleanup. Separate process/window sample files
retain the preservation observations. Do not count unreached checks as passes.
