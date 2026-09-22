"""Build the current operating system's offline desktop bundle."""

import subprocess
import sys
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from desktop.integrity import MANIFEST_NAME, write_manifest

manifest_path = root / 'build' / MANIFEST_NAME
write_manifest(root / 'desktop', manifest_path)
anchor = root / 'build' / '_release_integrity.py'
anchor.write_text('MANIFEST_DIGEST = ' + repr(hashlib.sha256(manifest_path.read_bytes()).hexdigest()) + '\n', encoding='utf-8')
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'LynkCoHelper',
           '--windowed', '--onedir', '--paths', str(root),
           '--paths', str(root / 'build'), '--hidden-import', '_release_integrity',
           '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
           '--specpath', str(root / 'build'),
           '--add-data', str(root / 'desktop' / 'web') + ':desktop/web',
           '--add-data', str(root / 'desktop' / 'service.json') + ':desktop',
           '--add-data', str(root / 'desktop' / 'capture_addon.py') + ':desktop',
           '--add-data', str(manifest_path) + ':desktop',
           '--collect-all', 'mitmproxy', '--collect-all', 'mitmproxy_rs',
           '--collect-all', 'keyring', '--collect-all', 'certifi',
           '--hidden-import', 'qrcode.image.svg', '--hidden-import', 'psutil',
           str(root / 'desktop' / 'launcher.py')]
if sys.platform == 'win32':
    command[3:3] = ['--manifest', str(root / 'desktop' / 'packaging' / 'windows.manifest')]
if sys.platform == 'darwin':
    command[3:3] = ['--osx-bundle-identifier', 'community.lynkco.helper']
subprocess.run(command, cwd=root, check=True)
