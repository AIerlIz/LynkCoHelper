import json
import io
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
from urllib.error import URLError
from desktop.cloud_client import CloudClient, CloudError


class CloudClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response = {'ok': True, 'data': {'value': 1}}
        self.redirect = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append(dict(self.headers))
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.send_response(302 if owner.redirect else 200)
                self.send_header('Content-Type', 'application/json')
                if owner.redirect:
                    self.send_header('Location', '/v1/stolen')
                self.end_headers()
                self.wfile.write(json.dumps(owner.response).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = CloudClient('http://127.0.0.1:' + str(self.server.server_port))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_failed_retry_reuses_idempotency_key(self):
        self.response = {'ok': False, 'error': {'code': 'CONFLICT', 'message': 'secret-raw-upstream'}}
        for _ in range(2):
            with self.assertRaises(CloudError) as caught:
                self.client.request('POST', '/v1/owners', {'inviteCode': 'fixture'})
            self.assertNotIn('secret-raw-upstream', str(caught.exception))
        self.assertEqual(self.requests[0]['Idempotency-Key'], self.requests[1]['Idempotency-Key'])

    def test_successful_operations_get_new_keys(self):
        for _ in range(2):
            self.client.request('POST', '/v1/owners', {'inviteCode': 'fixture'})
        self.assertNotEqual(self.requests[0]['Idempotency-Key'], self.requests[1]['Idempotency-Key'])

    def test_request_identifies_the_desktop_application(self):
        self.client.request('POST', '/v1/owners', {'inviteCode':'fixture'})
        self.assertEqual(self.requests[0].get('User-Agent'), 'LynkCoHelper/0.1 (Desktop)')

    def test_http_remote_and_embedded_credentials_rejected(self):
        for url in ['http://example.com', 'https://user:password@example.com', 'https://example.com/redirect', 'https://example.com#token']:
            with self.assertRaises(ValueError):
                CloudClient(url)

    def test_redirect_never_receives_bearer(self):
        self.redirect = True
        self.response = {}
        with self.assertRaises(CloudError):
            self.client.request('POST', '/v1/owners', {}, 'secret')
        self.assertEqual(len(self.requests), 1)

    def test_remote_request_retries_direct_when_system_proxy_cannot_connect(self):
        client = CloudClient('https://cloud.example')
        client.opener = Mock()
        client.opener.open.side_effect = URLError('proxy unavailable')
        client.direct_opener = Mock()
        client.direct_opener.open.return_value = io.BytesIO(b'{"ok":true,"data":{"value":1}}')

        self.assertEqual(client.request('GET', '/health'), {'value': 1})

        client.opener.open.assert_called_once()
        client.direct_opener.open.assert_called_once()


if __name__ == '__main__':
    unittest.main()
