import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from desktop.bootstrap import cleanup_stale, download, extract, main


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def archive(self, name, link=None):
        archive = self.root / 'test.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            info = tarfile.TarInfo(name)
            info.mode = 0o755
            if link:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                bundle.addfile(info)
            else:
                info.size = 4
                bundle.addfile(info, io.BytesIO(b'test'))
        return archive

    def test_extract_preserves_executable(self):
        extract(self.archive('client/run'), self.root / 'out')
        path = self.root / 'out/client/run'
        self.assertEqual(path.read_bytes(), b'test')
        if os.name != 'nt':
            self.assertTrue(path.stat().st_mode & 0o100)

    def test_path_traversal_and_external_symlink_rejected(self):
        for name, link in [('../outside', None), ('escape', '../../outside')]:
            with self.assertRaises(tarfile.FilterError):
                extract(self.archive(name, link), self.root / 'out')
        self.assertFalse((self.root / 'outside').exists())

    def test_hash_mismatch_rejected(self):
        for expected, valid in [(hashlib.sha256(b'test').hexdigest(), True), ('0' * 64, False)]:
            with patch('desktop.bootstrap.build_opener') as opener:
                opener.return_value.open.return_value = io.BytesIO(b'test')
                if valid:
                    download('https://github.com/example/asset', self.root / 'download', expected)
                else:
                    with self.assertRaisesRegex(ValueError, 'SHA-256'):
                        download('https://github.com/example/asset', self.root / 'download', expected)

    def test_cleanup_preserves_active_and_recent_sessions(self):
        for name in ['active', 'dead', 'recent']:
            directory = self.root / f'session-{name}'
            directory.mkdir()
            (directory / 'processes.json').write_text(json.dumps([{'pid': name}]))
            if name != 'recent':
                os.utime(directory, (0, 0))
        with patch('desktop.bootstrap.alive', side_effect=lambda record: record['pid'] == 'active'):
            cleanup_stale(self.root)
        self.assertTrue((self.root / 'session-active').exists())
        self.assertTrue((self.root / 'session-recent').exists())
        self.assertFalse((self.root / 'session-dead').exists())

    def test_exit_removes_session_but_preserves_other_user_files(self):
        sentinel = self.root / 'credentials'
        sentinel.write_text('preserve')
        config = SimpleNamespace(URL='https://github.com/fixture', SHA256='fixture', EXECUTABLE='client')
        def unpack(archive, destination):
            destination.mkdir()
            (destination / 'client').touch()
        with patch.dict(sys.modules, {'_bootstrap_release': config}), \
                patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}), \
                patch('desktop.bootstrap.download'), patch('desktop.bootstrap.extract', side_effect=unpack), \
                patch('desktop.bootstrap.signal.signal'), patch('desktop.bootstrap.subprocess.Popen') as launch:
            launch.return_value.pid = os.getpid()
            launch.return_value.wait.return_value = 0
            launch.return_value.poll.return_value = 0
            self.assertEqual(main(), 0)
        self.assertEqual(list((self.root / 'LynkCoHelper/downloads').iterdir()), [])
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_bad_download_never_starts_client_and_is_cleaned(self):
        config = SimpleNamespace(URL='https://github.com/fixture', SHA256='fixture', EXECUTABLE='client')
        with patch.dict(sys.modules, {'_bootstrap_release': config}), \
                patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}), \
                patch('desktop.bootstrap.download', side_effect=ValueError('SHA-256 mismatch')), \
                patch('desktop.bootstrap.signal.signal'), patch('builtins.input'), \
                patch('desktop.bootstrap.subprocess.Popen') as launch:
            self.assertEqual(main(), 1)
            launch.assert_not_called()
        self.assertEqual(list((self.root / 'LynkCoHelper/downloads').iterdir()), [])
