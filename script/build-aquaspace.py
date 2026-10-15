#!/usr/bin/env python3
"""Build a local AquaSpace app and CLI, retaining AeroSpace's icon and config format.

By default this stages a signed app under .debug/aquaspace/release.
--install-and-restart explicitly invokes the serialized updater afterward.
Requires Swift/Command Line Tools and an existing AeroSpace app as the icon/resource donor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--scratch-path", type=Path, default=root / ".debug/aquaspace/build")
    parser.add_argument("--output", type=Path, default=root / ".debug/aquaspace/release")
    parser.add_argument("--base-app", type=Path, default=Path("/Applications/AeroSpace.app"))
    parser.add_argument("--version", default="0.21.3-aquaspace.20261014.2")
    parser.add_argument("--jobs", type=int, default=2, help="Limit compilation workers while using the machine")
    parser.add_argument("--install-and-restart", action="store_true", help="After building, use the serialized installer to replace and restart the sole installed AquaSpace")
    args = parser.parse_args()
    source = root / ".debug/aquaspace/source"
    source.mkdir(parents=True, exist_ok=True)
    for name in ("Package.swift", "Package.resolved"):
        shutil.copy2(root / name, source / name)
    if (source / "Sources").exists():
        # Remove deleted source files too, so incremental builds cannot retain old code.
        for old in (source / "Sources").rglob("*"):
            if old.is_file() and not (root / old.relative_to(source)).exists():
                old.unlink()
    shutil.copytree(root / "Sources", source / "Sources", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("versionGenerated.swift", "gitHashGenerated.swift"))
    manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (root / "Sources").rglob("*.swift") if "AppBundleTests" not in str(p)}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    generated = {"versionGenerated.swift": "public let aeroSpaceAppVersion = " + json.dumps(args.version) + "\n",
                 "gitHashGenerated.swift": "public let gitHash = " + json.dumps(revision + "-aquaspace-" + digest) + "\n" +
                 "public let gitShortHash = " + json.dumps(revision[:8] + "-aqua-" + digest[:6]) + "\n"}
    for name, text in generated.items():
        path = source / "Sources/Common" / name
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
    flags = ["-c", "release", "--jobs", str(args.jobs), "--disable-sandbox", "--disable-automatic-resolution", "--skip-update",
             "--package-path", str(source), "--scratch-path", str(args.scratch_path)]
    if args.sdk:
        flags += ["--sdk", str(args.sdk)]
    env = dict(os.environ)
    env.setdefault("CLANG_MODULE_CACHE_PATH", str(args.scratch_path / "clang-cache"))
    env.setdefault("SWIFTPM_MODULECACHE_OVERRIDE", str(args.scratch_path / "swift-cache"))
    # Build the app and CLI in one graph; switching --product graphs can rebuild
    # shared dependencies unnecessarily with newer Swift package builders.
    subprocess.run(["swift", "build", *flags], check=True, env=env)
    binaries = Path(subprocess.check_output(["swift", "build", *flags, "--show-bin-path"], text=True, env=env).strip())
    app = args.output / "AquaSpace.app"
    args.output.mkdir(parents=True, exist_ok=True)
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(args.base_app, app, symlinks=True)
    macos = app / "Contents/MacOS"
    shutil.rmtree(macos)
    macos.mkdir()
    shutil.copy2(binaries / "AeroSpaceApp", macos / "AquaSpace")
    # AquaSpace and aquaspace collide on the default case-insensitive filesystem.
    shutil.copy2(binaries / "aquaspace", macos / "aquaspace-cli")
    helpers = app / "Contents/Helpers"
    helpers.mkdir(exist_ok=True)
    legacy_cli = helpers / "aerospace"
    if legacy_cli.is_symlink() or legacy_cli.exists():
        legacy_cli.unlink()
    legacy_cli.symlink_to("../MacOS/aquaspace-cli")
    shutil.copy2(root / "docs/config-examples/default-config.toml", app / "Contents/Resources/default-config.toml")
    info = app / "Contents/Info.plist"
    metadata = plistlib.loads(info.read_bytes())
    metadata.update(CFBundleIdentifier="local.aquaspace", CFBundleExecutable="AquaSpace",
                    CFBundleName="AquaSpace", CFBundleDisplayName="AquaSpace",
                    CFBundleShortVersionString=args.version, CFBundleVersion="20261014.1")
    info.write_bytes(plistlib.dumps(metadata))
    subprocess.run(["codesign", "--force", "--sign", "-", str(macos / "aquaspace-cli")], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "local.aquaspace", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    (args.output / "build.json").write_text(json.dumps({"version": args.version, "revision": revision,
        "source_sha256": manifest, "app_sha256": hashlib.sha256((macos / "AquaSpace").read_bytes()).hexdigest(),
        "cli_sha256": hashlib.sha256((macos / "aquaspace-cli").read_bytes()).hexdigest()}, indent=2) + "\n")
    print(f"Built {app}; CLI: {macos / 'aquaspace-cli'}")
    if args.install_and_restart:
        subprocess.run([sys.executable, str(root / "script/install-aquaspace.py"),
                        "--source", str(app), "--restart"], check=True)


if __name__ == "__main__":
    main()
