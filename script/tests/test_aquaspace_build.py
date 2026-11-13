"""Offline builder checks: fake git, swift, macOS tools and signer; no compile, keychain or app."""
import contextlib
from datetime import datetime, timezone
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
spec = importlib.util.spec_from_file_location('build_aquaspace', SCRIPT / 'build-aquaspace.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

REVISION = '79e53f21' + '0' * 32
COMMIT_TIME = int(datetime(2026, 10, 26, 23, 3, 44, tzinfo=timezone.utc).timestamp())
REQUIREMENT = 'identifier "local.aquaspace" and certificate leaf = H"1234"'
EXPECTED_LAYOUT = {
    'Contents', 'Contents/Info.plist', 'Contents/PkgInfo',
    'Contents/MacOS', 'Contents/MacOS/AquaSpace', 'Contents/MacOS/aquaspace-cli',
    'Contents/Helpers', 'Contents/Helpers/aerospace',
    'Contents/Resources', 'Contents/Resources/AppIcon.icns', 'Contents/Resources/default-config.toml',
    'Contents/_CodeSignature', 'Contents/_CodeSignature/CodeResources',
}


def png(width, height):
    """A PNG header with these dimensions: enough for every size check."""
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IEND', b'')


def png_size(path):
    data = Path(path).read_bytes()
    return struct.unpack('>II', data[16:24]) if data[:8] == b'\x89PNG\r\n\x1a\n' else None


ICONSET_MEMBERS = {f'icon_{p}x{p}{suffix}.png': p * scale
                   for p in (16, 32, 128, 256, 512) for scale, suffix in ((1, ''), (2, '@2x'))}


def layout(app):
    return {str(p.relative_to(app)) for p in app.rglob('*')}


class FakeTools:
    """Answers git, swift, sips, iconutil, lipo and codesign like a clean checkout on arm64."""

    def __init__(self, root, tag='v0.21.3-Beta', distance='71', dirty=False, arch='arm64'):
        self.root, self.tag, self.distance, self.dirty, self.arch = root, tag, distance, dirty, arch
        self.calls = []
        self.bin = root / 'fake-bin'
        self.sips_skips, self.sips_wrong_size, self.drop_from_icns = set(), set(), set()

    def sips(self, pixels, source, out):
        # Like sips: a missing or unreadable input is skipped with a warning and exit status 0.
        if not source.is_file() or png_size(source) is None or out.name in self.sips_skips:
            return ''
        size = pixels // 2 if out.name in self.sips_wrong_size else pixels
        out.write_bytes(png(size, size))
        return ''

    def __call__(self, args, env=None, capture=True):
        args = [str(a) for a in args]
        self.calls.append(args)
        tool = Path(args[0]).name
        if tool == 'git':
            return self.git(args[3:])
        if tool == 'swift':
            if '--show-bin-path' in args:
                return str(self.bin) + '\n'
            self.compile(Path(args[args.index('--package-path') + 1]))
            return ''
        if tool == 'sips':
            return self.sips(int(args[2]), Path(args[4]), Path(args[6]))
        if tool == 'iconutil':
            if args[2] == 'icns':
                # Like iconutil, pack whatever the iconset holds, complete or not.
                members = {p.name: p.read_bytes().hex() for p in Path(args[3]).iterdir() if p.name not in self.drop_from_icns}
                Path(args[5]).write_text(json.dumps(members))
            else:
                out = Path(args[5]); out.mkdir()
                for name, data in json.loads(Path(args[3]).read_text()).items():
                    (out / name).write_bytes(bytes.fromhex(data))
            return ''
        if tool == 'lipo':
            return self.arch + '\n'
        if tool == 'codesign':
            return ''
        raise AssertionError('Unexpected command: ' + ' '.join(args))

    def git(self, args):
        if args == ['rev-parse', 'HEAD']:
            return REVISION
        if args[:2] == ['status', '--porcelain']:
            return ' M Sources/Common/x.swift\n' if self.dirty else ''
        if args[0] == 'describe':
            if self.tag is None:
                raise build.BuildError('git describe failed with exit code 128: fatal: No names found')
            return self.tag
        if args == ['rev-list', '--count', f'refs/tags/{self.tag}..HEAD']:
            return self.distance
        if args == ['show', '-s', '--format=%ct', 'HEAD']:
            return str(COMMIT_TIME)
        raise AssertionError(f'Unexpected git call: {args}')

    def compile(self, package):
        # A stand-in binary embeds the generated Swift sources, as the compiler embeds their strings.
        common = package / 'Sources/Common'
        text = (common / 'versionGenerated.swift').read_bytes() + (common / 'gitHashGenerated.swift').read_bytes()
        self.bin.mkdir(parents=True, exist_ok=True)
        for name in ('AeroSpaceApp', 'aerospace'):
            (self.bin / name).write_bytes(b'\xcf\xfa\xed\xfe' + text)
            (self.bin / name).chmod(0o755)


def fake_sign(app, identity, keychain=None):
    signature = Path(app) / 'Contents/_CodeSignature'
    signature.mkdir()
    (signature / 'CodeResources').write_text('sealed')
    return REQUIREMENT


class Checkout:
    """A minimal repository root with the inputs the builder reads."""

    def __init__(self, path):
        self.path = path
        real = SCRIPT.parent
        for name in ('Package.swift', 'Package.resolved', 'script/aquaspace-Info.plist',
                     'docs/config-examples/default-config.toml'):
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real / name, path / name)
        icon = path / 'resources/Assets.xcassets/AppIcon.appiconset/icon.png'
        icon.parent.mkdir(parents=True)
        icon.write_bytes(png(1024, 1024))
        common = path / 'Sources/Common'
        common.mkdir(parents=True)
        (common / 'versionGenerated.swift').write_text('public let aeroSpaceAppVersion = "0.0.0-SNAPSHOT"\n')
        (common / 'gitHashGenerated.swift').write_text('public let gitHash = "SNAPSHOT"\n')
        (common / 'appMetadata.swift').write_text('public let stableAeroSpaceAppId = "local.aquaspace"\n')


class BuilderTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='aquaspace-build-offline-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        Checkout(self.root)
        self.tools = FakeTools(self.root)
        self.output = self.root / '.debug/aquaspace/release'
        self.app = self.output / 'AquaSpace.app'
        for target, value in (('run', self.tools), ('sign_app', fake_sign),
                              ('local_identity', lambda: ('FINGERPRINT', Path('/fake/keychain')))):
            patcher = patch.object(build, target, side_effect=value) if target != 'run' else patch.object(build, 'run', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(build.platform, 'machine', return_value='arm64'),
                        patch.object(build.shutil, 'which', side_effect=lambda tool: '/fake/' + tool)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            build.main(['--output', str(self.output), '--scratch-path', str(self.root / 'scratch'), *args], root=self.root)

    def info(self):
        return plistlib.loads((self.app / 'Contents/Info.plist').read_bytes())


class VersionTests(BuilderTest):
    def test_clean_tree_version_is_the_same_at_every_build_of_a_commit(self):
        first = build.derive_version(self.root, now=lambda: COMMIT_TIME + 10)
        second = build.derive_version(self.root, now=lambda: COMMIT_TIME + 99999)
        self.assertEqual(first.short, '0.21.3-aquaspace.71.g79e53f21')
        self.assertEqual((first.short, first.bundle), (second.short, second.bundle))
        self.assertEqual(first.bundle, '20261026230344')
        self.assertFalse(first.dirty)

    def test_dirty_tree_is_marked_and_numbered_after_the_commit(self):
        self.tools.dirty = True
        clean_bundle = '20261026230344'
        version = build.derive_version(self.root, now=lambda: COMMIT_TIME + 3600)
        self.assertEqual(version.short, '0.21.3-aquaspace.71.g79e53f21.dirty')
        self.assertTrue(version.dirty)
        self.assertGreater(int(version.bundle), int(clean_bundle))

    def test_newer_commit_gets_a_higher_bundle_version(self):
        old = build.derive_version(self.root)
        with patch.object(self.tools, 'git', side_effect=lambda args: str(COMMIT_TIME + 60)
                          if args[0] == 'show' else FakeTools.git(self.tools, args)):
            new = build.derive_version(self.root)
        self.assertGreater(int(new.bundle), int(old.bundle))
        self.assertRegex(new.bundle, r'^\d{14}$')

    def test_release_tag_suffix_is_dropped_from_the_base_version(self):
        for tag, base in (('v0.22.0-Beta', '0.22.0'), ('v1.2.3', '1.2.3')):
            with self.subTest(tag=tag):
                self.tools.tag = tag
                self.assertEqual(build.derive_version(self.root).short, base + '-aquaspace.71.g79e53f21')

    def test_checkout_without_tags_uses_the_fallback_base_version(self):
        self.tools.tag = None
        self.assertEqual(build.derive_version(self.root).short,
                         build.FALLBACK_BASE_VERSION + '-aquaspace.g79e53f21')

    def test_override_replaces_only_the_short_version(self):
        version = build.derive_version(self.root, override='local-test')
        self.assertEqual((version.short, version.bundle), ('local-test', '20261026230344'))


class BundleTests(BuilderTest):
    def test_build_stages_a_complete_signed_bundle_without_any_other_app(self):
        self.main()
        self.assertEqual(layout(self.app), EXPECTED_LAYOUT)
        info = self.info()
        self.assertEqual(info['CFBundleIdentifier'], 'local.aquaspace')
        self.assertEqual(info['CFBundleExecutable'], 'AquaSpace')
        self.assertEqual(info['CFBundleIconFile'], 'AppIcon')
        self.assertEqual(info['CFBundleShortVersionString'], '0.21.3-aquaspace.71.g79e53f21')
        self.assertEqual(info['CFBundleVersion'], '20261026230344')
        self.assertIs(info['LSUIElement'], True)
        self.assertEqual(info['LSMinimumSystemVersion'], '13.0')  # Package.swift: .macOS(.v13)
        self.assertNotIn('CFBundleIconName', info)  # No Assets.car is shipped.
        self.assertEqual(os.readlink(self.app / 'Contents/Helpers/aerospace'), '../MacOS/aquaspace-cli')
        self.assertEqual((self.app / 'Contents/PkgInfo').read_text(), 'APPL????')
        self.assertEqual((self.app / 'Contents/Resources/default-config.toml').read_bytes(),
                         (self.root / 'docs/config-examples/default-config.toml').read_bytes())
        self.assertFalse(any('/Applications' in ' '.join(c) for c in self.tools.calls))
        manifest = (self.output / 'build.json').read_text()
        self.assertIn('"designated_requirement": "identifier \\"local.aquaspace\\"', manifest)
        self.assertEqual(list(self.output.glob('.staging-*')), [])

    def test_icon_contains_every_finder_size_from_the_repository_image(self):
        self.main()
        rendered = {(c[2], Path(c[6]).name) for c in self.tools.calls if Path(c[0]).name == 'sips'}
        self.assertEqual(rendered, {
            ('16', 'icon_16x16.png'), ('32', 'icon_16x16@2x.png'), ('32', 'icon_32x32.png'),
            ('64', 'icon_32x32@2x.png'), ('128', 'icon_128x128.png'), ('256', 'icon_128x128@2x.png'),
            ('256', 'icon_256x256.png'), ('512', 'icon_256x256@2x.png'), ('512', 'icon_512x512.png'),
            ('1024', 'icon_512x512@2x.png')})
        sources = {c[4] for c in self.tools.calls if Path(c[0]).name == 'sips'}
        self.assertEqual(sources, {str(self.root / 'resources/Assets.xcassets/AppIcon.appiconset/icon.png')})

    def test_binaries_embed_the_derived_version_and_revision(self):
        self.main()
        for name in ('AquaSpace', 'aquaspace-cli'):
            data = (self.app / 'Contents/MacOS' / name).read_bytes()
            self.assertIn(b'0.21.3-aquaspace.71.g79e53f21', data)
            self.assertIn(REVISION.encode(), data)

    def test_package_sandbox_is_on_unless_disabled(self):
        self.main()
        swift = [c for c in self.tools.calls if Path(c[0]).name == 'swift']
        self.assertTrue(swift)
        self.assertFalse(any('--disable-sandbox' in c for c in swift))
        self.tools.calls.clear()
        self.main('--disable-sandbox')
        swift = [c for c in self.tools.calls if Path(c[0]).name == 'swift']
        self.assertTrue(swift and all('--disable-sandbox' in c for c in swift))


class FailureTests(BuilderTest):
    def stage_previous_build(self):
        (self.app / 'Contents').mkdir(parents=True)
        (self.app / 'Contents/marker').write_text('previous')
        (self.output / 'build.json').write_text('previous manifest')

    def assert_previous_build_kept(self):
        self.assertEqual((self.app / 'Contents/marker').read_text(), 'previous')
        self.assertEqual((self.output / 'build.json').read_text(), 'previous manifest')
        self.assertEqual(list(self.output.glob('.staging-*')), [])

    def assert_stops(self, message, *args):
        with patch('sys.stderr') as stderr, self.assertRaises(SystemExit) as stopped:
            self.main(*args)
        self.assertEqual(stopped.exception.code, 1)
        written = ''.join(c.args[0] for c in stderr.write.call_args_list)
        self.assertRegex(written, message)

    def test_missing_tool_stops_before_signing_identity_or_compile(self):
        self.stage_previous_build()
        build.shutil.which.side_effect = lambda tool: None if tool.endswith('iconutil') else '/fake/' + tool
        self.assert_stops('Missing required tools: /usr/bin/iconutil')
        build.local_identity.assert_not_called()
        self.assertEqual(self.tools.calls, [])
        self.assert_previous_build_kept()

    def test_unconfigured_signing_stops_before_compile(self):
        self.stage_previous_build()
        build.local_identity.side_effect = build.SigningError('Persistent AquaSpace signing is not configured.')
        self.assert_stops('signing is not configured')
        self.assertFalse(any(Path(c[0]).name == 'swift' for c in self.tools.calls))
        self.assert_previous_build_kept()

    def test_failed_compile_keeps_the_previous_build(self):
        self.stage_previous_build()
        def failing_compile(package):
            raise build.BuildError('swift build failed with exit code 1')
        self.tools.compile = failing_compile
        self.assert_stops('swift build failed')
        self.assert_previous_build_kept()

    def test_wrong_architecture_fails_validation_and_keeps_the_previous_build(self):
        self.stage_previous_build()
        self.tools.arch = 'x86_64'
        self.assert_stops(r"AquaSpace has architectures \['x86_64'\]; expected \['arm64'\]")
        build.sign_app.assert_not_called()
        self.assert_previous_build_kept()

    def test_binary_without_the_revision_fails_validation(self):
        self.stage_previous_build()
        original = self.tools.compile
        def stale_compile(package):
            original(package)
            (self.tools.bin / 'aerospace').write_bytes(b'stale binary')
            (self.tools.bin / 'aerospace').chmod(0o755)
        self.tools.compile = stale_compile
        self.assert_stops(r'aquaspace-cli does not contain the version')
        self.assert_previous_build_kept()

    def test_unstable_signature_is_rejected_after_signing(self):
        self.stage_previous_build()
        def ad_hoc_like(app, identity, keychain=None):
            fake_sign(app, identity, keychain)
            return 'identifier "local.aquaspace" and cdhash H"abcd"'
        build.sign_app.side_effect = ad_hoc_like
        self.assert_stops('Unexpected designated requirement')
        self.assert_previous_build_kept()

    def test_failed_strict_verification_is_reported(self):
        self.stage_previous_build()
        original = self.tools.__call__
        def tools(args, env=None, capture=True):
            if Path(str(args[0])).name == 'codesign':
                raise build.BuildError('/usr/bin/codesign --verify failed with exit code 1: invalid signature')
            return original(args, env, capture)
        with patch.object(build, 'run', tools):
            self.assert_stops('invalid signature')
        self.assert_previous_build_kept()

    def test_extra_file_from_signing_fails_layout_validation(self):
        def noisy_sign(app, identity, keychain=None):
            (Path(app) / 'Contents/Resources/stray').write_text('x')
            return fake_sign(app, identity, keychain)
        build.sign_app.side_effect = noisy_sign
        self.assert_stops(r"Unexpected: \['Contents/Resources/stray'\]")
        self.assertFalse(self.app.exists())

    def test_wrong_helper_link_fails_validation(self):
        app = self.root / 'manual.app'
        version = build.derive_version(self.root)
        self.tools.compile(self.root)  # Needs Sources/Common in the fake root.
        build.assemble_bundle(app, self.tools.bin, self.root, version, self.root)
        link = app / 'Contents/Helpers/aerospace'
        link.unlink()
        link.symlink_to('/usr/local/bin/aerospace')
        with self.assertRaisesRegex(build.BuildError, 'must be a symlink to ../MacOS/aquaspace-cli'):
            build.validate_bundle(app, version.short, 'SNAPSHOT', 'arm64', signed=False)


class SourceStagingTests(BuilderTest):
    def test_generated_version_files_change_only_when_their_content_changes(self):
        source = self.root / 'staged'
        build.stage_sources(self.root, source)
        common = source / 'Sources/Common'
        build.write_legacy_version_sources(common, '1.0.0-aquaspace.1.gabc', REVISION, 'digest123456')
        before = (common / 'versionGenerated.swift').stat().st_mtime_ns
        os.utime(common / 'versionGenerated.swift', ns=(before - 10**9, before - 10**9))
        build.stage_sources(self.root, source)
        hash_text = build.write_legacy_version_sources(common, '1.0.0-aquaspace.1.gabc', REVISION, 'digest123456')
        self.assertEqual((common / 'versionGenerated.swift').stat().st_mtime_ns, before - 10**9)
        self.assertIn('1.0.0-aquaspace.1.gabc', (common / 'versionGenerated.swift').read_text())
        self.assertEqual(hash_text, REVISION + '-aquaspace-digest123456')

    def test_deleted_source_files_leave_the_staged_copy(self):
        source = self.root / 'staged'
        extra = self.root / 'Sources/Common/removed.swift'
        extra.write_text('let removed = 1\n')
        build.stage_sources(self.root, source)
        extra.unlink()
        manifest, _ = build.stage_sources(self.root, source)
        self.assertFalse((source / 'Sources/Common/removed.swift').exists())
        self.assertNotIn('Sources/Common/removed.swift', manifest)



class Crash(BaseException):
    """Simulated power loss: no handler or cleanup runs after it."""


class IconTests(FailureTests):
    """Finding 7: a missing or partial icon fails the build with a precise message."""
    ICON = 'resources/Assets.xcassets/AppIcon.appiconset/icon.png'

    def test_missing_source_image_stops_the_build(self):
        self.stage_previous_build()
        (self.root / self.ICON).unlink()
        self.assert_stops(r'icon\.png.*not a readable PNG')
        self.assert_previous_build_kept()

    def test_source_image_that_is_not_a_png_or_too_small_stops_the_build(self):
        for data, message in ((b'not an image', 'not a readable PNG'), (png(512, 512), '512x512'),
                              (png(1024, 768), '1024x768')):
            with self.subTest(message=message):
                (self.root / self.ICON).write_bytes(data)
                self.assert_stops(message)

    def test_size_that_sips_did_not_render_stops_the_build(self):
        self.stage_previous_build()
        self.tools.sips_skips.add('icon_32x32@2x.png')
        self.assert_stops(r'icon_32x32@2x\.png')
        self.assert_previous_build_kept()

    def test_size_with_wrong_pixel_dimensions_stops_the_build(self):
        self.tools.sips_wrong_size.add('icon_256x256.png')
        self.assert_stops(r'icon_256x256\.png.*128x128.*expected 256x256')

    def test_icon_file_missing_a_member_stops_the_build(self):
        self.stage_previous_build()
        self.tools.drop_from_icns.add('icon_512x512@2x.png')
        self.assert_stops(r'AppIcon\.icns.*icon_512x512@2x\.png')
        self.assert_previous_build_kept()

    def test_complete_icon_has_every_member_at_its_size(self):
        self.main()
        check = self.root / 'check.iconset'
        self.tools(['/usr/bin/iconutil', '-c', 'iconset', self.app / 'Contents/Resources/AppIcon.icns', '-o', check])
        self.assertEqual({p.name: png_size(p) for p in check.iterdir()},
                         {name: (pixels, pixels) for name, pixels in ICONSET_MEMBERS.items()})


class PublishTests(FailureTests):
    """Finding 8: no crash leaves the output without a complete build; builds never overlap."""

    def test_publish_replaces_the_previous_build_and_leaves_no_staging(self):
        self.stage_previous_build()
        self.main()
        self.assertEqual(layout(self.app), EXPECTED_LAYOUT)
        self.assertEqual(list(self.output.glob('.staging-*')), [])

    def without_exchange_and_crashing_between_renames(self, crash):
        """Force the two-rename path and stop after the first rename."""
        real = os.rename
        calls = self.renames = []
        def rename(source, target):
            if calls and isinstance(crash, Crash): raise Crash('the builder is gone')
            real(source, target); calls.append(target)
            if len(calls) == 1 and Path(target).name == 'previous.app': raise crash
        unsupported = lambda first, second: (_ for _ in ()).throw(build.ExchangeUnsupported(45, 'unsupported'))
        return (patch.object(build, 'exchange_paths', side_effect=unsupported, create=True),
                patch.object(build.os, 'rename', side_effect=rename))

    def test_interrupt_between_the_two_renames_keeps_a_build_in_place(self):
        self.stage_previous_build()
        no_exchange, rename = self.without_exchange_and_crashing_between_renames(KeyboardInterrupt())
        with no_exchange, rename, self.assertRaises(KeyboardInterrupt):
            with contextlib.redirect_stdout(io.StringIO()):
                build.main(['--output', str(self.output), '--scratch-path', str(self.root / 'scratch')], root=self.root)
        self.assertTrue((self.app / 'Contents').is_dir(), 'The output has no AquaSpace.app')

    def test_next_build_restores_a_previous_build_stranded_by_a_crash(self):
        self.stage_previous_build()
        no_exchange, rename = self.without_exchange_and_crashing_between_renames(Crash())
        real_rmtree = shutil.rmtree
        def frozen(*args, **kwargs):
            if self.renames: raise Crash('the builder is gone')  # No cleanup runs after a power loss.
            return real_rmtree(*args, **kwargs)
        with no_exchange, rename, patch.object(build.shutil, 'rmtree', side_effect=frozen):
            with self.assertRaises(Crash), contextlib.redirect_stdout(io.StringIO()):
                build.main(['--output', str(self.output), '--scratch-path', str(self.root / 'scratch')], root=self.root)
        self.assertFalse(self.app.exists())
        # The next build fails before publishing; the previous build must be back in place.
        def failing_compile(package):
            raise build.BuildError('swift build failed with exit code 1')
        self.tools.compile = failing_compile
        self.assert_stops('swift build failed')
        self.assertEqual((self.app / 'Contents/marker').read_text(), 'previous')
        self.assertEqual(list(self.output.glob('.staging-*')), [])

    def test_a_running_build_blocks_a_second_build_of_the_same_output(self):
        self.stage_previous_build()
        lock = self.output / '.aquaspace-build.lock'
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        leftover = self.output / '.staging-other-build'
        (leftover / 'AquaSpace.app').mkdir(parents=True)
        self.assert_stops('Another AquaSpace build')
        self.assertFalse(any(Path(c[0]).name == 'swift' for c in self.tools.calls))
        self.assertTrue(leftover.is_dir(), "The other build's staging directory was deleted")
        self.assert_previous_build_kept_except(leftover)

    def test_a_running_build_blocks_a_second_build_that_shares_the_source_copy(self):
        lock = self.root / '.debug/aquaspace/.aquaspace-source.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.output = self.root / 'other-output'
        self.assert_stops('Another AquaSpace build')
        self.assertFalse(any(Path(c[0]).name == 'swift' for c in self.tools.calls))

    def test_build_lock_file_is_kept_after_the_build(self):
        self.main()
        lock = self.output / '.aquaspace-build.lock'
        inode = lock.stat().st_ino
        self.main()
        self.assertEqual(lock.stat().st_ino, inode)

    def assert_previous_build_kept_except(self, leftover):
        self.assertEqual((self.app / 'Contents/marker').read_text(), 'previous')
        self.assertEqual((self.output / 'build.json').read_text(), 'previous manifest')


class InstallerExitStatusTests(BuilderTest):
    """Finding 6: the installer's exit status reaches the caller unchanged, with no traceback."""

    def fake_installer(self, code):
        script = self.root / 'script/install-aquaspace.py'
        script.parent.mkdir(parents=True, exist_ok=True)
        record = self.root / 'installer-arguments'
        script.write_text(f'import json, sys\nopen({str(record)!r}, "w").write(json.dumps(sys.argv[1:]))\n'
                          f'sys.exit({code})\n')
        return record

    def install(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = build.main(['--output', str(self.output), '--scratch-path', str(self.root / 'scratch'),
                               '--install-and-restart'], root=self.root)
        return code, err.getvalue()

    def test_installer_status_is_returned_unchanged(self):
        for status in (0, 1, 3, 130):
            with self.subTest(status=status):
                record = self.fake_installer(status)
                code, err = self.install()
                self.assertEqual(code or 0, status)
                self.assertNotIn('Traceback', err)
                self.assertEqual(json.loads(record.read_text()), ['--source', str(self.app), '--restart'])

    def test_interrupt_waits_for_the_installer_instead_of_killing_it(self):
        self.fake_installer(130)
        real_popen = build.subprocess.Popen
        class InterruptedOnce(real_popen):
            interrupted = False
            def wait(self, timeout=None):
                if not InterruptedOnce.interrupted:
                    InterruptedOnce.interrupted = True
                    raise KeyboardInterrupt
                return super().wait(timeout)
            def kill(self):
                raise AssertionError('The builder must never kill the installer')
        with patch.object(build.subprocess, 'Popen', InterruptedOnce):
            code, err = self.install()
        self.assertEqual(code, 130)
        self.assertNotIn('Traceback', err)


if __name__ == '__main__':
    unittest.main()
