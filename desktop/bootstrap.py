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


class ProgressUI:
    def __init__(self):
        self.root = self.label = self.detail = self.progress = None
        try:
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.title('LynkCoHelper')
            self.root.geometry('460x180')
            self.root.resizable(False, False)
            self.root.protocol('WM_DELETE_WINDOW', lambda: None)
            frame = ttk.Frame(self.root, padding=24)
            frame.pack(fill='both', expand=True)
            self.label = ttk.Label(frame, text='🔍 正在检查版本', font=('Arial', 15))
            self.label.pack(anchor='w')
            self.detail = ttk.Label(frame, text='正在准备启动器', foreground='#666')
            self.detail.pack(anchor='w', pady=(10, 14))
            self.progress = ttk.Progressbar(frame, maximum=100, mode='determinate')
            self.progress.pack(fill='x')
            self.root.update()
        except Exception:
            self.root = None

    def phase(self, text, detail=''):
        if self.root:
            self.label.config(text=text)
            self.detail.config(text=detail)
            self.progress.config(value=0)
            self.root.update_idletasks()
            self.root.update()
        else:
            print(text, detail, flush=True)

    def download(self, done, total):
        if self.root:
            value = (done / total * 100) if total else 0
            self.progress.config(value=value)
            self.detail.config(text=f'{done / 1048576:.1f} MB' + (f' / {total / 1048576:.1f} MB' if total else ''))
            self.root.update_idletasks()
            self.root.update()

    def error(self, message):
        if self.root:
            from tkinter import messagebox
            messagebox.showerror('启动失败', message, parent=self.root)
        else:
            print(message, file=sys.stderr, flush=True)

    def close(self):
        if self.root:
            self.root.destroy()


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, expected, ui=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        raise ValueError('Invalid release URL')
    opener = build_opener(SecureRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    digest, size = hashlib.sha256(), 0
    with opener.open(Request(url, headers={'User-Agent': 'LynkCoHelper-bootstrap/1'}), timeout=30) as response, destination.open('wb') as output:
        headers = getattr(response, 'headers', None)
        total = int(headers.get('Content-Length', '0') or 0) if headers else 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise ValueError('Resource archive too large')
            digest.update(chunk)
            output.write(chunk)
            if ui:
                ui.download(size, total)
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
    ui = ProgressUI()
    stop_file = session / 'stop'
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        ui.phase('🔍 正在检查版本', '准备下载客户端资源')
        ui.phase('⬇️ 正在下载客户端资源', '资源将保存到临时目录，客户端退出后自动删除')
        archive = session / 'resources.tar.gz'
        download(URL, archive, SHA256, ui)
        ui.phase('🧮 正在校验 SHA-256', '校验通过后才会解压和运行')
        archive_size = archive.stat().st_size if archive.exists() else 0
        ui.download(archive_size, archive_size)
        ui.phase('📦 正在准备运行环境', '正在解压客户端资源')
        extract(archive, session / 'app')
        executable = session / 'app' / EXECUTABLE
        if not executable.is_file():
            raise ValueError('Client executable missing')
        child = subprocess.Popen([str(executable), '--bootstrap-parent', json.dumps(owner), '--bootstrap-stop', str(stop_file)])
        try:
            record.write_text(json.dumps([owner, identity(child.pid)]))
        except psutil.NoSuchProcess:
            return child.wait()
        ui.phase('🚀 正在启动客户端', '启动完成后可关闭此窗口')
        return child.wait()
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        ui.error(f'客户端启动失败：{error}')
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
        ui.phase('🧹 正在清理临时资源', '正在删除本次下载和解压目录')
        shutil.rmtree(session, ignore_errors=True)
        ui.close()


if __name__ == '__main__':
    raise SystemExit(main())
