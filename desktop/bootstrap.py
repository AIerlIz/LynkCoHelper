"""Release-pinned downloader. Hashes are compiled into the bootstrap executable."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

import certifi
import psutil

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXTRACTED = 2 * 1024 * 1024 * 1024


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, expected):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        raise ValueError('Invalid release URL')
    opener = build_opener(SecureRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    digest, size = hashlib.sha256(), 0
    with opener.open(Request(url, headers={'User-Agent': 'LynkCoHelper-bootstrap/1'}), timeout=30) as response, destination.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise ValueError('Resource archive too large')
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('Resource SHA-256 mismatch; download rejected')


def extract(archive, destination):
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) > 50000 or sum(m.size for m in members) > MAX_EXTRACTED:
            raise ValueError('Resource archive exceeds extraction limit')
        # Validate all links and paths before any file is written.
        for member in members:
            tarfile.data_filter(member, str(destination))
        bundle.extractall(destination, members=members, filter='data')


def identity(pid):
    return {'pid': pid, 'created': psutil.Process(pid).create_time()}


def alive(record):
    try:
        return psutil.Process(record['pid']).create_time() == record['created']
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, KeyError, TypeError):
        return True


def cleanup_stale(root):
    for directory in root.glob('session-*'):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            # Avoid a race between directory creation and recording its owner.
            if time.time() - directory.stat().st_mtime < 60:
                continue
            records = json.loads((directory / 'processes.json').read_text())
            if not any(alive(record) for record in records):
                shutil.rmtree(directory)
        except (OSError, ValueError, TypeError):
            continue


def main():
    from _bootstrap_release import URL, SHA256, EXECUTABLE
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'Library' / 'Caches'))) / 'LynkCoHelper' / 'downloads'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    cleanup_stale(base)
    session = Path(tempfile.mkdtemp(prefix='session-', dir=base))
    owner = identity(os.getpid())
    record = session / 'processes.json'
    record.write_text(json.dumps([owner]))
    child = None
    stop_file = session / 'stop'
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        print('Downloading client resources...', flush=True)
        archive = session / 'resources.tar.gz'
        download(URL, archive, SHA256)
        print('Verifying and extracting client...', flush=True)
        extract(archive, session / 'app')
        executable = session / 'app' / EXECUTABLE
        if not executable.is_file():
            raise ValueError('Client executable missing')
        child = subprocess.Popen([str(executable), '--bootstrap-parent', json.dumps(owner), '--bootstrap-stop', str(stop_file)])
        try:
            record.write_text(json.dumps([owner, identity(child.pid)]))
        except psutil.NoSuchProcess:
            return child.wait()
        print('Client running. Use Quit in the client to close it. Closing this launcher also stops the client.', flush=True)
        return child.wait()
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f'Cannot start client: {error}', file=sys.stderr, flush=True)
        if sys.stdin and sys.stdin.isatty():
            input('Press Enter to close.')
        return 1
    finally:
        if child is not None and child.poll() is None:
            stop_file.touch()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        shutil.rmtree(session, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
