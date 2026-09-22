import importlib.util
import json
import threading
import time
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
        self.prepare_started = threading.Event()
        self.prepare_release = threading.Event()
        self.prepare_release.set()
        self.prepare_gates = {}
        self.prepare_error = None
        self.action_started = {}
        self.action_gates = {}
        self.action_errors = {}
        self.base_url = 'https://lynkco.ltools.asia'

    def wait_for_action(self, path):
        self.action_started.setdefault(path, threading.Event()).set()
        gate = self.action_gates.get(path)
        if gate:
            gate.wait(2)
        error = self.action_errors.get(path)
        if error:
            raise error

    def request(self, method, path, body=None, token=None):
        self.calls.append((method, path, body))
        if path.startswith('/v1/claim/'):
            return dict(userId='owner', managementToken='management-secret', recoveryCode='recovery-secret')
        if path == '/v1/users/recover':
            self.wait_for_action(path)
            if body and body.get('recoveryToken') == 'admin-reset-token':
                return dict(userId='owner', managementToken='new-management', recoveryCode='new-recovery')
            return dict(userId='owner', managementToken='management-secret', recoveryCode='recovery-secret')
        if path == '/v1/binding-candidates':
            self.prepare_started.set()
            self.prepare_gates.get(body['session']['token'], self.prepare_release).wait(2)
            if self.on_prepare:
                self.on_prepare()
            if self.prepare_error:
                raise self.prepare_error
            candidate_id = 'candidate-new-account' if body['session']['token'] == 'new-account' else 'candidate-current-account'
            return dict(id=candidate_id, expiresAt=9999999999999,
                        preview=dict(points='10', alreadySigned=True), capabilities=dict(share=False))
        if path.endswith('/activate'):
            return dict(id='binding', label='car', status='active', doShare=False, canShare=False, scheduleTime='08:10', nextRunAt=0)
        if path == '/v1/binding' and method == 'DELETE':
            self.wait_for_action(path)
            return {'deleted': True}
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

    def wait_for_stage(self, stage):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.controller.public_state()['capture']['stage'] == stage:
                return self.controller.public_state()
            time.sleep(.01)
        self.fail(f'capture did not reach {stage}: {self.controller.public_state()}')

    def capture_and_verify(self, session=SESSION):
        self.assertTrue(self.controller.receive_capture(session))
        return self.wait_for_stage('verified')

    def test_claim_link_redeems_without_persisting_link(self):
        controller = __import__('desktop.binding', fromlist=['Controller']).Controller(self.cloud, MemoryStore())
        result = controller.claim('https://lynkco.ltools.asia/claim/claim-token_123456')
        self.assertTrue(result['saved'])
        self.assertIn(('POST', '/v1/claim/claim-token_123456', {}), self.cloud.calls)

    def test_claim_accepts_invitation_code_without_full_link(self):
        controller = __import__('desktop.binding', fromlist=['Controller']).Controller(self.cloud, MemoryStore())
        result = controller.claim('claim-token_123456')
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

    def test_complete_capture_starts_one_background_verification_without_blocking_callback(self):
        self.cloud.prepare_release.clear()
        before = len(self.cloud.calls)
        started = time.monotonic()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertLess(time.monotonic() - started, .1)
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'verifying')
        self.assertEqual(len(self.cloud.calls), before + 1)
        self.cloud.prepare_release.set()
        self.wait_for_stage('verified')
        self.assertEqual(len(self.cloud.calls), before + 1)

    def test_duplicate_capture_does_not_start_a_second_verification(self):
        self.cloud.prepare_release.clear()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.assertTrue(self.controller.receive_capture(dict(SESSION)))
        self.assertEqual(len([call for call in self.cloud.calls if call[1] == '/v1/binding-candidates']), 1)
        self.cloud.prepare_release.set()
        self.wait_for_stage('verified')

    def test_failed_verification_exposes_a_safe_error_and_retry_keeps_session(self):
        self.cloud.prepare_error = ValueError('upstream token=mobile-token-secret')
        self.assertTrue(self.controller.receive_capture(SESSION))
        state = self.wait_for_stage('verification_failed')
        self.assertEqual(state['capture']['verificationError'], '个人信息验证暂时失败，请稍后重试')
        self.assertNotIn('mobile-token-secret', json.dumps(state))
        self.cloud.prepare_error = None
        self.controller.retry_verification()
        self.wait_for_stage('verified')

    def test_new_capture_prevents_the_old_verification_from_publishing(self):
        old_request = threading.Event()
        self.cloud.prepare_gates[SESSION['token']] = old_request
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.assertTrue(self.controller.receive_capture({**SESSION, 'token': 'new-account'}))
        state = self.wait_for_stage('verified')
        old_request.set()
        time.sleep(.05)
        self.assertEqual(len([call for call in self.cloud.calls if call[1] == '/v1/binding-candidates']), 2)
        self.assertEqual(state['candidate']['id'], 'candidate-new-account')

    def test_reset_prevents_inflight_verification_from_publishing(self):
        self.cloud.prepare_release.clear()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.controller.reset_capture()
        self.cloud.prepare_release.set()
        time.sleep(.05)
        state = self.controller.public_state()
        self.assertEqual(state['capture']['stage'], 'idle')
        self.assertIsNone(state['candidate'])

    def test_activation_prevents_an_older_verification_from_publishing(self):
        self.cloud.prepare_release.clear()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.controller.candidate = {'id': 'previous-candidate', 'expiresAt': 9999999999999}
        self.controller.activate({'label': 'car', 'scheduleTime': '08:00-10:00', 'doShare': False})
        self.cloud.prepare_release.set()
        time.sleep(.05)
        state = self.controller.public_state()
        self.assertEqual(state['capture']['stage'], 'cleanup')
        self.assertIsNone(state['candidate'])

    def test_identity_adoption_prevents_an_older_verification_from_publishing(self):
        self.cloud.prepare_release.clear()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        self.controller.register('admin-reset-token', recover=True)
        self.cloud.prepare_release.set()
        time.sleep(.05)
        state = self.controller.public_state()
        self.assertEqual(state['capture']['stage'], 'idle')
        self.assertIsNone(state['candidate'])

    def test_binding_deletion_clears_capture_state_and_blocks_stale_verification(self):
        delete_release = threading.Event()
        self.cloud.action_gates['/v1/binding'] = delete_release
        self.cloud.prepare_release.clear()
        self.controller.binding = {'id': 'binding'}
        self.controller.record_capture({'host': 'app-services.lynkco.com.cn', 'path': '/auth/login/refresh',
                                        'method': 'POST', 'status': 200, 'outcome': 'captured', 'id': 'deadbeef',
                                        'fields': {'token': True, 'refreshToken': True, 'deviceId': True, 'platform': True}})
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        thread = threading.Thread(target=self.controller.delete)
        thread.start()
        self.assertTrue(self.cloud.action_started['/v1/binding'].wait(.5))
        self.cloud.prepare_release.set()
        time.sleep(.05)
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'cleanup')
        delete_release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        state = self.controller.public_state()
        self.assertEqual(state['capture']['stage'], 'idle')
        self.assertIsNone(state['candidate'])
        self.assertIsNone(state['capture']['platform'])
        self.assertIsNone(state['capture']['verificationError'])
        self.assertEqual(state['capture']['events'], [])

    def test_recovery_freezes_verification_before_the_remote_action_completes(self):
        recovery_release = threading.Event()
        self.cloud.action_gates['/v1/users/recover'] = recovery_release
        self.cloud.prepare_release.clear()
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        thread = threading.Thread(target=lambda: self.controller.register('admin-reset-token', recover=True))
        thread.start()
        self.assertTrue(self.cloud.action_started['/v1/users/recover'].wait(.5))
        self.cloud.prepare_release.set()
        time.sleep(.05)
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'cleanup')
        recovery_release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        state = self.controller.public_state()
        self.assertEqual(state['capture']['stage'], 'idle')
        self.assertIsNone(state['candidate'])

    def test_failed_recovery_restores_a_retryable_capture_state(self):
        self.cloud.prepare_release.clear()
        self.cloud.action_errors['/v1/users/recover'] = ValueError('fixture recovery failure')
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        with self.assertRaisesRegex(ValueError, 'fixture recovery failure'):
            self.controller.register('admin-reset-token', recover=True)
        self.cloud.prepare_release.set()
        state = self.wait_for_stage('verification_failed')
        self.assertIsNone(state['candidate'])
        self.assertEqual(state['capture']['verificationError'], '个人信息验证已中断，请重试')

    def test_failed_deletion_restores_a_retryable_capture_state(self):
        self.cloud.prepare_release.clear()
        self.cloud.action_errors['/v1/binding'] = ValueError('fixture delete failure')
        self.controller.binding = {'id': 'binding'}
        self.assertTrue(self.controller.receive_capture(SESSION))
        self.assertTrue(self.cloud.prepare_started.wait(.5))
        with self.assertRaisesRegex(ValueError, 'fixture delete failure'):
            self.controller.delete()
        self.cloud.prepare_release.set()
        state = self.wait_for_stage('verification_failed')
        self.assertEqual(state['capture']['verificationError'], '个人信息验证已中断，请重试')
        self.assertIsNone(state['candidate'])

    def test_slot_counts_are_cached_locally_until_explicit_refresh(self):
        self.assertEqual(self.controller.public_state()['scheduleWindows']['items'][0]['remaining'],9)
        before = len(self.cloud.calls)
        self.controller.public_state()
        self.controller.public_state()
        self.assertEqual(len(self.cloud.calls),before)

    def test_full_slot_refreshes_counts_and_preserves_candidate(self):
        from desktop.cloud_client import CloudError
        self.capture_and_verify()
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
        self.capture_and_verify()
        output = json.dumps(self.controller.public_state())
        for secret in [*SESSION.values(), 'management-secret', 'recovery-secret']:
            if secret not in ['IOS', 'device']:
                self.assertNotIn(secret, output)

    def test_activation_clears_local_session(self):
        self.capture_and_verify()
        self.controller.activate(dict(label='car', scheduleTime='08:10', doShare=False))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'cleanup')
        with self.assertRaises(ValueError):
            self.controller.prepare()

    def test_token_only_capture_rejected(self):
        self.assertFalse(self.controller.receive_capture(dict(token='short-only')))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'idle')

    def test_expired_preview_returns_to_capture_step(self):
        self.capture_and_verify()
        self.controller.candidate['expiresAt'] = 1
        with self.assertRaisesRegex(ValueError, '过期'):
            self.controller.activate(dict(label='car', scheduleTime='08:10', doShare=False))
        self.assertEqual(self.controller.public_state()['capture']['stage'], 'captured')
        self.assertIsNone(self.controller.public_state()['candidate'])

    def test_activation_failure_keeps_preview_and_does_not_claim_success(self):
        self.capture_and_verify()
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

    def test_reset_capture_returns_replacement_flow_to_first_step(self):
        self.capture_and_verify()
        self.controller.reset_capture()
        capture = self.controller.public_state()['capture']
        self.assertEqual(capture['stage'], 'idle')
        self.assertIsNone(self.controller.public_state()['candidate'])
        self.assertEqual(capture['events'], [])

    def test_notification_test_is_forwarded_to_cloud(self):
        result = self.controller.test_notification()
        self.assertIsNone(result)
        self.assertIn(('POST', '/v1/binding/notifications/test', {}), self.cloud.calls)


if __name__ == '__main__':
    unittest.main()
