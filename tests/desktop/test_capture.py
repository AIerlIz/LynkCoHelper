import importlib
import importlib.util
import unittest


class CaptureTests(unittest.TestCase):
    def test_general_request_metadata_retains_route_without_query_secrets(self):
        from desktop import capture
        self.assertTrue(hasattr(capture, 'request_summary'))
        result = capture.request_summary('https://example.com/app/user/info?token=secret#secret', 'POST', None, 'pending')
        self.assertEqual(result['host'], 'example.com')
        self.assertEqual(result['method'], 'POST')
        self.assertEqual(result['path'], '/app/user/info')
        self.assertNotIn('secret', str(result))

    def test_path_redacts_identifier_and_credential_segments(self):
        from desktop.capture import display_path
        self.assertEqual(display_path('/app/user/13800000000/info?token=secret'), '/app/user/[redacted]/info')
        self.assertEqual(display_path('/auth/token/secret-value'), '/auth/token/[redacted]')

    def test_diagnostic_contains_only_allowlisted_metadata(self):
        from desktop import capture
        self.assertTrue(hasattr(capture, 'capture_summary'))
        result = capture.capture_summary(self.url + '&mobile=13800000000', self.headers, 200, self.response, 'IOS')
        self.assertEqual(result['outcome'], 'captured')
        self.assertTrue(result['fields']['refreshToken'])
        import json
        for secret in ('new-token', 'new-refresh', 'old-refresh', 'phone-a', '13800000000'):
            self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result['path'], '/auth/login/refresh')

    def test_diagnostic_distinguishes_failure_and_missing_fields(self):
        from desktop import capture
        self.assertTrue(hasattr(capture, 'capture_summary'))
        self.assertEqual(capture.capture_summary(self.url, self.headers, 401, {}, 'IOS')['outcome'], 'http_error')
        self.assertEqual(capture.capture_summary(self.url, self.headers, 200, None, 'IOS')['outcome'], 'invalid_json')
        self.assertEqual(capture.capture_summary(self.url, self.headers, 200, {'code': 'success', 'data': {}}, 'IOS')['outcome'], 'incomplete')

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("desktop.capture"), "capture parser is not implemented")
        self.parse = importlib.import_module("desktop.capture").parse_session
        self.url = "https://app-services.lynkco.com.cn/auth/login/refresh?deviceId=phone-a&refreshToken=old-refresh"
        self.headers = {"publicplatform": "iOS", "gl_dev_id": "gl-phone-a", "appversioncode": "4.2.7"}
        self.response = {"code": "success", "data": {"centerTokenDto": {"token": "new-token", "refreshToken": "new-refresh"}}}

    def test_pairs_device_from_request_with_new_refresh_token_from_response(self):
        self.assertEqual(self.parse(self.url, self.headers, 200, self.response), {
            "token": "new-token", "refreshToken": "new-refresh", "deviceId": "phone-a",
            "platform": "IOS", "glDevId": "gl-phone-a", "appVersion": "4.2.7",
        })

    def test_refresh_without_rotated_value_retains_same_request_refresh(self):
        self.response["data"]["centerTokenDto"].pop("refreshToken")
        self.assertEqual(self.parse(self.url, self.headers, 200, self.response)["refreshToken"], "old-refresh")

    def test_login_uses_hardware_id_and_discards_phone_and_sms(self):
        url = "https://app-services.lynkco.com.cn/auth/login/mobileCodeLogin?hardwareDeviceId=android-a&deviceType=ANDROID&mobile=13800000000&verificationCode=123456"
        self.assertEqual(self.parse(url, {}, 200, self.response), {
            "token": "new-token", "refreshToken": "new-refresh", "deviceId": "android-a", "platform": "ANDROID",
        })

    def test_unrelated_domains_and_business_requests_never_produce_credentials(self):
        for url in [self.url.replace("app-services.lynkco.com.cn", "app-services.lynkco.com.cn.evil.test"),
                    self.url.replace("https://", "http://"),
                    "https://app-api-gw-toc.lynkco.com/app/energy/myEnergy"]:
            with self.subTest(url=url):
                self.assertIsNone(self.parse(url, self.headers, 200, self.response))

    def test_failed_business_or_http_response_is_rejected(self):
        self.assertIsNone(self.parse(self.url, self.headers, 401, self.response))
        self.response["code"] = "invalid.token"
        self.assertIsNone(self.parse(self.url, self.headers, 200, self.response))

    def test_incomplete_or_malformed_credentials_are_rejected(self):
        for dto in [{"token": "short-token"}, {"token": ["not-a-string"], "refreshToken": "refresh"},
                    {"token": "token\r\ninjected", "refreshToken": "refresh"}]:
            with self.subTest(dto=dto):
                url = "https://app-services.lynkco.com.cn/auth/login/mobileCodeLogin?hardwareDeviceId=phone"
                self.assertIsNone(self.parse(url, self.headers, 200, {"code": "success", "data": {"centerTokenDto": dto}}))

    def test_missing_device_cannot_be_replaced_by_another_flow(self):
        url = "https://app-services.lynkco.com.cn/auth/login/refresh?refreshToken=old"
        self.assertIsNone(self.parse(url, self.headers, 200, self.response))

    def test_malformed_urls_and_payloads_are_ignored(self):
        for url, payload in [("https://[broken", self.response), (self.url, []), (self.url, {"data": []}),
                             (self.url, {"code": "success", "data": {"centerTokenDto": []}})]:
            with self.subTest(url=url, payload=payload):
                self.assertIsNone(self.parse(url, self.headers, 200, payload))


if __name__ == "__main__":
    unittest.main()
