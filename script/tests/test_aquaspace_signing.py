"""Offline signing-policy checks; never access real keys or macOS services."""
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_signing as signing


def split_interactive_line(line):
    """Split one line as `security -i` does; this is the rule the tests check against.

    security(1)'s man page documents no quoting rules for interactive mode. This
    follows split_line() in Apple's published SecurityTool source (security.c):
    words end at white space; a word that starts with a double or single quote
    ends at the same quote; a backslash makes the next character literal, inside
    or outside quotes; a quote inside an unquoted word is literal. It is
    deliberately not shlex: shell rules differ (for example, a backslash inside
    double quotes before an ordinary character).
    """
    words, word, state, quote = [], None, 'space', None
    for character in line:
        if state == 'space':
            if character.isspace(): continue
            word = ''
            if character in '"\'':
                quote, state = character, 'quoted'; continue
            state = 'word'
        if state == 'word':
            if character == '\\': state = 'word-escaped'
            elif character.isspace(): words.append(word); state = 'space'
            else: word += character
        elif state == 'quoted':
            if character == '\\': state = 'quoted-escaped'
            elif character == quote: words.append(word); state = 'space'
            else: word += character
        elif state == 'word-escaped':
            word += character; state = 'word'
        elif state == 'quoted-escaped':
            word += character; state = 'quoted'
    if state != 'space': words.append(word)
    return words


class FakeTools:
    """Stands in for openssl and security(1): records argv and stdin, writes outputs."""
    def __init__(self):
        self.calls = []
        self.security_commands = []
        self.stderr = b''
        self.timeout = False

    def __call__(self, argv, input=None, capture_output=True, timeout=None, **kwargs):
        assert timeout, 'Every signing command needs a timeout'
        self.calls.append((list(argv), input))
        if self.timeout: raise subprocess.TimeoutExpired(argv, timeout)
        tool = Path(argv[0]).name
        if tool == 'openssl' and argv[1] in ('req', 'pkcs12'):
            for flag in ('-keyout', '-out'):
                if flag in argv: Path(argv[argv.index(flag) + 1]).write_text(flag)
        if tool == 'security' and argv[1:] == ['-i']:
            lines = input.decode().split('\n')
            assert len(lines) == 2 and lines[1] == '', 'One command per line, then a newline'
            words = split_interactive_line(lines[0])
            self.security_commands.append(words)
            if words[0] == 'create-keychain': Path(words[-1]).write_text('keychain')
        stdout = b'DER' if argv[1:2] == ['x509'] else b''
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=self.stderr)

    def arguments(self): return [' '.join(argv) for argv, _ in self.calls]
    def inputs(self): return [(input or b'').decode() for _, input in self.calls]


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aquaspace-signing-offline-')
        self.addCleanup(self.tmp.cleanup)
        # A space, as in "Application Support", must survive `security -i` parsing.
        self.directory = Path(self.tmp.name) / 'Application Support/AquaSpace/Signing'
        self.tools = FakeTools()
        guard = patch.object(signing.subprocess, 'run', side_effect=self.tools)
        guard.start(); self.addCleanup(guard.stop)
        self.reports = []

    def material(self, *names):
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in names: (self.directory / name).write_text('existing ' + name)

    def test_requirement_display_reads_codesign_standard_output(self):
        requirement = 'identifier "local.aquaspace" and certificate leaf = H"123"'
        reply = SimpleNamespace(returncode=0, stdout='designated => ' + requirement + '\n', stderr='Executable=/fake/app\n')
        with patch.object(signing.subprocess, 'run', return_value=reply):
            self.assertEqual(signing.designated_requirement('/fake/app'), requirement)

    def test_setup_keeps_every_secret_out_of_command_arguments(self):
        signing.setup(self.directory, report=self.reports.append)
        password = (self.directory / 'keychain-password').read_text()
        export = [input for argv, input in self.tools.calls if argv[1:2] == ['pkcs12']][0]
        import_password = export.decode().strip()
        for secret in (password, import_password):
            self.assertTrue(secret)
            self.assertFalse([a for a in self.tools.arguments() if secret in a])
            self.assertTrue([i for i in self.tools.inputs() if secret in i])
        commands = self.tools.security_commands
        keychain = str(self.directory / 'AquaSpace.keychain-db')
        self.assertIn(['create-keychain', '-p', password, keychain], commands)
        self.assertIn(['unlock-keychain', '-p', password, keychain], commands)
        self.assertTrue((self.directory / 'certificate.pem').is_file())

    def test_unlock_for_a_build_passes_the_password_on_standard_input_only(self):
        self.material('AquaSpace.keychain-db', 'keychain-password', 'certificate.pem')
        signing.local_identity(self.directory)
        unlock = [(argv, input) for argv, input in self.tools.calls if argv[0] == signing.SECURITY]
        self.assertEqual(unlock[0][0], [signing.SECURITY, '-i'])
        self.assertIn('existing keychain-password', unlock[0][1].decode())

    def test_security_error_reported_in_interactive_mode_fails(self):
        self.material('AquaSpace.keychain-db', 'keychain-password', 'certificate.pem')
        self.tools.stderr = b'security: SecKeychainUnlock AquaSpace.keychain-db: The user name or passphrase you entered is not correct.\n'
        with self.assertRaisesRegex(signing.SigningError, 'passphrase'):
            signing.local_identity(self.directory)

    def test_command_timeout_is_a_clear_error(self):
        self.material('AquaSpace.keychain-db', 'keychain-password', 'certificate.pem')
        self.tools.timeout = True
        with self.assertRaisesRegex(signing.SigningError, 'did not finish.*keychain'):
            signing.local_identity(self.directory)

    def test_argument_that_cannot_be_quoted_is_refused_before_running(self):
        with self.assertRaises(signing.SigningError):
            signing.security_with_secret('unlock-keychain', '-p', 'secret', '/a "quoted" path')
        self.assertEqual(self.tools.calls, [])

    def test_partial_setup_without_certificate_is_set_aside_and_finished(self):
        # The certificate is written last, so no build can have used this material.
        self.material('keychain-password', 'AquaSpace.keychain-db')
        signing.setup(self.directory, report=self.reports.append)
        aside, = self.directory.glob('incomplete-*')
        self.assertEqual((aside / 'keychain-password').read_text(), 'existing keychain-password')
        self.assertEqual((aside / 'AquaSpace.keychain-db').read_text(), 'existing AquaSpace.keychain-db')
        self.assertNotEqual((self.directory / 'keychain-password').read_text(), 'existing keychain-password')
        self.assertTrue((self.directory / 'certificate.pem').is_file())
        self.assertIn(str(aside), self.reports[0])

    def test_identity_with_certificate_but_missing_key_material_is_never_replaced(self):
        self.material('keychain-password', 'certificate.pem')
        with self.assertRaises(signing.SigningError) as caught:
            signing.setup(self.directory, report=self.reports.append)
        self.assertIn('AquaSpace.keychain-db', str(caught.exception))
        self.assertIn('mv ', str(caught.exception))
        self.assertEqual(self.tools.calls, [])
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ['certificate.pem', 'keychain-password'])

    def test_complete_identity_is_reused_unchanged(self):
        self.material('AquaSpace.keychain-db', 'keychain-password', 'certificate.pem')
        signing.setup(self.directory, report=self.reports.append)
        self.assertFalse([a for a in self.tools.arguments() if 'req' in a or 'pkcs12' in a])
        self.assertFalse([i for i in self.tools.inputs() if 'create-keychain' in i])
        self.assertEqual((self.directory / 'keychain-password').read_text(), 'existing keychain-password')

    def test_missing_identity_never_falls_back_for_builds(self):
        with self.assertRaises(signing.SigningError): signing.local_identity(self.directory)
        self.assertEqual(self.tools.calls, [])

    def test_ad_hoc_identity_is_refused_before_signing(self):
        with patch.object(signing, 'run') as run:
            with self.assertRaises(signing.SigningError): signing.sign_app('/fake/app', '-')
            run.assert_not_called()

    def test_bad_signature_requirement_cannot_be_published(self):
        for requirement in ('cdhash H"123"', 'identifier "local.aquaspace"'):
            with self.subTest(requirement=requirement), patch.object(signing, 'run'):
                with patch.object(signing, 'designated_requirement', return_value=requirement):
                    with self.assertRaises(signing.SigningError): signing.sign_app('/fake/app', 'persistent')



class InteractiveQuotingTests(unittest.TestCase):
    """security -i reads secrets on standard input; every path must reach it unchanged."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aquaspace-signing-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.tools = FakeTools()
        guard = patch.object(signing.subprocess, 'run', side_effect=self.tools)
        guard.start(); self.addCleanup(guard.stop)

    def home(self, name):
        return Path(self.tmp.name) / name / 'Library/Application Support/AquaSpace/Signing'

    def test_home_paths_with_a_space_or_single_quote_reach_security_unchanged(self):
        for name in ('John Appleseed', "O'Brien", "it's a home", "'leading quote"):
            with self.subTest(home=name):
                self.tools.security_commands.clear()
                directory = self.home(name)
                signing.setup(directory, report=lambda text: None)
                password = (directory / 'keychain-password').read_text()
                keychain = str(directory / 'AquaSpace.keychain-db')
                self.assertIn(['create-keychain', '-p', password, keychain], self.tools.security_commands)
                self.assertIn(['unlock-keychain', '-p', password, keychain], self.tools.security_commands)
                imports = [c for c in self.tools.security_commands if c[0] == 'import']
                self.assertEqual(len(imports), 1)
                self.assertTrue(imports[0][1].startswith(str(directory) + '/'))
                self.assertEqual(imports[0][imports[0].index('-k') + 1], keychain)

    def test_home_paths_whose_quoting_is_undocumented_are_refused_before_anything_runs(self):
        # A double quote or backslash inside a quoted word depends on the escape
        # rule, which security(1) does not document. Refuse instead of guessing.
        for name in ('say "hi"', 'back\\slash', 'line\nbreak'):
            with self.subTest(home=name):
                self.tools.calls.clear()
                directory = self.home(name)
                with self.assertRaises(signing.SigningError) as caught:
                    signing.setup(directory, report=lambda text: None)
                self.assertIn(repr(str(directory)), str(caught.exception))
                self.assertEqual(self.tools.calls, [])
                self.assertFalse(directory.exists() and any(directory.iterdir()))

    def test_unlock_refuses_an_unpassable_signing_directory_without_printing_the_secret(self):
        directory = self.home('back\\slash')
        directory.mkdir(parents=True)
        for name in ('AquaSpace.keychain-db', 'certificate.pem'): (directory / name).write_text('x')
        (directory / 'keychain-password').write_text('do-not-print-me')
        with self.assertRaises(signing.SigningError) as caught: signing.local_identity(directory)
        self.assertNotIn('do-not-print-me', str(caught.exception))
        self.assertEqual(self.tools.calls, [])


if __name__ == '__main__': unittest.main()
