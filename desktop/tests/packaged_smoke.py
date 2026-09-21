"""Exercise the shipped executable, including its embedded proxy runtime."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

executable = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ports = []
    for _ in range(3):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            ports.append(probe.getsockname()[1])
    (root / 'state').mkdir()
    (root / 'state' / 'ports.json').write_text(json.dumps({'proxy': ports[0], 'certificate': ports[1]}))
    app = subprocess.Popen([str(executable), '--no-browser', '--state-dir', str(root / 'state'),
                            '--cloud-url', f'http://127.0.0.1:{ports[2]}'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 45
        while not (root / 'state' / 'instance.json').exists():
            if app.poll() is not None or time.monotonic() > deadline:
                detail = app.stderr.read().decode(errors='replace') if app.poll() is not None else 'still waiting for startup'
                raise AssertionError('Packaged application did not start: ' + detail)
            time.sleep(.1)
        info = json.loads((root / 'state' / 'instance.json').read_text())
        base, token = info['url'].split('#')
        for asset in ('', 'app.js', 'style.css', 'lucide.js'):
            with urlopen(base + asset, timeout=3) as response:
                assert response.status == 200 and len(response.read()) > 100
        with urlopen(Request(base + 'api/status', headers={'Authorization': 'Bearer ' + token}), timeout=3) as response:
            assert json.load(response)['ok'] is True
        with urlopen(Request(base + 'api/quit', data=b'{}', headers={'Content-Type':'application/json', 'Authorization':'Bearer '+token}), timeout=3) as response:
            assert json.load(response)['ok'] is True
        assert app.wait(timeout=8) == 0
    finally:
        if app.poll() is None:
            app.terminate()
            app.wait(timeout=8)
    port = ports[0]
    proxy_state = root / 'proxy.json'
    proxy_state.write_text(json.dumps({'peerIp':'127.0.0.1','captureEnabled':False}))
    if sys.platform == 'darwin':
        addon = executable.parent.parent / 'Resources' / 'desktop' / 'capture_addon.py'
    else:
        addon = executable.parent / '_internal' / 'desktop' / 'capture_addon.py'
    assert addon.is_file(), 'Bundled addon missing'
    process = subprocess.Popen([str(executable), '--proxy', '-q', '--listen-host', '127.0.0.1', '--listen-port', str(port),
                                '--set', 'confdir=' + str(root / 'ca'), '-s', str(addon)],
                               env=dict(os.environ, LYNKCO_PROXY_STATE=str(proxy_state), LYNKCO_CALLBACK_URL='http://127.0.0.1:1/internal/capture',
                                        LYNKCO_CALLBACK_TOKEN='fixture', LYNKCO_PHONE_PLATFORM='IOS'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError('Packaged proxy exited early')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Packaged proxy did not listen')
        assert (root / 'ca' / 'mitmproxy-ca-cert.cer').is_file()
    finally:
        process.terminate()
        process.wait(timeout=8)
print('PASS: packaged app, static assets, authenticated API, graceful exit, embedded proxy and local CA')
