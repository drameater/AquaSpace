"""Shared code for the offline Swift test runners (script/test-*-offline.py).

Each runner writes an isolated Swift package from production sources and
memory-only stubs, then runs `swift test` on it. This module holds the parts
that all runners need: toolchain lookup, package writing, source extraction and
the test command. It does not start an app, read AX or WindowServer state, or
open a socket.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SHARED_SWIFT = ROOT / 'script/offline-shared'
# Individual runners and script/test-offline.py use the same default, so they
# reuse each other's incremental builds.
DEFAULT_SCRATCH_ROOT = ROOT / '.debug'
TESTING_PLUGIN = Path('host/plugins/testing/libTestingMacros.dylib')


class HarnessError(Exception):
    """A runner cannot prepare its package. The message tells the user what to fix."""


def runner_name():
    return Path(sys.argv[0]).name or 'the offline runner'


# Toolchain

def find_swift():
    found = shutil.which('swift')
    if found:
        return Path(found)
    try:
        found = subprocess.run(['xcrun', '--find', 'swift'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        found = ''
    if not found:
        raise HarnessError('No Swift toolchain found. Install the Command Line Tools (xcode-select --install) or Xcode.')
    return Path(found)


def testing_plugin_path(target_info, exists=Path.is_file):
    """Return the Swift Testing macro plugin of the toolchain that `target_info` describes.

    SwiftPM from the Command Line Tools does not load this plugin by itself, so
    the runners pass it to the compiler explicitly.
    """
    try:
        resource = Path(target_info['paths']['runtimeResourcePath'])
    except (KeyError, TypeError) as error:
        raise HarnessError('`swift -print-target-info` did not report paths.runtimeResourcePath; '
                           'cannot locate the Swift Testing macro plugin.') from error
    plugin = resource / TESTING_PLUGIN
    if not exists(plugin):
        raise HarnessError(f'The Swift Testing macro plugin is missing: {plugin}. The offline runners need a '
                           'toolchain that includes Swift Testing (Swift 6 Command Line Tools or Xcode).')
    return plugin


def testing_plugin(swift):
    result = subprocess.run([str(swift), '-print-target-info'], capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessError(f'`{swift} -print-target-info` failed: {result.stderr.strip()}')
    return testing_plugin_path(json.loads(result.stdout))


def path_package_identity(path):
    """SwiftPM names a path dependency after its directory: last component, lowercased, no `.git`."""
    name = Path(path).name.lower()
    return name[:-len('.git')] if name.endswith('.git') else name


# Swift source extraction

def code_mask(text):
    """Mark each character of Swift source: 1 for code, 0 for comment or string literal."""
    mask = bytearray(len(text))
    i = 0
    while i < len(text):
        if text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            i = _skip_block_comment(text, i)
        elif text[i] == '"' or _is_raw_string_start(text, i):
            i = _skip_string(text, i)
        else:
            mask[i] = 1
            i += 1
    return mask


def _skip_block_comment(text, i):
    depth = 0
    while i < len(text):
        if text.startswith('/*', i):
            depth += 1
            i += 2
        elif text.startswith('*/', i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return i


def _is_raw_string_start(text, i):
    j = i
    while j < len(text) and text[j] == '#':
        j += 1
    return j > i and j < len(text) and text[j] == '"'


def _skip_string(text, i):
    hashes = 0
    while text[i] == '#':
        hashes += 1
        i += 1
    quote = '"""' if text.startswith('"""', i) else '"'
    i += len(quote)
    closing, escape = quote + '#' * hashes, '\\' + '#' * hashes
    while i < len(text):
        if text.startswith(escape, i):
            i += len(escape)
            i = _skip_interpolation(text, i) if text.startswith('(', i) else i + 1
        elif text.startswith(closing, i):
            return i + len(closing)
        elif quote == '"' and text[i] == '\n':
            return i
        else:
            i += 1
    return i


def _skip_interpolation(text, i):
    depth = 0
    while i < len(text):
        if text[i] == '"' or _is_raw_string_start(text, i):
            i = _skip_string(text, i)
            continue
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _tolerant(signature):
    """Match `signature` with any amount of whitespace where it has whitespace."""
    return re.compile(r'(?<![\w.])' + r'\s+'.join(re.escape(part) for part in signature.split()))


_ATTACHED = re.compile(r'\s*(@|///|//|/\*|\*)')


class SwiftSource:
    """Production Swift text that runners cut by declaration, not by exact text.

    A signature is the start of a declaration, for example `func run(` or
    `struct WindowManagerSession:`. Whitespace in it matches any whitespace, and
    matches inside comments and string literals are ignored. A declaration
    includes the attribute and comment lines directly above it. Each lookup
    must match exactly once; otherwise HarnessError names the file and the
    signature, so a production edit fails with a precise message.
    """

    def __init__(self, relative, text=None, root=ROOT):
        self.label = str(relative)
        self.text = (Path(root) / relative).read_text() if text is None else text
        self._mask = code_mask(self.text)

    def _error(self, what, signature, count):
        return HarnessError(f'{self.label}: expected exactly one {what} "{signature}", found {count}. '
                            f'Production code changed; update the extraction in {runner_name()}.')

    def _matches(self, signature):
        return [m for m in _tolerant(signature).finditer(self.text) if self._mask[m.start()]]

    def _find(self, signature):
        matches = self._matches(signature)
        if len(matches) != 1:
            raise self._error('declaration', signature, len(matches))
        return matches[0].start()

    def _line_start(self, index):
        return self.text.rfind('\n', 0, index) + 1

    def start_of(self, signature):
        """Index of the first line of the declaration, with its attributes and comments."""
        start = self._line_start(self._find(signature))
        while start > 0:
            previous = self._line_start(start - 1)
            if not _ATTACHED.match(self.text[previous:start - 1]):
                break
            start = previous
        return start

    def end_of(self, signature):
        """Index after the line that holds the closing brace of the declaration body."""
        i = self._body_start(signature)
        depth = 0
        for j in range(i, len(self.text)):
            if not self._mask[j]:
                continue
            if self.text[j] == '{':
                depth += 1
            elif self.text[j] == '}':
                depth -= 1
                if depth == 0:
                    end = self.text.find('\n', j)
                    return len(self.text) if end < 0 else end + 1
        raise self._error('braced declaration', signature, 0)

    def _body_start(self, signature):
        i = self._find(signature)
        while i < len(self.text) and not (self._mask[i] and self.text[i] == '{'):
            i += 1
        return i

    def decl(self, *signatures):
        """The complete declarations, in the given order."""
        return '\n'.join(self.text[self.start_of(s):self.end_of(s)] for s in signatures)

    def between(self, start, stop=None):
        """From the declaration `start` up to the declaration `stop` (or the end of the file)."""
        first = self.start_of(start)
        last = len(self.text) if stop is None else self.start_of(stop)
        if last < first:
            raise HarnessError(f'{self.label}: "{stop}" comes before "{start}"; update the extraction in {runner_name()}.')
        return self.text[first:last]

    def before(self, stop):
        """Everything above the declaration `stop`."""
        return self.text[:self.start_of(stop)]

    def without(self, *signatures):
        """The whole file without the given declarations."""
        spans = sorted((self.start_of(s), self.end_of(s)) for s in signatures)
        parts, cursor = [], 0
        for first, last in spans:
            if self.text.endswith('\n\n', 0, first):
                first -= 1  # Drop the blank line that separated the declaration.
            parts.append(self.text[cursor:first])
            cursor = last
        return ''.join(parts) + self.text[cursor:]


def retarget_imports(text, drop=(), add=()):
    """Remove `import M` lines for modules in `drop` and put `import A` lines for `add` first."""
    lines = [line for line in text.splitlines(keepends=True)
             if not any(line.strip() == f'import {module}' for module in drop)]
    present = {line.strip() for line in lines}
    header = ''.join(f'import {module}\n' for module in add if f'import {module}' not in present)
    return header + ''.join(lines)


# Package writing and running

class OfflinePackage:
    """A generated Swift package. Writes keep unchanged files, so builds stay incremental."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.written = set()

    def write(self, relative, text):
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
        self.written.add(path)
        return path

    def copy(self, relative, source):
        return self.write(relative, Path(source).read_text())

    def prune(self):
        """Remove generated sources from earlier runs that this run did not write."""
        for top in ('Sources', 'Tests'):
            for path in sorted((self.directory / top).rglob('*'), reverse=True):
                if path.is_file() and path not in self.written:
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()


def argument_parser(description):
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--scratch-root', type=Path, default=DEFAULT_SCRATCH_ROOT,
                        help='directory for generated packages, builds and module caches (default: %(default)s)')
    parser.add_argument('--sdk', type=Path, help='macOS SDK to compile against')
    parser.add_argument('--jobs', type=int, default=2, help='parallel compile jobs (default: %(default)s)')
    return parser


def package_for(args, name):
    return OfflinePackage(args.scratch_root.resolve() / name)


def swift_test(package, args, swift, plugin, build_path=None, extra=()):
    """Run `swift test` on the generated package and return its exit status."""
    cache = args.scratch_root.resolve() / 'offline-module-cache'
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache / 'clang'),
               SWIFTPM_MODULECACHE_OVERRIDE=str(cache / 'swiftpm'))
    command = [str(swift), 'test', '--package-path', str(package.directory),
               '--scratch-path', str(build_path or package.directory / 'build'),
               '--disable-automatic-resolution', '--jobs', str(args.jobs),
               '-Xswiftc', '-load-plugin-library', '-Xswiftc', str(plugin), *extra]
    if args.sdk:
        command += ['--sdk', str(args.sdk)]
    return subprocess.run(command, env=env).returncode


def main(prepare, description, configure=None):
    """Parse the flags, find the toolchain, let `prepare(args)` write the package, then test it.

    `prepare` returns the package and keyword options for swift_test().
    """
    parser = argument_parser(description)
    if configure:
        configure(parser)
    args = parser.parse_args()
    try:
        swift = find_swift()
        plugin = testing_plugin(swift)
        package, options = prepare(args)
        package.prune()
    except HarnessError as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    return swift_test(package, args, swift, plugin, **options)


# Result parsing for script/test-offline.py

_SWIFT_TESTING = re.compile(r'Test run with (\d+) tests? in \d+ suites? (passed|failed)')
_UNITTEST = re.compile(r'^Ran (\d+) tests? in ', re.MULTILINE)


def tests_run(output):
    """Number of tests that a Swift Testing or unittest run reports, or None."""
    counts = [int(m.group(1)) for m in _SWIFT_TESTING.finditer(output)] or \
        [int(m.group(1)) for m in _UNITTEST.finditer(output)]
    return counts[-1] if counts else None
