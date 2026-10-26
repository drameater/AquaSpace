"""Persistent, machine-local signing for AquaSpace; never fall back to ad-hoc.

The certificate pins the designated requirement across rebuilds. Its private key
lives in a dedicated keychain and is imported non-exportable, accessible only to
codesign. No system trust settings or privacy grants are changed here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

SIGNING_DIRECTORY = Path.home() / 'Library/Application Support/AquaSpace/Signing'
CERTIFICATE_NAME = 'AquaSpace Local Development'


class SigningError(RuntimeError):
    pass


def run(arguments):
    # Never print command arguments: keychain operations carry secret passwords.
    result = subprocess.run([str(a) for a in arguments], capture_output=True)
    if result.returncode:
        raise SigningError(result.stderr.decode(errors='replace').strip() or 'Signing command failed.')
    return result.stdout


def local_identity(directory=SIGNING_DIRECTORY):
    directory = Path(directory)
    keychain = directory / 'AquaSpace.keychain-db'
    certificate = directory / 'certificate.pem'
    password = directory / 'keychain-password'
    if not all(p.is_file() for p in (keychain, certificate, password)):
        raise SigningError('Persistent AquaSpace signing is not configured. Run python3 script/aquaspace_signing.py --setup. '
                           'Existing signing material will not be replaced automatically.')
    der = run(['/usr/bin/openssl', 'x509', '-in', certificate, '-outform', 'DER'])
    fingerprint = hashlib.sha1(der).hexdigest().upper()
    run(['/usr/bin/security', 'unlock-keychain', '-p', password.read_text(), keychain])
    return fingerprint, keychain


def setup(directory=SIGNING_DIRECTORY):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    keychain = directory / 'AquaSpace.keychain-db'
    password_file = directory / 'keychain-password'
    if keychain.exists() or password_file.exists() or (directory / 'certificate.pem').exists():
        # A broken setup must never silently rotate an identity with live grants.
        return local_identity(directory)
    with tempfile.TemporaryDirectory(prefix='setup-', dir=directory) as temporary:
        temporary = Path(temporary)
        config = temporary / 'openssl.cnf'
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=codesign\nprompt=no\n'
                          '[dn]\nCN=' + CERTIFICATE_NAME + '\n[codesign]\n'
                          'basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\n'
                          'extendedKeyUsage=critical,codeSigning\n')
        certificate, key, identity = (temporary / name for name in ('certificate.pem', 'private.pem', 'identity.p12'))
        run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
             '-config', config, '-keyout', key, '-out', certificate])
        password, import_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        fd = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(password)
        run(['/usr/bin/security', 'create-keychain', '-p', password, keychain])
        run(['/usr/bin/security', 'set-keychain-settings', '-lut', '21600', keychain])
        run(['/usr/bin/security', 'unlock-keychain', '-p', password, keychain])
        run(['/usr/bin/openssl', 'pkcs12', '-export', '-inkey', key, '-in', certificate,
             '-out', identity, '-passout', 'pass:' + import_password])
        run(['/usr/bin/security', 'import', identity, '-k', keychain, '-P', import_password,
             '-x', '-T', '/usr/bin/codesign'])
        shutil.copy2(certificate, directory / 'certificate.pem')
    return local_identity(directory)


def designated_requirement(app):
    result = subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(app)], capture_output=True, text=True)
    if result.returncode:
        raise SigningError(result.stderr.strip())
    match = re.search(r'^#?\s*designated => (.+)$', result.stdout + '\n' + result.stderr, re.MULTILINE)
    if not match:
        raise SigningError('No designated signing requirement found.')
    return match[1]


def sign_app(app, identity, keychain=None):
    if identity == '-':
        raise SigningError('Ad-hoc signing loses privacy grants after rebuilds; use a persistent certificate.')
    app = Path(app)
    flags = ['--keychain', keychain] if keychain else []
    run(['/usr/bin/codesign', '--force', '--sign', identity, *flags,
         '--identifier', 'local.aquaspace.cli', app / 'Contents/MacOS/aquaspace-cli'])
    run(['/usr/bin/codesign', '--force', '--sign', identity, *flags,
         '--identifier', 'local.aquaspace', app])
    run(['/usr/bin/codesign', '--verify', '--strict', app])
    requirement = designated_requirement(app)
    if 'cdhash' in requirement or 'certificate' not in requirement:
        raise SigningError('AquaSpace needs a certificate-bound identity that survives rebuilds.')
    return requirement


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true', help='Create the local signing identity once, or reuse it unchanged')
    args = parser.parse_args()
    try:
        fingerprint, keychain = setup() if args.setup else local_identity()
        print(f'AquaSpace signing identity: {fingerprint}\nKeychain: {keychain}')
    except (OSError, SigningError) as error:
        parser.exit(1, f'AquaSpace signing stopped: {error}\n')
