#!/usr/bin/env python3
"""Live Ghostty regression using its documented AppleScript API and a scoped debug app.

The debug app MUST have the test-only prefix/baseline registration gates described
in dev-docs/native-tabs.md. This never types into a terminal. All terminals created
here execute the included idle fixture, and every tab operation checks its UUID.
"""
import argparse
import hashlib
import json
import os
import select as readiness
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


def run(args, *, check=True):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=25)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-app", type=Path, required=True)
    parser.add_argument("--fixture-app", type=Path, help="Use a self-owned AppKit app for merge/detach topology tests")
    parser.add_argument("--topology-only", action="store_true", help="Skip repeated selections while investigating fixture topology")
    parser.add_argument("--debug-cli", type=Path, default=root / ".debug/native-tabs/aerospace")
    parser.add_argument("--release-cli", type=Path, default=Path("/opt/homebrew/bin/aerospace"))
    args = parser.parse_args()
    executable = args.debug_app / "Contents/MacOS/AeroSpaceApp"
    manifest = json.loads((args.debug_app.parent / "scope.json").read_text())
    if manifest.get("kind") != "ghostty-prefix-and-baseline-exclusion" or manifest.get("sha256") != hashlib.sha256(executable.read_bytes()).hexdigest():
        raise RuntimeError("Refusing to launch an app without matching test-scope attestation")
    expected_app = "org.aerospace.NativeTabFixture" if args.fixture_app else "com.mitchellh.ghostty"
    if manifest.get("application_id", "com.mitchellh.ghostty") != expected_app:
        raise RuntimeError("Test backend does not match the scoped application")
    out = Path(tempfile.mkdtemp(prefix="aerospace-ghostty-live-", dir="/private/tmp"))
    prefix = f"AeroSpace-NativeTabs-{uuid.uuid4().hex[:10]}-"
    ws_a, ws_b, ws_moved = ("native-tab-test-ghostty-" + x for x in ("a", "b", "moved"))
    events = []
    created = {}
    labels = {}
    known_test_window_ids = {}
    process = subscriber = None
    fixture = None
    touched_manager = False
    passed = False
    restore_errors = []
    log = (out / "assertions.jsonl").open("w")

    def record(kind, **data):
        item = {"kind": kind, **data}
        events.append(item)
        line = json.dumps(item, sort_keys=True)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    compiled = out / "driver.scpt"
    run(["osacompile", "-o", compiled, Path(__file__).with_name("ghostty-driver.applescript")])

    def personal_ghostty(operation, *values):
        return run(["osascript", compiled, operation, *values]).stdout.strip()

    def fixture_command(operation, **values):
        fixture.stdin.write(json.dumps({"operation": operation, **values}) + "\n")
        fixture.stdin.flush()
        if not readiness.select([fixture.stdout], [], [], 15)[0]:
            raise RuntimeError("AppKit fixture timed out")
        result = json.loads(fixture.stdout.readline())
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def ghostty(operation, *values):
        if not args.fixture_app:
            return personal_ghostty(operation, *values)
        if operation in ("create", "add"):
            result = fixture_command(operation, title=values[0], **({"id": values[2]} if operation == "add" else {}))
            return result["tab"] + "\t" + result["terminal"]
        result = fixture_command(operation, id=values[0])
        if operation == "status":
            return result["selected"] + "\t" + str(result["count"])
        return result.get("selected", "closed")

    def debug(*values, check=True):
        return run([args.debug_cli, *values], check=check)

    def snapshot():
        text = debug("list-windows", "--all", "--format", "%{window-id}\t%{workspace}\t%{window-title}").stdout
        rows = []
        for line in text.splitlines():
            wid, workspace, title = line.split("\t", 2)
            window_id = int(wid)
            if title.startswith(prefix):
                label = title.removeprefix(prefix)
                known_test_window_ids[window_id] = label
            elif not title and window_id in known_test_window_ids:
                # Ghostty can clear a closing NSWindow's title before AX destroys
                # its root. Keep waiting for GC rather than misclassifying it as
                # a personal terminal. Never admit an unknown ID this way.
                label = known_test_window_ids[window_id]
            else:
                raise AssertionError(f"The isolated manager registered an unknown window ID {window_id}")
            rows.append({"id": window_id, "workspace": workspace, "title": label})
        return sorted(rows, key=lambda r: r["title"])

    def settle(expected, *, focused=None, note):
        deadline = time.monotonic() + 15
        stable = 0
        last = None
        while time.monotonic() < deadline:
            rows = snapshot()
            current = {r["title"]: r["workspace"] for r in rows}
            focus = debug("list-workspaces", "--focused").stdout.strip() if focused else None
            valid = len(rows) == len(expected) and current == expected and (focused is None or focus == focused)
            stable = stable + 1 if valid and rows == last else int(valid)
            if stable >= 2:
                record("assertion", note=note, windows=rows, focused=focus)
                return {r["title"]: r for r in rows}
            last = rows
            time.sleep(0.4)
        raise AssertionError(f"{note}: expected={expected}, focused={focused}; actual={last}, focus={focus}")

    def create(label, anchor=None):
        if anchor:
            text = ghostty("add", prefix + label, out, *created[anchor])
        else:
            text = ghostty("create", prefix + label, out)
        tab_id, terminal_id = text.split("\t")
        if terminal_id in original_terminal_ids:
            raise AssertionError("Creation returned an existing personal terminal")
        created[label] = (tab_id, terminal_id)
        labels[label] = prefix + label
        record("created", label=label, tab=tab_id, terminal=terminal_id)

    def select(label):
        actual = ghostty("select", *created[label])
        if actual != created[label][0]:
            raise AssertionError(f"Ghostty selected a different tab for {label}")

    def close(label):
        record("closed", label=label, result=ghostty("close", *created[label]))

    def selected_label(anchor):
        selected_id, count = ghostty("status", *created[anchor]).split("\t")
        selected = next(label for label, (tab_id, _) in created.items() if tab_id == selected_id)
        return selected, int(count)

    def move(row, destination):
        if row["id"] in original_window_ids:
            raise AssertionError("Refusing to move a pre-existing window")
        debug("move-node-to-workspace", "--window-id", str(row["id"]), "--focus-follows-window", destination)

    version = personal_ghostty("probe")
    original_ghostty = set(personal_ghostty("baseline").splitlines())
    original_terminal_ids = {x.split(":", 1)[1] for x in original_ghostty}
    before = run([args.release_cli, "list-windows", "--all", "--format", "%{window-id}\t%{workspace}"]).stdout
    original_windows = dict(line.split("\t") for line in before.splitlines())
    original_window_ids = {int(x) for x in original_windows}
    original_workspace = run([args.release_cli, "list-workspaces", "--focused"]).stdout.strip()
    record("baseline", ghostty_version=version, original_workspace=original_workspace,
           existing_terminal_count=len(original_ghostty), existing_window_ids=sorted(original_window_ids), output=str(out))
    (out / "idle.sh").write_text("#!/bin/sh\nprintf '\\033]2;%s\\007' \"$AEROSPACE_TAB_TEST_LABEL\"\nexec /bin/cat\n")
    config = out / "aerospace.toml"
    config.write_text(f"start-at-login = false\nafter-startup-command = ['workspace {ws_a}']\n[mode.main.binding]\n")
    try:
        # Only the installed manager switches workspaces. No existing window is moved.
        run([args.release_cli, "workspace", ws_a])
        touched_manager = True
        env = dict(os.environ, AEROSPACE_NATIVE_TAB_TEST_PREFIX=prefix)
        process = subprocess.Popen([str(args.debug_app / "Contents/MacOS/AeroSpaceApp"), "--config-path", str(config)],
                                   env=env, stdout=(out / "debug-app.log").open("w"), stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise RuntimeError("Isolated debug app exited; see debug-app.log")
            ready = debug("list-workspaces", "--focused", check=False)
            if ready.returncode == 0 and ready.stdout.strip() == ws_a:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Isolated debug server did not start")
            time.sleep(0.4)
        if snapshot():
            raise AssertionError("Isolated manager must initially contain no windows")
        if args.fixture_app:
            fixture = subprocess.Popen([str(args.fixture_app / "Contents/MacOS/Fixture")], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=(out / "fixture.log").open("w"), text=True)
        subscriber = subprocess.Popen([str(args.debug_cli), "subscribe", "--all"],
                                      stdout=(out / "server-events.jsonl").open("w"), stderr=subprocess.DEVNULL)

        create("g1-a")
        rows = settle({"g1-a": ws_a}, focused=ws_a, note="first standalone window")
        a_id = rows["g1-a"]["id"]
        create("g1-b", "g1-a")
        rows = settle({"g1-b": ws_a}, focused=ws_a, note="first native tab replaces one slot")
        if rows["g1-b"]["id"] == a_id:
            raise AssertionError("First tab did not exercise a native window-ID handoff")
        move(rows["g1-b"], ws_moved)
        settle({"g1-b": ws_moved}, focused=ws_moved, note="move tab group to another workspace")
        for label in ["g1-a", "g1-b"] * (0 if args.topology_only else 5):
            select(label)
            settle({label: ws_moved}, focused=ws_moved, note="selection retains moved workspace")

        create("g1-c", "g1-a")
        settle({"g1-c": ws_moved}, focused=ws_moved, note="third tab keeps one slot")
        close("g1-c")
        sibling, count = selected_label("g1-a")
        if count != 2:
            raise AssertionError("Closing third tab did not leave two siblings")
        settle({sibling: ws_moved}, focused=ws_moved, note="closing selected third tab retains slot")
        select("g1-b")
        settle({"g1-b": ws_moved}, focused=ws_moved, note="select sibling before two-to-one closure")
        close("g1-b")
        settle({"g1-a": ws_moved}, focused=ws_moved, note="two tabs to one retains slot")
        create("g1-d", "g1-a")
        settle({"g1-d": ws_moved}, focused=ws_moved, note="recreate first native tab after closing sibling")

        create("g2-a")
        rows = settle({"g1-d": ws_moved, "g2-a": ws_moved}, focused=ws_moved, note="second physical group")
        move(rows["g2-a"], ws_b)
        settle({"g1-d": ws_moved, "g2-a": ws_b}, focused=ws_b, note="separate groups have separate owners")
        create("g2-b", "g2-a")
        settle({"g1-d": ws_moved, "g2-b": ws_b}, focused=ws_b, note="second group first-tab creation")
        selected_second = "g2-b"
        for _ in range(0 if args.topology_only else 5):
            for first, second in [("g1-a", "g2-a"), ("g1-d", "g2-b")]:
                select(first)
                settle({first: ws_moved, selected_second: ws_b}, focused=ws_moved,
                       note="first group cannot inherit second group's workspace")
                select(second)
                selected_second = second
                settle({first: ws_moved, second: ws_b}, focused=ws_b,
                       note="second group cannot inherit first group's workspace")
        close("g2-b")
        settle({"g1-d": ws_moved, "g2-a": ws_b}, focused=ws_b, note="closing second group's selected tab")
        if args.fixture_app:
            def merge(source, target, incoming=False):
                fixture_command("merge", id=created[source][0], target=created[target][0], selectIncoming=incoming)

            def detach(label):
                fixture_command("detach", id=created[label][0])

            merge("g1-d", "g2-a")
            settle({"g2-a": ws_b}, focused=ws_b, note="merge across workspaces keeps destination owner and removes source slot")
            for label in ("g1-a", "g1-d", "g2-a"):
                select(label)
                settle({label: ws_b}, focused=ws_b, note="incoming merged tabs inherit destination workspace")
            select("g1-a")
            detach("g1-a")
            sibling, count = selected_label("g2-a")
            assert count == 2
            rows = settle({"g1-a": ws_b, sibling: ws_b}, focused=ws_b, note="detach selected tab creates two slots on original workspace")
            move(rows["g1-a"], ws_a)
            settle({"g1-a": ws_a, sibling: ws_b}, focused=ws_a, note="detached group can move independently")
            merge("g1-a", sibling, incoming=True)
            settle({"g1-a": ws_b}, focused=ws_b, note="merge with incoming tab selected retains destination owner")
            detach("g1-d")
            sibling, count = selected_label("g2-a")
            assert count == 2
            rows = settle({"g1-d": ws_b, sibling: ws_b}, focused=ws_b, note="detach inactive merged tab forgets obsolete workspace")
            move(rows["g1-d"], ws_a)
            select("g2-a")
            settle({"g1-d": ws_a, "g2-a": ws_b}, focused=ws_b, note="remaining group retains destination after inactive detach")
            detach("g1-a")
            rows = settle({"g1-d": ws_a, "g2-a": ws_b, "g1-a": ws_b}, focused=ws_b, note="two-to-one detach preserves both bare windows")
            merge("g1-a", "g2-a", incoming=True)
            settle({"g1-d": ws_a, "g1-a": ws_b}, focused=ws_b, note="merge two bare windows with incoming selected")
            merge("g1-d", "g2-a")
            settle({"g2-a": ws_b}, focused=ws_b, note="merge detached group back without stale slots")
            for label in ("g1-d", "g1-a", "g2-a") * 2:
                select(label)
                settle({label: ws_b}, focused=ws_b, note="repeated selection after merge-detach cycles retains one owner")
        passed = True
        record("test-passed", assertions=sum(e["kind"] == "assertion" for e in events))
    except Exception as error:
        record("failure", error=str(error))
        raise
    finally:
        for label in reversed(created):
            try:
                close(label)
            except Exception as error:
                restore_errors.append(f"close {label}: {error}")
        if process is not None and process.poll() is None:
            try:
                settle({}, note="all disposable windows removed")
            except Exception as error:
                restore_errors.append(f"test window cleanup: {error}")
        if process is not None:
            try:
                debug("enable", "off", check=False)
            except Exception as error:
                restore_errors.append(f"pause test manager: {error}")
            if process.poll() is None:
                # Stop our test process without AeroSpace's quit-time recentering.
                process.kill()
            process.wait(timeout=10)
        if subscriber is not None:
            try:
                subscriber.wait(timeout=3)
            except subprocess.TimeoutExpired:
                subscriber.terminate()
                subscriber.wait(timeout=3)
        if fixture is not None:
            fixture.stdin.close()
            fixture.wait(timeout=5)
        if touched_manager:
            for command in [("enable", "on"), ("workspace", original_workspace)]:
                try:
                    run([args.release_cli, *command])
                except Exception as error:
                    restore_errors.append(f"restore {command}: {error}")
        remaining = set(personal_ghostty("baseline").splitlines())
        after = run([args.release_cli, "list-windows", "--all", "--format", "%{window-id}\t%{workspace}"]).stdout
        after_windows = dict(line.split("\t") for line in after.splitlines())
        assignments_unchanged = all(after_windows.get(wid) == ws for wid, ws in original_windows.items() if wid in after_windows)
        record("cleanup", personal_terminals_unchanged=remaining == original_ghostty,
               personal_workspace_assignments_unchanged=assignments_unchanged, errors=restore_errors)
        if remaining != original_ghostty or not assignments_unchanged or restore_errors:
            raise RuntimeError("Cleanup verification failed; inspect assertions.jsonl")
        log.close()
    if passed:
        print(f"PASS: automated Ghostty live test; results in {out}", flush=True)


if __name__ == "__main__":
    main()
