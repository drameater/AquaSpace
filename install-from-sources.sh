#!/usr/bin/env bash
# AquaSpace local updater. Original AeroSpace and Homebrew are left in place.
# macOS Bash 3.2 treats an empty argument array as unset under `set -u`.
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

rebuild=1
build_args=()
install_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dont-rebuild) rebuild=0; shift ;;
        --allow-identity-change) install_args+=("$1"); shift ;;
        --ready-timeout)
            if [[ $# -lt 2 ]]; then
                echo '--ready-timeout needs a number of seconds.' >&2
                exit 2
            fi
            install_args+=("$1" "$2"); shift 2 ;;
        *) build_args+=("$1"); shift ;;
    esac
done

if [[ "$rebuild" == 1 ]]; then
    if [[ ${#install_args[@]} != 0 ]]; then
        echo 'Installer options need a staged build: run ./build-release.sh, then ./install-from-sources.sh --dont-rebuild with these options.' >&2
        exit 2
    fi
    exec python3 script/build-aquaspace.py --install-and-restart "${build_args[@]}"
fi
if [[ ${#build_args[@]} != 0 ]]; then
    echo 'Build options cannot be combined with --dont-rebuild.' >&2
    exit 2
fi
exec python3 script/install-aquaspace.py --restart "${install_args[@]}"
