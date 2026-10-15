#!/usr/bin/env python3
"""Retired: the pause/resume watchdog could revive a competing controller.

Historical reports remain valid for their recorded runs. This entrypoint performs
no process inspection, signaling, launching, or recovery. Use the offline
ownership regressions; a future live harness must use ordered exclusive ownership.
"""
import sys


def main():
    print('This live handoff harness is retired. Run script/test-ownership-offline.py for offline ownership checks.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
