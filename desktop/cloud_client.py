"""Bounded cloud requests; credentials never cross a redirect."""

import hashlib
import json
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CloudError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class CloudClient:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'})) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('云端地址必须是 HTTPS 服务地址')
        self.base_url = base_url.rstrip('/')
        self.opener = build_opener(NoRedirect())
        self.direct_opener = build_opener(ProxyHandler({}), NoRedirect())
        self.loopback = parsed.hostname in {'127.0.0.1', 'localhost'}
        self.keys = {}
        self.lock = threading.Lock()

    def request(self, method, path, body=None, token=None):
        if not path.startswith('/v1/') and path != '/health':
            raise ValueError('无效的云端接口')
        data = json.dumps(body, separators=(',', ':')).encode() if body is not None else None
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'User-Agent': 'LynkCoHelper/0.1 (Desktop)'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if method != 'GET':
            digest = hashlib.sha256(method.encode() + path.encode() + (data or b'') + (token or '').encode()).hexdigest()
            with self.lock:
                now = time.monotonic()
                self.keys = {k: v for k, v in self.keys.items() if now - v[1] < 540}
                headers['Idempotency-Key'] = self.keys.setdefault(digest, (str(uuid.uuid4()), now))[0]
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            timeout = 150 if method == 'POST' and path == '/v1/binding/runs' else 40
            try:
                response = self.opener.open(request, timeout=timeout)
            except HTTPError as error:
                response = error
            except (URLError, OSError, TimeoutError):
                if self.loopback:
                    raise
                try:
                    response = self.direct_opener.open(request, timeout=timeout)
                except HTTPError as error:
                    response = error
            with response:
                payload = response.read(262145)
                if len(payload) > 262144:
                    raise ValueError()
                result = json.loads(payload)
            if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                raise ValueError()
        except (URLError, OSError, TimeoutError):
            raise CloudError('NETWORK', '暂时无法连接云端，请检查网络后重试') from None
        except (ValueError, TypeError):
            raise CloudError('BAD_RESPONSE', '云端返回异常，请稍后重试') from None
        if not result['ok']:
            code = result.get('error', {}).get('code', 'UNKNOWN')
            messages = {
                'CREDENTIAL_INVALID': '登录状态已失效，请重新获取',
                'UNAUTHORIZED': '管理凭证已失效，请使用恢复码恢复',
                'INVITATION_INVALID': '邀请码无效或已使用',
                'INVITE_INVALID': '邀请码无效或已使用',
                'CANDIDATE_EXPIRED': '验证结果已过期，请重新获取登录状态',
                'RATE_LIMITED': '操作太频繁，请稍后重试',
                'SERVICE_NOT_CONFIGURED': '云端尚未完成配置，请联系维护者',
                'APP_CONFIG_UNAVAILABLE': '云端应用配置需要维护，请联系维护者',
                'CAPTURE_INCOMPLETE': '登录状态不完整，请重新连接手机获取',
                'UPSTREAM_UNAVAILABLE': '上游服务暂时无法连接，请稍后重试',
                'BINDING_PAUSED': '请先恢复每日任务再执行',
                'QUOTA_REACHED': '试用名额已满，请联系维护者',
                'BATCH_FULL': '领取批次已领完或已过期，请联系管理员获取新链接',
                'SLOT_FULL': '该执行区间名额已满，请选择其他区间',
                'SHARE_UNAVAILABLE': '本次设备信息不支持分享，请关闭分享后重试',
                'NOTIFICATION_CONFLICT': 'Bark 和 Server 酱只能选择一个',
                'NOTIFICATION_NOT_CONFIGURED': '请先选择并保存一个推送渠道',
                'NOTIFICATION_TEST_FAILED': '配置已保存，但测试消息发送失败，请检查密钥',
                'SERVICE_UNAVAILABLE': '云端服务暂时不可用，请稍后重试',
                'CONFLICT': '状态正在更新，请刷新后重试',
                'NOT_FOUND': '记录不存在或已过期，请刷新后重试',
                'INVALID_REQUEST': '输入内容无效，请检查邀请码、恢复码或设置',
                'RESULT_UNKNOWN': '操作结果尚未确认，请先刷新状态',
            }
            raise CloudError(code, messages.get(code, '本次操作未完成，请刷新状态后重试'))
        if method != 'GET':
            with self.lock:
                self.keys.pop(digest, None)
        return result.get('data')
