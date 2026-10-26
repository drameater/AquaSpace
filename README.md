# AquaSpace

<img src="resources/Assets.xcassets/AppIcon.appiconset/icon.png" alt="AquaSpace icon" width="96">

AquaSpace is a fork of [AeroSpace](https://github.com/nikitabobko/AeroSpace). It keeps native macOS
tab groups together across workspaces while preserving AeroSpace's keyboard-driven
tiling, configuration format and original icon.

## What changes

- Native tabs share one workspace slot. Selecting another tab keeps the group on
  its assigned workspace.
- Merging groups adopts the destination's workspace; detached tabs can move
  independently.
- Identity-checked session transfer preserves workspace assignments when switching
  between AquaSpace and stock AeroSpace, including windows discovered later.
- The local updater waits for the old AquaSpace to exit before starting its
  replacement. Failed updates retain a recovery record and never launch a rollback
  manager automatically.

The [validation notes](dev-docs/aquaspace.md) record the exact releases and cases
checked in Finder, Ghostty and AppKit fixtures, plus offline regressions. AquaSpace
uses ordinary macOS Accessibility permission. It requires no sudo, SIP changes
or code injection.

## Build and install

You need macOS, Python 3, Git, a Swift toolchain compatible with
[Package.swift](Package.swift), and a compatible macOS SDK from Xcode or the
Command Line Tools. The [development guide](dev-docs/development.md) covers
additional tooling. Keep `/Applications/AeroSpace.app` installed: the local
builder copies its icon and bundle resources, then gives AquaSpace its separate
application identity. `--base-app` can select another local resource donor.

```sh
git clone https://github.com/drameater/AquaSpace.git
cd AquaSpace
python3 script/aquaspace_signing.py --setup
./build-release.sh
```

The one-time signing setup creates a persistent local certificate in a dedicated
private keychain. Reusing that certificate lets macOS remember AquaSpace's
permissions across builds. Keep `~/Library/Application Support/AquaSpace/Signing`;
the builder refuses to silently replace a missing identity or sign ad hoc.

This stages a certificate-signed bundle at
`.debug/aquaspace/release/AquaSpace.app` without installing or launching it.
If you need a specific SDK, pass `--sdk /path/to/MacOSX.sdk`; see the
[SDK and build options](dev-docs/aquaspace.md#build-locally).

For the first installation, quit any running window manager, then install the
staged build:

```sh
./install-from-sources.sh --dont-rebuild
```

This installs and starts `/Applications/AquaSpace.app`. Grant its ordinary
Accessibility permission in System Settings (called **Device Control and Data
Access** on some macOS versions). Stock `/Applications/AeroSpace.app` remains
installed separately. The upstream Homebrew cask installs stock AeroSpace;
it does not install this fork.

### Updates

```sh
./install-from-sources.sh
```

The updater builds, saves the sole installed AquaSpace's session, waits for it to
exit, then starts the replacement once. It refuses to update while stock
AeroSpace or another manager is running. Pass build options such as `--sdk` as
needed, or use `--dont-rebuild` after staging a build yourself. See
[update and recovery details](dev-docs/aquaspace.md#updating-aquaspace).

## Configuration and commands

AquaSpace keeps these existing config locations:

- `~/.aerospace.toml`
- `$XDG_CONFIG_HOME/aerospace/aerospace.toml` (normally
  `~/.config/aerospace/aerospace.toml`)

Your existing keybindings and `AEROSPACE_*` callback variables remain compatible.
The app is **AquaSpace.app**, the command is **aquaspace**, and its bundle ID is
`local.aquaspace`.

The bundled CLI is `/Applications/AquaSpace.app/Contents/MacOS/aquaspace-cli`.
If `aquaspace` is not on your PATH, use that full path or create a symlink named
`aquaspace` in a directory on your PATH. The installer does not add a global CLI
symlink. Stock `aerospace` and the fork's `aquaspace` address separate managers.
AquaSpace supplies a bundled `aerospace` compatibility alias to its own config
callbacks; external scripts and absolute CLI paths should explicitly use the
manager they intend to control.

The upstream [configuration guide](https://nikitabobko.github.io/AeroSpace/guide)
and [command reference](https://nikitabobko.github.io/AeroSpace/commands) remain
useful for the shared configuration and command syntax.

## Switching managers

Open **AquaSpace** normally to take over from stock AeroSpace. To switch back,
choose **Switch to AeroSpace** in AquaSpace's menu or run:

```sh
aquaspace switch-to-aerospace
```

Use this explicit switch so AquaSpace freezes and drains its window writes before
stock starts. Directly launching unmodified stock while AquaSpace is running has
a notification/polling race. Session transfer preserves workspace, root-layout,
floating/fullscreen and focus state; exact nested container structure and weights
are rebuilt across managers.

Startup failures report a readable reason at
`~/Library/Logs/AquaSpace/last-failure.txt`, with stderr and Console fallbacks.

## Checks and development

GitHub Actions is **manual-only**. Pushes, pull requests, issues and schedules do
not start jobs. The single optional workflow runs the offline Python suite on one
Linux runner with a five-minute limit. It does not build or launch either manager.
Run the same checks locally with Python's standard library:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s script/tests -v
```

Swift model/ownership regressions and build instructions are in the
[development guide](dev-docs/development.md). Live harnesses require explicit
foreground-testing authorization and are separate from offline checks.

## Attribution and license

AquaSpace builds on AeroSpace by [@nikitabobko](https://github.com/nikitabobko)
and its contributors. The original icon and upstream attribution are retained.
Licensed under the [MIT license](LICENSE.txt); third-party notices are in
[legal](legal/README.md). To support the upstream work, see
[AeroSpace sponsorship](https://github.com/sponsors/nikitabobko).
