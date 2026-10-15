#!/usr/bin/env python3
"""Opt-in installer integration checks. Never launches a rollback app.

Normal updates run the real shell entrypoint. Fault cases use the real transaction
and native backend with narrow injected errors. A monitor records process births,
parent PIDs and ownership; unexpected failures stop testing without auto-relaunch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'script'))
import aquaspace_update as update

APP = Path('/Applications/AquaSpace.app')
CLI = APP / 'Contents/MacOS/aquaspace-cli'
SOURCE = ROOT / '.debug/aquaspace/release/AquaSpace.app'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', help='Explicitly authorize actual AquaSpace updates and failure/recovery tests')
    args = parser.parse_args()
    if not args.run_live: parser.error('Actual updates require --run-live')
    out = ROOT / '.debug/aquaspace' / ('installer-live-' + str(time.time_ns()))
    out.mkdir(parents=True)
    native = update.NativeSystem()
    observer = update.DarwinProcesses()
    peers = native.managers()
    if len(peers) != 1 or peers[0].bundle_id != 'local.aquaspace' or peers[0].executable != APP / 'Contents/MacOS/AquaSpace':
        raise update.UpdateError('Expected only the installed AquaSpace; no actions taken.')
    baseline = native.capture_session(peers[0], CLI)
    update.atomic_json(out / 'baseline.json', baseline)
    events = []
    samples = []
    errors = []
    assertions = []
    parents = set()
    expected_launches = []
    shutdown = threading.Event()
    overlap = threading.Event()
    source_hash = update.sha256(SOURCE / 'Contents/MacOS/AquaSpace')

    def emit(event, **fields):
        row = dict(at=time.time_ns(), event=event, **fields)
        events.append(row)
        with (out / 'events.jsonl').open('a') as stream: stream.write(json.dumps(row) + '\n')

    def records(processes):
        result = []
        for process in processes:
            row = process.record()
            info = observer._info(process.pid)
            row['parent_pid'] = info.pbi_ppid if info else None
            result.append(row)
        return result

    def watch():
        previous = None
        try:
            while not shutdown.is_set():
                current = observer.managers()
                row = dict(at=time.time_ns(), managers=records(current))
                samples.append(row)
                identities = tuple(p.identity for p in current)
                if identities != previous:
                    emit('process-inventory', managers=row['managers'])
                    previous = identities
                if len(current) > 1:
                    overlap.set()
                    # Only stop a child that this test's updater created. Never
                    # resume or launch a manager in response to an error.
                    for p, detail in zip(current, row['managers']):
                        if detail['parent_pid'] in parents or any(p.identity == child.identity for child in expected_launches):
                            native.stop(p)
                    errors.append('More than one manager observed; stopped test-created children.')
                    return
                shutdown.wait(.015)
        except Exception as error:
            errors.append(repr(error))

    monitor = threading.Thread(target=watch, daemon=True)
    monitor.start()

    def check(condition, note, **fields):
        if overlap.is_set() or errors: raise update.UpdateError(f'Monitor failed: {errors}')
        if not condition: raise update.UpdateError(note)
        assertions.append(dict(note=note, **fields))
        emit('passed', note=note, **fields)

    def current():
        p = native.managers()
        if len(p) != 1: raise update.UpdateError(f'Expected one manager, got {len(p)}')
        return p[0]

    def verify_session(note):
        process = current()
        status = native.status(process, CLI) if process.pid in native.children else json.loads(native.run([CLI, 'management-status']))
        check(update.valid_status(status, process), note + ': sole ownership')
        snapshot = native.capture_session(process, CLI)
        before = {r[0]: r[1] for r in baseline['windows']}
        after = {r[0]: r[1] for r in snapshot['windows']}
        check(all(after.get(wid) == workspace for wid, workspace in before.items()),
              note + ': workspace assignments preserved', surviving_windows=len(set(before) & set(after)))
        return process

    def tracked(installer):
        def checkpoint(phase):
            processes = native.managers()
            emit('checkpoint', phase=phase, managers=records(processes))
            if phase in ['stopped', 'replacing', 'replaced', 'launch_attempted']:
                check(not processes, phase + ': no manager alive')
        installer.checkpoint = checkpoint
        return installer

    class RecordedSystem(update.NativeSystem):
        def launch(self, executable, session, log):
            check(not self.managers(), 'kernel inventory empty immediately before spawn')
            emit('spawn-call', executable=str(executable), restore_session=str(session) if session else None)
            child = super().launch(executable, session, log)
            expected_launches.append(child)
            emit('spawn-returned', child=child.record())
            return child

        def stop(self, process):
            emit('stop-call', process=process.record())
            super().stop(process)

    parents.add(os.getpid())
    try:
        # Exercise the public shell/Python CLI unchanged, including startup args.
        for number in [1, 2]:
            old = current()
            emit('public-update-start', round=number, old=old.record())
            with (out / f'public-update-{number}.log').open('wb') as log:
                installer = subprocess.Popen(['/bin/bash', str(ROOT / 'install-from-sources.sh'), '--dont-rebuild'],
                                             cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                parents.add(installer.pid)
                try: result = installer.wait(timeout=40)
                except BaseException:
                    installer.terminate()
                    installer.wait(timeout=3)
                    raise
            check(result == 0, f'public update {number} succeeds')
            child = verify_session(f'public update {number}')
            check(child.identity != old.identity and native.inspect(old.pid) is None,
                  f'public update {number}: predecessor exited and replacement has a new identity')
            check(update.sha256(APP / 'Contents/MacOS/AquaSpace') == source_hash,
                  f'public update {number}: installed binary matches staged build')

        # Real cross-process contention, while the parent holds the real global lock.
        before = current()
        with update.InstallLock(Path('/tmp')):
            result = subprocess.run([sys.executable, str(ROOT / 'script/install-aquaspace.py'), '--restart'],
                                    capture_output=True, text=True, timeout=15)
        (out / 'concurrent-refusal.log').write_text(result.stdout + result.stderr)
        check(result.returncode != 0 and current().identity == before.identity,
              'concurrent installer refused without restarting the active manager')

        # Reproduce the dangerous old failure after a successful spawn: loss of
        # process inventory/readiness must leave that child alone, never roll back.
        class FalseInventory(RecordedSystem):
            launched = False
            def launch(self, executable, session, log):
                child = super().launch(executable, session, log)
                self.launched = True
                return child
            def managers(self):
                return [] if self.launched else super().managers()
        faulty = FalseInventory()
        candidate = tracked(update.Installer(SOURCE, APP, faulty, lock_parent=Path('/tmp')))
        try:
            candidate.install(restart=True)
            raise update.UpdateError('Expected injected inventory failure')
        except update.UpdateError as error:
            emit('expected-failure', message=str(error))
            check(faulty.launched and len(faulty.children) == 1,
                  'injected false absence starts exactly one replacement')
        child = current()
        # Use its retained handle to wait for startup; do not relaunch on timeout.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if update.valid_status(faulty.status(child, CLI), child): break
            except (update.UpdateError, OSError, ValueError, subprocess.TimeoutExpired): pass
            time.sleep(.05)
        else: raise update.UpdateError('Failure-test child did not become ready; left alone.')
        verify_session('injected false absence')
        transaction = candidate.pending_transactions()[0]
        check(candidate.journal(transaction)['phase'] == 'child_started', 'failed update retains its interrupted journal')
        before = child.identity
        result = subprocess.run([sys.executable, str(ROOT / 'script/install-aquaspace.py'), '--restart'], capture_output=True, text=True, timeout=15)
        (out / 'interrupted-retry-refusal.log').write_text(result.stdout + result.stderr)
        check(result.returncode != 0 and current().identity == before,
              'retry after interrupted update does not launch or stop a manager')
        result = subprocess.run([sys.executable, str(ROOT / 'script/install-aquaspace.py'), '--recover', str(transaction)], capture_output=True, text=True, timeout=15)
        (out / 'active-recovery-refusal.log').write_text(result.stdout + result.stderr)
        check(result.returncode != 0 and current().identity == before,
              'file recovery refuses to touch a running manager')

        # Deliberately stop the *known* child, then test real file-only recovery.
        # The subsequent launch is an explicit next test step, never an error handler.
        emit('explicit-recovery-stop', child=child.record())
        native.stop(child)
        deadline = time.monotonic() + 5
        while native.inspect(child.pid) is not None and time.monotonic() < deadline: time.sleep(.02)
        check(not native.managers(), 'all managers stopped before file-only recovery')
        result = subprocess.run([sys.executable, str(ROOT / 'script/install-aquaspace.py'), '--recover', str(transaction)], capture_output=True, text=True, timeout=15)
        (out / 'file-recovery.log').write_text(result.stdout + result.stderr)
        check(result.returncode == 0 and not native.managers(), 'file-only recovery starts zero managers')
        check(not candidate.pending_transactions(), 'file-only recovery resolves the journal')

        # Restore the saved session through the real native launch backend after
        # proving emptiness. Then perform a final normal update on that instance.
        restore = RecordedSystem()
        check(not restore.managers(), 'no manager alive before explicit session restoration')
        resumed = restore.launch(APP / 'Contents/MacOS/AquaSpace', transaction / 'session.json', out / 'explicit-restore.log')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if update.valid_status(restore.status(resumed, CLI), resumed): break
            except (update.UpdateError, OSError, ValueError, subprocess.TimeoutExpired): pass
            time.sleep(.05)
        else: raise update.UpdateError('Explicit restoration did not become ready; left alone.')
        verify_session('explicit restoration')
        final = tracked(update.Installer(SOURCE, APP, RecordedSystem(), lock_parent=Path('/tmp')))
        tx = final.install(restart=True)
        check(final.journal(tx)['phase'] == 'committed', 'final native update committed')
        final_process = verify_session('final update')
        check(not final.pending_transactions(), 'no interrupted update remains')
        report = dict(assertions=assertions, peak_observed_managers=max(len(s['managers']) for s in samples),
                      samples=len(samples), monitor_errors=errors, final_manager=final_process.record(),
                      public_updates=2, injected_failure='process inventory falsely empty after launch',
                      automatic_recovery_launches=0, source_sha256=source_hash)
        update.atomic_json(out / 'results.json', report)
        print(f'PASS: {len(assertions)} assertions; peak managers {report["peak_observed_managers"]}; evidence {out}', flush=True)
    except BaseException as error:
        emit('test-stopped', error=repr(error))
        raise
    finally:
        shutdown.set(); monitor.join(timeout=3)
        with (out / 'samples.jsonl').open('w') as stream:
            for sample in samples: stream.write(json.dumps(sample) + '\n')
        update.atomic_json(out / 'monitor.json', dict(errors=errors, overlap=overlap.is_set(), samples=len(samples),
                           peak_managers=max((len(s['managers']) for s in samples), default=0)))


if __name__ == '__main__': main()
