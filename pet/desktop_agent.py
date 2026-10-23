# -*- coding: utf-8 -*-
"""Opt-in OpenClaw Gateway bridge. No Qt imports; safe to test without Windows.

Only an explicit /agent command reaches the external operator-capable Gateway.
When enabled, recent plain-text chat is compressed by Dafeiyu's chat model and
only the short summary is forwarded. Full history, attachments, screenshots, and
regular autonomous decisions are never forwarded by this bridge.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

PREFIX = re.compile(r'^\s*[/／]agent(?:\s+|$)', re.IGNORECASE)
TOKEN_REF = 'desktop-agent/openclaw-gateway'
DEFAULTS = {
    'enabled': False,
    'gateway_url': 'http://127.0.0.1:18789',
    'agent_model': 'openclaw/default',
    'backend_model': '',
    'thinking_level': 'default',
    'include_chat_context': True,
    'windows_allowlist': [],
}


def task_from_message(text: str) -> str | None:
    matched = PREFIX.match(str(text or ''))
    return text[matched.end():].strip() if matched else None


def set_agent_prefix(text: str, enabled: bool) -> str:
    """Switch the explicit /agent prefix without modifying the typed task body."""
    value = str(text or '')
    match = PREFIX.match(value)
    if enabled:
        return value if match else '/agent ' + value
    # Our own toggle inserts exactly one separator. Remove only that separator
    # so the user's original leading whitespace survives a toggle round-trip.
    if value[:7].lower() == '/agent ':
        return value[7:]
    return value[match.end():] if match else value


def normalized_settings(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULTS)
    result['enabled'] = src.get('enabled') is True
    result['gateway_url'] = str(src.get('gateway_url') or DEFAULTS['gateway_url']).strip().rstrip('/')[:200]
    result['agent_model'] = str(src.get('agent_model') or DEFAULTS['agent_model']).strip()[:100]
    # HTTP API recognizes x-openclaw-model for per-request backend selection.
    result['backend_model'] = str(src.get('backend_model') or '').strip()[:160]
    level = str(src.get('thinking_level') or 'default').strip().lower()
    result['thinking_level'] = level if level in {'default','off','low','medium','high','xhigh','max','ultra'} else 'default'
    result['include_chat_context'] = src.get('include_chat_context', True) is True
    whitelist = src.get('windows_allowlist')
    if isinstance(whitelist, str):
        whitelist = whitelist.splitlines()
    allowed = []
    for item in whitelist if isinstance(whitelist, (list, tuple)) else []:
        name = str(item).strip().lower()
        if re.fullmatch(r'[a-z0-9_.-]{1,100}\.exe', name) and name not in allowed:
            allowed.append(name)
    result['windows_allowlist'] = allowed[:30]
    return result


def gateway_endpoint(raw_url: str, path: str) -> str:
    """Only permit HTTP requests to a loopback Gateway; never forward bearer remotely."""
    parts = urllib.parse.urlsplit(str(raw_url or '').strip())
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', '::1', 'localhost')
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path.rstrip('/') not in ('', '/v1') or not parts.port or not 0 < parts.port < 65536):
        raise ValueError('OpenClaw Gateway 只允许本机 http://127.0.0.1:端口（或 localhost / [::1]）；请检查设置。')
    host = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    return f'http://{host}:{parts.port}{path}'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not allow a Gateway redirect to exfiltrate the operator token.


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _gateway_token() -> str:
    try:
        from .chat.models import SecretStore
        return SecretStore().get(TOKEN_REF) or os.environ.get('OPENCLAW_GATEWAY_TOKEN', '')
    except Exception:
        return os.environ.get('OPENCLAW_GATEWAY_TOKEN', '')


def test_gateway(settings, *, token='', timeout=5.0):
    """Read-only /v1/models probe; never creates or runs an agent task."""
    endpoint = gateway_endpoint(settings['gateway_url'], '/v1/models')
    key = token or _gateway_token()
    if not key:
        return False, 'Gateway Token 为空；请填写并保存到 Windows 凭据存储。'
    request = urllib.request.Request(endpoint, headers={'Authorization': 'Bearer ' + key})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.load(response)
        models = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return False, '服务器返回的不是 OpenClaw 模型列表。'
        return True, '连接成功，发现 %d 个 Agent 入口。' % len(models)
    except urllib.error.HTTPError as exc:
        return False, '连接失败（HTTP %d）；确认已开启 chatCompletions 接口以及 Token 正确。' % exc.code
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, '连接失败：' + type(exc).__name__


@dataclass
class OpenClawProvider:
    """Implements ChatService provider.stream protocol, reused by all three chats."""
    settings: dict
    conversation_id: str
    task: str
    history: list[dict] = field(default_factory=list)

    def stream(self, messages, config, cancel, *, response_holder=None):
        from .chat.providers import StreamEvent
        settings = normalized_settings(self.settings)
        if not settings['enabled']:
            raise RuntimeError('电脑 Agent 尚未开启，请到 AI 设置 → OpenClaw Agent 中开启。')
        if not self.task:
            raise RuntimeError('请在 /agent 后面写任务，例如：/agent 帮我整理今天的待办。')
        endpoint = gateway_endpoint(settings['gateway_url'], '/v1/chat/completions')
        token = _gateway_token()
        if not token:
            raise RuntimeError('没有找到 OpenClaw Gateway Token，请在 AI 设置中填写并保存。')
        # Only an explicit /agent task reaches this code. If enabled, recent chat
        # history is sent to Dafeiyu's chat model for compression; only the short
        # summary (never the raw transcript) is then sent to OpenClaw.
        content = self.task[:12000]
        if settings['include_chat_context'] and self.history and config is not None:
            yield StreamEvent('status', '正在整理与当前任务有关的聊天背景…')
            from .agent_context_summary import summarize_history, compose_agent_message
            summary = summarize_history(self.history, self.task, config, cancel)
            if cancel.is_set():
                return
            content = compose_agent_message(self.task, summary)
        # Isolated session namespace; no attachments/screenshots or full transcript.
        session = re.sub(r'[^a-zA-Z0-9_-]', '-', str(self.conversation_id))[:100] or 'pet'
        payload = {
            'model': settings['agent_model'],
            'user': 'dafeiyu:' + session,
            'messages': [{'role': 'user', 'content': content}],
            'stream': True,
        }
        # /think:<level> is parsed as a per-turn inline OpenClaw directive and
        # does not change the Agent global default or the next turn's selection.
        # OpenClaw rejects unsupported tiers; never silently downgrade the level.
        if settings['thinking_level'] != 'default':
            payload['messages'][0]['content'] = ('/think:' + settings['thinking_level'] + '\n' + content)
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                   'Accept': 'text/event-stream'}
        if settings['backend_model']:
            headers['x-openclaw-model'] = settings['backend_model']
        request = urllib.request.Request(
            endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers=headers, method='POST')
        try:
            response = _OPENER.open(request, timeout=180)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('OpenClaw 请求失败（HTTP %d）；检查 Gateway、接口开关与 Token。' % exc.code) from None
        except (OSError, ValueError) as exc:
            raise RuntimeError('OpenClaw 连接失败（%s）；确认本机 Gateway 已运行。' % type(exc).__name__) from None
        if response_holder is not None:
            response_holder.append(response)
        received_done = False
        try:
            yield StreamEvent('status', 'OpenClaw Agent 正在执行任务…')
            for raw in response:
                if cancel.is_set():
                    break
                line = raw.decode('utf-8', errors='replace').strip()
                if not line.startswith('data:'):
                    continue
                value = line[5:].strip()
                if value == '[DONE]':
                    received_done = True
                    break
                try:
                    event = json.loads(value)
                except (ValueError, TypeError):
                    continue
                if 'error' in event:
                    raise RuntimeError('OpenClaw 执行失败，请查看 Gateway 本地日志。')
                for choice in event.get('choices', []):
                    delta = choice.get('delta') or {}
                    content = delta.get('content') or ''
                    if isinstance(content, str) and content:
                        yield content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get('type') == 'text':
                                yield str(part.get('text') or '')
            if not received_done and not cancel.is_set():
                raise RuntimeError('OpenClaw 流式连接提前中断；请检查任务是否完成，勿直接重试有副作用的操作。')
        finally:
            try:
                response.close()
            except Exception:
                pass
            if response_holder is not None and response in response_holder:
                response_holder.remove(response)
