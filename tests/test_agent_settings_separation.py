# -*- coding: utf-8 -*-
"""Model isolation and domain-row contract tests, runnable without Qt/Windows."""
from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path

# The packaged pet.chat.__init__ loads GUI services; model contract tests don't.
if 'pet.chat' not in sys.modules:
    module = types.ModuleType('pet.chat')
    module.__path__ = [str(Path(__file__).resolve().parents[1] / 'pet' / 'chat')]
    sys.modules['pet.chat'] = module
from pet.chat.models import ChatSettings, ProviderConfig

ROOT = Path(__file__).resolve().parents[1]


class AutonomyProviderTests(unittest.TestCase):
    def _settings(self):
        settings = ChatSettings.defaults()
        settings.providers['chat'] = ProviderConfig(
            provider_id='chat', name='Chat', base_url='https://example.test/v1',
            model='gpt-chat', api_key_ref='chat-key', api_protocol='openai_chat',
            thinking='auto', reasoning_effort='high')
        settings.providers['brain'] = ProviderConfig(
            provider_id='brain', name='Brain', base_url='https://example.test/v1',
            model='gpt-brain', api_key_ref='brain-key', api_protocol='openai_responses',
            thinking='disabled', reasoning_effort='low')
        settings.active_provider = 'chat'
        return settings

    def test_old_settings_migrate_to_fixed_provider(self):
        original = self._settings()
        raw = original.to_dict()
        raw.pop('autonomy_provider', None)  # old on-disk format followed active_provider
        converted = ChatSettings.from_dict(raw)
        self.assertEqual(converted.autonomy_provider, 'chat')
        self.assertEqual(converted.autonomy_config.model, 'gpt-chat')
        converted.active_provider = 'brain'  # switching dialog no longer changes autonomy
        self.assertEqual(converted.autonomy_config.model, 'gpt-chat')

    def test_different_provider_and_model_without_chat_mutation(self):
        s = self._settings()
        s.autonomy_provider = 'brain'
        s.autonomy_model = 'gpt-brain-strong'
        s.autonomy_thinking = 'enabled'
        s.autonomy_reasoning_effort = 'medium'
        s = ChatSettings.from_dict(s.to_dict(include_secrets=False))
        a = s.autonomy_config
        self.assertEqual(a.provider_id, 'brain')
        self.assertEqual(a.model, 'gpt-brain-strong')
        self.assertEqual(a.api_protocol, 'openai_responses')
        self.assertEqual(a.api_key_ref, 'brain-key')
        self.assertEqual(a.thinking, 'enabled')
        self.assertEqual(a.reasoning_effort, 'medium')
        self.assertEqual(s.active_config.model, 'gpt-chat')
        self.assertEqual(s.providers['brain'].model, 'gpt-brain')
        self.assertEqual(s.providers['brain'].thinking, 'disabled')
        a.model = 'changed'
        self.assertEqual(s.autonomy_config.model, 'gpt-brain-strong')

    def test_custom_model_same_provider_keeps_chat_intact(self):
        s = self._settings()
        s.autonomy_model = 'cheap-decision-model'
        self.assertEqual(s.autonomy_config.model, 'cheap-decision-model')
        self.assertEqual(s.active_config.model, 'gpt-chat')

    def test_deleted_provider_falls_back(self):
        s = self._settings()
        s.autonomy_provider = 'provider-deleted-by-user'
        s.autonomy_model = 'fallback-model'
        self.assertEqual(s.autonomy_config.provider_id, 'chat')
        self.assertEqual(s.autonomy_config.model, 'fallback-model')

    def test_visible_settings_and_both_autonomy_flows(self):
        dialog = (ROOT / 'pet/modern_settings_dialog.py').read_text('utf-8')
        page = (ROOT / 'pet/chat/ai_settings_page.py').read_text('utf-8')
        autonomy = (ROOT / 'pet/autonomy.py').read_text('utf-8')
        for key in (
            'desktop_agent_enabled', 'desktop_agent_gateway', 'desktop_agent_model',
            'desktop_agent_token', 'desktop_agent_allowed_windows',
            'desktop_agent_test', 'desktop_agent_test_result',
            'autonomy_provider', 'autonomy_model', 'autonomy_thinking',
            'autonomy_reasoning_effort',
        ):
            self.assertTrue((f'"{key}"' in dialog or f"'{key}'" in dialog), key)
            self.assertTrue((f'"{key}"' in page or f"'{key}'" in page), key)
        self.assertEqual(autonomy.count('provider = settings.autonomy_config'), 2)
        for relative in ('pet/modern_settings_dialog.py', 'pet/chat/ai_settings_page.py',
                         'pet/chat/models.py', 'pet/autonomy.py'):
            ast.parse((ROOT / relative).read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
