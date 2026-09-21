"""Extract a complete session from a single successful authentication exchange."""

import re
from urllib.parse import parse_qs, urlsplit, unquote

AUTH_HOSTS = frozenset({"app-services.lynkco.com.cn", "app-api-gw-toc.lynkco.com"})
AUTH_PATHS = frozenset({"/auth/login/refresh", "/auth/login/mobileCodeLogin", "/auth/login/sliding/login"})


def display_path(value):
    path = urlsplit(value).path
    parts = path.split('/')
    sensitive = {'token', 'refreshtoken', 'authorization', 'password', 'mobile', 'phone', 'email'}
    result = []
    for index, part in enumerate(parts):
        decoded = unquote(part)
        previous = unquote(parts[index - 1]).lower() if index else ''
        hidden = (previous in sensitive or len(decoded) > 48 or '@' in decoded or
                  re.search(r'\d{6,}|[a-fA-F0-9]{24,}', decoded) or
                  any(c in decoded for c in '?;=\r\n'))
        result.append('[redacted]' if hidden else part)
    return '/'.join(result)[:1024]


def request_summary(url, method, status, outcome):
    parsed = urlsplit(url)
    return {'host': parsed.hostname or '',
            'path': display_path(url),
            'method': method if method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'CONNECT') else 'OTHER',
            'status': status, 'outcome': outcome, 'fields': {}}


def capture_summary(url, headers, status, response, platform_hint=None):
    """Never return query values, headers, response values, or exception text."""
    parsed = urlsplit(url)
    if parsed.hostname not in AUTH_HOSTS or parsed.path not in AUTH_PATHS:
        return None
    data = response.get('data') if isinstance(response, dict) else None
    dto = data.get('centerTokenDto') if isinstance(data, dict) else None
    dto = dto if isinstance(dto, dict) else {}
    query = parse_qs(parsed.query, max_num_fields=64)
    header = {str(k).lower(): v for k, v in headers.items()}
    def parameter(name):
        values = query.get(name, [])
        return values[0] if len(values) == 1 else None
    fields = {
        'token': bool(clean_string(dto.get('token'))),
        'refreshToken': bool(clean_string(dto.get('refreshToken')) or
                             (parsed.path == '/auth/login/refresh' and clean_string(parameter('refreshToken')))),
        'deviceId': bool(clean_string(parameter('deviceId') or parameter('hardwareDeviceId'), 256)),
        'platform': (clean_string(parameter('deviceType') or header.get('publicplatform') or platform_hint, 32) or '').upper() in ('IOS', 'ANDROID'),
    }
    outcome = ('http_error' if status != 200 else 'invalid_json' if not isinstance(response, dict)
               else 'business_error' if response.get('code') != 'success'
               else 'captured' if parse_session(url, headers, status, response, platform_hint)
               else 'incomplete')
    return {'host': parsed.hostname, 'path': parsed.path, 'status': status,
            'outcome': outcome, 'fields': fields}


def clean_string(value, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum:
        return None
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def parse_session(url, headers, status, response, platform_hint=None):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in AUTH_HOSTS or parsed.port not in (None, 443):
            return None
        if parsed.path not in AUTH_PATHS or status != 200 or not isinstance(response, dict):
            return None
        if response.get("code") != "success":
            return None
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("centerTokenDto"), dict):
            return None
        dto = data["centerTokenDto"]
        query = parse_qs(parsed.query, max_num_fields=64)
        header = {str(key).lower(): value for key, value in headers.items()}

        def parameter(name):
            values = query.get(name, [])
            return values[0] if len(values) == 1 else None

        token = clean_string(dto.get("token"))
        refresh = clean_string(dto.get("refreshToken"))
        if not refresh and parsed.path == "/auth/login/refresh":
            refresh = clean_string(parameter("refreshToken"))
        device = clean_string(parameter("deviceId") or parameter("hardwareDeviceId"), 256)
        platform = clean_string(parameter("deviceType") or header.get("publicplatform") or platform_hint, 32)
        platform = platform.upper() if platform else None
        if not token or not refresh or not device or platform not in {"IOS", "ANDROID"}:
            return None
        session = {"token": token, "refreshToken": refresh, "deviceId": device, "platform": platform}
        for target, value in {
            "glDevId": header.get("gl_dev_id"),
            "deviceImei": header.get("imei"),
            "appVersion": parameter("appVersion") or header.get("appversioncode") or header.get("gl_app_version"),
            "appBuild": header.get("appversionname") or header.get("gl_app_build"),
        }.items():
            cleaned = clean_string(value, 256)
            if cleaned:
                session[target] = cleaned
        return session
    except (ValueError, TypeError, AttributeError):
        return None
