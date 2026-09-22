import json
import tempfile
import unittest
import socket
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from desktop.proxy import ProxyManager, network_addresses


class ProxyLifecycleTests(unittest.TestCase):
    def test_start_accepts_and_persists_configured_proxy_port(self):
        with tempfile.TemporaryDirectory() as directory:
            ports = []
            for _ in range(2):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    ports.append(probe.getsockname()[1])
            (Path(directory) / 'ports.json').write_text(json.dumps({'proxy': ports[0], 'certificate': ports[1]}))
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture')
            child = MagicMock()
            child.poll.return_value = None
            with patch('desktop.proxy.network_addresses', return_value=[{'name':'fixture','address':'127.0.0.1'}]), \
                    patch('desktop.proxy.subprocess.Popen', return_value=child) as launch, \
                    patch('desktop.proxy.socket.create_connection', return_value=MagicMock()):
                state = manager.start('127.0.0.1', 'IOS', proxy_port=ports[0] + 2)
            try:
                self.assertEqual(state['port'], ports[0] + 2)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index('--listen-port') + 1], str(ports[0] + 2))
                saved = json.loads((Path(directory) / 'ports.json').read_text())
                self.assertEqual(saved['proxy'], ports[0] + 2)
            finally:
                manager.stop()

    def test_start_rejects_proxy_port_matching_certificate_port(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'ports.json').write_text(json.dumps({'proxy': 55255, 'certificate': 55268}))
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture')
            with patch('desktop.proxy.network_addresses', return_value=[{'address': '127.0.0.1'}]):
                with self.assertRaisesRegex(ValueError, '代理端口'):
                    manager.start('127.0.0.1', 'IOS', proxy_port=55268)

    def test_public_state_reports_phone_request_activity_separately_from_proxy_process(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture')
            manager.process = MagicMock()
            manager.process.poll.return_value = None
            manager.peer = '127.0.0.1'
            with patch('desktop.proxy.network_addresses', return_value=[{'address': '127.0.0.1'}]), \
                    patch('desktop.proxy.psutil.net_connections', return_value=[]):
                state = manager.public_state()
            self.assertEqual(state['phoneProxyState'], 'idle')
            self.assertEqual(state['phoneRequests']['active'], 0)

            manager.note_activity()
            with patch('desktop.proxy.network_addresses', return_value=[{'address': '127.0.0.1'}]), \
                    patch('desktop.proxy.psutil.net_connections', return_value=[]):
                state = manager.public_state()
            self.assertEqual(state['phoneProxyState'], 'recent')
            self.assertIsNotNone(state['phoneRequests']['lastAt'])

    def test_proxy_child_receives_parent_process_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            ports = []
            for _ in range(2):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    ports.append(probe.getsockname()[1])
            (Path(directory) / 'ports.json').write_text(json.dumps({'proxy': ports[0], 'certificate': ports[1]}))
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture')
            child = MagicMock()
            child.poll.return_value = None
            with patch('desktop.proxy.network_addresses', return_value=[{'name':'fixture','address':'127.0.0.1'}]), \
                    patch('desktop.proxy.subprocess.Popen', return_value=child) as launch, \
                    patch('desktop.proxy.socket.create_connection', return_value=MagicMock()):
                manager.start('127.0.0.1', 'IOS')
            try:
                parent = json.loads(launch.call_args.kwargs['env']['LYNKCO_PROXY_PARENT'])
                self.assertEqual(parent['pid'], os.getpid())
                self.assertGreater(parent['created'], 0)
            finally:
                manager.stop()

    def test_fixed_ports_do_not_fall_back_when_occupied(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            port = occupied.getsockname()[1]
            (Path(directory) / 'ports.json').write_text(json.dumps({'proxy': port, 'certificate': port + 1}))
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture')
            with patch('desktop.proxy.network_addresses', return_value=[{'address': '127.0.0.1'}]):
                with self.assertRaisesRegex(ValueError, '端口'):
                    manager.start('127.0.0.1', 'IOS')

    def test_active_wifi_can_use_non_rfc1918_address(self):
        entry = lambda address: SimpleNamespace(family=socket.AF_INET, address=address)
        interfaces = {'en0':[entry('11.39.142.1')], 'en1':[entry('100.64.1.2')],
                      'utun8':[entry('11.39.142.1')], 'lo0':[entry('127.0.0.1')],
                      'en2':[entry('192.168.1.2')]}
        stats = {name:SimpleNamespace(isup=name != 'en2') for name in interfaces}
        with patch('psutil.net_if_addrs',return_value=interfaces), patch('psutil.net_if_stats',return_value=stats):
            self.assertEqual(network_addresses(), [{'name':'en0','address':'11.39.142.1'}, {'name':'en1','address':'100.64.1.2'}])

    def test_real_proxy_certificate_pairing_passthrough_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProxyManager(directory, 'http://127.0.0.1:1/internal/capture', 'fixture-callback')
            try:
                with patch('desktop.proxy.network_addresses', return_value=[{'name':'fixture','address':'127.0.0.1'}]):
                    state = manager.start('127.0.0.1', 'IOS')
                process = manager.process
                self.assertTrue(state['running'])
                self.assertEqual(state['phoneProxyState'], 'not_paired')
                self.assertFalse(manager.accepts_peer('127.0.0.1'))
                with urlopen(state['pairUrl'], timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    pairing_page = response.read().decode('utf-8')
                self.assertEqual(manager.public_state()['phoneProxyState'], 'idle')
                self.assertIn('已安装并信任过本机证书，无需重复安装', pairing_page)
                self.assertNotIn('移除本次证书', pairing_page)
                self.assertNotIn('领克', pairing_page)
                self.assertTrue(manager.accepts_peer('127.0.0.1'))
                with urlopen(state['pairUrl'] + '/certificate.cer', timeout=3) as response:
                    certificate = response.read()
                    self.assertIn(b'BEGIN CERTIFICATE', certificate)
                    self.assertNotIn(b'PRIVATE KEY', certificate)
                    self.assertEqual(response.headers.get('Content-Disposition'), 'attachment; filename="LynkCoHelper-CA.cer"')
                for path in ['/mitmproxy-ca.pem', '/../ca/mitmproxy-ca.pem', '/env.json']:
                    from urllib.parse import urlsplit
                    parsed = urlsplit(state['pairUrl'])
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(f'{parsed.scheme}://{parsed.netloc}' + path, timeout=3)
                    self.assertEqual(caught.exception.code, 404)
                manager.disable_capture()
                self.assertTrue(manager.public_state()['running'])
                self.assertFalse(manager.accepts_peer('127.0.0.1'))
                self.assertFalse(json.loads((Path(directory)/'proxy-state.json').read_text())['captureEnabled'])
                manager.stop()
                self.assertIsNotNone(process.poll())
                self.assertFalse(manager.public_state()['running'])
                with patch('desktop.proxy.network_addresses', return_value=[{'name':'fixture','address':'127.0.0.1'}]):
                    restarted = manager.start('127.0.0.1', 'IOS')
                self.assertEqual(restarted['port'], state['port'])
                self.assertEqual(urlsplit(restarted['pairUrl']).port, urlsplit(state['pairUrl']).port)
            finally:
                manager.stop()


if __name__ == '__main__':
    unittest.main()
