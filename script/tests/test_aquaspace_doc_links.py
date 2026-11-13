"""Every relative link and heading anchor in the tracked Markdown files resolves.

External URLs are not fetched. Anchors follow GitHub's rules: the rendered heading
text in lower case, without punctuation, with each space replaced by a hyphen, and
"-1", "-2" ... added to repeated headings.
"""
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
FENCE = re.compile(r'^\s*(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$')
INLINE_CODE = re.compile(r'(`+)(.+?)\1')
# [text](target) and ![alt](target); the target may be <bracketed> and may have a "title".
LINK = re.compile(r'!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+"[^"]*")?\s*\)')
HTML_REFERENCE = re.compile(r'<(?:a|img)\b[^>]*\b(?:href|src)="([^"]*)"', re.IGNORECASE)
HTML_ANCHOR = re.compile(r'<a\b[^>]*\b(?:id|name)="([^"]*)"', re.IGNORECASE)
EXTERNAL = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|//)', re.IGNORECASE)


def tracked_files():
    output = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout
    return {Path(name) for name in output.decode().split('\0') if name}


def prose_lines(text):
    """Yield (line number, line) outside fenced code blocks, with inline code removed."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if match:
            if fence is None: fence = match[1]
            elif match[1] == fence: fence = None
            continue
        if fence is None:
            yield number, line


def slug(heading):
    text = INLINE_CODE.sub(lambda m: m[2], heading)
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)  # A link renders as its text.
    text = re.sub(r'<[^>]+>', '', text).replace('*', '')
    return re.sub(r'[^\w\- ]', '', text.lower()).replace(' ', '-')


def anchors(text):
    found, seen = set(), {}
    for _, line in prose_lines(text):
        found.update(HTML_ANCHOR.findall(line))
        match = HEADING.match(line)
        if not match: continue
        base = slug(match[2])
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f'{base}-{count}')
    return found


def references(text):
    for number, line in prose_lines(text):
        line = INLINE_CODE.sub('', line)
        for target in LINK.findall(line) + HTML_REFERENCE.findall(line):
            target = target.strip('<>')
            if target and not EXTERNAL.match(target):
                yield number, target


class DocumentLinkTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tracked = tracked_files()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('Not a Git checkout; the tracked file list is unknown.')
        self.directories = {parent for path in self.tracked for parent in path.parents}

    def test_every_relative_link_and_anchor_resolves(self):
        documents = sorted(path for path in self.tracked if path.suffix == '.md')
        self.assertTrue(documents)
        cache = {}
        def anchors_of(path):
            if path not in cache: cache[path] = anchors((ROOT / path).read_text())
            return cache[path]
        broken = []
        for document in documents:
            for number, target in references((ROOT / document).read_text()):
                path_part, _, fragment = target.partition('#')
                path_part, fragment = unquote(path_part), unquote(fragment)
                if path_part.startswith('/'):
                    resolved = Path(path_part.lstrip('/'))
                elif path_part:
                    resolved = Path(*(document.parent / path_part).parts)
                else:
                    resolved = document
                # Normalize "a/../b" without touching the filesystem.
                parts = []
                for part in resolved.parts:
                    if part == '..' and parts: parts.pop()
                    elif part != '.': parts.append(part)
                resolved = Path(*parts) if parts else Path('.')
                where = f'{document}:{number}: {target}'
                if resolved not in self.tracked and resolved not in self.directories:
                    broken.append(f'{where} (no tracked file {resolved})')
                elif fragment and resolved.suffix == '.md' and fragment not in anchors_of(resolved):
                    broken.append(f'{where} (no heading #{fragment} in {resolved})')
        self.assertEqual(broken, [], 'Broken links:\n' + '\n'.join(broken))

    def test_anchor_rules_match_github(self):
        self.assertEqual(slug('Install and update'), 'install-and-update')
        self.assertEqual(slug('Fixed harness after 4508f0e8 — 2026-10-15'), 'fixed-harness-after-4508f0e8--2026-10-15')
        self.assertEqual(slug('Permission identity repair (2026-10-17)'), 'permission-identity-repair-2026-10-17')
        self.assertEqual(slug('Automated native Ghostty merge/detach validation'), 'automated-native-ghostty-mergedetach-validation')
        self.assertEqual(slug('The `--resume` command'), 'the---resume-command')
        self.assertEqual(anchors('# A\n## A\n```\n# A\n```\n## A\n'), {'a', 'a-1', 'a-2'})


if __name__ == '__main__':
    unittest.main()
