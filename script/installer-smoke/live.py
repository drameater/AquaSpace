#!/usr/bin/env python3
"""Opt-in installer integration checks. Never launches a rollback app.

Normal updates run the real shell entrypoint. Fault cases use the real transaction
and native backend with narrow injected errors. A monitor records process births,
parent PIDs and ownership; unexpected failures stop testing without auto-relaunch.

Before a run: AquaSpace must be the only window manager, built and staged with the
same signing identity as the installed copy. The run restarts AquaSpace several
times. Like every update, it deletes all but the newest completed update records
in /Applications.

Not yet run against the updater of 2026-11 (--resume, --confirm, per-user lock,
record pruning). The cases below follow that updater's code; the first live run
must confirm them. The wait limits come from the updater's own constants.
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
INSTALLER = ROOT / 'script/install-aquaspace.py'
# Long enough for a stop, a launch and the updater's whole readiness wait.
COMMAND_TIMEOUT = update.LAUNCH_APPEAR_TIMEOUT + update.READY_TIMEOUT + 60


def installer_for(system):
    """An Installer with the same locks as install-aquaspace.py."""
    return update.Installer(SOURCE, APP, system, lock_parent=update.USER_STATE_DIRECTORY,
                            legacy_lock_parents=(*update.LEGACY_LOCK_DIRECTORIES, APP.parent))


def records():
    return sorted(APP.parent.glob(update.RECORD_PREFIX + '*'))


def main(argv=None):
    # No abbreviations: only the full --run-live flag authorizes a run.
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     allow_abbrev=False)
    parser.add_argument('--run-live', action='store_true', help='Explicitly authorize actual AquaSpace updates and failure/recovery tests')
    args = parser.parse_args(argv)
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

    def process_rows(processes):
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
                row = dict(at=time.time_ns(), managers=process_rows(current))
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
        check(update.valid_status(native.status(process, CLI), process), note + ': sole ownership')
        snapshot = native.capture_session(process, CLI)
        before = {r[0]: r[1] for r in baseline['windows']}
        after = {r[0]: r[1] for r in snapshot['windows']}
        check(all(after.get(wid) == workspace for wid, workspace in before.items()),
              note + ': workspace assignments preserved', surviving_windows=len(set(before) & set(after)))
        return process

    def tracked(installer, stop_at=None):
        """Check each journal phase; with stop_at, stop the update there as a crash would."""
        def checkpoint(phase):
            processes = native.managers()
            emit('checkpoint', phase=phase, managers=process_rows(processes))
            if phase in ['stopped', 'replacing', 'replaced', 'launch_attempted']:
                check(not processes, phase + ': no manager alive')
            if phase == stop_at:
                raise update.UpdateError(f'Injected stop at phase {phase}')
        installer.checkpoint = checkpoint
        installer.report = lambda text: emit('installer-report', text=text)
        return installer

    def run_installer(name, *arguments):
        result = subprocess.run([sys.executable, str(INSTALLER), *map(str, arguments)],
                                capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        (out / f'{name}.log').write_text(f'exit {result.returncode}\n' + result.stdout + result.stderr)
        return result

    def await_control(system, process, note):
        # Wait as long as the updater would; never relaunch on a timeout.
        deadline = time.monotonic() + update.READY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                if update.valid_status(system.status(process, CLI), process): return
            except (update.UpdateError, OSError, ValueError, subprocess.TimeoutExpired): pass
            time.sleep(.05)
        raise update.UpdateError(note + ' did not take control; left alone.')

    def injected_stop(note):
        """Stop an update after the old AquaSpace exited and the files were replaced."""
        candidate = tracked(installer_for(RecordedSystem()), stop_at='replaced')
        try:
            candidate.install(restart=True)
            raise update.UpdateError('Expected the injected stop')
        except update.UpdateError as error:
            emit('expected-failure', message=str(error))
        (transaction,) = candidate.pending_transactions()
        check(candidate.journal(transaction)['phase'] == 'replaced' and not native.managers(),
              note + ': stopped after replacement with no manager running')
        return candidate, transaction

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
                try: result = installer.wait(timeout=COMMAND_TIMEOUT)
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
            # Each install keeps the newest completed records and adds its own.
            check(len(records()) <= update.KEEP_COMPLETED_RECORDS + 1,
                  f'public update {number}: old update records pruned', records=len(records()))

        # Real cross-process contention, while the parent holds the real per-user lock.
        before = current()
        with update.InstallLock(update.USER_STATE_DIRECTORY):
            result = run_installer('concurrent-refusal', '--restart')
        check(result.returncode == 1 and 'Another installer is running' in result.stderr
              and current().identity == before.identity,
              'concurrent installer refused without restarting the active manager')

        # Reproduce the dangerous old failure after a successful spawn: loss of
        # process inventory/readiness must leave that child alone, never roll back.
        # The updater's readiness wait reads the inventory; here it is falsely empty.
        class FalseInventory(RecordedSystem):
            launches = 0
            def launch(self, executable, session, log):
                child = super().launch(executable, session, log)
                self.launches += 1
                return child
            def managers(self):
                return [] if self.launches else super().managers()
        faulty = FalseInventory()
        candidate = tracked(installer_for(faulty))
        try:
            candidate.install(restart=True)
            raise update.UpdateError('Expected injected inventory failure')
        except update.UpdateError as error:
            emit('expected-failure', message=str(error))
            check(faulty.launches == 1, 'injected false absence starts exactly one replacement')
        child = current()
        await_control(faulty, child, 'Failure-test child')
        verify_session('injected false absence')
        (transaction,) = candidate.pending_transactions()
        check(candidate.journal(transaction)['phase'] == 'child_started', 'failed update retains its interrupted journal')
        before = child.identity
        result = run_installer('interrupted-retry-refusal', '--restart')
        check(result.returncode == 1 and current().identity == before,
              'retry after interrupted update does not launch or stop a manager')
        result = run_installer('active-recovery-refusal', '--recover', transaction)
        check(result.returncode == 1 and '--confirm' in result.stderr and current().identity == before,
              'file recovery refuses to touch a running manager and names --confirm')
        result = run_installer('confirm', '--confirm', transaction)
        check(result.returncode == 0 and current().identity == before,
              '--confirm completes the update and stops or starts nothing')
        check(candidate.journal(transaction)['phase'] == 'committed', '--confirm commits the record')

        # An update that stopped after the replacement: file-only recovery.
        # The launch after it is an explicit next test step, never an error handler.
        candidate, transaction = injected_stop('recovery case')
        result = run_installer('blocked-retry', '--restart')
        check(result.returncode == 1 and not native.managers(), 'retry with an unfinished update starts nothing')
        result = run_installer('file-recovery', '--recover', transaction)
        check(result.returncode == 0 and not native.managers(), 'file-only recovery starts zero managers')
        check(candidate.journal(transaction)['phase'] == 'recovered' and not candidate.pending_transactions(),
              'file-only recovery resolves the journal')
        restore = RecordedSystem()
        check(not restore.managers(), 'no manager alive before explicit session restoration')
        resumed = restore.launch(APP / 'Contents/MacOS/AquaSpace', transaction / 'session.json', out / 'explicit-restore.log')
        await_control(restore, resumed, 'Explicit restoration')
        verify_session('explicit restoration')

        # An update that stopped after the replacement: --resume launches once.
        candidate, transaction = injected_stop('resume case')
        result = run_installer('resume', '--resume', transaction)
        check(result.returncode == 0 and candidate.journal(transaction)['phase'] == 'committed',
              '--resume finishes the update')
        verify_session('resumed update')

        final = tracked(installer_for(RecordedSystem()))
        tx = final.install(restart=True)
        check(final.journal(tx)['phase'] == 'committed', 'final native update committed')
        final_process = verify_session('final update')
        check(not final.pending_transactions(), 'no interrupted update remains')
        report = dict(assertions=assertions, peak_observed_managers=max(len(s['managers']) for s in samples),
                      samples=len(samples), monitor_errors=errors, final_manager=final_process.record(),
                      public_updates=2, injected_failures=['process inventory falsely empty after launch',
                                                           'stop after replacement, then --recover',
                                                           'stop after replacement, then --resume'],
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
