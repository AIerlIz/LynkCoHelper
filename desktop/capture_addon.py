"""mitmproxy subprocess entry: no flow logging or retained capture files."""

import asyncio
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from desktop.capture import AUTH_HOSTS, AUTH_PATHS, parse_session, capture_summary, request_summary


class CaptureAddon:
    def state(self):
        try:
            return json.loads(Path(os.environ['LYNKCO_PROXY_STATE']).read_text())
        except (OSError, ValueError, KeyError):
            return {}

    def client_connected(self, client):
        peer = self.state().get('peerIp')
        if not peer or client.peername[0] != peer:
            client.error = 'Phone is not paired'

    async def requestheaders(self, flow):
        await self.report(flow, 'pending')

    async def http_connect(self, flow):
        await self.report(flow, 'tunnel')

    async def error(self, flow):
        await self.report(flow, 'network_error')

    async def report(self, flow, outcome, session=None, summary=None):
        state = self.state()
        if not state.get('captureEnabled') or flow.client_conn.peername[0] != state.get('peerIp'):
            return
        try:
            summary = summary or request_summary(flow.request.pretty_url, flow.request.method,
                                                 flow.response.status_code if flow.response else None, outcome)
            summary['id'] = flow.id
            summary['method'] = flow.request.method
            await asyncio.to_thread(self.notify, session, flow.client_conn.peername[0], summary)
        except Exception:
            pass

    async def response(self, flow):
        state = self.state()
        if not state.get('captureEnabled') or flow.client_conn.peername[0] != state.get('peerIp'):
            return
        if flow.request.host == 'app-services.lynkco.com.cn' and flow.request.path.split('?', 1)[0] == '/auth/user/info':
            self.profile_contract(flow)
        if flow.request.host not in AUTH_HOSTS or flow.request.path.split('?', 1)[0] not in AUTH_PATHS:
            await self.report(flow, 'completed')
            return
        if not flow.response:
            return
        try:
            payload = flow.response.json() if len(flow.response.raw_content or b'') <= 65536 else None
        except (ValueError, UnicodeError):
            payload = None
        try:
            session = parse_session(flow.request.pretty_url, flow.request.headers, flow.response.status_code,
                                    payload, os.environ.get('LYNKCO_PHONE_PLATFORM'))
            summary = capture_summary(flow.request.pretty_url, flow.request.headers, flow.response.status_code,
                                      payload, os.environ.get('LYNKCO_PHONE_PLATFORM'))
            await self.report(flow, summary['outcome'], session, summary)
        except Exception:
            pass

    def notify(self, session, peer, summary):
        data = json.dumps({'session': session, 'peer': peer, 'summary': summary}).encode()
        request = Request(os.environ['LYNKCO_CALLBACK_URL'], data=data,
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['LYNKCO_CALLBACK_TOKEN']})
        with urlopen(request, timeout=3) as response:
            response.read(1024)

    def profile_contract(self, flow):
        """Keep protocol structure only, never personal values or credentials."""
        try:
            if not flow.response or len(flow.response.raw_content or b'') > 65536:
                return
            payload = flow.response.json()
            if not isinstance(payload, dict):
                return
            data = payload.get('data')
            headers = flow.request.headers
            record = {'httpStatus': flow.response.status_code, 'success': payload.get('code') == 'success',
                      'dataFields': [k for k in data if isinstance(k, str) and k.isidentifier() and len(k) < 64][:80] if isinstance(data, dict) else [],
                      'hasAppCode': headers.get('authorization', '').startswith('APPCODE '),
                      'hasSignature': bool(headers.get('x-ca-signature')),
                      'hasTokenHeader': bool(headers.get('token')),
                      'tokenHasBearerPrefix': headers.get('token', '').startswith('bearer'),
                      'hasAuthorizationBearer': headers.get('authorization', '').lower().startswith('bearer ')}
            destination = Path(os.environ['LYNKCO_PROXY_STATE']).with_name('profile-contract.json')
            destination.write_text(json.dumps(record))
            destination.chmod(0o600)
        except Exception:
            pass


addons = [CaptureAddon()]
