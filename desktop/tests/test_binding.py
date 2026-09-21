import importlib.util
import json
import unittest


class MemoryStore:
    value = None

    def load(self):
        return self.value

    def save(self, value):
        self.value = value


class FakeCloud:
    def __init__(self):
        self.calls = []
        self.on_prepare = None
        self.base_url = 'https://lynkco.ltools.asia'

    def request(self, method, path, body=None, token=None):
        self.calls.append((method, path, body))
        if path.startswith('/v1/claim/'):
            return dict(userId='owner', managementToken='management-secret', recoveryCode='recovery-secret')
        if path == '/v1/users/recover':
            if body and body.get('recoveryToken') == 'admin-reset-token':
                return dict(userId='owner', managementToken='new-management', recoveryCode='new-recovery')
            return dict(userId='owner', managementToken='management-secret', recoveryCode='recovery-secret')
        if path == '/v1/binding-candidates':
            if self.on_prepare:
                self.on_prepare()
            return dict(id='candidate', expiresAt=9999999999999, preview=dict(points='10', alreadySigned=True), capabilities=dict(share=False))
        if path.endswith('/activate'):
            return dict(id='binding', label='car', status='active', doShare=False, canShare=False, scheduleTime='08:10', nextRunAt=0)
        if path.endswith('/runs'):
            return dict(items=[], nextCursor=None)
        if path == '/health':
            return dict(service='lynkco-helper', configured=True)
        if path == '/v1/schedule-windows':
            return {'items':[{'value':'08:00-10:00','limit':10,'used':1,'remaining':9,'current':False}]}
        return None


SESSION = dict(token='mobile-token-secret', refreshToken='mobile-refresh-secret', deviceId='device', platform='IOS')


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop.binding'), 'binding controller must exist')
        from desktop.binding import Controller
        self.cloud = FakeCloud()
        self.controller = Controller(self.cloud, MemoryStore())
        self.controller.claim('https://lynkco.ltools.asia/claim/claim-token_123456')

    def test_claim_link_redeems_without_persisting_link(self):
        controller = __import__('desktop.binding', fromlist=['Controller']).Controller(self.cloud, MemoryStore())
        result = controller.claim('https://lynkco.ltools.asia/claim/claim-token_123456')
        self.assertTrue(result['saved'])
        self.assertIn(('POST', '/v1/claim/claim-token_123456', {}), self.cloud.calls)

    def test_claim_link_rejects_other_hosts(self):
        controller = __import__('desktop.binding', fromlist=['Controller']).Controller(self.cloud, MemoryStore())
        with self.assertRaisesRegex(ValueError, '不是本助手的云端链接'):
            controller.claim('https://example.com/claim/claim-token_123456')

    def test_recover_accepts_admin_reset_link_from_the_fixed_cloud_host(self):
        controller = __import__('desktop.binding', fromlist=['Controller']).Controller(self.cloud, MemoryStore())
        result = controller.register('https://lynkco.ltools.asia/recover#token=admin-reset-token', recover=True)
        self.assertTrue(result['saved'])
        self.assertIn(('POST', '/v1/users/recover', {'recoveryToken': 'admin-reset-token'}), self.cloud.calls)

    def test_capture_remains_local_until_explicit_prepare(self):
        before = len(self.cloud.calls)
        self.controller.receive_capture(SESSION)
        self.assertEqual(len(self.cloud.calls), before)
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'captured')

    def test_slot_counts_are_cached_locally_until_explicit_refresh(self):
        self.assertEqual(self.controller.public_state()['scheduleWindows']['items'][0]['remaining'],9)
        before = len(self.cloud.calls)
        self.controller.public_state()
        self.controller.public_state()
        self.assertEqual(len(self.cloud.calls),before)

    def test_full_slot_refreshes_counts_and_preserves_candidate(self):
        from desktop.cloud_client import CloudError
        self.controller.receive_capture(SESSION)
        self.controller.prepare()
        original = self.cloud.request
        def request(method,path,body=None,token=None):
            if path.endswith('/activate'):
                raise CloudError('SLOT_FULL','该区间名额已满')
            if path == '/v1/schedule-windows':
                return {'items':[{'value':'08:00-10:00','limit':10,'used':10,'remaining':0,'current':False}]}
            return original(method,path,body,token)
        self.cloud.request = request
        with self.assertRaisesRegex(ValueError,'名额已满'):
            self.controller.activate({'scheduleTime':'08:00-10:00'})
        state = self.controller.public_state()
        self.assertEqual(state['scheduleWindows']['items'][0]['remaining'],0)
        self.assertEqual(state['capture']['stage'],'verified')
        self.assertIsNotNone(state['candidate'])

    def test_public_state_never_exposes_credentials(self):
        self.controller.receive_capture(SESSION)
        self.controller.prepare()
        output = json.dumps(self.controller.public_state())
        for secret in [*SESSION.values(), 'management-secret', 'recovery-secret']:
            if secret not in ['IOS', 'device']:
                self.assertNotIn(secret, output)

    def test_new_capture_invalidates_inflight_validation(self):
        self.controller.receive_capture(SESSION)
        self.cloud.on_prepare = lambda: self.controller.receive_capture({**SESSION, 'token': 'new-account'})
        with self.assertRaisesRegex(ValueError, '重新'):
            self.controller.prepare()
        self.assertIsNone(self.controller.public_state()['candidate'])

    def test_activation_clears_local_session(self):
        self.controller.receive_capture(SESSION)
        self.controller.prepare()
        self.controller.activate(dict(label='car', scheduleTime='08:10', doShare=False))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'cleanup')
        with self.assertRaises(ValueError):
            self.controller.prepare()

    def test_token_only_capture_rejected(self):
        self.assertFalse(self.controller.receive_capture(dict(token='short-only')))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'idle')

    def test_expired_preview_returns_to_capture_step(self):
        self.controller.receive_capture(SESSION)
        self.controller.prepare()
        self.controller.candidate['expiresAt'] = 1
        with self.assertRaisesRegex(ValueError, '过期'):
            self.controller.activate(dict(label='car', scheduleTime='08:10', doShare=False))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'captured')
        self.assertIsNone(self.controller.public_state()['candidate'])

    def test_activation_failure_keeps_preview_and_does_not_claim_success(self):
        self.controller.receive_capture(SESSION)
        self.controller.prepare()
        original = self.cloud.request
        def failing(method, path, body=None, token=None):
            if path.endswith('/activate'):
                raise ValueError('fixture unavailable')
            return original(method, path, body, token)
        self.cloud.request = failing
        with self.assertRaises(ValueError):
            self.controller.activate(dict(label='car', scheduleTime='08:10', doShare=False))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'verified')
        self.assertIsNone(self.controller.public_state()['binding'])


if __name__ == '__main__':
    unittest.main()
