"""Archive the runtime and compile a matching, version-pinned single-file launcher."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import quote

parser = argparse.ArgumentParser()
parser.add_argument('--tag', required=True)
parser.add_argument('--platform', choices=['windows-x64', 'macos-arm64', 'macos-intel'], required=True)
args = parser.parse_args()
if not re.fullmatch(r'cloud-v[0-9][A-Za-z0-9._-]*', args.tag):
    parser.error('tag must start with cloud-v followed by a version')
root = Path(__file__).resolve().parents[2]
out = root / 'release'
out.mkdir(exist_ok=True)
name = f'LynkCoHelper-cloud-{args.platform}'
source = root / 'dist' / ('LynkCoHelper' if args.platform == 'windows-x64' else 'LynkCoHelper.app')
archive = out / f'{name}-resources.tar.gz'
with tarfile.open(archive, 'w:gz', dereference=False) as bundle:
    bundle.add(source, arcname=source.name)
with archive.open('rb') as stream:
    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
(out / f'{archive.name}.sha256').write_text(f'{checksum}  {archive.name}\n')
generated = root / 'build' / 'bootstrap-config'
generated.mkdir(parents=True, exist_ok=True)
url = f'https://github.com/shovelshit/LynkCoHelper/releases/download/{quote(args.tag)}/{archive.name}'
executable = 'LynkCoHelper/LynkCoHelper.exe' if args.platform == 'windows-x64' else 'LynkCoHelper.app/Contents/MacOS/LynkCoHelper'
(generated / '_bootstrap_release.py').write_text(f'URL = {url!r}\nSHA256 = {checksum!r}\nEXECUTABLE = {executable!r}\n')
mode = '--onefile' if args.platform == 'windows-x64' else '--onedir'
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', mode, '--windowed',
                '--name', name, '--paths', str(generated), '--hidden-import', '_bootstrap_release',
                '--distpath', str(out), '--workpath', str(root / 'build' / 'bootstrap'),
                '--specpath', str(root / 'build' / 'bootstrap'), str(root / 'desktop' / 'bootstrap.py')], check=True)
# A macOS executable needs its execute bit preserved during browser download.
if args.platform != 'windows-x64':
    launcher = out / f'{name}.app'
    archive_path = out / f'{name}-launcher.zip'
    subprocess.run(['ditto', '-c', '-k', '--keepParent', launcher.name, archive_path.name], cwd=out, check=True)
    shutil.rmtree(launcher)
