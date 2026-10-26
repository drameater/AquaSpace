"""Offline signing-policy checks; never access real keys or macOS services."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_signing as signing


class SigningTests(unittest.TestCase):
    def test_requirement_display_reads_codesign_standard_output(self):
        requirement = 'identifier "local.aquaspace" and certificate leaf = H"123"'
        reply = SimpleNamespace(returncode=0, stdout='designated => ' + requirement + '\n', stderr='Executable=/fake/app\n')
        with patch.object(signing.subprocess, 'run', return_value=reply):
            self.assertEqual(signing.designated_requirement('/fake/app'), requirement)

    def test_missing_or_partial_identity_never_regenerates_or_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(signing, 'run') as run:
            directory = Path(temporary)
            with self.assertRaises(signing.SigningError): signing.local_identity(directory)
            (directory / 'keychain-password').write_text('existing')
            with self.assertRaises(signing.SigningError): signing.setup(directory)
            self.assertEqual((directory / 'keychain-password').read_text(), 'existing')
            run.assert_not_called()

    def test_ad_hoc_identity_is_refused_before_signing(self):
        with patch.object(signing, 'run') as run:
            with self.assertRaises(signing.SigningError): signing.sign_app('/fake/app', '-')
            run.assert_not_called()

    def test_bad_signature_requirement_cannot_be_published(self):
        for requirement in ('cdhash H"123"', 'identifier "local.aquaspace"'):
            with self.subTest(requirement=requirement), patch.object(signing, 'run'):
                with patch.object(signing, 'designated_requirement', return_value=requirement):
                    with self.assertRaises(signing.SigningError): signing.sign_app('/fake/app', 'persistent')
