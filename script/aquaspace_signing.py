"""Persistent, machine-local signing for AquaSpace; never fall back to ad-hoc.

The certificate pins the designated requirement across rebuilds. Its private key
lives in a dedicated keychain and is imported non-exportable, accessible only to
codesign. No system trust settings or privacy grants are changed here.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import tempfile
import time

SIGNING_DIRECTORY = Path.home() / 'Library/Application Support/AquaSpace/Signing'
CERTIFICATE_NAME = 'AquaSpace Local Development'
SECURITY, OPENSSL, CODESIGN = '/usr/bin/security', '/usr/bin/openssl', '/usr/bin/codesign'
COMMAND_TIMEOUT = 120
# Inside a double-quoted word of `security -i`, only these characters depend on rules
# that security(1) does not document (see security_with_secret).
UNPASSABLE = '"\\\n\r\0'
# security.c reads interactive lines into a 4096-byte buffer.
MAX_INTERACTIVE_LINE = 4095


class SigningError(RuntimeError):
    pass


def run(arguments, input=None, timeout=COMMAND_TIMEOUT):
    # Never print command arguments or input. Secrets travel only on standard
    # input: any local user can read another process's arguments with `ps`.
    try:
        result = subprocess.run([str(a) for a in arguments], input=input, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SigningError(f'{Path(str(arguments[0])).name} did not finish within {timeout} s. A locked keychain '
                           'can wait for a password dialog: look for one, or unlock the keychain, then try again.') from None
    if result.returncode:
        raise SigningError(result.stderr.decode(errors='replace').strip() or 'Signing command failed.')
    return result


def check_passable(path):
    """Refuse a signing path that `security -i` might not receive unchanged."""
    if any(character in str(path) for character in UNPASSABLE):
        raise SigningError(f'The signing directory {str(path)!r} contains a double quote, a backslash, or a line '
                           'break. security(1) does not document how it reads these characters, so AquaSpace '
                           'signing cannot use this path. Use a home folder path without them.')


def security_with_secret(*arguments):
    """Run one security(1) command read from standard input, so no secret is in argv.

    security(1)'s man page documents no quoting rules for interactive mode.
    Apple's published source (SecurityTool security.c, split_line) splits a line
    at white space, keeps a word that starts with a double or single quote
    together up to the same quote, and lets a backslash make the next character
    literal. Every word here is double-quoted, so white space and single quotes
    are plain characters. Inside double quotes, only a double quote and a
    backslash depend on the escape rule, so they are refused instead of escaped.
    Never print an argument: one of them is a secret.
    """
    words = []
    for argument in map(str, arguments):
        if any(character in argument for character in UNPASSABLE):
            raise SigningError('A signing argument contains a double quote, a backslash or a line break, '
                               'which security(1) cannot take safely.')
        words.append(f'"{argument}"')
    line = ' '.join(words) + '\n'
    if len(line.encode()) > MAX_INTERACTIVE_LINE:
        raise SigningError('A signing command is too long for security(1) interactive mode; use a shorter path.')
    result = run([SECURITY, '-i'], input=line.encode())
    # Interactive mode can exit 0 after a failed command; it still prints "security: <error>".
    failure = re.search(rb'^security: .*$', result.stderr + b'\n' + result.stdout, re.MULTILINE)
    if failure:
        raise SigningError(failure[0].decode(errors='replace'))
    return result.stdout


def local_identity(directory=SIGNING_DIRECTORY):
    directory = Path(directory)
    keychain = directory / 'AquaSpace.keychain-db'
    certificate = directory / 'certificate.pem'
    password = directory / 'keychain-password'
    check_passable(directory)
    if not all(p.is_file() for p in (keychain, certificate, password)):
        raise SigningError('Persistent AquaSpace signing is not configured. Run python3 script/aquaspace_signing.py --setup. '
                           'Existing signing material will not be replaced automatically.')
    der = run([OPENSSL, 'x509', '-in', certificate, '-outform', 'DER']).stdout
    fingerprint = hashlib.sha1(der).hexdigest().upper()
    # Unattended builds need the keychain password beside the keychain. So any
    # process that runs as this user can read it, sign code that satisfies
    # AquaSpace's designated requirement, and inherit AquaSpace's Accessibility
    # grant. That is inherent to local signing without a prompt; the private
    # directory mode only keeps other user accounts out.
    security_with_secret('unlock-keychain', '-p', password.read_text(), keychain)
    return fingerprint, keychain


def _set_aside_incomplete(directory, present, report):
    aside = directory / time.strftime('incomplete-%Y%m%d-%H%M%S')
    while aside.exists(): aside = aside.with_name(aside.name + '-1')
    aside.mkdir(mode=0o700)
    for path in present: path.rename(aside / path.name)
    report(f'Moved an incomplete earlier signing setup to {aside}. No build was signed with it. '
           'Creating the signing identity now.')


def _remove_setup_leftovers(directory):
    """Remove setup-* directories that a hard crash left; they can hold a plain private key.

    The caller holds the setup lock, so no other setup uses them.
    """
    for leftover in directory.glob('setup-*'):
        info = os.lstat(leftover)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            shutil.rmtree(leftover)


def setup(directory=SIGNING_DIRECTORY, report=print):
    directory = Path(directory)
    check_passable(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    # Never unlink the lock file: a new inode would let a second setup in.
    lock = os.open(directory / '.setup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SigningError('Another AquaSpace signing setup is running. Wait for it to finish, then try again.') from error
        _remove_setup_leftovers(directory)
        return _setup_locked(directory, report)
    finally:
        os.close(lock)


def _setup_locked(directory, report):
    keychain = directory / 'AquaSpace.keychain-db'
    password_file = directory / 'keychain-password'
    certificate_file = directory / 'certificate.pem'
    present = [p for p in (keychain, password_file, certificate_file) if p.exists() or p.is_symlink()]
    if certificate_file in present:
        if len(present) == 3:
            return local_identity(directory)
        # Builds may already be signed with this identity, and privacy grants may
        # depend on it. Never rotate it silently.
        missing = ', '.join(p.name for p in (keychain, password_file) if p not in present)
        aside = shlex.quote(str(directory) + '.old')
        raise SigningError(
            f'The signing identity in {directory} is incomplete ({missing} missing). It will not be replaced '
            'automatically, because AquaSpace permissions may depend on it. Restore the missing file from a backup. '
            'To create a new identity instead (then allow AquaSpace in Accessibility settings again), run:\n'
            f'  mv {shlex.quote(str(directory))} {aside}\n  python3 script/aquaspace_signing.py --setup')
    if present:
        # The certificate is published last, so no build was signed with this material.
        _set_aside_incomplete(directory, present, report)
    # The context removes the plain private key on any exception or interrupt.
    with tempfile.TemporaryDirectory(prefix='setup-', dir=directory) as temporary:
        temporary = Path(temporary)
        config = temporary / 'openssl.cnf'
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=codesign\nprompt=no\n'
                          '[dn]\nCN=' + CERTIFICATE_NAME + '\n[codesign]\n'
                          'basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\n'
                          'extendedKeyUsage=critical,codeSigning\n')
        certificate, key, identity = (temporary / name for name in ('certificate.pem', 'private.pem', 'identity.p12'))
        run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
             '-config', config, '-keyout', key, '-out', certificate])
        password, import_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        fd = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(password)
        security_with_secret('create-keychain', '-p', password, keychain)
        run([SECURITY, 'set-keychain-settings', '-lut', '21600', keychain])
        security_with_secret('unlock-keychain', '-p', password, keychain)
        run([OPENSSL, 'pkcs12', '-export', '-inkey', key, '-in', certificate,
             '-out', identity, '-passout', 'stdin'], input=(import_password + '\n').encode())
        security_with_secret('import', identity, '-k', keychain, '-P', import_password, '-x', '-T', CODESIGN)
        # Publishing the certificate marks the identity complete.
        staged = directory / 'certificate.pem.tmp'
        shutil.copy2(certificate, staged)
        os.replace(staged, certificate_file)
    return local_identity(directory)


def designated_requirement(app):
    try:
        result = subprocess.run([CODESIGN, '-d', '-r-', str(app)], capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SigningError(f'codesign did not report the signature of {app} within {COMMAND_TIMEOUT} s.') from None
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
    run([CODESIGN, '--force', '--sign', identity, *flags,
         '--identifier', 'local.aquaspace.cli', app / 'Contents/MacOS/aquaspace-cli'])
    run([CODESIGN, '--force', '--sign', identity, *flags,
         '--identifier', 'local.aquaspace', app])
    run([CODESIGN, '--verify', '--strict', app])
    requirement = designated_requirement(app)
    if 'cdhash' in requirement or 'certificate' not in requirement:
        raise SigningError('AquaSpace needs a certificate-bound identity that survives rebuilds.')
    return requirement


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true', help='Create the local signing identity once, finish an interrupted setup, or reuse a complete identity unchanged')
    args = parser.parse_args()
    try:
        fingerprint, keychain = setup() if args.setup else local_identity()
        print(f'AquaSpace signing identity: {fingerprint}\nKeychain: {keychain}')
    except (OSError, SigningError) as error:
        parser.exit(1, f'AquaSpace signing stopped: {error}\n')
