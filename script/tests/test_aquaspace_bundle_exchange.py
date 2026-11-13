"""Atomic bundle exchange on temporary directories only; no apps, CLIs or signals."""
import ctypes
import errno
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aquaspace_update as update


class UnsupportedVolume:
    """A libc whose renamex_np reports a volume without exchange support."""
    def __init__(self, code):
        self.code, self.calls = code, 0
        def renamex_np(first, second, flags):
            self.calls += 1
            ctypes.set_errno(self.code)
            return -1
        self.renamex_np = renamex_np


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aquaspace-exchange-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.first, self.second = self.root / 'new.app', self.root / 'AquaSpace.app'
        for path, marker in ((self.first, 'new'), (self.second, 'old')):
            path.mkdir(); (path / 'marker').write_text(marker)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.kill'):
            guard = patch(name, side_effect=AssertionError('Live API forbidden: ' + name))
            guard.start(); self.addCleanup(guard.stop)

    @unittest.skipUnless(sys.platform == 'darwin', 'renamex_np is a Darwin system call')
    def test_native_exchange_swaps_both_directories(self):
        update.exchange_paths(self.first, self.second)
        self.assertEqual((self.second / 'marker').read_text(), 'new')
        self.assertEqual((self.first / 'marker').read_text(), 'old')

    @unittest.skipUnless(sys.platform == 'darwin', 'renamex_np is a Darwin system call')
    def test_native_exchange_refuses_a_missing_path_without_moving_the_other(self):
        missing = self.root / 'missing.app'
        with self.assertRaises(OSError):
            update.exchange_paths(missing, self.second)
        self.assertEqual((self.second / 'marker').read_text(), 'old')

    def test_volume_without_exchange_support_is_reported_without_changes(self):
        for code in (errno.ENOTSUP, errno.EINVAL):
            with self.subTest(code=code):
                with self.assertRaises(update.ExchangeUnsupported):
                    update.exchange_paths(self.first, self.second, libc=UnsupportedVolume(code))
                self.assertEqual((self.second / 'marker').read_text(), 'old')
                self.assertEqual((self.first / 'marker').read_text(), 'new')

    def test_other_exchange_errors_are_not_mistaken_for_missing_support(self):
        with self.assertRaises(OSError) as caught:
            update.exchange_paths(self.first, self.second, libc=UnsupportedVolume(errno.EACCES))
        self.assertNotIsInstance(caught.exception, update.ExchangeUnsupported)


if __name__ == '__main__': unittest.main()
