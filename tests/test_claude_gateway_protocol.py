"""Claude native wire-format checks; no API requests or credentials."""
import unittest
from pet.chat.models import ProviderConfig
from pet.chat.providers import thinking_payload, _to_anthropic_payload


class TestClaudeGatewayProtocol(unittest.TestCase):
    def test_claude_effort(self):
        cfg = ProviderConfig('claude')
        cfg.base_url = 'https://4router.net'
        cfg.api_protocol = 'anthropic_messages'
        cfg.model = 'claude-sonnet-5'
        cfg.thinking = 'enabled'
        cfg.reasoning_effort = 'high'
        self.assertEqual(thinking_payload(cfg), {'output_config': {'effort': 'high'}})
        cfg.reasoning_effort = 'max'
        self.assertEqual(thinking_payload(cfg), {'output_config': {'effort': 'max'}})
        self.assertEqual(thinking_payload(cfg, force_disable=True), {'output_config': {'effort': 'low'}})

    def test_older_claude_uses_default_when_auto(self):
        cfg = ProviderConfig('older')
        cfg.base_url = 'https://4router.net'
        cfg.api_protocol = 'anthropic_messages'
        cfg.model = 'claude-haiku-4-5-20251001'
        cfg.thinking = 'auto'
        self.assertEqual(thinking_payload(cfg), {})
        # 用户手动开启后，不再按模型名称静默改写或丢弃输入值。
        cfg.thinking = 'enabled'
        cfg.reasoning_effort = 'custom'
        self.assertEqual(thinking_payload(cfg), {'output_config': {'effort': 'custom'}})

    def test_system_prompt_moves_to_top_level(self):
        request = {'model':'claude-sonnet-5','max_tokens':1024,
                   'messages':[{'role':'system','content':'小鲸鱼角色设定'},
                               {'role':'user','content':'早上好'}]}
        native = _to_anthropic_payload(request)
        self.assertEqual(native['system'],'小鲸鱼角色设定')
        self.assertEqual(len(native['messages']),1)
        self.assertEqual(native['messages'][0]['role'],'user')


if __name__ == '__main__': unittest.main()
