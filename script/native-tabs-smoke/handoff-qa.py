#!/usr/bin/env python3
"""Bounded background QA: only disposable windows, one controller, simulated handoff.

The current manager is SIGSTOP'ed only after a read-only preflight. An independent
watchdog resumes it after 10 seconds even if this harness stops responding.
"""
import argparse
import hashlib
import json
import os
import pty
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid


def run(*args, check=True, timeout=3):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true", help="Verify permissions and isolation without pausing the active manager")
    args = parser.parse_args()
    manifest = json.loads((args.app.parent / "scope.json").read_text())
    executable = args.app / "Contents/MacOS/AeroSpaceApp"
    assert manifest.get("handoff_qa") and manifest["application_id"] == "org.aerospace.NativeTabFixture"
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == manifest["sha256"]
    cli = args.app / "Contents/MacOS/aquaspace-cli"
    old_cli = Path("/opt/homebrew/bin/aerospace")
    out = Path(tempfile.mkdtemp(prefix="aquaspace-background-qa-", dir="/private/tmp"))
    prefix = "AeroSpace-NativeTabs-" + uuid.uuid4().hex[:10] + "-"
    env = dict(os.environ, AEROSPACE_NATIVE_TAB_TEST_PREFIX=prefix)
    config = out / "aerospace.toml"
    config.write_text("start-at-login = false\nafter-startup-command = ['workspace native-tab-test-qa-a']\n[mode.main.binding]\n")
    assertions = []
    candidate = fixture = watchdog = None
    paused = False
    peer_bound = False
    tabs = {}
    peer = {"ids": [], "commands": [], "started": False}
    stop_peer = threading.Event()
    peer_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def manager_pids():
        text = run("pgrep", "-f", "^/Applications/(AeroSpace|AquaSpace).app/Contents/MacOS/(AeroSpace|AquaSpace)( |$)", check=False)
        return [int(x) for x in text.splitlines()]

    managers = manager_pids()
    assert len(managers) == 1, "Expected exactly one existing manager; refusing to start QA"
    old_pid = managers[0]
    # New AquaSpace has a separate endpoint; earlier local builds used stock IPC.
    try:
        status = json.loads(run("/opt/homebrew/bin/aquaspace", "management-status", timeout=.5))
        if status["pid"] == old_pid and status["controlsWindows"]:
            old_cli = Path("/opt/homebrew/bin/aquaspace")
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    before = dict(line.split("\t") for line in run(old_cli, "list-windows", "--all", "--format", "%{window-id}\t%{workspace}").splitlines())
    workspace_before = run(old_cli, "list-workspaces", "--focused")
    guard_file = out / "watchdog.json"

    def update_guard(done=False):
        data = {"old": old_pid, "candidate": candidate.pid if candidate else None,
                "fixture": fixture.pid if fixture else None, "done": done, "paused": paused}
        temporary = guard_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(guard_file)

    def start_candidate(read_only=False):
        extra = ["--read-only"] if read_only else []
        master, slave = pty.openpty()
        log = (out / ("preflight.log" if read_only else "candidate.log")).open("wb")
        def collect():
            try:
                while True:
                    chunk = os.read(master, 16384)
                    if not chunk: break
                    log.write(chunk); log.flush()
            except OSError:
                pass
            finally:
                os.close(master); log.close()
        threading.Thread(target=collect, daemon=True).start()
        try:
            return subprocess.Popen([str(executable), "--config-path", str(config), *extra], env=env,
                                    stdin=subprocess.DEVNULL, stdout=slave, stderr=slave)
        finally:
            os.close(slave)

    def ready(read_only):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if candidate.poll() is not None:
                raise RuntimeError("Candidate exited during startup; inspect QA log")
            try:
                state = json.loads(run(cli, "management-status", timeout=.5))
                if state["readOnly"] == read_only and state["controlsWindows"] == (not read_only):
                    return state
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
                pass
            time.sleep(.04)
        raise RuntimeError("Candidate did not reach the required ownership state")

    def fixture_call(operation, **values):
        fixture.stdin.write(json.dumps({"operation": operation, **values}) + "\n")
        fixture.stdin.flush()
        if not select.select([fixture.stdout], [], [], 1)[0]:
            raise RuntimeError("Background AppKit fixture timed out")
        return json.loads(fixture.stdout.readline())

    def settle(expected, note):
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            rows = [line.split("\t", 2) for line in run(cli, "list-windows", "--all", "--format", "%{window-id}\t%{workspace}\t%{window-title}", timeout=1).splitlines()]
            assert all(row[2].startswith(prefix) for row in rows), "A non-fixture window escaped the scope gate"
            actual = {r[2].removeprefix(prefix): r[1] for r in rows}
            if len(rows) == len(expected) and actual == expected:
                assertions.append({"note": note, "windows": [{"id": r[0], "workspace": r[1]} for r in rows]})
                return {r[2].removeprefix(prefix): r[0] for r in rows}
            time.sleep(.04)
        raise AssertionError(f"{note}: expected {expected}; actual {actual}")

    def create(label, anchor=None):
        result = fixture_call("add" if anchor else "create", title=prefix + label, **({"id": tabs[anchor]} if anchor else {}))
        tabs[label] = result["tab"]

    def receive_exact(connection, length):
        data = b""
        while len(data) < length:
            chunk = connection.recv(length - len(data))
            if not chunk:
                raise EOFError()
            data += chunk
        return data

    def serve_peer():
        while not stop_peer.is_set():
            try:
                connection, _ = peer_socket.accept()
            except (socket.timeout, OSError):
                continue
            with connection:
                try:
                    assert struct.unpack("<I", receive_exact(connection, 4))[0] == 1
                    connection.sendall(struct.pack("<I", 1))
                    request = json.loads(receive_exact(connection, struct.unpack("<I", receive_exact(connection, 4))[0]))
                    command = request["args"]
                    if command == ["qa-target-start"]:
                        status = json.loads(run(cli, "management-status", timeout=1))
                        assert not status["controlsWindows"] and status["phase"] == "yielding"
                        peer["started"] = True
                        peer["ownership_at_launch"] = status
                    peer["commands"].append(command)
                    stdout = "\n".join(peer["ids"]) if command[:1] == ["list-windows"] else ""
                    answer = {"exitCode": 0, "stdout": stdout, "stderr": "", "serverVersionAndHash": "qa-simulated-stock"}
                except Exception as error:
                    answer = {"exitCode": 2, "stdout": "", "stderr": str(error), "serverVersionAndHash": "qa-simulated-stock"}
                payload = json.dumps(answer).encode()
                try:
                    connection.sendall(struct.pack("<I", len(payload)) + payload)
                except OSError:
                    pass

    try:
        socket_path = Path(manifest["peer_socket"])
        assert not socket_path.exists(), "Refusing to replace another peer socket"
        peer_socket.bind(str(socket_path))
        peer_bound = True
        peer_socket.listen()
        peer_socket.settimeout(.2)
        threading.Thread(target=serve_peer, daemon=True).start()
        candidate = start_candidate(read_only=True)
        preflight = ready(read_only=True)
        assert not run(cli, "list-windows", "--all", "--format", "%{window-id}")
        candidate.kill(); candidate.wait(timeout=2); candidate = None
        assert manager_pids() == [old_pid]
        if args.preflight_only:
            (out / "preflight.json").write_text(json.dumps(preflight, indent=2) + "\n")
            print(f"PASS: read-only candidate registered no windows; current manager untouched. {out}")
            return
        update_guard()
        # Watchdog is independent of this Python process and the candidate's actor loop.
        watchdog_code = '''import json,os,signal,time,sys,subprocess
from pathlib import Path
p=Path(sys.argv[1]); end=time.monotonic()+10
while time.monotonic()<end:
 try:
  s=json.loads(p.read_text())
  if s['done']:sys.exit(0)
 except (OSError,ValueError):pass
 time.sleep(.05)
s=json.loads(p.read_text())
for k in ['candidate','fixture']:
 if s.get(k):
  try:os.kill(s[k],signal.SIGKILL)
  except ProcessLookupError:pass
if s.get('paused'):
 others=subprocess.run(['pgrep','-f','^/Applications/(AeroSpace|AquaSpace).app/Contents/MacOS/(AeroSpace|AquaSpace)( |$)'],capture_output=True,text=True).stdout.split()
 action=signal.SIGKILL if any(int(pid)!=s['old'] for pid in others) else signal.SIGCONT
 try:os.kill(s['old'],action)
 except ProcessLookupError:pass
'''
        watchdog = subprocess.Popen(["/usr/bin/python3", "-c", watchdog_code, str(guard_file)], start_new_session=True)
        os.kill(old_pid, signal.SIGSTOP)
        paused = True
        update_guard()
        assert "T" in run("ps", "-p", str(old_pid), "-o", "stat=")
        candidate = start_candidate()
        update_guard()
        ownership = ready(read_only=False)
        fixture = subprocess.Popen([str(args.fixture / "Contents/MacOS/Fixture"), "--background"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=(out / "fixture.log").open("w"), text=True)
        update_guard()
        a, b = "native-tab-test-qa-a", "native-tab-test-qa-b"
        create("a"); settle({"a": a}, "standalone")
        create("b", "a"); rows = settle({"b": a}, "first tab owns one slot")
        run(cli, "move-node-to-workspace", "--window-id", rows["b"], "--focus-follows-window", b)
        for label in ["a", "b"]:
            fixture_call("select", id=tabs[label]); settle({label: b}, "selection keeps moved workspace")
        create("c"); rows = settle({"b": b, "c": b}, "second group")
        run(cli, "move-node-to-workspace", "--window-id", rows["c"], "--focus-follows-window", a)
        create("d", "c"); settle({"b": b, "d": a}, "independent group")
        fixture_call("merge", id=tabs["b"], target=tabs["d"])
        settle({"d": a}, "cross-workspace merge retains destination")
        fixture_call("detach", id=tabs["d"])
        selected = fixture_call("status", id=tabs["a"])["selected"]
        sibling = next(k for k, v in tabs.items() if v == selected)
        rows = settle({"d": a, sibling: a}, "selected detach inherits destination")
        run(cli, "move-node-to-workspace", "--window-id", rows["d"], "--focus-follows-window", b)
        fixture_call("merge", id=tabs["d"], target=tabs[sibling], selectIncoming=True)
        settle({"d": a}, "incoming selected merge retains destination")
        fixture_call("detach", id=tabs["b"])
        rows = settle({"d": a, "b": a}, "inactive detach forgets obsolete owner")
        peer["ids"] = list(rows.values())
        run(cli, "switch-to-aerospace")
        candidate.wait(timeout=2)
        assert candidate.returncode == 0 and peer["started"]
        restored = {c[2]: c[3] for c in peer["commands"] if c[:1] == ["move-node-to-workspace"]}
        assert restored == {wid: a for wid in rows.values()}
        assertions.append({"note": "handoff starts peer only after freeze and drain", "ownership": peer["ownership_at_launch"]})
    finally:
        if candidate is not None and candidate.poll() is None:
            candidate.kill(); candidate.wait(timeout=2)
        if fixture is not None:
            try: fixture_call("quit"); fixture.wait(timeout=1)
            except Exception: fixture.kill(); fixture.wait(timeout=1)
        if paused:
            others = [pid for pid in manager_pids() if pid != old_pid]
            # A user-launched manager takes priority. Never resume a competing one.
            os.kill(old_pid, signal.SIGKILL if others else signal.SIGCONT)
            paused = False
        update_guard(done=True)
        if watchdog is not None:
            watchdog.wait(timeout=1)
        stop_peer.set(); peer_socket.close()
        if peer_bound:
            try: Path(manifest["peer_socket"]).unlink()
            except FileNotFoundError: pass
    after = dict(line.split("\t") for line in run(old_cli, "list-windows", "--all", "--format", "%{window-id}\t%{workspace}").splitlines())
    report = {"assertions": assertions, "preflight": preflight, "candidate_ownership": ownership,
              "same_manager_resumed": manager_pids() == [old_pid],
              "existing_assignments_preserved": all(after.get(wid) == ws for wid, ws in before.items() if wid in after),
              "workspace_before": workspace_before, "workspace_after": run(old_cli, "list-workspaces", "--focused"),
              "peer_commands": peer["commands"]}
    (out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["same_manager_resumed"] and report["existing_assignments_preserved"]
    print(f"PASS: {len(assertions)} scoped background assertions; same manager resumed. {out}")


if __name__ == "__main__":
    main()
