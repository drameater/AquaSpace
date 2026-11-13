#!/usr/bin/env python3
"""Build a local AquaSpace app and CLI, retaining AeroSpace's icon and config format.

By default this stages a signed app under .debug/aquaspace/release.
--install-and-restart explicitly invokes the serialized updater afterward.
Requires Swift (Command Line Tools are enough), git, and the macOS tools sips,
iconutil, lipo and codesign. No other installed app is used.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from aquaspace_signing import SigningError, local_identity, sign_app
from aquaspace_update import ExchangeUnsupported, exchange_paths

ROOT = Path(__file__).resolve().parents[1]
# Used only when no upstream release tag (v<major>.<minor>.<patch>...) is reachable from HEAD.
FALLBACK_BASE_VERSION = "0.21.3"
BUNDLE_ID = "local.aquaspace"
# SwiftPM products keep upstream's names; the bundle gives them AquaSpace's names.
APP_PRODUCT, CLI_PRODUCT = "AeroSpaceApp", "aerospace"
APP_EXECUTABLE = "AquaSpace"
# AquaSpace and aquaspace collide on the default case-insensitive filesystem.
CLI_EXECUTABLE = "aquaspace-cli"
LEGACY_CLI_TARGET = "../MacOS/" + CLI_EXECUTABLE
INFO_TEMPLATE = "script/aquaspace-Info.plist"
ICON_SOURCE = "resources/Assets.xcassets/AppIcon.appiconset/icon.png"
DEFAULT_CONFIG = "docs/config-examples/default-config.toml"
# Every input that reaches the bundle. A change to one of them marks the version dirty.
BUILD_INPUTS = ("Package.swift", "Package.resolved", "Sources", INFO_TEMPLATE, ICON_SOURCE, DEFAULT_CONFIG)
REQUIRED_TOOLS = ("swift", "git", "/usr/bin/sips", "/usr/bin/iconutil", "/usr/bin/lipo", "/usr/bin/codesign")
ICON_SIZES = (16, 32, 128, 256, 512)
# Each Finder icon size at 1x and 2x, with its edge length in pixels.
ICONSET_MEMBERS = {f"icon_{points}x{points}{suffix}.png": points * scale
                   for points in ICON_SIZES for scale, suffix in ((1, ""), (2, "@2x"))}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# --version is written into Swift source and Info.plist. These characters need no
# escape in a Swift or JSON string literal, a plist, or a file name.
VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
LOCK_NAME = ".aquaspace-build.lock"
UNSIGNED_LAYOUT = frozenset((
    "Contents",
    "Contents/Info.plist",
    "Contents/PkgInfo",
    "Contents/MacOS",
    "Contents/MacOS/" + APP_EXECUTABLE,
    "Contents/MacOS/" + CLI_EXECUTABLE,
    "Contents/Helpers",
    "Contents/Helpers/aerospace",
    "Contents/Resources",
    "Contents/Resources/AppIcon.icns",
    "Contents/Resources/default-config.toml",
))
SIGNED_LAYOUT = UNSIGNED_LAYOUT | {"Contents/_CodeSignature", "Contents/_CodeSignature/CodeResources"}


class BuildError(RuntimeError):
    pass


def run(args, env=None, capture=True):
    """Run a command; return its standard output when captured. Tests replace this function."""
    args = [str(a) for a in args]
    try:
        result = subprocess.run(args, env=env, check=True, text=True, capture_output=capture)
    except FileNotFoundError as error:
        raise BuildError(f"Cannot run {args[0]}: {error.strerror}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        raise BuildError(f"{' '.join(args[:2])} failed with exit code {error.returncode}" +
                         (f": {detail}" if detail else "")) from error
    return result.stdout if capture else ""


def git(root, *args):
    return run(["git", "-C", root, *args]).strip()


class BuildVersion:
    def __init__(self, short, bundle, revision, dirty):
        self.short, self.bundle, self.revision, self.dirty = short, bundle, revision, dirty


def derive_version(root, override=None, now=time.time):
    """Derive CFBundleShortVersionString and CFBundleVersion from git.

    A clean tree gives the same versions at every build of a commit, for example
    0.21.3-aquaspace.71.g79e53f21 (upstream tag, commits since the tag, commit).
    A tree with uncommitted build inputs adds ".dirty". CFBundleVersion is the
    commit time in UTC, or the build time for a dirty tree, so each newer
    commit or later dirty build gets a higher number.
    """
    revision = git(root, "rev-parse", "HEAD")
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=all", "--", *BUILD_INPUTS))
    try:
        tag = git(root, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD")
    except BuildError:
        tag = ""  # No tags in this checkout.
    release = re.match(r"v(\d+\.\d+\.\d+)", tag)
    if release:
        distance = git(root, "rev-list", "--count", f"refs/tags/{tag}..HEAD")
        short = f"{release[1]}-aquaspace.{distance}.g{revision[:8]}"
    else:
        short = f"{FALLBACK_BASE_VERSION}-aquaspace.g{revision[:8]}"
    if dirty:
        short += ".dirty"
    seconds = now() if dirty else int(git(root, "show", "-s", "--format=%ct", "HEAD"))
    bundle = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y%m%d%H%M%S")
    return BuildVersion(override or short, bundle, revision, dirty)


# Old version mechanism: generated Swift files compiled into both binaries.
# Upstream commit 78346bcd replaces them with a __TEXT,__aero_build Mach-O section
# that Sources/Common/buildInfo.swift reads. Replace these two names and the
# function below when the fork adopts that commit.
LEGACY_GENERATED_SOURCES = ("versionGenerated.swift", "gitHashGenerated.swift")


def write_legacy_version_sources(common, version, revision, digest):
    """Write the generated version files into the staged Common module; return the git hash they embed."""
    git_hash = revision + "-aquaspace-" + digest
    generated = {"versionGenerated.swift": "public let aeroSpaceAppVersion = " + json.dumps(version) + "\n",
                 "gitHashGenerated.swift": "public let gitHash = " + json.dumps(git_hash) + "\n" +
                 "public let gitShortHash = " + json.dumps(revision[:8] + "-aqua-" + digest[:6]) + "\n"}
    for name, text in generated.items():
        path = common / name
        # Unchanged files keep their timestamps, so incremental builds stay incremental.
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
    return git_hash


def stage_sources(root, source):
    """Mirror the package into source; return the Swift source manifest and its short digest."""
    source.mkdir(parents=True, exist_ok=True)
    for name in ("Package.swift", "Package.resolved"):
        shutil.copy2(root / name, source / name)
    if (source / "Sources").exists():
        # Remove deleted source files too, so incremental builds cannot retain old code.
        for old in (source / "Sources").rglob("*"):
            if old.is_file() and not (root / old.relative_to(source)).exists():
                old.unlink()
    shutil.copytree(root / "Sources", source / "Sources", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*LEGACY_GENERATED_SOURCES))
    manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (root / "Sources").rglob("*.swift") if "AppBundleTests" not in str(p)}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
    return manifest, digest


def compile_products(source, scratch, sdk=None, jobs=2, sandbox=True):
    """Compile the app and CLI in release configuration; return the binary directory."""
    flags = ["-c", "release", "--jobs", str(jobs), "--disable-automatic-resolution", "--skip-update",
             "--package-path", source, "--scratch-path", scratch]
    if not sandbox:
        flags.append("--disable-sandbox")
    if sdk:
        flags += ["--sdk", sdk]
    env = dict(os.environ)
    env.setdefault("CLANG_MODULE_CACHE_PATH", str(Path(scratch) / "clang-cache"))
    env.setdefault("SWIFTPM_MODULECACHE_OVERRIDE", str(Path(scratch) / "swift-cache"))
    # Build the app and CLI in one graph; switching --product graphs can rebuild
    # shared dependencies unnecessarily with newer Swift package builders.
    run(["swift", "build", *flags], env=env, capture=False)
    return Path(run(["swift", "build", *flags, "--show-bin-path"], env=env).strip())


def info_plist(template, short_version, bundle_version):
    metadata = plistlib.loads(template)
    metadata.update(CFBundleShortVersionString=short_version, CFBundleVersion=bundle_version)
    return plistlib.dumps(metadata)


def png_size(path):
    """Return a PNG file's (width, height) from its IHDR header, or None if it is not a PNG."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def check_png(path, pixels, what):
    size = png_size(path)
    if size != (pixels, pixels):
        found = "is not a readable PNG image" if size is None else f"is {size[0]}x{size[1]} pixels"
        raise BuildError(f"{what} {path} {found}; expected {pixels}x{pixels} pixels.")


def make_icon(png, icns, work):
    """Render every Finder icon size from the asset catalog's 1024-pixel image.

    sips exits 0 when it skips an unreadable input, and iconutil packs an
    incomplete iconset, so check each image and unpack the result again.
    """
    largest = max(ICONSET_MEMBERS.values())
    size = png_size(png)
    if size is None or size[0] != size[1] or size[0] < largest:
        found = "is not a readable PNG image" if size is None else f"is {size[0]}x{size[1]} pixels"
        raise BuildError(f"The icon source {png} {found}; expected a square PNG of at least {largest}x{largest} pixels.")
    iconset = Path(tempfile.mkdtemp(dir=work)) / "AppIcon.iconset"
    iconset.mkdir()
    for name, pixels in ICONSET_MEMBERS.items():
        run(["/usr/bin/sips", "-z", pixels, pixels, png, "--out", iconset / name])
        check_png(iconset / name, pixels, "The rendered icon")
    run(["/usr/bin/iconutil", "-c", "icns", iconset, "-o", icns])
    unpacked = Path(tempfile.mkdtemp(dir=work)) / "check.iconset"
    run(["/usr/bin/iconutil", "-c", "iconset", icns, "-o", unpacked])
    for name, pixels in ICONSET_MEMBERS.items():
        check_png(unpacked / name, pixels, f"The icon file {icns} has a bad member:")


def assemble_bundle(app, binaries, root, version, work):
    """Create an unsigned AquaSpace.app at app from the compiled products and repository resources."""
    contents = app / "Contents"
    for name in ("MacOS", "Helpers", "Resources"):
        (contents / name).mkdir(parents=True)
    shutil.copy2(binaries / APP_PRODUCT, contents / "MacOS" / APP_EXECUTABLE)
    shutil.copy2(binaries / CLI_PRODUCT, contents / "MacOS" / CLI_EXECUTABLE)
    # Config callbacks that call `aerospace` find this fork's CLI through Helpers.
    (contents / "Helpers/aerospace").symlink_to(LEGACY_CLI_TARGET)
    shutil.copy2(root / DEFAULT_CONFIG, contents / "Resources/default-config.toml")
    make_icon(root / ICON_SOURCE, contents / "Resources/AppIcon.icns", work)
    (contents / "Info.plist").write_bytes(info_plist((root / INFO_TEMPLATE).read_bytes(), version.short, version.bundle))
    (contents / "PkgInfo").write_text("APPL????")


def validate_bundle(app, version, git_hash, arch, signed):
    """Fail unless the bundle has the expected layout, metadata, architecture and build identity."""
    expected = SIGNED_LAYOUT if signed else UNSIGNED_LAYOUT
    layout = {str(p.relative_to(app)) for p in app.rglob("*")}
    if layout != expected:
        raise BuildError(f"Unexpected layout in {app}. Missing: {sorted(expected - layout)}. "
                         f"Unexpected: {sorted(layout - expected)}.")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    for key, value in (("CFBundleIdentifier", BUNDLE_ID), ("CFBundleExecutable", APP_EXECUTABLE),
                       ("CFBundleShortVersionString", version)):
        if info.get(key) != value:
            raise BuildError(f"Info.plist {key} is {info.get(key)!r}; expected {value!r}.")
    link = app / "Contents/Helpers/aerospace"
    if not link.is_symlink() or os.readlink(link) != LEGACY_CLI_TARGET:
        raise BuildError(f"{link} must be a symlink to {LEGACY_CLI_TARGET}.")
    for name in (APP_EXECUTABLE, CLI_EXECUTABLE):
        binary = app / "Contents/MacOS" / name
        if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise BuildError(f"{binary} is not a regular executable file.")
        archs = run(["/usr/bin/lipo", "-archs", binary]).split()
        if archs != [arch]:
            raise BuildError(f"{binary} has architectures {archs}; expected [{arch!r}].")
        data = binary.read_bytes()
        for label, value in (("version", version), ("git hash", git_hash)):
            if value.encode() not in data:
                raise BuildError(f"{binary} does not contain the {label} {value!r}.")


def validate_signature(app, requirement):
    """Check the signing step's result: a certificate-bound identity that verifies strictly."""
    if f'identifier "{BUNDLE_ID}"' not in requirement or "certificate" not in requirement or "cdhash" in requirement:
        raise BuildError(f"Unexpected designated requirement for {app}: {requirement}")
    run(["/usr/bin/codesign", "--verify", "--strict", app])
    run(["/usr/bin/codesign", "--verify", "--strict", app / "Contents/MacOS" / CLI_EXECUTABLE])


def publish(staged, app):
    """Put staged at app. At no moment is app absent, unless the volume cannot exchange paths.

    After an exchange, staged holds the previous bundle; the caller removes it.
    Without exchange support, two renames are used: an interruption restores the
    previous bundle, and after a crash the next build restores it.
    """
    if not (app.exists() or app.is_symlink()):
        os.rename(staged, app)
        return
    try:
        exchange_paths(staged, app)
        return
    except ExchangeUnsupported:
        pass
    previous = staged.parent / "previous.app"
    try:
        os.rename(app, previous)
        os.rename(staged, app)
    except BaseException:
        if not (app.exists() or app.is_symlink()) and previous.is_dir():
            os.rename(previous, app)
        raise


def restore_stranded_build(output):
    """Put back a previous build that a crash left between the two renames of publish."""
    app = output / "AquaSpace.app"
    if app.exists() or app.is_symlink():
        return
    stranded = [p for p in output.glob(".staging-*/previous.app") if p.is_dir() and not p.is_symlink()]
    if len(stranded) > 1:
        raise BuildError(f"{app} is missing and several earlier builds are left in {output}: "
                         + ", ".join(map(str, stranded)) + ". Move one of them to " + str(app) + ", then try again.")
    if stranded:
        os.rename(stranded[0], app)
        print(f"Restored the previous build that an interrupted build left at {stranded[0]}.", file=sys.stderr)


class BuildLock:
    """A flock on a file that is never unlinked, so that two builds cannot overlap."""

    def __init__(self, directory, name=LOCK_NAME):
        self.path, self.fd = Path(directory) / name, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise BuildError(f"The build lock {self.path} has an unexpected owner or file type.")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BuildError) as error:
            self.__exit__()
            if isinstance(error, BuildError):
                raise
            raise BuildError(f"Another AquaSpace build is using {self.path.parent}. "
                             "Wait for it to finish, then try again.") from error
        return self

    def __exit__(self, *args):
        # Never unlink the lock file: a new inode would let a second build in.
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def build(args, root):
    missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if missing:
        raise BuildError("Missing required tools: " + ", ".join(missing))
    identity, keychain = ((args.signing_identity, args.signing_keychain) if args.signing_identity else local_identity())
    source = root / ".debug/aquaspace/source"
    # Builds share the staged source copy, and builds into one output share its staging.
    with BuildLock(source.parent, ".aquaspace-source.lock"), BuildLock(args.output):
        return build_locked(args, root, identity, keychain, source)


def build_locked(args, root, identity, keychain, source):
    version = derive_version(root, args.version)
    manifest, digest = stage_sources(root, source)
    git_hash = write_legacy_version_sources(source / "Sources/Common", version.short, version.revision, digest)
    restore_stranded_build(args.output)
    for leftover in args.output.glob(".staging-*"):
        shutil.rmtree(leftover, ignore_errors=True)  # From an interrupted build; this build holds the lock.
    binaries = compile_products(source, args.scratch_path, args.sdk, args.jobs, not args.disable_sandbox)
    work = Path(tempfile.mkdtemp(prefix=".staging-", dir=args.output))
    try:
        staged = work / "AquaSpace.app"
        assemble_bundle(staged, binaries, root, version, work)
        validate_bundle(staged, version.short, git_hash, platform.machine(), signed=False)
        requirement = sign_app(staged, identity, keychain)
        validate_bundle(staged, version.short, git_hash, platform.machine(), signed=True)
        validate_signature(staged, requirement)
        macos = staged / "Contents/MacOS"
        (work / "build.json").write_text(json.dumps({
            "version": version.short, "bundle_version": version.bundle, "revision": version.revision,
            "dirty": version.dirty, "source_sha256": manifest,
            "app_sha256": hashlib.sha256((macos / APP_EXECUTABLE).read_bytes()).hexdigest(),
            "cli_sha256": hashlib.sha256((macos / CLI_EXECUTABLE).read_bytes()).hexdigest(),
            "designated_requirement": requirement}, indent=2) + "\n")
        app = args.output / "AquaSpace.app"
        publish(staged, app)
        # An interruption here leaves a manifest that the installer rejects as mismatched.
        os.replace(work / "build.json", args.output / "build.json")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return app


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--scratch-path", type=Path, default=root / ".debug/aquaspace/build")
    parser.add_argument("--output", type=Path, default=root / ".debug/aquaspace/release")
    parser.add_argument("--version", help="Override CFBundleShortVersionString; by default it is derived from git")
    parser.add_argument("--jobs", type=int, default=2, help="Limit compilation workers while using the machine")
    parser.add_argument("--disable-sandbox", action="store_true",
                        help="Evaluate package manifests without SwiftPM's sandbox. Use it only when this "
                             "script runs inside another sandbox that does not allow nested sandboxes")
    parser.add_argument("--signing-identity", help="Persistent code-signing certificate; defaults to AquaSpace's local identity")
    parser.add_argument("--signing-keychain", type=Path, help="Keychain containing the explicit signing identity")
    parser.add_argument("--install-and-restart", action="store_true", help="After building, use the serialized installer to replace and restart the sole installed AquaSpace")
    args = parser.parse_args(argv)
    if args.signing_keychain and not args.signing_identity:
        parser.error('--signing-keychain requires --signing-identity')
    if args.signing_identity == '-':
        parser.error('Ad-hoc signing resets privacy grants; use a persistent signing certificate.')
    if args.version is not None and not VERSION_PATTERN.fullmatch(args.version):
        parser.error('--version must be 1 to 64 characters: ASCII letters, digits, ".", "_", "+" and "-", '
                     'starting with a letter or digit.')
    try:
        app = build(args, root)
    except (BuildError, SigningError, OSError) as error:
        parser.exit(1, f"AquaSpace build stopped: {error}\n")
    print(f"Built {app}; CLI: {app / 'Contents/MacOS' / CLI_EXECUTABLE}")
    if args.install_and_restart:
        return run_installer([sys.executable, str(root / "script/install-aquaspace.py"),
                              "--source", str(app), "--restart"])


def run_installer(arguments):
    """Run the installer and return its exit status unchanged; it prints its own messages.

    Ctrl-C reaches the installer directly, because it is in the same process
    group. Wait for it to report what remains: never kill it while it may be
    changing files (subprocess.run would send SIGKILL after 0.25 s).
    """
    sys.stdout.flush()
    process = subprocess.Popen(arguments)
    while True:
        try:
            code = process.wait()
            break
        except KeyboardInterrupt:
            continue
    return code if code >= 0 else 128 - code  # Killed by a signal: report it as a shell does.


if __name__ == "__main__":
    sys.exit(main())
