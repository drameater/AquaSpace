# AquaSpace

<img src="resources/Assets.xcassets/AppIcon.appiconset/icon.png" alt="AquaSpace icon" width="96">

AquaSpace is a personal fork of [AeroSpace](https://github.com/nikitabobko/AeroSpace),
a tiling window manager for macOS. It keeps native macOS tab groups together
across workspaces. It keeps AeroSpace's keyboard-driven tiling, configuration
format and icon.

## What changes

- A native tab group uses one workspace slot. Selecting another tab keeps the
  group on its workspace.
- Merging groups keeps the destination's workspace. A detached tab can move on
  its own.
- Only one window manager controls windows at a time. AquaSpace takes the
  session (workspace assignments, layouts, focus) from stock AeroSpace when it
  starts, and gives the session back if stock AeroSpace starts later.
- A local updater stops the old AquaSpace before it starts the new one. A
  failed update keeps a recovery record and never starts an old copy by itself.

AquaSpace uses the ordinary macOS Accessibility permission. It needs no sudo,
no SIP change and no code injection.

The [AquaSpace guide](dev-docs/aquaspace.md) explains how the fork works and how
to build, install, update, recover and test it. Dated test records are in
[dev-docs/history](dev-docs/history/README.md).

## Quick start

You need macOS, Python 3, Git and a Swift toolchain (the Command Line Tools are
enough). Quit every window manager, including stock AeroSpace, before the first
installation.

```sh
git clone https://github.com/drameater/AquaSpace.git
cd AquaSpace
python3 script/aquaspace_signing.py --setup   # once: persistent signing identity
./install-from-sources.sh                     # build, install and start AquaSpace
```

AquaSpace then waits for the Accessibility permission. Allow it in System
Settings > Privacy & Security > Accessibility (called **Device Control and Data
Access** on some macOS versions). If the installer stops waiting first (exit
status 3), allow AquaSpace and run the `--confirm` command that the installer
prints. To update later, run `./install-from-sources.sh` again. The guide
explains the [build](dev-docs/aquaspace.md#build),
[install and update](dev-docs/aquaspace.md#install-and-update) and
[recovery](dev-docs/aquaspace.md#recovery) steps.

The upstream Homebrew cask installs stock AeroSpace, not this fork.

## Configuration and commands

AquaSpace reads the same config files as AeroSpace:

- `~/.aerospace.toml`
- `$XDG_CONFIG_HOME/aerospace/aerospace.toml` (normally
  `~/.config/aerospace/aerospace.toml`)

Key bindings and the `AEROSPACE_*` callback variables stay compatible. The app
is **AquaSpace.app** and its bundle ID is `local.aquaspace`.

The CLI is `/Applications/AquaSpace.app/Contents/MacOS/aquaspace-cli`. The
installer does not put it on your `PATH`; use the full path or make your own
symlink. Stock `aerospace` and `aquaspace-cli` talk to different managers.
Inside AquaSpace's own config callbacks, `aerospace` reaches AquaSpace through a
bundled alias. Scripts outside the config must call the CLI of the manager they
mean.

The upstream [configuration guide](https://nikitabobko.github.io/AeroSpace/guide)
and [command reference](https://nikitabobko.github.io/AeroSpace/commands) apply
to the shared configuration and command syntax.

## Switching managers

Open **AquaSpace** to take over from stock AeroSpace. AquaSpace has no command
to switch back: start stock AeroSpace, and AquaSpace gives it the session and
quits. The session keeps workspace assignments, root layouts, floating and
fullscreen flags, and focus. Nested containers and their sizes are rebuilt.

If AquaSpace stops or refuses to start, read
`~/Library/Logs/AquaSpace/last-failure.txt`.

## Checks

GitHub Actions runs only when started by hand. Its one workflow runs the offline
Python suite on Linux; it does not build or start a window manager. Run the same
suite locally:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -v
```

The [guide](dev-docs/aquaspace.md#tests) lists the Swift suites and the live
harnesses. Live harnesses need explicit authorization.

## Attribution and license

AquaSpace builds on AeroSpace by [@nikitabobko](https://github.com/nikitabobko)
and its contributors. The original icon and upstream attribution are retained.
Licensed under the [MIT license](LICENSE.txt); third-party notices are in
[legal](legal/README.md). To support the upstream work, see
[AeroSpace sponsorship](https://github.com/sponsors/nikitabobko).
