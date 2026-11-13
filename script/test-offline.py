#!/usr/bin/env python3
"""Run every offline suite in sequence and print one summary table.

Runs the Python suite (script/tests) and each Swift runner below. The runners
share one scratch root, so their builds stay incremental between this script and
individual runs. Each suite's output goes to <scratch-root>/offline-logs/<suite>.log;
a failed suite also prints the end of its log. Exit status is 1 if any suite fails.
No suite starts an app or reads live AX, WindowServer or process state.

Example: nice -n 10 python3 script/test-offline.py --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
"""
import os
import subprocess
import sys
import time

import offline_harness as harness

SWIFT_RUNNERS = {
    'ownership': 'test-ownership-offline.py',
    'native-tabs': 'test-native-tabs-offline.py',
    'hidden-handoff': 'test-hidden-handoff-offline.py',
    'window-discovery': 'test-window-discovery-offline.py',
    'native-tab-model': 'test-native-tab-model-offline.py',
}
SUITES = ('python', *SWIFT_RUNNERS)


def command(suite, args):
    if suite == 'python':
        return [sys.executable, '-m', 'unittest', 'discover', '-s', str(harness.ROOT / 'script/tests')]
    flags = ['--scratch-root', str(args.scratch_root), '--jobs', str(args.jobs)]
    if args.sdk:
        flags += ['--sdk', str(args.sdk)]
    return [sys.executable, str(harness.ROOT / 'script' / SWIFT_RUNNERS[suite]), *flags]


def run(suite, args, logs):
    log = logs / f'{suite}.log'
    started = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    with log.open('w') as output:
        status = subprocess.run(command(suite, args), cwd=harness.ROOT, env=env,
                                stdout=output, stderr=subprocess.STDOUT).returncode
    text = log.read_text(errors='replace')
    return dict(suite=suite, status=status, tests=harness.tests_run(text),
                seconds=time.monotonic() - started, log=log, text=text)


def table(results):
    rows = [('suite', 'tests', 'result', 'seconds')]
    rows += [(r['suite'], '-' if r['tests'] is None else str(r['tests']),
              'pass' if r['status'] == 0 else f'FAIL ({r["status"]})', f'{r["seconds"]:.0f}') for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return '\n'.join('  '.join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows)


def main():
    parser = harness.argument_parser(__doc__)
    parser.add_argument('--suite', action='append', choices=SUITES,
                        help='run only this suite (repeat for more); default: all')
    args = parser.parse_args()
    args.scratch_root = args.scratch_root.resolve()
    logs = args.scratch_root / 'offline-logs'
    logs.mkdir(parents=True, exist_ok=True)
    selected = args.suite or SUITES
    results = []
    for index, suite in enumerate(selected, 1):
        print(f'[{index}/{len(selected)}] {suite} ...', flush=True)
        result = run(suite, args, logs)
        results.append(result)
        if result['status'] != 0:
            print(''.join(result['text'].splitlines(keepends=True)[-30:]), end='')
            print(f'{suite} failed; full log: {result["log"]}', flush=True)
    print()
    print(table(results))
    failed = [r['suite'] for r in results if r['status'] != 0]
    print(f'\n{len(failed)} of {len(results)} suites failed: {", ".join(failed)}' if failed
          else f'\nAll {len(results)} suites passed. Logs: {logs}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
