"""Management identity is stored only in the operating system credential vault."""

import hashlib
import json
import os
import sys
from pathlib import Path


class CredentialStore:
    def __init__(self, cloud_url):
        self.account = hashlib.sha256(cloud_url.encode()).hexdigest()

    def _invalid_marker(self):
        if sys.platform == 'darwin':
            root = Path.home() / 'Library' / 'Application Support' / 'LynkCoHelper'
        else:
            root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'LynkCoHelper'
        return root / f'.credential-invalid-{self.account}'

    def _keyring(self):
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(('keyring.backends.macOS', 'keyring.backends.Windows')):
            raise ValueError('系统凭据库不可用；请使用 macOS 或 Windows 的正式版本')
        return keyring

    def load(self):
        if self._invalid_marker().is_file():
            return None
        try:
            value = self._keyring().get_password('LynkCoHelper', self.account)
            return json.loads(value) if value else None
        except Exception:
            raise ValueError('无法读取系统凭据库，请允许每日任务助手访问钥匙串或凭据管理器') from None

    def save(self, value):
        try:
            self._keyring().set_password('LynkCoHelper', self.account, json.dumps(value))
            self._invalid_marker().unlink(missing_ok=True)
        except Exception:
            raise ValueError('无法保存到系统凭据库，请允许访问后重试；请保留恢复码') from None

    def invalidate(self):
        try:
            marker = self._invalid_marker()
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker.touch(mode=0o600, exist_ok=True)
        except Exception:
            raise ValueError('无法记录失效凭据，请允许每日任务助手访问应用数据目录') from None

    def delete(self):
        try:
            keyring = self._keyring()
            try:
                keyring.delete_password('LynkCoHelper', self.account)
            except keyring.errors.PasswordDeleteError:
                pass
            self._invalid_marker().unlink(missing_ok=True)
        except Exception:
            raise ValueError('无法清理系统凭据库，请允许每日任务助手访问钥匙串或凭据管理器') from None
