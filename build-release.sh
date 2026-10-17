#!/usr/bin/env bash
# Build the local fork without publishing, installing, or resetting the checkout.
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-version) args+=(--version "$2"); shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
exec python3 script/build-aquaspace.py "${args[@]}"
