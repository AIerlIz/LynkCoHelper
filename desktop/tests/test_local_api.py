import http.client
import importlib.util
import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from test_binding import FakeCloud, MemoryStore, SESSION
from desktop.binding import Controller


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop.local_api'), 'local API must exist')
        from desktop.local_api import make_server
        self.controller = Controller(FakeCloud(), MemoryStore())
        self.server = make_server(self.controller, Path('desktop/web'), 'local-secret', 'callback-secret')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()

    def request(self, path, token='local-secret', origin=None, host=None, body=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        headers = {'Authorization': 'Bearer ' + token}
        if origin:
            headers['Origin'] = origin
        if host:
            headers['Host'] = host
        if body is not None:
            headers['Content-Type'] = 'application/json'
        client.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = client.getresponse()
        result = response.status, response.read()
        client.close()
        return result

    def test_status_requires_token(self):
        self.assertEqual(self.request('/api/status', token='')[0], 401)
        self.assertEqual(self.request('/api/status')[0], 200)

    def test_static_resources_are_never_cached(self):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        for path in ('/', '/app.js', '/style.css'):
            client.request('GET', path)
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertIn("img-src 'self' data: blob: https:", response.getheader('Content-Security-Policy'))
            response.read()
        client.close()

    def test_claim_link_route_adopts_cloud_identity(self):
        status, body = self.request('/api/claim', body={'claimUrl': 'https://lynkco.ltools.asia/claim/claim-token_123456'})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['data']['saved'])

    def test_claim_route_accepts_invitation_code(self):
        status, body = self.request('/api/claim', body={'claimCode': 'claim-token_123456'})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['data']['saved'])

    def test_capture_reset_route_clears_stale_replacement_state(self):
        self.controller.claim('claim-token_123456')
        self.controller.receive_capture(SESSION)
        status, body = self.request('/api/capture/reset', body={})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['data']['reset'])
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'idle')

    def test_notification_test_route_calls_cloud(self):
        self.controller.claim('claim-token_123456')
        status, body = self.request('/api/binding/notification-test', body={})
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)['data'])
        self.assertIn(('POST', '/v1/binding/notifications/test', {}), self.controller.cloud.calls)

    def test_startup_never_uses_reverse_dns(self):
        from desktop.local_api import make_server
        with patch('socket.getfqdn', side_effect=AssertionError('unexpected DNS dependency')):
            server = make_server(self.controller, Path('desktop/web'), 'fixture', 'callback')
            server.server_close()

    def test_host_and_origin_are_checked(self):
        self.assertEqual(self.request('/api/status', host='evil.example')[0], 403)
        self.assertEqual(self.request('/api/status', origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('/api/status', origin=self.origin)[0], 200)

    def test_internal_callback_has_separate_authorization(self):
        self.assertEqual(self.request('/internal/capture', body={'session': SESSION})[0], 401)

    def test_rejected_post_does_not_poison_keepalive_connection(self):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        headers = {'Authorization': 'Bearer local-secret', 'Content-Type': 'application/json'}
        client.request('POST', '/internal/capture', json.dumps({'session': SESSION}), headers)
        self.assertEqual(client.getresponse().status, 401)
        client.request('GET', '/api/status', headers={'Authorization': 'Bearer local-secret'})
        self.assertEqual(client.getresponse().status, 200)
        client.close()

    def test_traversal_does_not_serve_local_files(self):
        for path in ['/../capture.py', '/%2e%2e/capture.py', '/env.json', '/desktop/capture.py']:
            self.assertEqual(self.request(path)[0], 404)

    def test_capture_secrets_are_not_in_status(self):
        self.controller.receive_capture(SESSION)
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(b'mobile-token-secret', body)
        self.assertNotIn(b'mobile-refresh-secret', body)

    def test_diagnostics_are_bounded_and_strip_unknown_values(self):
        summary = {'host': 'app-services.lynkco.com.cn', 'path': '/auth/login/refresh',
                   'status': 200, 'outcome': 'captured', 'token': 'secret-value',
                   'fields': {'token': True, 'refreshToken': 'secret-value', 'deviceId': True}}
        for _ in range(205):
            self.controller.record_capture(summary)
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(b'secret-value', body)
        events = json.loads(body)['data']['capture']['events']
        self.assertEqual(len(events), 200)
        self.assertFalse(events[0]['fields']['refreshToken'])

    def test_diagnostics_callback_requires_paired_peer(self):
        from unittest.mock import Mock
        self.controller.proxy = Mock()
        self.controller.proxy.accepts_peer.return_value = False
        self.assertEqual(self.request('/internal/capture', token='callback-secret', body={'peer': 'other'})[0], 403)
        self.assertEqual(self.controller.capture_events, [])


if __name__ == '__main__':
    unittest.main()
