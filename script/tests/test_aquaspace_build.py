"""Offline builder checks: fake git, swift, macOS tools and signer; no compile, keychain or app."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
spec = importlib.util.spec_from_file_location('build_aquaspace', SCRIPT / 'build-aquaspace.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
import aquaspace_signing as signing
import aquaspace_update as update

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


def layout(app):
    return {str(p.relative_to(app)) for p in app.rglob('*')}


class FakeTools:
    """Answers git, swift, sips, iconutil, lipo and codesign like a clean checkout on arm64."""

    def __init__(self, root, tag='v0.21.3-Beta', distance='71', dirty=False, arch='arm64'):
        self.root, self.tag, self.distance, self.dirty, self.arch = root, tag, distance, dirty, arch
        self.calls = []
        self.bin = root / 'fake-bin'

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
            shutil.copyfile(args[4], args[6])
            return ''
        if tool == 'iconutil':
            Path(args[5]).write_bytes(b'icns')
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
                     'docs/config-examples/default-config.toml',
                     'resources/Assets.xcassets/AppIcon.appiconset/icon.png'):
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real / name, path / name)
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
        # The installer replaces the builder process; record the call instead.
        self.exec_calls = []
        for patcher in (patch.object(build.platform, 'machine', return_value='arm64'),
                        patch.object(build.shutil, 'which', side_effect=lambda tool: '/fake/' + tool),
                        patch.object(build.os, 'execv', side_effect=lambda *call: self.exec_calls.append(call))):
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


class InstallerOptionTests(BuilderTest):
    def installer_command(self, *options):
        return [sys.executable, str(self.root / 'script/install-aquaspace.py'), '--source', str(self.app), '--restart',
                *options]

    def test_install_and_restart_replaces_the_builder_with_the_installer(self):
        self.main('--install-and-restart')
        self.assertEqual(self.exec_calls, [(sys.executable, self.installer_command())])

    def test_installer_options_are_forwarded(self):
        self.main('--install-and-restart', '--ready-timeout', '120', '--allow-identity-change')
        self.assertEqual(self.exec_calls, [(sys.executable, self.installer_command(
            '--allow-identity-change', '--ready-timeout', '120'))])

    def test_build_without_install_and_restart_starts_no_installer(self):
        self.main()
        self.assertEqual(self.exec_calls, [])

    def test_invalid_installer_options_stop_before_signing_or_compile(self):
        for options in (['--allow-identity-change'], ['--ready-timeout', '60'],
                        ['--install-and-restart', '--ready-timeout', '0'],
                        ['--install-and-restart', '--ready-timeout', '-5'],
                        ['--install-and-restart', '--ready-timeout', '3601'],
                        ['--install-and-restart', '--ready-timeout', 'soon']):
            with self.subTest(options=options):
                with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    self.main(*options)
                self.assertEqual(stopped.exception.code, 2)
                build.local_identity.assert_not_called()
                self.assertEqual(self.tools.calls, [])
                self.assertEqual(self.exec_calls, [])

    def test_builder_and_installer_reject_the_same_ready_timeouts(self):
        for value in ('0', '-5', '3601', 'soon'):
            with self.subTest(value=value), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    update.main(['--restart', '--ready-timeout', value])  # Stops while parsing.
                self.assertEqual(stopped.exception.code, 2)
        for value in ('0.5', '3600'):
            self.assertEqual(build.ready_timeout(value), value)

    def test_failed_build_starts_no_installer(self):
        def failing_compile(package):
            raise build.BuildError('swift build failed with exit code 1')
        self.tools.compile = failing_compile
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as stopped:
            self.main('--install-and-restart', '--allow-identity-change')
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(self.exec_calls, [])


class FakeCodesign:
    """Answers codesign like a certificate signature whose leaf is the signing identity."""

    def __call__(self, args, **kwargs):
        args = [str(a) for a in args]
        if args[0] != signing.CODESIGN:
            raise AssertionError('Unexpected command: ' + ' '.join(args))
        output = ''
        if args[1] == '--force':
            target = Path(args[-1])
            if target.name.endswith('.app'):
                (target / 'Contents/_CodeSignature').mkdir()
                (target / 'Contents/_CodeSignature/CodeResources').write_text(args[args.index('--sign') + 1])
        elif args[1] == '-d':
            leaf = (Path(args[-1]) / 'Contents/_CodeSignature/CodeResources').read_text()
            output = f'Executable={args[-1]}\ndesignated => identifier "local.aquaspace" and certificate leaf = H"{leaf}"\n'
        elif args[1] != '--verify':
            raise AssertionError('Unexpected codesign call: ' + ' '.join(args))
        if not kwargs.get('text'):
            output = output.encode()
        return subprocess.CompletedProcess(args, 0, output, '' if kwargs.get('text') else b'')


class ManifestSystem:
    """No running manager; the bundle checks are the updater's real ones."""
    CODESIGN = update.NativeSystem.CODESIGN
    run = staticmethod(update.NativeSystem.run)
    verify_bundle = update.NativeSystem.verify_bundle
    requirement = update.NativeSystem.requirement

    def managers(self):
        return []


class ManifestCompatibilityTests(BuilderTest):
    """The updater accepts the manifest that the builder writes, and only that one."""

    def setUp(self):
        super().setUp()
        build.sign_app.side_effect = signing.sign_app  # The real signing step, with codesign faked.
        codesign = patch('subprocess.run', FakeCodesign())
        codesign.start()
        self.addCleanup(codesign.stop)

    def install(self, name):
        applications = self.root / name
        applications.mkdir()
        installer = update.Installer(self.app, applications / 'AquaSpace.app', ManifestSystem(),
                                     lock_parent=self.root / 'lock')
        installer.report = lambda text: None
        return installer, installer.install()

    def test_updater_accepts_the_builders_manifest(self):
        for name, dirty, options in (('clean', False, []), ('dirty', True, []), ('override', False, ['--version', 'local-test'])):
            with self.subTest(name):
                self.tools.dirty = dirty
                self.main(*options)
                manifest = json.loads((self.output / 'build.json').read_text())
                self.assertEqual(manifest['version'], self.info()['CFBundleShortVersionString'])
                self.assertEqual(manifest['designated_requirement'],
                                 'identifier "local.aquaspace" and certificate leaf = H"FINGERPRINT"')
                installer, record = self.install(name)
                self.assertEqual(installer.journal(record)['phase'], 'committed')
                self.assertEqual(json.loads((record / 'build.json').read_text()), manifest)

    def test_updater_rejects_a_manifest_that_does_not_match_the_build(self):
        self.main()
        published = json.loads((self.output / 'build.json').read_text())
        for number, (key, value) in enumerate((('version', 'other'), ('app_sha256', '0' * 64), ('cli_sha256', '0' * 64),
                                               ('designated_requirement', 'identifier "local.aquaspace" and certificate leaf = H"OTHER"'))):
            with self.subTest(key):
                (self.output / 'build.json').write_text(json.dumps(dict(published, **{key: value})))
                with self.assertRaisesRegex(update.UpdateError, 'does not match its build manifest'):
                    self.install(f'target-{number}')
                self.assertFalse((self.root / f'target-{number}/AquaSpace.app').exists())


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


if __name__ == '__main__':
    unittest.main()
