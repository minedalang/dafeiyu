# -*- coding: utf-8 -*-
"""Opt-in Windows MCP stdio tool server for OpenClaw.

Run separately from a normal Python environment:
    python -m pet.windows_agent_mcp

Tools are intentionally restricted to an explicit EXE-name allowlist configured
in the pet's user-data config. No general shell, filesystem, click, or delete tool.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

from .desktop_agent import normalized_settings


def _settings() -> dict:
    # This standalone tool deliberately avoids importing Qt or the whole pet.
    # Source mode: %%APPDATA%%/dsh-pet-standalone/config.json.
    # For an EXE use --config <the EXE variant's AppData config.json>.
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--config')
    args, _ = parser.parse_known_args()
    base = Path(os.environ.get('APPDATA') or str(Path.home()))
    location = args.config or os.environ.get('DAFEIYU_AGENT_CONFIG')
    path = Path(location) if location else base / 'dsh-pet-standalone' / 'config.json'
    if not path.is_file():
        raise FileNotFoundError('找不到桌宠配置文件：%s；EXE 版本请使用 --config 指定。' % path)
    with path.open('r', encoding='utf-8-sig') as stream:
        data = json.load(stream)
    return normalized_settings(data.get('desktop_agent') if isinstance(data, dict) else {})


def _require_windows():
    if sys.platform != 'win32':
        raise RuntimeError('该操作只支持 Windows。')


def _allowlist() -> set[str]:
    settings = _settings()
    if not settings['enabled']:
        raise PermissionError('大胖鱼 AI 设置中未开启电脑 Agent。')
    items = set(settings['windows_allowlist'])
    if not items:
        raise PermissionError('Windows 操作白名单为空；请先在大胖鱼 AI 设置中授权 EXE 名称。')
    return items


def _window_info(hwnd):
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    if not user32.IsWindowVisible(hwnd):
        return None
    size = user32.GetWindowTextLengthW(hwnd)
    if size <= 0:
        return None
    title = ctypes.create_unicode_buffer(min(size + 1, 512))
    user32.GetWindowTextW(hwnd, title, len(title))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buff = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buff))
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buff, ctypes.byref(length)):
            return None
        return {'hwnd': int(hwnd), 'title': title.value[:150],
                'exe': Path(buff.value).name.lower()}
    finally:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def _windows():
    _require_windows()
    user32 = ctypes.windll.user32
    result = []
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        item = _window_info(hwnd)
        if item:
            result.append(item)
        return True
    cb = CALLBACK(collect)
    user32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
    user32.EnumWindows(cb, 0)
    return result


def list_allowed_windows():
    allowed = _allowlist()
    return {'windows': [item for item in _windows() if item['exe'] in allowed][:50]}


def focus_allowed_window(hwnd):
    allowed = _allowlist()
    matches = [w for w in _windows() if w['hwnd'] == int(hwnd)]
    if not matches or matches[0]['exe'] not in allowed:
        raise PermissionError('目标窗口不存在或不在允许操作的程序白名单内。')
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(wintypes.HWND(hwnd)):
        raise RuntimeError('无法将窗口切到前台；请手动激活后再操作。')
    time.sleep(0.15)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError('前台窗口未成功切换；未执行后续操作。')
    return {'focused': True, 'exe': matches[0]['exe'], 'title': matches[0]['title']}


def open_notepad():
    _require_windows()
    if 'notepad.exe' not in _allowlist():
        raise PermissionError('要打开记事本，必须将 notepad.exe 加入电脑操作白名单。')
    import subprocess
    win = os.environ.get('SystemRoot', r'C:\Windows')
    candidate = Path(win) / 'System32' / 'notepad.exe'
    if not candidate.is_file():
        raise FileNotFoundError('Windows 系统目录中的记事本不存在。')
    subprocess.Popen([str(candidate)], shell=False)
    return {'launched': 'notepad.exe', 'hint': '窗口打开后请先调用 list_allowed_windows 再确认操作窗口。'}


# Define ctypes structures for Win32 SendInput. Must use the full INPUT union
# (40 bytes on x64) or adjacent memory is misread by user32.dll.
def _send_unicode(units: list[int]):
    ULONG_PTR = ctypes.c_size_t
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                    ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                    ('dwExtraInfo', ULONG_PTR)]
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                    ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]
    class INPUTUNION(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]
    class INPUT(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    for unit in units:
        pair = (INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(wScan=unit, dwFlags=4))),
                INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(wScan=unit, dwFlags=6))))
        for event in pair:
            if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
                raise RuntimeError('键盘输入被 Windows 拒绝，已停止发送。')


def type_into_allowed_window(hwnd: int, text: str):
    _require_windows()
    if not isinstance(text, str) or not 0 < len(text) <= 2000:
        raise ValueError('文本必须为 1～2000 字，较长内容请拆成小段。')
    # Do not type control characters or hotkeys. No arbitrary shell execution.
    if any(ord(ch) < 32 and ch not in '\n\t' for ch in text):
        raise ValueError('不支持控制字符。')
    allowed = _allowlist()
    hwnd = int(hwnd)
    if hwnd not in {w['hwnd'] for w in _windows() if w['exe'] in allowed}:
        raise PermissionError('目标不在白名单内，拒绝输入。')
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    if user32.GetForegroundWindow() != hwnd:
        raise PermissionError('只有当前前台白名单窗口可以接收输入，请先手动切换。')
    # Validate foreground both during long text and at every operation boundary.
    for offset in range(0, len(text), 32):
        if user32.GetForegroundWindow() != hwnd:
            raise RuntimeError('前台窗口已切换，立即停止输入。')
        # Prevent cross-target window substitution if HWND was reused.
        current = _window_info(hwnd)
        if not current or current['exe'] not in allowed:
            raise PermissionError('前台进程不再是允许操作的程序。')
        chunk = text[offset:offset + 32]
        units = [int.from_bytes(chunk.encode('utf-16-le')[i:i+2], 'little')
                 for i in range(0, len(chunk.encode('utf-16-le')), 2)]
        _send_unicode(units)
    return {'typed_characters': len(text), 'target_exe': current['exe']}


TOOLS = [
    {'name': 'list_allowed_windows', 'description': '只查看大胖鱼已授权 EXE 的可见窗口。',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'focus_allowed_window', 'description': '将白名单中指定窗口切到前台，不执行点击。',
     'inputSchema': {'type': 'object', 'properties': {'hwnd': {'type': 'integer'}},
                     'required': ['hwnd'], 'additionalProperties': False}},
    {'name': 'open_notepad', 'description': '仅可启动 Windows 系统记事本，需白名单包含 notepad.exe。',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'type_into_allowed_window', 'description': '向已授权且正位于前台的窗口输入普通文字；不可执行热键或命令。',
     'inputSchema': {'type': 'object', 'properties': {
         'hwnd': {'type': 'integer'}, 'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000}},
                     'required': ['hwnd', 'text'], 'additionalProperties': False}},
]


def dispatch(method, params):
    if method == 'initialize':
        return {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'dafeiyu-windows-restricted', 'version': '0.1.0'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method != 'tools/call':
        raise ValueError('Unknown MCP method')
    name = params.get('name')
    args = params.get('arguments') or {}
    functions = {
        'list_allowed_windows': lambda: list_allowed_windows(),
        'focus_allowed_window': lambda: focus_allowed_window(args['hwnd']),
        'open_notepad': lambda: open_notepad(),
        'type_into_allowed_window': lambda: type_into_allowed_window(args['hwnd'], args['text']),
    }
    if name not in functions:
        raise ValueError('Unknown tool')
    try:
        result = functions[name]()
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return {'content': [{'type': 'text', 'text': type(exc).__name__ + ': ' + str(exc)[:200]}],
                'isError': True}


def _read_message(stream):
    first = stream.readline()
    if not first:
        return None
    if first.lower().startswith(b'content-length:'):
        size = int(first.split(b':', 1)[1].strip())
        if size < 0 or size > 1024 * 1024:
            raise ValueError('MCP message too large')
        while True:
            line = stream.readline()
            if line in (b'\r\n', b'\n', b''):
                break
        return json.loads(stream.read(size))
    return json.loads(first)


def main():
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            req = _read_message(incoming)
            if req is None:
                break
            if 'id' not in req:
                continue
            try:
                response = {'jsonrpc': '2.0', 'id': req['id'], 'result':
                            dispatch(req.get('method', ''), req.get('params') or {})}
            except Exception as exc:
                response = {'jsonrpc': '2.0', 'id': req['id'], 'error':
                            {'code': -32603, 'message': type(exc).__name__ + ': ' + str(exc)[:200]}}
            data = json.dumps(response, ensure_ascii=False).encode('utf-8')
            # MCP stdio framing is NDJSON, NOT LSP Content-Length headers.
            outgoing.write(data + b'\n')
            outgoing.flush()
        except Exception as exc:
            print('MCP input error: ' + type(exc).__name__, file=sys.stderr)
            break


if __name__ == '__main__':
    main()
