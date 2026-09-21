"""Management identity is stored only in the operating system credential vault."""

import hashlib
import json


class CredentialStore:
    def __init__(self, cloud_url):
        self.account = hashlib.sha256(cloud_url.encode()).hexdigest()

    def _keyring(self):
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(('keyring.backends.macOS', 'keyring.backends.Windows')):
            raise ValueError('系统凭据库不可用；请使用 macOS 或 Windows 的正式版本')
        return keyring

    def load(self):
        try:
            value = self._keyring().get_password('LynkCoHelper', self.account)
            return json.loads(value) if value else None
        except Exception:
            raise ValueError('无法读取系统凭据库，请允许每日任务助手访问钥匙串或凭据管理器') from None

    def save(self, value):
        try:
            self._keyring().set_password('LynkCoHelper', self.account, json.dumps(value))
        except Exception:
            raise ValueError('无法保存到系统凭据库，请允许访问后重试；请保留恢复码') from None
