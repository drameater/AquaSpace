#!/usr/bin/env bash
# AquaSpace local updater. Original AeroSpace and Homebrew are left in place.
# macOS Bash 3.2 treats an empty argument array as unset under `set -u`.
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

rebuild=1
build_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dont-rebuild) rebuild=0; shift ;;
        *) build_args+=("$1"); shift ;;
    esac
done

if [[ "$rebuild" == 1 ]]; then
    exec python3 script/build-aquaspace.py --install-and-restart "${build_args[@]}"
fi
if [[ ${#build_args[@]} != 0 ]]; then
    echo 'Build options cannot be combined with --dont-rebuild.' >&2
    exit 2
fi
exec python3 script/install-aquaspace.py --restart
