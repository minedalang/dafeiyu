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

from .autonomy_intent import (
    ACTIVITY_CLIPS, ActionIntent, ActivityPlan,
    build_decision_messages, parse_activity_plan,
)
from .autonomy_monitor import get_autonomy_monitor

log = logging.getLogger(__name__)


def _request_intent(provider, observation: dict, animations: tuple[str, ...],
                    recent: tuple[str, ...], recent_activities: tuple[str, ...],
                    screen_enabled: bool, cancelled):
    """Perform one bounded Chat Completions call; never run on Qt's GUI thread."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload

    messages = build_decision_messages(
        observation, animations=animations, recent_actions=recent,
        recent_activities=recent_activities)
    p = copy.copy(provider)
    # A multimodal multi-step plan needs more time/output than one small action.
    p.timeout = min(float(p.timeout), 35.0)
    p.max_tokens = 720

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
        "stream": False, "max_tokens": 720, "temperature": 0.5,
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
    return parse_activity_plan(answer, available_animations=animations)


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
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.timeout.connect(self._advance_plan)
        self._plan_active = False
        self._plan_steps = deque()
        self._plan_name = ""
        self._plan_total = 0
        self._plan_done = 0
        self._plan_deferrals = 0
        self._plan_from_chat = False
        self._busy = False
        self._generation = 0
        self._request_serial = 0
        self._active_request = -1
        self._cancelled = threading.Event()
        self._enabled = False
        self._screen_enabled = False
        self._history = deque(maxlen=8)
        self._recent_activities = deque(maxlen=8)
        self._pending_user_text = ""
        self._current_request_user = False
        self._interval_seconds = 40
        self._monitor = get_autonomy_monitor()
        self._monitor.share_with(getattr(config, "dir", None), writer=True)
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
            self._cancel_plan()
        if not enabled:
            self._timer.stop()
            self._cancel_plan()
        else:
            self._timer.start(interval)
            if not was_enabled:
                # Explicitly enabling is consent for the first non-visual call.
                QTimer.singleShot(2500, self._tick)
        self._monitor.publish(
            enabled=enabled, vision=screen, interval_seconds=self._interval_seconds,
            busy=self._busy if enabled else False,
            state=("已关闭" if not enabled else
                   "AI 请求中" if self._busy else
                   f"正在执行活动：{self._plan_name}" if self._plan_active else
                   "等待下一次决策"),
            next_decision_at=(time.time() + (2.5 if not was_enabled else
                               self._interval_seconds)) if enabled and not self._plan_active
                               else None,
        )

    def stop(self):
        self._enabled = False
        self._generation += 1
        self._cancelled.set()
        self._timer.stop()
        self._cancel_plan()
        self._pending_user_text = ""
        self._monitor.publish(
            enabled=False, busy=False, state="已停止", next_decision_at=None)

    def _cancel_plan(self) -> None:
        """Cancel queued AI steps without interrupting user-controlled animation."""
        self._plan_timer.stop()
        self._plan_steps.clear()
        self._plan_active = False
        self._plan_name = ""
        self._plan_total = 0
        self._plan_done = 0
        self._plan_deferrals = 0
        self._plan_from_chat = False

    def queue_user_message(self, text: str) -> None:
        """A user's chat overrides an idle plan; actions remain model-selected.

        Only the latest short plain-text message is handed to the autonomous
        planner. The normal chat provider independently writes its answer.
        """
        if not self._enabled:
            return
        message = str(text or "").strip()[:300]
        if not message:
            return
        self._pending_user_text = message
        self._cancel_plan()
        self._monitor.publish(state="收到聊天消息，准备选择对应动作",
                              next_decision_at=None)
        # An in-flight request cannot be interrupted, but its result becomes
        # obsolete; _accept_decision will run this pending command next.
        if not self._busy:
            QTimer.singleShot(0, self._tick)

    def _tick(self):
        if not self._enabled:
            return
        if self._plan_active:
            self._monitor.publish(
                state=(f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                       else f"正在执行活动：{self._plan_name}"),
                next_decision_at=None)
            return  # Finish the current local sequence before another API call.
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
        user_message = self._pending_user_text
        if user_message:
            world = {**world, "user_message": user_message}
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
        self._current_request_user = bool(user_message)
        if user_message:
            self._pending_user_text = ""
        self._request_serial += 1
        request_id = self._request_serial
        self._active_request = request_id
        generation = self._generation
        cancel = self._cancelled
        self._monitor.publish(
            busy=True, state=("正在理解聊天指令" if user_message else "正在等待模型决策"),
            model=str(provider.model),
            vision=self._screen_enabled, last_request_at=time.time(),
            requests=self._monitor.snapshot()["requests"] + 1, last_error="",
        )
        thread = threading.Thread(
            target=self._run_worker,
            args=(request_id, generation, cancel, provider, world,
                  tuple(animations), tuple(self._history),
                  tuple(self._recent_activities), self._screen_enabled),
            name="pet-autonomy-llm", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._busy = False
            self._monitor.publish(
                busy=False, state="请求线程启动失败", last_error="RuntimeError")

    def _run_worker(self, request_id, generation, cancel, provider, world,
                    animations, recent, recent_activities, vision):
        intent = None
        error_kind = ""
        try:
            intent = _request_intent(provider, world, animations, recent,
                                     recent_activities, vision, cancel)
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
        if self._pending_user_text:
            # A chat message arrived after this request started. The old
            # spontaneous action must not play on top of a new user request.
            self._monitor.publish(busy=False, state="收到新的聊天指令，重新决策")
            QTimer.singleShot(0, self._tick)
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
        if isinstance(intent, ActionIntent):  # Backward compatible worker result.
            intent = ActivityPlan("free", (intent,))
        if not isinstance(intent, ActivityPlan) or not intent.steps:
            self._monitor.publish(
                record=True, busy=False, state="模型决策格式错误",
                last_action="—", last_result="无法识别活动计划",
                last_error="InvalidActivityPlan", last_decision_at=finished_at,
                completed=completed)
            return
        self._cancel_plan()
        self._plan_steps.extend(intent.steps)
        self._plan_total = len(intent.steps)
        self._plan_name = ACTIVITY_CLIPS.get(intent.activity, ("自由活动", ()))[0]
        self._plan_from_chat = self._current_request_user
        self._plan_active = True
        self._recent_activities.append(intent.activity)
        self._monitor.publish(
            busy=False, state=(f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                               else f"AI 已规划：{self._plan_name}"),
            last_error="", last_decision_at=finished_at, completed=completed,
            next_decision_at=None)
        self._advance_plan()

    def _advance_plan(self) -> None:
        """Execute the next validated step on Qt's thread, no further API calls."""
        if not self._plan_active or not self._enabled:
            return
        if not self._plan_steps:
            self._cancel_plan()
            self._monitor.publish(state="等待下一次决策",
                                  next_decision_at=time.time() + self._interval_seconds)
            return
        # User dragging/clicking or the pet disappearing always wins over AI.
        try:
            ready = self._observe() is not None
        except Exception:
            ready = False
        if not ready:
            self._plan_deferrals += 1
            if self._plan_deferrals <= 3:
                self._monitor.publish(state="用户交互优先，暂缓活动")
                self._plan_timer.start(2500)
                return
            self._cancel_plan()
            self._monitor.publish(state="用户交互或桌宠不可用，已中断活动",
                                  next_decision_at=time.time() + self._interval_seconds)
            return
        self._plan_deferrals = 0
        intent = self._plan_steps.popleft()
        self._plan_done += 1
        action = f"{intent.verb}：{intent.animation}" if intent.animation else intent.verb
        result = "执行器未接受"
        try:
            executed = bool(self._execute(intent))
            if not executed and intent.say and intent.verb != "say":
                # A failed movement must not silence an otherwise valid line.
                fallback = ActionIntent(verb="say", say=intent.say,
                                        emotion=intent.emotion)
                if bool(self._execute(fallback)):
                    result = "动作未接受，台词已显示"
            elif executed:
                result = "执行器已接受"
        except (RuntimeError, TypeError, ValueError):
            log.warning("自主活动：执行器异常，跳过本步")
            result = "执行异常"
            executed = False
        if executed:
            self._history.append(action)
        self._monitor.publish(
            record=True, busy=False,
            state=(f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                   else f"正在执行活动：{self._plan_name}")
                  + f"（{self._plan_done}/{self._plan_total}）",
            last_action=action, last_result=result, last_error="",
            last_decision_at=time.time(), next_decision_at=None)
        # Existing source clips last around 10s. Never cut a play clip short;
        # execute the next step only after a bounded presentation interval.
        delay_ms = {"play": 11500, "walk": 8000, "approach_cursor": 8000,
                    "retreat_cursor": 8000, "say": 4500, "idle": 4500,
                    "turn": 3500, "look_at_cursor": 3500}.get(intent.verb, 5000)
        self._plan_timer.start(delay_ms)
