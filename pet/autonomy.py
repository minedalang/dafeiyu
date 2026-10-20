"""Optional, budgeted Mochi-style autonomous decisions for the desktop pet.

The GUI thread samples pet/cursor state and executes approved actions.  The
background thread alone captures optional screenshots and calls the model.
There is never more than one outstanding request per process.
"""

from __future__ import annotations

import base64
import copy
import logging
import threading
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from .autonomy_intent import build_decision_messages, parse_action_intent

log = logging.getLogger(__name__)


def _request_intent(provider, observation: dict, animations: tuple[str, ...],
                    recent: tuple[str, ...], screen_enabled: bool, cancelled):
    """Perform one bounded model call; never run on Qt's GUI thread."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload, uses_anthropic_messages

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
    # Haiku 5.5 thinks adaptively by default. Keep autonomous action selection
    # fast and leave enough tokens for the final structured action.
    if uses_anthropic_messages(p) and str(p.model).lower().split('/')[-1] == 'claude-haiku-5-5':
        payload['max_tokens'] = 512
        payload['output_config'] = {'effort': 'low'}
    # DeepSeek thinking models can exhaust a small action budget on hidden
    # reasoning; the official endpoint accepts an explicit thinking-off flag.
    payload.update(thinking_payload(p, force_disable=True))
    message = OpenAICompatibleProvider()._complete_json(
        payload, p, None, max_response_bytes=128 * 1024)
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

    def stop(self):
        self._enabled = False
        self._generation += 1
        self._cancelled.set()
        self._timer.stop()

    def _tick(self):
        if not self._enabled or self._busy:
            return
        observed = self._observe()
        if observed is None:
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
                return
        except (AttributeError, ImportError, KeyError, ValueError):
            log.warning("自主决策：模型配置不可用")
            return
        self._busy = True
        self._request_serial += 1
        request_id = self._request_serial
        self._active_request = request_id
        generation = self._generation
        cancel = self._cancelled
        thread = threading.Thread(
            target=self._run_worker,
            args=(request_id, generation, cancel, provider, world,
                  tuple(animations), tuple(self._history), self._screen_enabled),
            name="pet-autonomy-llm", daemon=True)
        thread.start()

    def _run_worker(self, request_id, generation, cancel, provider, world,
                    animations, recent, vision):
        intent = None
        error_kind = ""
        try:
            intent = _request_intent(provider, world, animations, recent, vision, cancel)
        except Exception as exc:  # noqa: BLE001 - network/proxy/provider boundary
            # Never log raw HTTP errors, request URLs, screenshots or API Keys.
            error_kind = type(exc).__name__
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
            return
        if error_kind:
            log.warning("自主决策：请求未完成（%s），保留待机动画", error_kind)
            return
        if intent is not None:
            try:
                if self._execute(intent):
                    self._history.append(intent.verb)
            except (RuntimeError, TypeError, ValueError):
                log.warning("自主决策：动作未执行")
