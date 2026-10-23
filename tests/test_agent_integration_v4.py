# -*- coding: utf-8 -*-
"""v4 contract tests with fake local Gateway; never requires API Key/Qt/Windows."""
import os
import sys
import json
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if 'pet.chat' not in sys.modules:
    mod = types.ModuleType('pet.chat')
    mod.__path__ = [str(Path(__file__).resolve().parents[1] / 'pet' / 'chat')]
    sys.modules['pet.chat'] = mod
from pet.desktop_agent import OpenClawProvider, normalized_settings
from pet.chat.models import ChatSettings, ProviderConfig

class Gateway(BaseHTTPRequestHandler):
    seen = {}
    def log_message(self, *args): pass
    def do_POST(self):
        Gateway.seen = {'path':self.path,
                        'model_override':self.headers.get('x-openclaw-model'),
                        'payload':json.loads(self.rfile.read(int(self.headers['Content-Length'])))}
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
        self.wfile.flush()

class V4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1', 0),Gateway)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
        cls.setting={'enabled':True,'gateway_url':f'http://127.0.0.1:{cls.server.server_port}',
                     'agent_model':'openclaw/default'}
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()
    def test_override_backend_model_and_turn_think_only(self):
        settings={**self.setting,'backend_model':'4router/claude-haiku-5-5','thinking_level':'high'}
        with patch('pet.desktop_agent._gateway_token',return_value='test-key'):
            texts=list(OpenClawProvider(settings,'c123','整理我的任务').stream([],None,threading.Event()))
        self.assertTrue(texts)
        self.assertEqual(Gateway.seen['model_override'],'4router/claude-haiku-5-5')
        self.assertEqual(Gateway.seen['payload']['model'],'openclaw/default')
        self.assertEqual(Gateway.seen['payload']['messages'][0]['content'],'/think:high\n整理我的任务')
    def test_default_omits_override_and_command(self):
        with patch('pet.desktop_agent._gateway_token',return_value='test-key'):
            list(OpenClawProvider(self.setting,'c1','你好').stream([],None,threading.Event()))
        self.assertIsNone(Gateway.seen['model_override'])
        self.assertEqual(Gateway.seen['payload']['messages'][0]['content'],'你好')
    def test_invalid_thinking_becomes_default(self):
        self.assertEqual(normalized_settings({'thinking_level':'an-unexpected-level'})['thinking_level'],'default')
    def test_autonomy_provider_migration_and_independence(self):
        a=ProviderConfig('a',model='chat-model')
        b=ProviderConfig('b',model='autonomy-model')
        raw={'providers':{'a':a.to_dict(),'b':b.to_dict()},'active_provider':'a'}
        s=ChatSettings.from_dict(raw)
        self.assertEqual(s.autonomy_provider,'a')
        s.active_provider='b'
        self.assertEqual(s.autonomy_provider,'a')
        self.assertEqual(s.autonomy_config.model,'chat-model')
    def test_no_hidden_settings_section_and_chat_web_ui(self):
        root=Path(__file__).resolve().parents[1]
        modern=(root/'pet/modern_settings_dialog.py').read_text('utf-8')
        chat=(root/'pet/chat/widgets.py').read_text('utf-8')
        self.assertNotIn('"AI 对话 · 深度思考与联网"',modern)
        for name in ('web_search_toggle','_open_web_search_settings',"search_engine",'chat_effort_selector'):
            self.assertIn(name,chat)
        ai=(root/'pet/chat/ai_settings_page.py').read_text('utf-8')
        self.assertNotIn('combo.addItem("跟随 AI 对话（兼容旧设置）", "")',ai)
        self.assertIn('desktop_agent_backend_model',modern)
        self.assertIn('desktop_agent_thinking',modern)

if __name__=='__main__':unittest.main()
