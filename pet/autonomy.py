"""Optional, budgeted Mochi-style autonomous decisions for the desktop pet.

The GUI thread samples pet/cursor state and executes approved actions.  The
background thread alone captures optional screenshots and calls the model.
There is never more than one outstanding request per process.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from .autonomy_intent import build_decision_messages, parse_action_intent
from .autonomy_monitor import get_autonomy_monitor

log = logging.getLogger(__name__)


def _request_intent(provider, observation: dict, animations: tuple[str, ...],
                    recent: tuple[str, ...], screen_enabled: bool, cancelled):
    """Perform one bounded Chat Completions call; never run on Qt's GUI thread."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload

    messages = build_decision_messages(
        observation, animations=animations, recent_actions=recent)
    p = copy.copy(provider)
    p.timeout = min(float(p.timeout), 18.0)
    p.max_tokens = 320

    if screen_enabled:
        from .vision import capture_screen_bytes, resolve_vision_model

        jpeg = capture_screen_bytes()  # in memory only; opt-in, on background thread
        if cancelled.is_set():
            return None
        p.model = resolve_vision_model(p)
        if not p.vision_same_as_chat:
            p.base_url = p.vision_base_url or p.base_url
            p.api_key = p.vision_api_key or p.api_key
        messages[-1]["content"] = [
            {"type": "text", "text": messages[-1]["content"]},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}},
        ]

    if cancelled.is_set():
        return None
    payload = {
        "model": p.model, "messages": messages,
        "stream": False, "max_tokens": 320, "temperature": 0.5,
    }
    # DeepSeek thinking models can exhaust a small action budget on hidden
    # reasoning; the official endpoint accepts an explicit thinking-off flag.
    payload.update(thinking_payload(p, force_disable=True))
    with OpenAICompatibleProvider()._open(payload, p, "application/json") as response:
        raw = response.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("model response exceeds limit")
    data = json.loads(raw)
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("model response has no choices")
    message = choices[0].get("message", {})
    answer = message.get("content") if isinstance(message, dict) else None
    if isinstance(answer, list):
        answer = "".join(str(item.get("text", "")) for item in answer
                         if isinstance(item, dict) and item.get("type") == "text")
    return parse_action_intent(answer, available_animations=animations)


class AutonomyService(QObject):
    """Single process-level autonomy loop, enabled explicitly in AI settings."""

    decision_ready = Signal(int, object, str)

    def __init__(self, config, observe, execute, parent=None):
        super().__init__(parent)
        self._config = config
        self._observe = observe
        self._execute = execute
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._busy = False
        self._generation = 0
        self._request_serial = 0
        self._active_request = -1
        self._cancelled = threading.Event()
        self._enabled = False
        self._screen_enabled = False
        self._history = deque(maxlen=8)
        self._interval_seconds = 40
        self._monitor = get_autonomy_monitor()
        self.decision_ready.connect(self._accept_decision)
        self.apply_config()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def apply_config(self):
        data = self._config.get("chat", {}) or {}
        enabled = data.get("autonomy_enabled") is True
        screen = data.get("autonomy_screen_enabled") is True
        try:
            seconds = int(data.get("autonomy_interval_seconds", 40))
        except (TypeError, ValueError):
            seconds = 40
        interval = max(20, min(300, seconds)) * 1000
        self._interval_seconds = interval // 1000
        was_enabled = self._enabled
        changed = (was_enabled != enabled or self._screen_enabled != screen)
        self._enabled = enabled
        self._screen_enabled = screen
        if changed:
            self._generation += 1
            self._cancelled.set()
            self._cancelled = threading.Event()
        if not enabled:
            self._timer.stop()
        else:
            self._timer.start(interval)
            if not was_enabled:
                # Explicitly enabling is consent for the first non-visual call.
                QTimer.singleShot(2500, self._tick)
        self._monitor.publish(
            enabled=enabled, vision=screen, interval_seconds=self._interval_seconds,
            busy=self._busy if enabled else False,
            state=("已关闭" if not enabled else
                   "AI 请求中" if self._busy else "等待下一次决策"),
            next_decision_at=(time.time() + (2.5 if not was_enabled else
                               self._interval_seconds)) if enabled else None,
        )

    def stop(self):
        self._enabled = False
        self._generation += 1
        self._cancelled.set()
        self._timer.stop()
        self._monitor.publish(
            enabled=False, busy=False, state="已停止", next_decision_at=None)

    def _tick(self):
        if not self._enabled:
            return
        self._monitor.publish(next_decision_at=time.time() + self._interval_seconds)
        if self._busy:
            return
        try:
            observed = self._observe()
        except Exception as exc:  # noqa: BLE001 - GUI observation callback boundary
            log.warning("自主决策：状态采集失败（%s）", type(exc).__name__)
            self._monitor.publish(state="桌宠状态采集失败", last_error=type(exc).__name__)
            return
        if observed is None:
            self._monitor.publish(state="未获取到桌宠状态，下次重试")
            return
        world, animations = observed
        try:
            settings = self._config.chat_settings()
            provider = settings.active_config
            provider.api_key = self._config.resolve_api_key(provider)
            if self._screen_enabled and not provider.vision_same_as_chat:
                from .chat.models import SecretStore
                provider.vision_api_key = (
                    SecretStore().get(provider.vision_api_key_ref)
                    or provider.vision_api_key or provider.api_key)
            if not provider.model or not provider.base_url:
                self._monitor.publish(state="模型尚未配置", last_error="缺少模型或 API 地址")
                return
        except (AttributeError, ImportError, KeyError, ValueError):
            log.warning("自主决策：模型配置不可用")
            self._monitor.publish(state="模型配置不可用", last_error="模型配置不可用")
            return
        self._busy = True
        self._request_serial += 1
        request_id = self._request_serial
        self._active_request = request_id
        generation = self._generation
        cancel = self._cancelled
        self._monitor.publish(
            busy=True, state="正在等待模型决策", model=str(provider.model),
            vision=self._screen_enabled, last_request_at=time.time(),
            requests=self._monitor.snapshot()["requests"] + 1, last_error="",
        )
        thread = threading.Thread(
            target=self._run_worker,
            args=(request_id, generation, cancel, provider, world,
                  tuple(animations), tuple(self._history), self._screen_enabled),
            name="pet-autonomy-llm", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._busy = False
            self._monitor.publish(
                busy=False, state="请求线程启动失败", last_error="RuntimeError")

    def _run_worker(self, request_id, generation, cancel, provider, world,
                    animations, recent, vision):
        intent = None
        error_kind = ""
        try:
            intent = _request_intent(provider, world, animations, recent, vision, cancel)
        except Exception as exc:  # noqa: BLE001 - network/proxy/provider boundary
            # Never log raw HTTP errors, request URLs, screenshots or API Keys.
            error_kind = type(exc).__name__
            status = getattr(exc, "status", None)
            if isinstance(status, int) and 400 <= status <= 599:
                error_kind += f" (HTTP {status})"
        try:
            self.decision_ready.emit(request_id, (generation, intent), error_kind)
        except RuntimeError:
            pass  # application exited while a daemon network call was pending

    def _accept_decision(self, request_id: int, result, error_kind: str):
        if request_id != self._active_request:
            return
        self._busy = False
        generation, intent = result
        if generation != self._generation or not self._enabled:
            self._monitor.publish(
                busy=False, state="已关闭" if not self._enabled else "等待下一次决策")
            return
        finished_at = time.time()
        completed = self._monitor.snapshot()["completed"] + 1
        if error_kind:
            log.warning("自主决策：请求未完成（%s），保留待机动画", error_kind)
            self._monitor.publish(
                record=True, busy=False, state="AI 请求失败",
                last_action="—", last_result="请求失败", last_error=error_kind,
                last_decision_at=finished_at, completed=completed)
            return
        if intent is None:
            self._monitor.publish(
                record=True, busy=False, state="本轮无可执行动作",
                last_action="—", last_result="无动作或无法解析",
                last_error="", last_decision_at=finished_at,
                completed=completed)
            return
        action = str(getattr(intent, "verb", "未知动作") or "未知动作")
        try:
            executed = bool(self._execute(intent))
        except (RuntimeError, TypeError, ValueError):
            log.warning("自主决策：动作未执行")
            self._monitor.publish(
                record=True, busy=False, state="执行动作时出错",
                last_action=action, last_result="执行异常",
                last_error="动作执行异常", last_decision_at=finished_at,
                completed=completed)
            return
        if executed:
            self._history.append(intent.verb)
        self._monitor.publish(
            record=True, busy=False,
            state="等待下一次决策" if executed else "AI 动作未执行",
            last_action=action, last_result="执行器已接受" if executed else "执行器未接受",
            last_error="", last_decision_at=finished_at,
            completed=completed)
