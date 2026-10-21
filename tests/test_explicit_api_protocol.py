"""Protocol selector, one-off migration, request/response/stream contract tests.

No external API requests and no Qt GUI required.
"""
import io
import json
import unittest
from unittest.mock import patch

from pet.chat.models import ProviderConfig, ChatSettings
from pet.chat.providers import (
    OpenAICompatibleProvider, ResponsesSSEParser, _to_responses_payload,
    _responses_chat_message, _to_anthropic_payload, normalize_chat_endpoint,
    normalize_responses_endpoint, normalize_anthropic_endpoint,
    thinking_payload, uses_anthropic_messages, uses_openai_responses, test_connection as probe_connection,
)


class DummyHTTP(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    status = 200


class TestExplicitProtocol(unittest.TestCase):
    def test_existing_claude_migrates_once(self):
        legacy = {'base_url':'https://4router.net/v1', 'model':'claude-haiku-5-5'}
        p = ProviderConfig.from_dict('legacy', legacy)
        self.assertEqual(p.api_protocol,'anthropic_messages')
        saved = p.to_dict()
        saved['model'] = 'gpt-6'
        self.assertEqual(ProviderConfig.from_dict('legacy',saved).api_protocol,'anthropic_messages')
        self.assertTrue(uses_anthropic_messages(ProviderConfig.from_dict('legacy',saved)))

    def test_gateway_gpt_and_plain_defaults(self):
        for base, model in [('https://4router.net/v1','gpt-6'),
                            ('https://example.com/v1','claude-haiku-5-5')]:
            self.assertEqual(ProviderConfig.from_dict('p',{'base_url':base,'model':model}).api_protocol,
                             'openai_chat')
        p=ProviderConfig.from_dict('p',{'base_url':'https://example.com/v1/messages'})
        self.assertEqual(p.api_protocol,'anthropic_messages')

    def test_manual_choice_is_always_authoritative(self):
        p=ProviderConfig('p', base_url='https://4router.net/v1', model='claude-haiku-5-5',
                         api_protocol='openai_responses')
        self.assertTrue(uses_openai_responses(p))
        self.assertFalse(uses_anthropic_messages(p))
        d=ChatSettings(providers={'p':p},active_provider='p').to_dict()
        self.assertEqual(ChatSettings.from_dict(d).active_config.api_protocol,'openai_responses')

    def test_endpoints_no_double_prefix(self):
        self.assertEqual(normalize_chat_endpoint('https://4router.net/v1'),
                         'https://4router.net/v1/chat/completions')
        self.assertEqual(normalize_responses_endpoint('https://4router.net/v1'),
                         'https://4router.net/v1/responses')
        self.assertEqual(normalize_anthropic_endpoint('https://4router.net/v1'),
                         'https://4router.net/v1/messages')
        self.assertEqual(normalize_responses_endpoint('https://4router.net/v1/messages'),
                         'https://4router.net/v1/responses')
        self.assertEqual(normalize_chat_endpoint('https://4router.net/v1/messages'),
                         'https://4router.net/v1/chat/completions')

    def test_reasoning_fields_not_mixed(self):
        p=ProviderConfig('p', base_url='https://4router.net/v1', api_protocol='openai_chat', thinking='enabled',reasoning_effort='high')
        self.assertEqual(thinking_payload(p), {'reasoning_effort':'high'})
        p.api_protocol='openai_responses'
        self.assertEqual(thinking_payload(p), {'reasoning': {'effort':'high'}})
        p.api_protocol='anthropic_messages'
        self.assertEqual(thinking_payload(p), {'output_config': {'effort':'high'}})
        p.reasoning_effort='xhigh'
        self.assertEqual(thinking_payload(p), {'output_config': {'effort':'xhigh'}})

    def test_responses_conversion_with_images_and_tools(self):
        p={'model':'gpt-6','max_tokens':512,'stream':False,
           'messages':[{'role':'system','content':'人设'},
                       {'role':'user','content':[{'type':'text','text':'看图'},
                               {'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}]},
                       {'role':'assistant','content':'', 'tool_calls':[
                           {'id':'call_1','function':{'name':'web_search','arguments':'{"query":"a"}'}}]},
                       {'role':'tool','tool_call_id':'call_1','content':'result'}],
           'tools':[{'type':'function','function':{'name':'web_search','description':'search',
                        'parameters':{'type':'object','properties':{}}}}],
           'tool_choice':'auto', 'reasoning':{'effort':'medium'}}
        output=_to_responses_payload(p)
        self.assertEqual(output['max_output_tokens'],512)
        self.assertEqual(output['input'][0],{'role':'system','content':'人设'})
        self.assertEqual(output['input'][1]['content'][1]['type'],'input_image')
        self.assertEqual(output['input'][2]['type'],'function_call')
        self.assertEqual(output['input'][3]['type'],'function_call_output')
        self.assertEqual(output['tools'][0]['name'],'web_search')
        self.assertEqual(output['reasoning']['effort'],'medium')
        self.assertNotIn('messages', output)
        self.assertNotIn('temperature', output)

    def test_responses_parse_tool_calls(self):
        r={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'好了'}]},
            {'type':'function_call','call_id':'call_1','name':'web_search','arguments':'{"query":"a"}'}]}
        msg=_responses_chat_message(r)
        self.assertEqual(msg['content'],'好了')
        self.assertEqual(msg['tool_calls'][0]['id'],'call_1')

    def test_responses_stream_fragmented_events(self):
        parser=ResponsesSSEParser()
        source=b'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n' \
               b'data: {"type":"response.completed"}\n\n'
        events=[]
        for chunk in (source[:7],source[7:31],source[31:80],source[80:]):
            events.extend(parser.feed_events(chunk))
        self.assertEqual(events[0]['content'],'Hi')
        self.assertTrue(parser.done)

    def test_test_connection_uses_selected_protocol(self):
        captured=[]
        def fake_open(request, timeout, context):
            captured.append((request.full_url, json.loads(request.data)))
            return DummyHTTP(b'{"ok":true}')
        with patch('pet.chat.providers.urllib.request.urlopen',side_effect=fake_open):
            for protocol in ('openai_chat','openai_responses','anthropic_messages'):
                p=ProviderConfig('test', base_url='https://4router.net/v1',
                                 model='claude-haiku-5-5',api_protocol=protocol)
                self.assertTrue(probe_connection(p)[0])
        self.assertTrue(captured[0][0].endswith('/chat/completions'))
        self.assertTrue(captured[1][0].endswith('/responses'))
        self.assertTrue(captured[2][0].endswith('/messages'))
        self.assertIn('system', _to_anthropic_payload({
            'model':'m','messages':[{'role':'system','content':'hello'},
                                    {'role':'user','content':'hi'}]}))

    def test_chat_completion_and_responses_use_one_dispatch(self):
        p=OpenAICompatibleProvider()
        config=ProviderConfig('p',api_protocol='openai_responses')
        body=json.dumps({'output':[{'type':'message','content':[{'type':'output_text','text':'yes'}]}]}).encode()
        with patch.object(p,'_open_responses',return_value=DummyHTTP(body)) as route:
            ans=p._complete_json({'model':'gpt','messages':[{'role':'user','content':'hi'}]},config,None)
            self.assertEqual(ans['content'],'yes')
            self.assertTrue(route.called)

    def test_stream_path_is_responses_when_selected(self):
        p=OpenAICompatibleProvider()
        config=ProviderConfig('g', model='gpt-test', api_protocol='openai_responses', thinking='enabled', reasoning_effort='medium')
        source=(b'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
                b'data: {"type":"response.completed"}\n\n')
        with patch.object(p, '_open_responses', return_value=DummyHTTP(source)) as route:
            import threading
            chunks=list(p._stream_answer([{'role':'user','content':'hi'}], config, threading.Event(), None))
            self.assertEqual(chunks,['Hello'])
            self.assertTrue(route.called)
            self.assertEqual(route.call_args[0][0]['reasoning'], {'effort':'medium'})

    def test_modern_ui_has_only_three_protocol_items(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'pet/chat/ai_settings_page.py').read_text()
        self.assertIn('self.api_protocol.setCurrentData(provider.api_protocol)',source)
        self.assertIn('"api_protocol": str(self.api_protocol.currentData()',source)
        self.assertIn('p.api_protocol = str(draft.get(',source)
        for value in ('openai_chat','openai_responses','anthropic_messages'):
            self.assertIn("'"+value+"'",source)
        self.assertNotIn("self.api_protocol.addItem('自动",source)

    def test_background_calls_share_adapter(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'pet/autonomy.py').read_text()
        self.assertIn("_complete_json(\n        payload, p, None, max_response_bytes=128 * 1024)",source)
        self.assertIn("_complete_json(\n        payload, p, None, max_response_bytes=16 * 1024)",source)


if __name__=='__main__': unittest.main()
