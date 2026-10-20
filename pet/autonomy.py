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
from .activity_lifecycle import (
    SLEEP_ANIMATION, WAKE_ANIMATIONS, duration_seconds, is_night, sleep_pressure,
)

log = logging.getLogger(__name__)


def _request_intent(provider, observation: dict, animations: tuple[str, ...],
                    recent: tuple[str, ...], recent_activities: tuple[str, ...],
                    screen_enabled: bool, cancelled, *,
                    recent_dialogue: tuple[dict, ...] = (),
                    visual_frame: bytes | None = None):
    """Perform one bounded Chat Completions call; never run on Qt's GUI thread."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload

    messages = build_decision_messages(
        observation, animations=animations, recent_actions=recent,
        recent_activities=recent_activities, recent_dialogue=recent_dialogue)
    p = copy.copy(provider)
    # A multimodal multi-step plan needs more time/output than one small action.
    p.timeout = min(float(p.timeout), 35.0)
    p.max_tokens = 720

    if visual_frame is not None or screen_enabled:
        from .vision import capture_screen_bytes, resolve_vision_model

        # A whitelisted foreground-window frame supplied by the proactive
        # watcher takes priority. Never capture again for the same decision.
        jpeg = visual_frame if visual_frame is not None else capture_screen_bytes()
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

    def __init__(self, config, observe, execute, parent=None, *,
                 recent_dialogue=None, prefer_external_vision=None,
                 begin_sleep_loop=None, sleep_loop_active=None, end_sleep_loop=None):
        super().__init__(parent)
        self._config = config
        self._observe = observe
        self._execute = execute
        self._get_dialogue = recent_dialogue or (lambda: ())
        self._prefer_external_vision = prefer_external_vision or (lambda: False)
        self._begin_sleep_loop = begin_sleep_loop or (lambda _animation: False)
        self._sleep_loop_active = sleep_loop_active or (lambda _animation: False)
        self._end_sleep_loop = end_sleep_loop or (lambda _animation: False)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.timeout.connect(self._advance_plan)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(500)
        self._activity_timer.timeout.connect(self._activity_watch)
        self._plan_active = False
        self._plan_steps = deque()
        self._plan_name = ""
        self._plan_total = 0
        self._plan_done = 0
        self._plan_deferrals = 0
        self._plan_from_chat = False
        self._plan_activity = ""
        self._plan_deadline = 0.0
        self._accepted_repeats = []
        self._sleep_held = False
        self._last_awakened_at = 0.0
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
        # One most-recent foreground-window frame, kept in RAM only. The
        # observer and autonomous planner must not each charge an API request.
        self._pending_visual = None
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
            self._pending_visual = None
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
        self._pending_visual = None
        self._monitor.publish(
            enabled=False, busy=False, state="已停止", next_decision_at=None)

    def _cancel_plan(self, *, awakened: bool = False) -> None:
        """Cancel queued AI steps without interrupting user-controlled animation."""
        if self._sleep_held:
            try:
                self._end_sleep_loop(SLEEP_ANIMATION)
            except (RuntimeError, TypeError, ValueError):
                log.warning("自主睡眠：动画循环释放失败")
            if awakened and is_night():
                self._last_awakened_at = time.monotonic()
        self._activity_timer.stop()
        self._plan_timer.stop()
        self._plan_steps.clear()
        self._plan_active = False
        self._plan_name = ""
        self._plan_total = 0
        self._plan_done = 0
        self._plan_deferrals = 0
        self._plan_from_chat = False
        self._plan_activity = ""
        self._plan_deadline = 0.0
        self._accepted_repeats = []
        self._sleep_held = False

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
        self._cancel_plan(awakened=True)
        self._monitor.publish(state="收到聊天消息，准备选择对应动作",
                              next_decision_at=None)
        # An in-flight request cannot be interrupted, but its result becomes
        # obsolete; _accept_decision will run this pending command next.
        if not self._busy:
            QTimer.singleShot(0, self._tick)

    def queue_visual_observation(self, jpeg: bytes, *, process: str = "",
                                 activity: str = "") -> bool:
        """Accept one already-authorized window image; never call a second LLM.

        Invoked on Qt's thread, after the proactive watcher's window whitelist,
        foreground checks and cooldown have passed. Frames expire quickly and
        are never stored on disk or added to textual chat history.
        """
        if not self._enabled or not isinstance(jpeg, bytes) or not jpeg:
            return False
        self._pending_visual = (time.monotonic(), jpeg,
                                str(process)[:100], str(activity)[:100])
        self._monitor.publish(state="观察到窗口变化，等待统一 AI 决策")
        if not self._busy and not self._plan_active:
            QTimer.singleShot(0, self._tick)
        return True

    def _tick(self):
        if not self._enabled:
            return
        if self._plan_active:
            self._monitor.publish(
                state=("正在持续睡眠（现有动画中段循环，可叫醒）"
                       if self._sleep_held else
                       f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
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
        awakened_age = (time.monotonic() - self._last_awakened_at
                        if self._last_awakened_at else None)
        world = {**world, "sleep_state": {
            "pressure": sleep_pressure(), "night": is_night(),
            "awakened_by_user_seconds_ago": (
                round(awakened_age) if awakened_age is not None and awakened_age < 600
                else None),
        }}
        queued_visual = self._pending_visual
        if queued_visual is not None:
            self._pending_visual = None
            age, jpeg, process, activity = queued_visual
            if time.monotonic() - age <= 30:
                world = {**world, "foreground_observation": {
                    "process": process, "activity": activity,
                    "source": "whitelisted_foreground_window"}}
                visual_frame = jpeg
            else:
                visual_frame = None
        else:
            visual_frame = None
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
        try:
            dialogue = tuple(self._get_dialogue())
        except Exception:  # Bad/unavailable history must not stop pet behavior.
            log.warning("自主决策：近期对话读取失败")
            dialogue = ()
        try:
            # When both vision switches are enabled, the proactive watcher
            # owns capture and supplies whitelisted frames. No full-screen
            # snapshot or independent visual model call in that mode.
            take_own_screen = self._screen_enabled and not bool(self._prefer_external_vision())
        except Exception:
            take_own_screen = self._screen_enabled
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
            vision=bool(visual_frame is not None or take_own_screen), last_request_at=time.time(),
            requests=self._monitor.snapshot()["requests"] + 1, last_error="",
        )
        thread = threading.Thread(
            target=self._run_worker,
            args=(request_id, generation, cancel, provider, world,
                  tuple(animations), tuple(self._history),
                  tuple(self._recent_activities), take_own_screen,
                  dialogue, visual_frame),
            name="pet-autonomy-llm", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._busy = False
            self._monitor.publish(
                busy=False, state="请求线程启动失败", last_error="RuntimeError")

    def _run_worker(self, request_id, generation, cancel, provider, world,
                    animations, recent, recent_activities, vision,
                    dialogue=(), visual_frame=None):
        intent = None
        error_kind = ""
        try:
            intent = _request_intent(provider, world, animations, recent,
                                     recent_activities, vision, cancel,
                                     recent_dialogue=dialogue,
                                     visual_frame=visual_frame)
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
        steps = list(intent.steps)
        duration = duration_seconds(intent.activity, intent.duration_minutes)
        if intent.activity == "nap":
            # Never play the awakening tail at the start of a 20-minute nap.
            # The sleep clip is required to hold this activity for minutes.
            try:
                observation = self._observe()
                available = frozenset(observation[1]) if observation else frozenset()
            except Exception:
                available = frozenset()
            if SLEEP_ANIMATION in available:
                steps = [step for step in steps if step.animation != SLEEP_ANIMATION
                         and step.animation not in WAKE_ANIMATIONS]
                steps = steps[:3] + [ActionIntent("play", animation=SLEEP_ANIMATION)]
            else:
                duration = 0  # No appropriate clip: do not claim to be sleeping.
        self._plan_steps.extend(steps)
        self._plan_total = len(steps)
        self._plan_name = ACTIVITY_CLIPS.get(intent.activity, ("自由活动", ()))[0]
        self._plan_activity = intent.activity
        self._plan_deadline = time.monotonic() + duration if duration else 0.0
        self._plan_from_chat = self._current_request_user
        self._plan_active = True
        self._recent_activities.append(intent.activity)
        self._monitor.publish(
            busy=False, state=(f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                               else f"AI 已规划：{self._plan_name}"),
            last_error="", last_decision_at=finished_at, completed=completed,
            next_decision_at=None)
        self._advance_plan()

    def _activity_watch(self) -> None:
        """Watch a held sleep without waking the model or replacing user's click."""
        if not self._enabled or not self._plan_active or not self._sleep_held:
            self._activity_timer.stop()
            return
        if time.monotonic() >= self._plan_deadline:
            self._cancel_plan()
            self._monitor.publish(state="休息结束，醒来继续活动",
                                  next_decision_at=time.time() + self._interval_seconds)
            return
        try:
            active = bool(self._sleep_loop_active(SLEEP_ANIMATION))
        except (RuntimeError, TypeError, ValueError):
            active = False
        if not active:
            self._cancel_plan(awakened=True)
            self._monitor.publish(state="主人叫醒了小鲸鱼，准备回应",
                                  next_decision_at=None)
            QTimer.singleShot(1000, self._tick)

    def _advance_plan(self) -> None:
        """Execute the next validated step on Qt's thread, no further API calls."""
        if not self._plan_active or not self._enabled:
            return
        if self._sleep_held:
            return  # 由 500ms 轻量时钟保持续播，绝不重复发 AI 请求。
        if not self._plan_steps:
            if self._plan_deadline and time.monotonic() < self._plan_deadline:
                if self._accepted_repeats:
                    self._plan_steps.extend(self._accepted_repeats)
                    self._plan_total = len(self._accepted_repeats)
                    self._plan_done = 0
                else:
                    self._plan_deadline = 0.0  # No usable clips: don't spin.
            if self._plan_steps:
                self._plan_timer.start(250)
                return
            self._cancel_plan()
            self._monitor.publish(state="等待下一次决策",
                                  next_decision_at=time.time() + self._interval_seconds)
            if self._pending_visual is not None:
                # A newer screen event arrived while an animation was playing.
                # Process it promptly if fresh rather than waiting a whole
                # interval and losing the 30-second in-memory frame.
                QTimer.singleShot(0, self._tick)
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
            self._cancel_plan(awakened=True)
            self._monitor.publish(state="用户交互或桌宠不可用，已中断活动",
                                  next_decision_at=time.time() + self._interval_seconds)
            return
        self._plan_deferrals = 0
        intent = self._plan_steps.popleft()
        self._plan_done += 1
        action = f"{intent.verb}：{intent.animation}" if intent.animation else intent.verb
        result = "执行器未接受"
        sleep_step = (self._plan_activity == "nap"
                      and intent.verb == "play"
                      and intent.animation == SLEEP_ANIMATION
                      and self._plan_deadline > time.monotonic())
        try:
            executed = bool(self._begin_sleep_loop(SLEEP_ANIMATION) if sleep_step
                            else self._execute(intent))
            if executed and sleep_step:
                self._sleep_held = True
                self._activity_timer.start()
                if intent.say:
                    # Sleep clip owns the picture; a separate short bubble may
                    # accompany it without replacing the animation.
                    self._execute(ActionIntent("say", say=intent.say,
                                               emotion=intent.emotion))
            if executed and intent.verb == "play" and not sleep_step:
                if intent not in self._accepted_repeats:
                    self._accepted_repeats.append(intent)
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
        if self._sleep_held:
            self._monitor.publish(state="正在持续睡眠（现有动画中段循环，可叫醒）")
            return
        # Existing source clips last around 10s. Never cut a play clip short;
        # execute the next step only after a bounded presentation interval.
        delay_ms = {"play": 11500, "walk": 8000, "approach_cursor": 8000,
                    "retreat_cursor": 8000, "say": 4500, "idle": 4500,
                    "turn": 3500, "look_at_cursor": 3500}.get(intent.verb, 5000)
        self._plan_timer.start(delay_ms)
