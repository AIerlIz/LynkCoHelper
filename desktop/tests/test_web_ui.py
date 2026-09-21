from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / 'desktop' / 'web' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'desktop' / 'web' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'desktop' / 'web' / 'style.css').read_text(encoding='utf-8')


class WebUIContractTests(unittest.TestCase):
    def test_confirmed_overview_sections_are_kept_in_preview_order(self):
        ids = ['account-overview', 'points', 'sign-cards', 'energy',
               'today-status', 'settings-form', 'push-title', 'recent-title']
        positions = [HTML.index(f'id="{item}"') for item in ids]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('class="two-columns"', HTML)
        self.assertIn('class="asset-metric"', HTML)
        self.assertNotIn('class="continue-metric"', HTML)
        self.assertNotIn('id="continue-days"', HTML)
        self.assertIn('data-ui-version="user-dashboard-v2"', HTML)
        self.assertRegex(CSS, r'#view-overview\s*\{[^}]*border-top:\s*0', re.S)

    def test_local_token_survives_refresh_only_in_session_storage(self):
        self.assertIn('sessionStorage', JS)
        self.assertNotIn('localStorage', JS)
        self.assertRegex(JS, r'location\.hash\.slice\(1\).*sessionStorage',
                         'URL token must be copied into sessionStorage before the hash is removed')

    def test_recovery_mode_hides_stale_dashboard(self):
        self.assertIn('name === view && state.hasIdentity && !forceRecover', JS)

    def test_claim_form_asks_for_invitation_code(self):
        self.assertIn('>邀请码<', HTML)
        self.assertIn('claimCode', JS)
        self.assertNotIn('id="identity-label">领取链接', HTML)

    def test_visible_product_copy_does_not_use_the_old_brand(self):
        self.assertNotIn('领克', HTML)
        for old_copy in ('领克助手', '领克账号'):
            self.assertNotIn(old_copy, JS)

    def test_capture_consent_drives_silent_prepare_without_manual_verify_button(self):
        self.assertNotIn('id="prepare"', HTML)
        self.assertRegex(JS, r'upload-consent["\)]*\)\.addEventListener\("change"')
        self.assertIn('/api/candidates/prepare', JS)
        self.assertIn('consent: true', JS)

    def test_replace_binding_resets_local_flow_before_navigation(self):
        handler = re.search(r'\$\("binding-replace"\).*?\n\s*\}\);', JS, re.S)
        self.assertIsNotNone(handler)
        self.assertIn('resetBindingFlow', handler.group(0))

    def test_stale_qr_failure_does_not_leak_into_the_dashboard(self):
        qr_handler = JS[JS.index('if (step === 0 && proxy.pairUrl && qrPair !== proxy.pairUrl)'):JS.index('text(\n      "phone-instructions-title"')]
        self.assertIn('requestedPair', qr_handler)
        self.assertIn('view === "bind"', qr_handler)
        self.assertIn('state?.proxy?.pairUrl === requestedPair', qr_handler)
        self.assertIn('step === 0', qr_handler)

    def test_reload_resumes_an_active_phone_connection(self):
        self.assertIn('if (state?.proxy.running) navigate("bind")', JS)

    def test_activation_keeps_cleanup_step_visible_until_proxy_is_disconnected(self):
        handler = JS[JS.index('$("activate-form")'):JS.index('$("stop-proxy")')]
        self.assertNotIn('view = "overview"', handler)

    def test_long_operations_have_stable_visible_loading_state(self):
        self.assertIn('setButtonLoading', JS)
        self.assertIn('data-loading-label', CSS)
        self.assertIn('.is-loading', CSS)
        self.assertIn('@keyframes button-spin', CSS)

    def test_binding_actions_wrap_without_overlapping(self):
        self.assertIn('class="binding-step-actions"', HTML)
        self.assertIn('id="bind-back"', HTML)
        self.assertIn('$("bind-back").addEventListener', JS)
        self.assertRegex(CSS, r'\.binding-step-actions\s*\{[^}]*flex-wrap:\s*wrap', re.S)
        self.assertNotRegex(CSS, r'\.binding-step-actions\s*\{[^}]*flex-direction:\s*column', re.S)

    def test_push_configuration_shows_one_channel_and_saves_then_tests(self):
        self.assertIn('id="push-form"', HTML)
        self.assertIn('id="bark-field"', HTML)
        self.assertIn('id="serverchan-field"', HTML)
        self.assertIn('id="push-save"', HTML)
        self.assertIn('/api/binding/notification-test', JS)
        self.assertIn('保存并测试', JS)
        self.assertRegex(JS, r'show\(`\$\{channel\}-field`,\s*active\)')

    def test_binding_stages_are_navigable_and_abandon_action_is_removed(self):
        self.assertEqual(HTML.count('data-binding-step='), 5)
        self.assertNotIn('id="cancel-capture"', HTML)
        self.assertNotIn('放弃本次绑定', HTML)
        self.assertNotIn('exitCapture', JS)
        self.assertIn('selectedBindingStep', JS)
        self.assertIn('document.querySelectorAll("[data-binding-step]")', JS)

    def test_pending_tasks_do_not_render_success_checkmarks(self):
        self.assertIn('id="sign-task-icon"', HTML)
        self.assertIn('id="share-task-icon"', HTML)
        self.assertIn('function taskIcon', JS)
        self.assertRegex(JS, r'taskIcon\("sign-task-icon"')
        self.assertRegex(JS, r'taskIcon\("share-task-icon"')
        self.assertIn('.task-state-icon.success', CSS)
        task_helper = JS[JS.index('function taskIcon'):JS.index('const show =')]
        self.assertNotIn('className =', task_helper)

    def test_member_info_assets_include_dynamic_details_and_medals(self):
        self.assertIn('id="member-details"', HTML)
        self.assertIn('id="member-medals"', HTML)
        self.assertIn('id="medal-list"', HTML)
        self.assertIn('function renderMemberAssets', JS)
        self.assertIn('inventory.details', JS)
        self.assertIn('inventory.medals', JS)
        self.assertIn('.slice(0, 48)', JS)
        self.assertIn('function safeImageUrl', JS)
        self.assertIn('data-lucide="medal"', HTML)
        self.assertIn('memberVisual(item.iconUrl, "medal")', JS)
        self.assertIn('.member-details', CSS)
        self.assertIn('.medal-list', CSS)

    def test_tablet_layout_does_not_reserve_removed_sidebar_space(self):
        self.assertRegex(CSS, r'@media\s*\(max-width:\s*1050px\)\s*and\s*\(min-width:\s*721px\)[^{]*\{[\s\S]*?main\s*\{[^}]*margin:\s*0 auto[^}]*width:\s*100%', re.S)


if __name__ == '__main__':
    unittest.main()
