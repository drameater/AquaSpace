#!/usr/bin/env python3
"""Retired: this runner depended on best-effort debug disable/re-enable.

It cannot safely coexist with the shared controller lock. This entrypoint never
launches, stops, enables, or resumes a manager. Ghostty driver/fixture sources are
retained for a future explicitly authorized harness with ordered ownership.
"""
import sys


def main():
    print('This live Ghostty runner is retired. Run script/test-ownership-offline.py for offline ownership checks.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
