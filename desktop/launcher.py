"""Double-click entrypoint; a single background process owns each user profile."""

import argparse
import atexit
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path


def state_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'LynkCoHelper'
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'LynkCoHelper'


class InstanceLock:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        os.chmod(path, 0o600)

    def acquire(self):
        try:
            if sys.platform == 'win32':
                import msvcrt
                self.file.seek(0)
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def check_integrity():
    if not getattr(sys, 'frozen', False):
        return
    import desktop
    from desktop.integrity import verify_release
    try:
        from _release_integrity import MANIFEST_DIGEST
        valid, reason = verify_release(Path(desktop.__file__).parent, MANIFEST_DIGEST)
    except ImportError:
        valid, reason = False, '缺少发布校验信息'
    if not valid:
        message = '客户端文件校验失败，请重新下载客户端。' + reason
        if '--no-browser' not in sys.argv and '--proxy' not in sys.argv:
            try:
                import tkinter
                from tkinter import messagebox
                window = tkinter.Tk()
                window.withdraw()
                messagebox.showerror('客户端校验失败', message)
                window.destroy()
            except Exception:
                pass
        raise SystemExit(message)


def open_user_page(url):
    """Open the local user page through the platform's foreground launcher."""
    if sys.platform == 'darwin':
        try:
            subprocess.Popen(['open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    return webbrowser.open(url)


def main():
    check_integrity()
    if '--proxy' in sys.argv:
        from mitmproxy.tools.main import mitmdump
        position = sys.argv.index('--proxy')
        mitmdump(sys.argv[position + 1:])
        return
    parser = argparse.ArgumentParser(description='LynkCo desktop assistant')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--cloud-url')
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--bootstrap-parent')
    parser.add_argument('--bootstrap-stop', type=Path)
    options = parser.parse_args()
    import desktop
    package_root = Path(desktop.__file__).parent
    root = options.state_dir or state_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    lock = InstanceLock(root / 'instance.lock')
    instance_path = root / 'instance.json'
    if not lock.acquire():
        try:
            info = json.loads(instance_path.read_text())
            webbrowser.open(info['url'])
        except (OSError, ValueError, KeyError):
            pass
        return
    from desktop.binding import Controller
    from desktop.cloud_client import CloudClient
    from desktop.credential_store import CredentialStore
    from desktop.local_api import make_server
    from desktop.proxy import ProxyManager

    config_path = package_root / 'service.json'
    config = json.loads(config_path.read_text())
    cloud_url = options.cloud_url or config['cloudUrl']
    controller = Controller(CloudClient(cloud_url), CredentialStore(cloud_url))
    token, callback_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    server = make_server(controller, package_root / 'web', token, callback_token)
    base = 'http://127.0.0.1:' + str(server.server_port)
    controller.proxy = ProxyManager(root, base + '/internal/capture', callback_token)
    url = base + '/#' + token
    instance_path.write_text(json.dumps({'pid': os.getpid(), 'url': url, 'port': server.server_port}))
    instance_path.chmod(0o600)

    def cleanup():
        controller.proxy.stop()
        instance_path.unlink(missing_ok=True)

    atexit.register(cleanup)

    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if options.bootstrap_parent:
        import psutil
        import time
        parent = json.loads(options.bootstrap_parent)

        def watch_bootstrap():
            while True:
                if options.bootstrap_stop and options.bootstrap_stop.exists():
                    break
                try:
                    if psutil.Process(parent['pid']).create_time() != parent['created']:
                        break
                except psutil.NoSuchProcess:
                    break
                except psutil.AccessDenied:
                    pass
                time.sleep(1)
            server.shutdown()

        threading.Thread(target=watch_bootstrap, daemon=True).start()
    if not options.no_browser:
        open_user_page(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
