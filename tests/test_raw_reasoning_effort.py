"""Raw effort persistence and request building, no GUI/network required."""
import ast
import unittest
from pathlib import Path

from pet.chat.models import ProviderConfig, ChatSettings
from pet.chat.providers import thinking_payload, _to_anthropic_payload


class TestRawReasoningEffort(unittest.TestCase):
    @staticmethod
    def claude(value='high', mode='enabled', model='claude-haiku-5-5'):
        cfg = ProviderConfig('claude-haiku')
        cfg.base_url = 'https://4router.net'
        cfg.api_protocol = 'anthropic_messages'
        cfg.model = model
        cfg.thinking = mode
        cfg.reasoning_effort = value
        return cfg

    def test_haiku_55_all_levels(self):
        for value in ('low', 'medium', 'high', 'xhigh', 'max'):
            with self.subTest(value=value):
                cfg = self.claude(value)
                self.assertEqual(thinking_payload(cfg), {'output_config': {'effort': value}})
                native = _to_anthropic_payload({
                    'model': cfg.model, 'max_tokens': 4096,
                    'messages': [{'role': 'system', 'content': '桌宠人设'},
                                 {'role': 'user', 'content': '你好'}],
                    **thinking_payload(cfg),
                })
                self.assertEqual(native['output_config']['effort'], value)
                self.assertEqual(native['system'], '桌宠人设')
                self.assertEqual(native['messages'][0]['role'], 'user')

    def test_custom_future_gateway_value_is_not_silently_replaced(self):
        cfg = self.claude('future-tier')
        self.assertEqual(thinking_payload(cfg)['output_config']['effort'], 'future-tier')
        native = _to_anthropic_payload({'model': cfg.model, 'messages': [{'role':'user','content':'test'}],
                                        **thinking_payload(cfg)})
        self.assertEqual(native['output_config']['effort'], 'future-tier')

    def test_empty_or_auto_do_not_send_effort(self):
        for value,mode in (('', 'enabled'), ('xhigh', 'auto')):
            cfg=self.claude(value,mode)
            self.assertNotIn('output_config', thinking_payload(cfg))

    def test_saved_profile_roundtrip_without_whitelist(self):
        settings = ChatSettings.defaults()
        cfg = self.claude('xhigh')
        settings.providers[cfg.provider_id] = cfg
        settings.active_provider = cfg.provider_id
        restored = ChatSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.active_config.reasoning_effort, 'xhigh')
        self.assertEqual(restored.active_config.thinking, 'enabled')
        restored.active_config.reasoning_effort = 'vendor-special'
        reread = ChatSettings.from_dict(restored.to_dict())
        self.assertEqual(reread.active_config.reasoning_effort, 'vendor-special')

    def test_older_claude_custom_is_sent_not_downgraded(self):
        cfg = self.claude('xhigh', model='claude-haiku-4-5-20251001')
        self.assertEqual(thinking_payload(cfg)['output_config']['effort'], 'xhigh')

    def test_background_force_disable_still_low(self):
        cfg = self.claude('xhigh')
        self.assertEqual(thinking_payload(cfg, force_disable=True), {'output_config': {'effort': 'low'}})

    def test_legacy_compatible_effort_keeps_raw_text(self):
        cfg = ProviderConfig('gpt')
        cfg.model = 'custom-gpt'
        cfg.base_url = 'https://gateway.example/v1'
        cfg.thinking = 'enabled'
        cfg.reasoning_effort = 'medium'
        self.assertEqual(thinking_payload(cfg)['reasoning_effort'], 'medium')
        cfg.reasoning_effort=''
        self.assertNotIn('reasoning_effort', thinking_payload(cfg))

    def test_ui_no_longer_uses_combo_for_effort(self):
        ui = Path(__file__).resolve().parents[1] / 'pet/chat/ai_settings_page.py'
        src = ui.read_text(encoding='utf-8')
        ast.parse(src)
        self.assertIn('self.reasoning_effort = _line_edit(', src)
        self.assertIn('self.reasoning_effort.text().strip()', src)
        self.assertNotIn('self.reasoning_effort.currentData()', src)

if __name__ == '__main__':
    unittest.main()
