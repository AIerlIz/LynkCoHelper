"""Local binding state, with explicit upload and stale-result protection."""

import copy
import threading
import time
from urllib.parse import urlsplit

from desktop.capture import clean_string


class Controller:
    OPERATION_TIMEOUT = 35

    def __init__(self, cloud, store):
        self.cloud, self.store = cloud, store
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.identity = None
        self.error = None
        try:
            self.identity = store.load()
        except ValueError as error:
            self.error = str(error)
        self.session = None
        self.generation = 0
        self.stage = 'idle'
        self.verification_error = None
        self.platform = None
        self.candidate = None
        self.binding = None
        self.runs = {'items': [], 'nextCursor': None}
        self.connected = False
        self.configured = False
        self.last_refresh = 0
        self.proxy = None
        self.capture_events = []
        self.schedule_windows = None
        self.schedule_windows_error = None

    def _operation_deadline(self):
        return time.monotonic() + self.OPERATION_TIMEOUT

    def _refresh_schedule_windows(self, deadline=None):
        try:
            result = self._request('GET', '/v1/schedule-windows', deadline=deadline) if self.identity else None
            if self.identity and (not isinstance(result, dict) or not isinstance(result.get('items'), list)):
                raise ValueError('暂时无法查询区间名额，请刷新后重试')
            with self.lock:
                self.schedule_windows, self.schedule_windows_error = result, None
        except ValueError:
            with self.lock:
                self.schedule_windows = None
                self.schedule_windows_error = '暂时无法查询区间名额，请刷新后重试'

    def record_capture(self, summary):
        from desktop.capture import AUTH_HOSTS, AUTH_PATHS, display_path
        if not isinstance(summary, dict):
            return
        host = clean_string(summary.get('host'), 253)
        if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
            return
        outcomes = ('captured', 'http_error', 'invalid_json', 'business_error', 'incomplete', 'pending', 'completed', 'network_error', 'tunnel')
        if summary.get('outcome') not in outcomes or (summary.get('status') is not None and type(summary.get('status')) is not int):
            return
        fields = summary.get('fields')
        if not isinstance(fields, dict):
            return
        event = {k: summary[k] for k in ('host', 'path', 'outcome', 'status')}
        is_auth = host in AUTH_HOSTS and event['path'] in AUTH_PATHS
        if not is_auth:
            path = clean_string(event['path'], 2048)
            event['path'] = display_path(path) if path and path.startswith('/') else '/[redacted]'
        event['fields'] = {k: fields.get(k) is True for k in ('token', 'refreshToken', 'deviceId', 'platform')} if is_auth else {}
        method = summary.get('method')
        event['method'] = method if method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'CONNECT') else 'OTHER'
        event_id = clean_string(summary.get('id'), 64)
        event['id'] = event_id if event_id and all(c in '0123456789abcdef-' for c in event_id) else None
        event['at'] = int(time.time() * 1000)
        with self.lock:
            if event['id']:
                self.capture_events = [e for e in self.capture_events if e.get('id') != event['id']]
            self.capture_events = (self.capture_events + [event])[-200:]

    def _cloud_request(self, method, path, body=None, token=None, deadline=None):
        if deadline is not None and deadline <= time.monotonic():
            raise ValueError('云端请求超时，请稍后重试')
        if deadline is None:
            return self.cloud.request(method, path, body, token)
        return self.cloud.request(method, path, body, token, deadline=deadline)

    def _request(self, method, path, body=None, deadline=None):
        if not self.identity:
            raise ValueError('请先输入邀请码或恢复码')
        return self._cloud_request(method, path, body, self.identity['managementToken'], deadline)

    def register(self, code, recover=False):
        with self.operation:
            deadline = self._operation_deadline()
            code = clean_string(code, 512)
            if not code:
                raise ValueError('请输入有效的邀请码或恢复码')
            if self.identity and not recover:
                raise ValueError('当前设备已经连接云端')
            if not recover:
                raise ValueError('请使用领取链接连接云端')
            payload = {'recoveryCode': code}
            parsed = urlsplit(code)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                base = urlsplit(self.cloud.base_url)
                if parsed.scheme != base.scheme or parsed.hostname != base.hostname or parsed.port != base.port or parsed.path.rstrip('/') != '/recover':
                    raise ValueError('恢复链接不是本助手的云端链接')
                if not parsed.fragment.startswith('token='):
                    raise ValueError('恢复链接格式无效')
                token = parsed.fragment[6:]
                if not token or len(token) > 128 or not all(char.isalnum() or char in '-_' for char in token):
                    raise ValueError('恢复链接格式无效')
                payload = {'recoveryToken': token}
            snapshot, version = self._freeze_capture()
            try:
                identity = self._cloud_request('POST', '/v1/users/recover', payload, deadline=deadline)
                result = self._adopt_identity(identity, deadline)
            except Exception:
                self._restore_capture(snapshot, version)
                raise
            if not result['saved']:
                self._restore_capture(snapshot, version)
            return result

    def claim(self, claim_code):
        """Redeem an invitation code, accepting legacy full claim links."""
        with self.operation:
            deadline = self._operation_deadline()
            value = clean_string(claim_code, 2048)
            if not value:
                raise ValueError('请输入有效的邀请码')
            parsed = urlsplit(value)
            token = value
            if parsed.scheme or parsed.netloc:
                base = urlsplit(self.cloud.base_url)
                if parsed.scheme != base.scheme or parsed.hostname != base.hostname or parsed.port != base.port:
                    raise ValueError('领取链接不是本助手的云端链接')
                parts = parsed.path.rstrip('/').split('/')
                if len(parts) != 3 or parts[1] != 'claim':
                    raise ValueError('领取链接格式无效')
                token = parts[2]
            if not clean_string(token, 128) or not all(c.isalnum() or c in '-_' for c in token):
                raise ValueError('邀请码格式无效')
            if self.identity:
                raise ValueError('当前设备已经连接云端')
            snapshot, version = self._freeze_capture()
            try:
                identity = self._cloud_request('POST', '/v1/claim/' + token, {}, deadline=deadline)
                result = self._adopt_identity(identity, deadline)
            except Exception:
                self._restore_capture(snapshot, version)
                raise
            if not result['saved']:
                self._restore_capture(snapshot, version)
            return result

    def reset_capture(self):
        """Discard a stale local binding flow before replacing an account."""
        with self.operation:
            if self.proxy and self.proxy.public_state().get('running'):
                raise ValueError('请先关闭手机代理并断开当前连接')
            with self.lock:
                self.session = self.candidate = None
                self.capture_events = []
                self.stage = 'idle'
                self.verification_error = None
                self.generation += 1
            return {'reset': True}

    def _adopt_identity(self, identity, deadline=None):
        recovery = identity['recoveryCode']
        saved = {key: identity[key] for key in ('userId', 'managementToken')}
        # Preserve the recovery code in the explicit response even if the OS vault denies access.
        try:
            self.store.save(saved)
        except ValueError:
            return {'recoveryCode': recovery, 'saved': False}
        with self.lock:
            self.identity = saved
            self.session = self.candidate = self.binding = None
            self.runs = {'items': [], 'nextCursor': None}
            self.capture_events = []
            self.generation += 1
            self.stage = 'idle'
            self.platform = None
            self.verification_error = None
            self.error = None
        self._refresh_schedule_windows(deadline)
        return {'recoveryCode': recovery, 'saved': True}

    def _freeze_capture(self):
        """Block a long-running account action from racing an active verification."""
        with self.lock:
            snapshot = {
                'session': copy.deepcopy(self.session),
                'candidate': copy.deepcopy(self.candidate),
                'stage': self.stage,
                'platform': self.platform,
                'verification_error': self.verification_error,
                'capture_events': copy.deepcopy(self.capture_events),
            }
            self.generation += 1
            version = self.generation
            self.candidate = None
            self.stage = 'cleanup'
            self.verification_error = None
            return snapshot, version

    def _restore_capture(self, snapshot, version):
        """Restore a cancelled account action without reviving its old worker."""
        with self.lock:
            if self.generation != version:
                return
            self.session = snapshot['session']
            self.platform = snapshot['platform']
            self.capture_events = snapshot['capture_events']
            if snapshot['stage'] == 'verifying':
                self.candidate = None
                self.stage = 'verification_failed'
                self.verification_error = '个人信息验证已中断，请重试'
                return
            self.candidate = snapshot['candidate']
            self.stage = snapshot['stage']
            self.verification_error = snapshot['verification_error']

    def receive_capture(self, session):
        if not isinstance(session, dict) or session.get('platform') not in ('IOS', 'ANDROID'):
            return False
        if not all(clean_string(session.get(key), 256 if key == 'deviceId' else 4096) for key in ('token', 'refreshToken', 'deviceId')):
            return False
        allowed = ('token', 'refreshToken', 'deviceId', 'platform', 'appVersion', 'appBuild', 'glDevId', 'deviceImei')
        captured = {k: session[k] for k in allowed if k in session and clean_string(session[k])}
        with self.lock:
            if self.stage == 'cleanup':
                return False
            if self.session == captured and self.stage in ('verifying', 'verified', 'verification_failed'):
                return True
            self.session = captured
            self.platform = session['platform']
            self.generation += 1
            self.candidate = None
            self.stage = 'verifying'
            self.verification_error = None
            version = self.generation
            captured = dict(self.session)
        threading.Thread(target=self._verify_capture, args=(captured, version), daemon=True).start()
        return True

    @staticmethod
    def _verification_error(error):
        if getattr(error, 'code', None) == 'CREDENTIAL_INVALID':
            return '登录状态已失效，请重新获取'
        return '个人信息验证暂时失败，请稍后重试'

    def _verify_capture(self, session, version):
        try:
            candidate = self._request('POST', '/v1/binding-candidates', {'session': session})
        except Exception as error:
            with self.lock:
                if version != self.generation or self.stage != 'verifying':
                    return
                self.candidate = None
                self.stage = 'verification_failed'
                self.verification_error = self._verification_error(error)
            return
        with self.lock:
            if version != self.generation or self.stage != 'verifying':
                return
            self.candidate = candidate
            self.stage = 'verified'
            self.verification_error = None

    def retry_verification(self):
        with self.lock:
            if not self.session:
                raise ValueError('尚未获取完整登录状态，请在手机上打开对应 App')
            if self.stage not in ('captured', 'verification_failed'):
                raise ValueError('个人信息正在验证或已经验证完成')
            self.stage = 'verifying'
            self.verification_error = None
            version, session = self.generation, dict(self.session)
        threading.Thread(target=self._verify_capture, args=(session, version), daemon=True).start()
        return {'started': True}

    def prepare(self):
        """Compatibility alias for old clients that requested verification explicitly."""
        return self.retry_verification()

    def stop_capture(self):
        """Invalidate background verification before the phone proxy is stopped."""
        with self.lock:
            self.session = self.candidate = None
            self.stage = 'idle'
            self.verification_error = None
            self.generation += 1

    def activate(self, settings):
        with self.operation:
            deadline = self._operation_deadline()
            with self.lock:
                candidate = self.candidate
                if not candidate or candidate['expiresAt'] <= time.time() * 1000:
                    self.candidate = None
                    self.stage = 'captured' if self.session else 'waiting'
                    raise ValueError('验证结果已过期，请重新获取登录状态')
                version = self.generation
                # Freeze capture during activation so a second phone flow cannot replace the preview.
                self.stage = 'cleanup'
            try:
                binding = self._request('POST', '/v1/binding-candidates/' + candidate['id'] + '/activate', settings, deadline)
            except Exception as error:
                with self.lock:
                    if version == self.generation:
                        self.stage = 'verified'
                if getattr(error, 'code', None) == 'SLOT_FULL':
                    self._refresh_schedule_windows(deadline)
                raise
            with self.lock:
                self.binding = binding
                self.session = self.candidate = None
                self.generation += 1
                self.verification_error = None
            if self.proxy:
                self.proxy.disable_capture()
            self._refresh_schedule_windows(deadline)
            return binding

    def refresh(self):
        with self.operation:
            deadline = self._operation_deadline()
            try:
                health = self._cloud_request('GET', '/health', deadline=deadline)
                binding = self._request('GET', '/v1/binding', deadline=deadline) if self.identity else None
                runs = self._request('GET', '/v1/binding/runs', deadline=deadline) if binding else {'items': [], 'nextCursor': None}
                self._refresh_schedule_windows(deadline)
                with self.lock:
                    self.connected, self.configured = True, health.get('configured', False)
                    self.binding, self.runs, self.error = binding, runs, None
                    self.last_refresh = time.time()
            except ValueError as error:
                with self.lock:
                    self.connected, self.error = False, str(error)
                raise
        return self.public_state()

    def settings(self, body):
        with self.operation:
            deadline = self._operation_deadline()
            try:
                binding = self._request('PATCH', '/v1/binding', body, deadline)
            except ValueError as error:
                if getattr(error, 'code', None) == 'SLOT_FULL':
                    self._refresh_schedule_windows(deadline)
                raise
            with self.lock:
                for key in ('inventory', 'inventoryError', 'avatarUrl'):
                    if self.binding and key in self.binding and key not in binding:
                        binding[key] = self.binding[key]
                self.binding = binding
            self._refresh_schedule_windows(deadline)
            return binding

    def test_notification(self):
        with self.operation:
            return self._request('POST', '/v1/binding/notifications/test', {})

    def run(self):
        with self.operation:
            return self._request('POST', '/v1/binding/runs', {})

    def delete(self):
        with self.operation:
            deadline = self._operation_deadline()
            snapshot, version = self._freeze_capture()
            try:
                result = self._request('DELETE', '/v1/binding', deadline=deadline)
            except Exception:
                self._restore_capture(snapshot, version)
                raise
            with self.lock:
                self.binding = None
                self.runs = {'items': [], 'nextCursor': None}
                if self.generation == version:
                    self.session = self.candidate = None
                    self.capture_events = []
                    self.platform = None
                    self.stage = 'idle'
                    self.verification_error = None
            self._refresh_schedule_windows(deadline)
            return result

    def history(self, cursor):
        from urllib.parse import urlencode
        with self.operation:
            return self._request('GET', '/v1/binding/runs?' + urlencode({'cursor': cursor}))

    def public_state(self):
        with self.lock:
            return copy.deepcopy({
                'hasIdentity': bool(self.identity), 'connected': self.connected, 'configured': self.configured,
                'error': self.error, 'lastRefreshAt': int(self.last_refresh * 1000),
                'binding': self.binding, 'runs': self.runs, 'candidate': self.candidate,
                'scheduleWindows': self.schedule_windows, 'scheduleWindowsError': self.schedule_windows_error,
                'capture': {'stage': self.stage, 'platform': self.platform,
                            'verificationError': self.verification_error, 'events': self.capture_events},
                'proxy': self.proxy.public_state() if self.proxy else {'running': False},
            })
