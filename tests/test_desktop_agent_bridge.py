# -*- coding: utf-8 -*-
"""Offline contract tests: gateway HTTP streaming, isolation, and limited MCP."""
import io
import json
import os
import subprocess
import sys
import threading
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

# The gateway protocol is intentionally testable without PySide6 or Windows.
if 'pet.chat' not in sys.modules:
    mod = types.ModuleType('pet.chat')
    mod.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'pet', 'chat')]
    sys.modules['pet.chat'] = mod

from pet.desktop_agent import (
    OpenClawProvider, gateway_endpoint, normalized_settings, task_from_message,
    test_gateway as probe_gateway,
)
from pet.windows_agent_mcp import _read_message, dispatch, _settings


class Handler(BaseHTTPRequestHandler):
    last_payload = None
    last_auth = None
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != '/v1/models':
            self.send_error(404); return
        Handler.last_auth = self.headers.get('Authorization')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps({'data': [{'id': 'openclaw/default'}]}).encode())

    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        Handler.last_auth = self.headers.get('Authorization')
        Handler.last_payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for value in ("收到", "任务完成"):
            line = json.dumps({'choices':[{'delta': {'content': value}}]}, ensure_ascii=False)
            self.wfile.write(('data: '+line+'\n\n').encode('utf-8'))
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.settings = {'enabled': True, 'gateway_url': 'http://127.0.0.1:%d' % cls.server.server_port,
                        'agent_model':'openclaw/default'}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def test_prefix(self):
        self.assertIsNone(task_from_message('帮我打开记事本'))
        self.assertEqual(task_from_message('/agent 打开记事本'), '打开记事本')
        self.assertEqual(task_from_message('/AGENT\t你好'), '你好')
        self.assertEqual(task_from_message('/agent'), '')
        self.assertIsNone(task_from_message('/agentfoo hi'))

    def test_whitelist_and_url(self):
        raw = normalized_settings({'enabled':True, 'windows_allowlist':['Spotify.exe','spotify.exe','..\\x.exe','notepad.exe']})
        self.assertEqual(raw['windows_allowlist'], ['spotify.exe', 'notepad.exe'])
        for candidate in ('http://evil.example:18789', 'https://127.0.0.1:18789',
                          'http://127.0.0.1:18789@evil.com', 'http://127.0.0.1:18789/evil',
                          'http://localhost:abc', 'http://localhost:18789?a=1'):
            with self.assertRaises(ValueError, msg=candidate):
                gateway_endpoint(candidate, '/v1/models')

    def test_gateway_probe_and_stream(self):
        with patch('pet.desktop_agent._gateway_token', return_value='test-secret'):
            ok, detail = probe_gateway(self.settings)
            self.assertTrue(ok, detail)
            self.assertEqual(Handler.last_auth, 'Bearer test-secret')
            provider = OpenClawProvider(self.settings, 'conversation-123', '打开记事本')
            events = list(provider.stream([{'role':'user','content':'SECRET OTHER CHAT'}], None, threading.Event()))
        self.assertEqual([x for x in events if isinstance(x, str)], ['收到','任务完成'])
        self.assertEqual(Handler.last_payload['messages'], [{'role':'user','content':'打开记事本'}])
        self.assertEqual(Handler.last_payload['user'], 'dafeiyu:conversation-123')
        self.assertEqual(Handler.last_payload['model'], 'openclaw/default')

    def test_disabled_agent_cannot_connect(self):
        agent = OpenClawProvider({}, 'id', 'hello')
        with self.assertRaisesRegex(RuntimeError, '尚未开启'):
            list(agent.stream([], None, threading.Event()))

    def test_config_roundtrip_no_gateway_token_on_disk(self):
        from pet.config import Config
        with patch.object(Config, '_normalize_pet_settings', lambda self: None), \
             patch.object(Config, '_migrate_plaintext_keys_to_keyring', lambda self: None):
            with TemporaryDirectory() as temporary:
                c = Config(base=Path(temporary), instance_id='')
                c.set('desktop_agent', {
                    'enabled': True, 'gateway_url': self.settings['gateway_url'],
                    'agent_model': 'openclaw/default',
                    'windows_allowlist': ['Notepad.exe'], 'token': 'DO-NOT-WRITE'})
                self.assertTrue(c.save())
                raw = c.path.read_text(encoding='utf-8')
                self.assertNotIn('DO-NOT-WRITE', raw)
                self.assertNotIn('"token"', raw)
                restored = Config(base=Path(temporary), instance_id='')
                self.assertTrue(restored.get('desktop_agent')['enabled'])
                self.assertEqual(restored.get('desktop_agent')['windows_allowlist'], ['notepad.exe'])
                with patch.object(sys, 'argv', ['pet.windows_agent_mcp', '--config', str(c.path)]):
                    self.assertEqual(_settings()['windows_allowlist'], ['notepad.exe'])

    def test_mcp_real_stdio_newline_framing(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        ]
        raw = ('\n'.join(json.dumps(item) for item in requests) + '\n').encode('utf-8')
        root = os.path.join(os.path.dirname(__file__), '..')
        done = subprocess.run([sys.executable, '-m', 'pet.windows_agent_mcp'],
                              input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=root, timeout=5, check=True)
        replies = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual([x['id'] for x in replies], [1, 2])
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'dafeiyu-windows-restricted')
        self.assertTrue(any(t['name'] == 'open_notepad' for t in replies[1]['result']['tools']))

    def test_mcp_tools_list_and_nonwindows_refusal(self):
        items = dispatch('tools/list', {})
        names = [x['name'] for x in items['tools']]
        self.assertIn('open_notepad', names)
        self.assertNotIn('shell', names)
        self.assertNotIn('delete_file', names)
        payload = {'jsonrpc':'2.0','id':1,'method':'tools/list'}
        body = json.dumps(payload).encode()
        wire = b'Content-Length: %d\r\n\r\n'%len(body)+body
        self.assertEqual(_read_message(io.BytesIO(wire)), payload)


if __name__ == '__main__':
    unittest.main()
