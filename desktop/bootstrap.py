"""Release-pinned downloader. Hashes are compiled into the bootstrap executable."""

import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler

import certifi
import psutil

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXTRACTED = 2 * 1024 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BUDGET = 90
SOCKET_TIMEOUT = 5


class ExistingInstanceBusy(RuntimeError):
    def __init__(self, message, url):
        super().__init__(message)
        self.url = url


def enable_windows_dpi_awareness():
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if hasattr(user32, 'SetProcessDpiAwarenessContext'):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        elif hasattr(ctypes.windll, 'shcore'):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        else:
            user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class ProgressUI:
    def __init__(self):
        self.root = self.label = self.detail = self.progress = None
        self.cancelled = False
        # Release launchers always show a Tk window. Source and test runs keep
        # terminal output unless the UI is explicitly requested.
        if not getattr(sys, 'frozen', False) and os.environ.get('LYNKCO_FORCE_PROGRESS_UI') != '1':
            return
        try:
            enable_windows_dpi_awareness()
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.title('LynkCoHelper')
            self.root.geometry('460x180')
            self.root.resizable(False, False)
            self.root.protocol('WM_DELETE_WINDOW', self.request_close)
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.focus_force()
            self.root.deiconify()
            self.root.after(3000, lambda: self.root.attributes('-topmost', False))
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
            self._check_cancelled()
            self.label.config(text=text)
            self.detail.config(text=detail)
            self.progress.stop()
            self.progress.config(mode='determinate')
            self.progress.config(value=0)
            self.root.update_idletasks()
            self.root.update()
            self._check_cancelled()
        else:
            self._terminal(text, detail)

    def download(self, done, total):
        if self.root:
            self._check_cancelled()
            if total:
                self.progress.stop()
                self.progress.config(mode='determinate', value=done / total * 100)
            else:
                self.progress.config(mode='indeterminate')
                self.progress.start(12)
            self.detail.config(text=f'{done / 1048576:.1f} MB' + (f' / {total / 1048576:.1f} MB' if total else ''))
            self.root.update_idletasks()
            self.root.update()
            self._check_cancelled()

    def request_close(self):
        self.cancelled = True

    def _check_cancelled(self):
        if self.cancelled:
            raise KeyboardInterrupt

    def error(self, message):
        if self.root:
            from tkinter import messagebox
            messagebox.showerror('启动失败', message, parent=self.root)
        else:
            self._terminal(message, error=True)

    @staticmethod
    def _terminal(*parts, error=False):
        stream = sys.stderr if error else sys.stdout
        if stream is None:
            return
        try:
            print(*parts, file=stream, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(stream, 'encoding', None) or 'ascii'
            safe = ' '.join(str(part).encode(encoding, 'replace').decode(encoding) for part in parts)
            print(safe, file=stream, flush=True)

    def close(self):
        if self.root:
            self.progress.stop()
            self.root.destroy()
            self.root = None

    def pump(self):
        if self.root:
            self._check_cancelled()
            self.root.update_idletasks()
            self.root.update()
            self._check_cancelled()


class WorkerEvents:
    def __init__(self):
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self._callbacks = set()
        self._callbacks_lock = threading.Lock()

    def phase(self, text, detail=''):
        self.events.put(('phase', (text, detail)))

    def download(self, done, total):
        self.events.put(('download', (done, total)))

    def cancel(self):
        self.cancelled.set()
        with self._callbacks_lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def register_cancel_callback(self, callback):
        with self._callbacks_lock:
            if self.cancelled.is_set():
                cancelled = True
            else:
                self._callbacks.add(callback)
                cancelled = False
        if cancelled:
            callback()

        def unregister():
            with self._callbacks_lock:
                self._callbacks.discard(callback)

        return unregister

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise KeyboardInterrupt


def run_worker(task, ui):
    events = WorkerEvents()
    completed = threading.Event()
    outcome = {}

    def work():
        try:
            outcome['result'] = task(events)
        except BaseException as error:
            outcome['error'] = error
        finally:
            completed.set()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    while not completed.is_set():
        try:
            ui.pump()
            dispatch_worker_events(events, ui)
        except KeyboardInterrupt:
            events.cancel()
        completed.wait(.05)
    dispatch_worker_events(events, ui)
    if events.cancelled.is_set():
        raise KeyboardInterrupt
    if 'error' in outcome:
        raise outcome['error']
    return outcome['result']


def dispatch_worker_events(events, ui):
    while True:
        try:
            name, arguments = events.events.get_nowait()
        except queue.Empty:
            return
        getattr(ui, name)(*arguments)


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def existing_instance_url(root, expected_release=None):
    try:
        record = json.loads((root / 'instance.json').read_text())
        pid, value = record['pid'], record['url']
        if type(pid) is not int or pid <= 0 or not psutil.pid_exists(pid):
            return None
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or not parsed.fragment or len(parsed.fragment) > 128):
            return None
        status = urlunsplit((parsed.scheme, parsed.netloc, '/api/status', '', ''))
        request = Request(status, headers={'Authorization': 'Bearer ' + parsed.fragment})
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
            payload = response.read(65537)
        result = json.loads(payload)
        if len(payload) > 65536 or not isinstance(result, dict) or result.get('ok') is not True:
            return None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if expected_release is None or record.get('releaseSha') == expected_release:
        return value
    data = result.get('data')
    proxy = data.get('proxy') if isinstance(data, dict) else None
    if isinstance(proxy, dict) and proxy.get('running') is True:
        raise ExistingInstanceBusy('旧版客户端仍连接着手机代理，请先在已打开的页面中断开连接，再重新启动新版。', value)
    quit_url = urlunsplit((parsed.scheme, parsed.netloc, '/api/quit', '', ''))
    quit_request = Request(
        quit_url,
        data=b'{}',
        headers={'Authorization': 'Bearer ' + parsed.fragment, 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(quit_request, timeout=2) as response:
            quit_payload = response.read(65537)
        quit_result = json.loads(quit_payload)
        if (len(quit_payload) > 65536 or not isinstance(quit_result, dict)
                or quit_result.get('ok') is not True):
            raise ValueError()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ExistingInstanceBusy('无法关闭正在运行的旧版客户端，请先退出旧版后重试。', value) from error
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            (root / 'instance.json').unlink(missing_ok=True)
            return None
        time.sleep(.1)
    raise ExistingInstanceBusy('旧版客户端尚未完全退出，请稍后重新启动新版。', value)


def open_page(url):
    if sys.platform == 'darwin':
        try:
            subprocess.Popen(['open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)


def wait_for_child(child, ui):
    while True:
        try:
            return child.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            ui.pump()


def remaining_download_time(deadline):
    if deadline is None:
        return 30
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Download exceeded the 90-second limit')
    return min(SOCKET_TIMEOUT, remaining)


def check_cancelled(ui):
    if ui and hasattr(ui, 'check_cancelled'):
        ui.check_cancelled()


def register_cancel_callback(ui, callback):
    if ui and hasattr(ui, 'register_cancel_callback'):
        return ui.register_cancel_callback(callback)
    return lambda: None


def open_with_budget(opener, request, deadline, ui):
    completed = threading.Event()
    abandoned = threading.Event()
    outcome = {}
    lock = threading.Lock()

    def connect():
        try:
            response = opener.open(request, timeout=remaining_download_time(deadline))
            with lock:
                if abandoned.is_set():
                    response.close()
                else:
                    outcome['response'] = response
        except BaseException as error:
            with lock:
                outcome['error'] = error
        finally:
            completed.set()

    thread = threading.Thread(target=connect, daemon=True)
    thread.start()
    try:
        while not completed.wait(.05):
            check_cancelled(ui)
            remaining_download_time(deadline)
        check_cancelled(ui)
        remaining_download_time(deadline)
        if 'error' in outcome:
            raise outcome['error']
        return outcome['response']
    except BaseException:
        abandoned.set()
        with lock:
            response = outcome.get('response')
            if response is not None:
                response.close()
        raise


def read_with_budget(response, size, deadline, ui):
    completed = threading.Event()
    abandoned = threading.Event()
    outcome = {}
    lock = threading.Lock()

    def read_chunk():
        try:
            chunk = response.read(size)
            with lock:
                if not abandoned.is_set():
                    outcome['chunk'] = chunk
        except BaseException as error:
            with lock:
                outcome['error'] = error
        finally:
            completed.set()

    thread = threading.Thread(target=read_chunk, daemon=True)
    thread.start()
    try:
        while not completed.wait(.05):
            check_cancelled(ui)
            remaining_download_time(deadline)
        check_cancelled(ui)
        remaining_download_time(deadline)
        with lock:
            if 'error' in outcome:
                raise outcome['error']
            return outcome.get('chunk', b'')
    except BaseException:
        abandoned.set()
        try:
            response.close()
        except BaseException:
            pass
        raise


def download(url, destination, expected, ui=None, deadline=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        raise ValueError('Invalid release URL')
    check_cancelled(ui)
    opener = build_opener(SecureRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    digest, size = hashlib.sha256(), 0
    request = Request(url, headers={'User-Agent': 'LynkCoHelper-bootstrap/1'})
    with open_with_budget(opener, request, deadline, ui) as response:
        unregister = register_cancel_callback(ui, response.close)
        timer = None
        if deadline is not None:
            timer = threading.Timer(max(0, deadline - time.monotonic()), response.close)
            timer.daemon = True
            timer.start()
        try:
            with destination.open('wb') as output:
                headers = getattr(response, 'headers', None)
                total = int(headers.get('Content-Length', '0') or 0) if headers else 0
                while chunk := read_with_budget(response, 1024 * 1024, deadline, ui):
                    check_cancelled(ui)
                    remaining_download_time(deadline)
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise ValueError('Resource archive too large')
                    digest.update(chunk)
                    output.write(chunk)
                    if ui:
                        ui.download(size, total)
                remaining_download_time(deadline)
        finally:
            unregister()
            if timer:
                timer.cancel()
    check_cancelled(ui)
    if digest.hexdigest() != expected:
        raise ValueError('Resource SHA-256 mismatch; download rejected')


def file_digest(path, ui=None, deadline=None):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            check_cancelled(ui)
            remaining_download_time(deadline)
            size += len(chunk)
            if size > MAX_ARCHIVE:
                return None
            digest.update(chunk)
    return digest.hexdigest()


def cached_archive(root, url, expected, ui=None, deadline=None):
    if len(expected) != 64 or any(character not in '0123456789abcdef' for character in expected.lower()):
        raise ValueError('Invalid resource SHA-256')
    cache = root / 'cache'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = cache / f'{expected.lower()}.tar.gz'
    if archive.is_file() and not archive.is_symlink() and file_digest(archive, ui, deadline) == expected.lower():
        prune_archives(cache, archive)
        return archive
    archive.unlink(missing_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix='download-', suffix='.tmp', dir=cache)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if ui:
            ui.phase('⬇️ 正在下载客户端资源', '首次下载完成后将复用已校验的本地资源')
        download(url, temporary, expected.lower(), ui, deadline)
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive)
        prune_archives(cache, archive)
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def download_with_retries(root, url, expected, ui=None):
    deadline = time.monotonic() + DOWNLOAD_BUDGET
    error = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        check_cancelled(ui)
        if ui:
            ui.phase('⬇️ 正在下载客户端资源', f'正在下载客户端资源（第 {attempt} / {DOWNLOAD_ATTEMPTS} 次）')
        try:
            return cached_archive(root, url, expected, ui, deadline)
        except KeyboardInterrupt:
            raise
        except (OSError, TimeoutError, ValueError) as caught:
            error = caught
            if time.monotonic() >= deadline:
                break
    raise RuntimeError(f'下载失败（已尝试 {attempt} 次）：{error}') from error


def prune_archives(cache, current):
    for candidate in cache.glob('*.tar.gz'):
        if candidate == current or candidate.is_symlink() or not candidate.is_file():
            continue
        name = candidate.name.removesuffix('.tar.gz')
        if len(name) == 64 and all(character in '0123456789abcdef' for character in name.lower()):
            candidate.unlink(missing_ok=True)


def extraction_path(destination, member):
    root = destination.resolve()
    path = destination / member.name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(root):
        raise tarfile.FilterError('Archive member escapes extraction directory')
    return path


def copy_member(bundle, member, destination, ui):
    path = extraction_path(destination, member)
    if member.isdir():
        path.mkdir(exist_ok=True)
        if member.mode is not None:
            os.chmod(path, member.mode)
        return
    if member.issym():
        os.symlink(member.linkname, path)
        return
    if not (member.isfile() or member.islnk()):
        raise tarfile.FilterError('Unsupported archive member')
    source = bundle.extractfile(member)
    if source is None:
        raise tarfile.FilterError('Archive member has no file content')
    with source, path.open('wb') as output:
        while chunk := source.read(1024 * 1024):
            check_cancelled(ui)
            output.write(chunk)
    if member.mode is not None:
        os.chmod(path, member.mode)


def extract(archive, destination, ui=None):
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) > 50000 or sum(m.size for m in members) > MAX_EXTRACTED:
            raise ValueError('Resource archive exceeds extraction limit')
        # Validate all links and paths before any file is written.
        safe_members = []
        for member in members:
            check_cancelled(ui)
            safe_members.append(tarfile.data_filter(member, str(destination)))
        for member in safe_members:
            check_cancelled(ui)
            copy_member(bundle, member, destination, ui)


def prepare_launch(base, session, url, expected, executable, events):
    try:
        archive = download_with_retries(base, url, expected, events)
    except KeyboardInterrupt:
        raise
    except Exception as error:
        raise RuntimeError(f'下载客户端资源失败：{error}') from error
    events.phase('🧮 正在校验 SHA-256', '校验通过后才会解压和运行')
    archive_size = archive.stat().st_size if archive.exists() else 0
    events.download(archive_size, archive_size)
    events.phase('📦 正在准备运行环境', '正在解压客户端资源')
    try:
        extract(archive, session / 'app', events)
    except KeyboardInterrupt:
        raise
    except Exception as error:
        raise RuntimeError(f'解压客户端资源失败：{error}') from error
    path = session / 'app' / executable
    if not path.is_file():
        raise ValueError('客户端启动准备失败：未找到客户端程序')
    return path


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
    state_root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'Library' / 'Application Support'))) / 'LynkCoHelper'
    try:
        existing = existing_instance_url(state_root, SHA256)
    except ExistingInstanceBusy as error:
        open_page(error.url)
        ui = ProgressUI()
        ui.error(str(error))
        ui.close()
        return 1
    if existing:
        open_page(existing)
        return 0
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
        ui.phase('🔍 正在检查版本', '正在检查本地客户端资源')
        executable = run_worker(
            lambda events: prepare_launch(base, session, URL, SHA256, EXECUTABLE, events), ui
        )
        child = subprocess.Popen([
            str(executable), '--bootstrap-parent', json.dumps(owner), '--bootstrap-stop', str(stop_file),
            '--release-sha', SHA256,
        ])
        try:
            record.write_text(json.dumps([owner, identity(child.pid)]))
        except psutil.NoSuchProcess:
            return child.wait()
        ui.phase('🚀 正在启动客户端', '启动完成后可关闭此窗口')
        return wait_for_child(child, ui)
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
        try:
            ui.cancelled = False
            ui.phase('🧹 正在清理临时资源', '正在删除本次解压目录')
        finally:
            shutil.rmtree(session, ignore_errors=True)
            ui.close()


if __name__ == '__main__':
    raise SystemExit(main())
