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
import random
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from .autonomy_intent import (
    ACTIVITY_CLIPS, ONE_SHOT_CLIPS, ActionIntent, ActivityPlan,
    activity_stage_steps, build_decision_messages, parse_activity_plan,
    silent_play_replay,
)
from .autonomy_monitor import get_autonomy_monitor
from .autonomy_speech import (build_speech_messages, parse_speech_response,
                              speech_interval_bounds)
from .activity_lifecycle import (
    SLEEP_ANIMATION, WAKE_ANIMATIONS, duration_seconds, is_night, sleep_pressure,
)
from .life_state import WhaleLife

log = logging.getLogger(__name__)
_REPLAN_INTERVAL_MS = 90_000


def _request_intent(provider, observation: dict, animations: tuple[str, ...],
                    recent: tuple[str, ...], recent_activities: tuple[str, ...],
                    screen_enabled: bool, cancelled, *,
                    recent_dialogue: tuple[dict, ...] = (),
                    visual_frame: bytes | None = None,
                    persona: str = "", whitelist: tuple[str, ...] = ()):
    """One autonomous request, with *only* allowlisted foreground visual input."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload

    messages = build_decision_messages(
        observation, animations=animations, recent_actions=recent,
        recent_activities=recent_activities, recent_dialogue=recent_dialogue)
    if persona:
        messages[0]["content"] += (
            "\n【角色个性设定：请遵守下列说话语气与身份，但仍严格按上面的 JSON 规则决策】\n"
            + persona[:1600])
    p = copy.copy(provider)
    # A multimodal multi-step plan needs more time/output than one small action.
    p.timeout = min(float(p.timeout), 35.0)
    p.max_tokens = 720

    if screen_enabled and not cancelled.is_set():
        from .autonomy_vision import capture_allowed_foreground
        from .vision import resolve_vision_model

        jpeg, process = capture_allowed_foreground(whitelist, cancelled=cancelled)
        if cancelled.is_set():
            return None
        if jpeg:
            p.model = resolve_vision_model(p)
            if not p.vision_same_as_chat:
                p.base_url = p.vision_base_url or p.base_url
                p.api_key = p.vision_api_key or p.api_key
            messages[-1]["content"] = [
                {"type": "text", "text": messages[-1]["content"] +
                 "\n视觉来源：已授权的前台程序 " + process},
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
    message = OpenAICompatibleProvider()._complete_json(
        payload, p, None, max_response_bytes=128 * 1024)
    return parse_activity_plan(message.get('content'), available_animations=animations)


def _request_speech(provider, observation: dict, *, trigger: str,
                    persona: str, dialogue: tuple[dict, ...],
                    recent_lines: tuple[str, ...], cancelled,
                    visual_frame: bytes | None = None) -> str | None:
    """One bounded LLM request to speak (or elect silence)."""
    from .chat.providers import OpenAICompatibleProvider, thinking_payload

    if cancelled.is_set():
        return None
    p = copy.copy(provider)
    p.timeout = min(float(p.timeout), 25.0)
    p.max_tokens = 180
    messages = build_speech_messages(
        observation, trigger=trigger, persona=persona,
        recent_dialogue=dialogue, recent_speech=recent_lines)
    if visual_frame is not None:
        from .vision import resolve_vision_model

        p.model = resolve_vision_model(p)
        if not p.vision_same_as_chat:
            p.base_url = p.vision_base_url or p.base_url
            p.api_key = p.vision_api_key or p.api_key
        messages[-1]["content"] = [
            {"type": "text", "text": messages[-1]["content"]},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(visual_frame).decode("ascii")}},
        ]
    payload = {
        "model": p.model,
        "messages": messages,
        "stream": False, "max_tokens": 180, "temperature": 0.65,
    }
    payload.update(thinking_payload(p, force_disable=True))
    message = OpenAICompatibleProvider()._complete_json(
        payload, p, None, max_response_bytes=16 * 1024)
    if cancelled.is_set():
        return None
    return parse_speech_response(message.get('content'))


class AutonomyService(QObject):
    """Single process-level autonomy loop, enabled explicitly in AI settings."""

    decision_ready = Signal(int, object, str)
    speech_ready = Signal(int, object, str)

    def __init__(self, config, observe, execute, parent=None, *,
                 recent_dialogue=None, prefer_external_vision=None,
                 begin_sleep_loop=None, sleep_loop_active=None, end_sleep_loop=None,
                 persona=None, can_speak=None, sleep_dragging=None):
        super().__init__(parent)
        self._config = config
        self._observe = observe
        self._execute = execute
        self._get_dialogue = recent_dialogue or (lambda: ())
        self._get_persona = persona or (lambda: "")
        self._can_speak = can_speak or (lambda _trigger: True)
        self._prefer_external_vision = prefer_external_vision or (lambda: False)
        self._begin_sleep_loop = begin_sleep_loop or (lambda _animation: False)
        self._sleep_loop_active = sleep_loop_active or (lambda _animation: False)
        self._end_sleep_loop = end_sleep_loop or (lambda _animation: False)
        self._sleep_dragging = sleep_dragging or (lambda: False)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.timeout.connect(self._advance_plan)
        self._replan_timer = QTimer(self)
        self._replan_timer.setSingleShot(True)
        self._replan_timer.timeout.connect(self._replan_tick)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(500)
        self._activity_timer.timeout.connect(self._activity_watch)
        self._life_timer = QTimer(self)
        self._life_timer.setInterval(30_000)
        self._life_timer.timeout.connect(self._update_life)
        self._speech_timer = QTimer(self)
        self._speech_timer.setInterval(15_000)
        self._speech_timer.timeout.connect(self._speech_tick)
        self._plan_active = False
        self._plan_steps = deque()
        self._plan_name = ""
        self._plan_total = 0
        self._plan_done = 0
        self._plan_deferrals = 0
        self._plan_from_chat = False
        self._plan_activity = ""
        self._plan_deadline = 0.0
        self._plan_deadline_wall = 0.0
        self._plan_available = frozenset()
        self._plan_revision = 0
        self._accepted_repeats = []
        self._plan_repeating = False
        self._sleep_held = False
        self._step_held_animation = ""
        self._life_activity_started = False
        self._last_awakened_at = 0.0
        self._busy = False
        self._generation = 0
        self._request_serial = 0
        self._active_request = -1
        self._request_animations = ()
        self._cancelled = threading.Event()
        self._enabled = False
        self._screen_enabled = False
        self._screen_whitelist = ()
        self._history = deque(maxlen=8)
        self._recent_activities = deque(maxlen=8)
        self._pending_user_text = ""
        self._current_request_user = False
        self._current_request_replan = False
        self._replan_request_revision = -1
        self._recent_clips = deque(maxlen=8)
        self._speech_busy = False
        self._speech_serial = 0
        self._speech_active = -1
        self._speech_last_at = 0.0
        self._speech_recent = deque(maxlen=5)
        self._speech_interval_min_seconds = 180
        self._speech_interval_max_seconds = 360
        self._speech_next_at = time.monotonic() + self._next_speech_delay()
        self._last_click_request_at = float("-inf")
        self._protected_click_at = float("-inf")
        self._protected_click_pos = None
        self._speaking_for_click = False
        # One most-recent foreground-window frame, kept in RAM only. The
        # observer and autonomous planner must not each charge an API request.
        self._pending_visual = None
        self._interval_seconds = 40
        self._monitor = get_autonomy_monitor()
        self._monitor.share_with(getattr(config, "dir", None), writer=True)
        self._life = WhaleLife(getattr(config, "dir", None))
        self.decision_ready.connect(self._accept_decision)
        self.speech_ready.connect(self._accept_speech)
        self.apply_config()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _next_speech_delay(self) -> float:
        return random.uniform(self._speech_interval_min_seconds,
                              self._speech_interval_max_seconds)

    def apply_config(self):
        data = self._config.get("chat", {}) or {}
        enabled = data.get("autonomy_enabled") is True
        screen = data.get("autonomy_screen_enabled") is True
        from .autonomy_vision import normalize_whitelist
        whitelist = normalize_whitelist(data.get("autonomy_screen_whitelist", []))
        try:
            seconds = int(data.get("autonomy_interval_seconds", 40))
        except (TypeError, ValueError):
            seconds = 40
        interval = max(20, min(300, seconds)) * 1000
        self._interval_seconds = interval // 1000
        old_speech_bounds = (self._speech_interval_min_seconds,
                             self._speech_interval_max_seconds)
        speech_bounds = speech_interval_bounds(
            self._config.get("autonomy_speech_min_minutes", 3),
            self._config.get("autonomy_speech_max_minutes", 6))
        (self._speech_interval_min_seconds,
         self._speech_interval_max_seconds) = speech_bounds
        was_enabled = self._enabled
        changed = (was_enabled != enabled or self._screen_enabled != screen
                   or self._screen_whitelist != whitelist)
        self._enabled = enabled
        self._screen_enabled = screen
        self._screen_whitelist = whitelist
        if changed:
            self._generation += 1
            self._cancelled.set()
            self._cancelled = threading.Event()
            self._cancel_plan()
            self._pending_visual = None
            self._speech_active = -1
            self._speech_busy = False
        if changed or old_speech_bounds != speech_bounds:
            self._speech_next_at = time.monotonic() + self._next_speech_delay()
        if not enabled:
            self._timer.stop()
            self._life_timer.stop()
            self._speech_timer.stop()
            self._cancel_plan()
        else:
            self._timer.start(interval)
            self._life_timer.start()
            self._speech_timer.start()
            if not was_enabled:
                # Explicitly enabling is consent for the first non-visual call.
                QTimer.singleShot(2500, self._tick)
        self._monitor.publish(
            enabled=enabled, vision=bool(screen and whitelist), interval_seconds=self._interval_seconds,
            speech_status="待机" if enabled else "已关闭",
            speech_next_at=(time.time() + max(0, self._speech_next_at - time.monotonic())
                            if enabled else None),
            life_state=self._life.snapshot(),
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
        self._life_timer.stop()
        self._speech_timer.stop()
        self._speech_active = -1
        self._speech_busy = False
        self._cancel_plan()
        self._pending_user_text = ""
        self._pending_visual = None
        self._monitor.publish(
            enabled=False, busy=False, state="已停止", next_decision_at=None,
            speech_status="已停止", speech_next_at=None)

    def _cancel_plan(self, *, awakened: bool = False) -> None:
        """Cancel queued AI steps without interrupting user-controlled animation."""
        # Settle the elapsed time into the *previous* activity before clearing
        # it. In particular, cancellation cannot erase 20 minutes of sleep.
        self._life.advance(activity=self._plan_activity if self._plan_active else None)
        self._life.end_activity(self._plan_activity)
        if awakened and self._sleep_held:
            self._life.awakened()
        self._life.save(force=True)
        self._plan_revision += 1
        self._replan_timer.stop()
        if self._step_held_animation:
            try:
                self._end_sleep_loop(self._step_held_animation)
            except (RuntimeError, TypeError, ValueError):
                log.warning("自主持续动画：释放失败")
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
        self._plan_deadline_wall = 0.0
        self._plan_available = frozenset()
        self._accepted_repeats = []
        self._plan_repeating = False
        self._sleep_held = False
        self._step_held_animation = ""
        self._life_activity_started = False
        self._monitor.publish(
            active_animation="", sustained=False,
            life_state=self._life.snapshot(),
            activity_name="", activity_started_at=None,
            activity_ends_at=None, activity_total_seconds=0)

    def _update_life(self) -> None:
        """Advance the local life clock without using a single model token."""
        self._life.advance(activity=self._plan_activity if self._plan_active else None)
        self._life.save()
        self._monitor.publish(life_state=self._life.snapshot())

    def _schedule_replan(self, delay_ms: int = _REPLAN_INTERVAL_MS) -> None:
        """Keep a long-lived activity fresh without resetting its deadline."""
        if (not self._enabled or not self._plan_active
                or self._plan_activity in ("nap", "free", "social")
                or self._plan_deadline - time.monotonic() <= 50):
            self._replan_timer.stop()
            return
        # An activity with just one usable clip should simply loop that clip.
        candidates = ACTIVITY_CLIPS.get(self._plan_activity, ("", ()))[1]
        if len(self._plan_available.intersection(candidates)) <= 1:
            self._replan_timer.stop()
            return
        self._replan_timer.start(delay_ms)

    def _replan_tick(self) -> None:
        if (not self._enabled or not self._plan_active
                or self._plan_activity == "nap"
                or self._plan_deadline - time.monotonic() <= 50):
            return
        if self._busy:
            self._schedule_replan(10_000)
            return
        self._tick(replan=True)

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
        self._life.user_chatted()
        self._monitor.publish(life_state=self._life.snapshot())
        self._pending_user_text = message
        # A real conversation takes precedence over the next casual remark.
        self._speech_next_at = time.monotonic() + self._next_speech_delay()
        self._cancel_plan(awakened=True)
        self._monitor.publish(state="收到聊天消息，准备选择对应动作",
                              next_decision_at=None)
        # An in-flight request cannot be interrupted, but its result becomes
        # obsolete; _accept_decision will run this pending command next.
        if not self._busy:
            QTimer.singleShot(0, self._tick)

    def queue_user_click(self) -> None:
        """React to a genuine left click; the click animation runs as before."""
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last_click_request_at < 12:
            return  # Rapid clicking must not spam the model or pop many bubbles.
        self._last_click_request_at = now
        self._speech_next_at = now + self._next_speech_delay()
        self._monitor.publish(speech_status="收到点击，等待 AI 判断",
                              speech_next_at=time.time() + (self._speech_next_at - now))
        # The normal animation/click sounds should already be visible before
        # the optional text is prepared; no animation is changed here.
        QTimer.singleShot(850, lambda: self._try_click_speech(0))

    def allow_user_click_interrupt(self, global_pos=None) -> bool:
        """Protect an ongoing activity from a stray click; double-click wakes.

        Called *before* the legacy or overlay renderer switches to its normal
        click animation. This gate is intentionally independent of the 12 s
        API rate limit: a double click is one user interaction, not two calls.
        """
        if not self._enabled or not self._plan_active:
            self._protected_click_at = float("-inf")
            self._protected_click_pos = None
            return True

        try:
            if isinstance(global_pos, (tuple, list)):
                point = (int(global_pos[0]), int(global_pos[1]))
            else:
                point = (int(global_pos.x()), int(global_pos.y()))
        except (AttributeError, IndexError, TypeError, ValueError):
            point = None
        now = time.monotonic()
        former = self._protected_click_pos
        near = (former is None or point is None or
                (point[0] - former[0]) ** 2 + (point[1] - former[1]) ** 2 <= 36 ** 2)
        if now - self._protected_click_at <= 0.55 and near:
            self._protected_click_at = float("-inf")
            self._protected_click_pos = None
            was_sleeping = self._sleep_held
            self._cancel_plan(awakened=True)
            self._monitor.publish(
                state="用户双击叫醒，等待新活动" if was_sleeping else "用户双击结束当前活动",
                next_decision_at=time.time() + self._interval_seconds)
            return True

        self._protected_click_at = now
        self._protected_click_pos = point
        self._monitor.publish(state=f"正在执行活动：{self._plan_name}（单击不打断，双击可中断）")
        return False

    def _try_click_speech(self, attempt: int) -> None:
        if not self._enabled:
            return
        if self._speech_busy or self._busy:
            if attempt < 24:
                self._monitor.publish(speech_status="AI 正在处理其他请求，点击已排队")
                QTimer.singleShot(1500, lambda: self._try_click_speech(attempt + 1))
            else:
                self._monitor.publish(speech_status="点击等待超时；请再点一次")
            return
        if self._sleep_held:
            try:
                still_asleep = self._sleep_loop_active(SLEEP_ANIMATION)
            except (RuntimeError, TypeError, ValueError):
                still_asleep = False
            if still_asleep:
                return  # Click didn't actually wake the pet.
            self._cancel_plan(awakened=True)
        self._start_speech("click")

    def _speech_tick(self) -> None:
        """Independently consider speaking during a long, otherwise silent plan."""
        if (not self._enabled or not self._plan_active or self._sleep_held
                or self._plan_activity == "nap"
                or self._step_held_animation == SLEEP_ANIMATION
                or time.monotonic() < self._speech_next_at):
            return
        if self._speech_busy or self._busy:
            return  # Keep a single concurrent model request per process.
        if time.monotonic() - self._speech_last_at < 45:
            return
        pending = self._pending_visual
        trigger = ("window" if pending is not None
                   and time.monotonic() - pending[0] <= 30 else "ambient")
        self._start_speech(trigger)

    def _start_speech(self, trigger: str) -> None:
        if not self._enabled or self._busy or self._speech_busy:
            return
        if trigger != "click" and (not self._plan_active or self._sleep_held
                                   or self._plan_activity == "nap"
                                   or self._step_held_animation == SLEEP_ANIMATION):
            return
        visual_frame = None
        visual_context = None
        if trigger == "window":
            pending = self._pending_visual
            if pending is None or time.monotonic() - pending[0] > 30:
                return
            _at, visual_frame, process, activity = pending
            visual_context = {"process": process, "activity": activity,
                              "source": "whitelisted_foreground_window"}
        try:
            if not self._can_speak(trigger):
                self._monitor.publish(speech_status="气泡被占用或设置窗口限制显示，未发起请求")
                return
            observed = self._observe()
            if observed is None:
                self._monitor.publish(speech_status="桌宠不可见或正在拖动，未发起请求")
                return
            world, _animations = observed
            settings = self._config.chat_settings()
            provider = settings.autonomy_config
            provider.api_key = self._config.resolve_api_key(provider)
            if visual_frame is not None and not provider.vision_same_as_chat:
                from .chat.models import SecretStore
                provider.vision_api_key = (
                    SecretStore().get(provider.vision_api_key_ref)
                    or provider.vision_api_key or provider.api_key)
            if not provider.model or not provider.base_url:
                self._monitor.publish(speech_status="未配置可用模型，未发起请求")
                return
            persona = str(self._get_persona() or "")[:1600]
            dialogue = tuple(self._get_dialogue())
        except Exception as exc:
            log.warning("主动搭话：准备失败（%s）", type(exc).__name__)
            self._monitor.publish(
                speech_status=f"搭话准备失败：{type(exc).__name__}",
                last_error=type(exc).__name__)
            return
        world = {
            **world,
            "current_activity": self._plan_name if self._plan_active else "",
            "life_state": self._life.snapshot(),
            "sleep_state": {"night": is_night(), "pressure": sleep_pressure()},
        }
        if visual_context is not None:
            world["foreground_observation"] = visual_context
            self._pending_visual = None
        now = time.monotonic()
        self._speech_next_at = now + self._next_speech_delay()
        self._speech_busy = True
        self._speech_serial += 1
        self._speech_active = request_id = self._speech_serial
        self._monitor.publish(
            model=str(provider.model), last_request_at=time.time(),
            speech_status=("点击：AI 正在决定如何回应" if trigger == "click" else
                           "AI 正在决定是否主动搭话"),
            speech_next_at=time.time() + (self._speech_next_at - now),
            requests=self._monitor.snapshot()["requests"] + 1,
        )
        thread = threading.Thread(
            target=self._speech_worker,
            args=(request_id, self._generation, self._cancelled, provider, world,
                  trigger, persona, dialogue, tuple(self._speech_recent), visual_frame),
            name="pet-autonomy-speech", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._speech_busy = False
            self._monitor.publish(last_error="SpeechThreadError", speech_status="搭话线程启动失败")

    def _speech_worker(self, request_id, generation, cancelled, provider, world,
                       trigger, persona, dialogue, recent_lines, visual_frame=None):
        line = None
        error_kind = ""
        try:
            line = _request_speech(
                provider, world, trigger=trigger, persona=persona,
                dialogue=dialogue, recent_lines=recent_lines,
                cancelled=cancelled, visual_frame=visual_frame)
        except Exception as exc:  # Model/proxy errors stay outside the GUI thread.
            error_kind = type(exc).__name__
            status = getattr(exc, "status", None)
            if isinstance(status, int) and 400 <= status <= 599:
                error_kind += f" (HTTP {status})"
        try:
            self.speech_ready.emit(request_id, (generation, trigger, line), error_kind)
        except RuntimeError:
            pass

    def _accept_speech(self, request_id: int, result, error_kind: str) -> None:
        if request_id != self._speech_active:
            return
        self._speech_busy = False
        generation, trigger, line = result
        if not self._enabled or generation != self._generation:
            return
        completed = self._monitor.snapshot()["completed"] + 1
        if self._pending_user_text:
            self._monitor.publish(completed=completed, speech_status="用户正在聊天，暂不搭话")
            QTimer.singleShot(0, self._tick)
            return
        if error_kind:
            log.warning("主动搭话：请求失败（%s）", error_kind)
            self._monitor.publish(completed=completed, last_error=error_kind,
                                  speech_status="搭话请求失败，不打断动画")
            return
        if not line:
            self._monitor.publish(completed=completed, last_error="",
                                  speech_status="AI 选择先安静陪伴")
            return  # AI deliberately chose silence, with no phantom bubble.
        if any(line == previous for previous in self._speech_recent):
            self._monitor.publish(completed=completed)
            return
        try:
            dialogue = tuple(self._get_dialogue())
            if any(line == str(entry.get("text", "")).strip()
                   for entry in dialogue[-5:] if isinstance(entry, dict)):
                self._monitor.publish(completed=completed)
                return
            if not self._can_speak(trigger):
                self._monitor.publish(completed=completed)
                return
            self._speaking_for_click = trigger == "click"
            shown = bool(self._execute(ActionIntent("say", say=line)))
        except Exception as exc:
            log.warning("主动搭话：展示失败（%s）", type(exc).__name__)
            shown = False
        finally:
            self._speaking_for_click = False
        if shown:
            self._speech_recent.append(line)
            self._speech_last_at = time.monotonic()
        self._monitor.publish(
            record=shown, completed=completed, last_error="",
            speech_status="点击已回应" if shown and trigger == "click" else
                          "自主搭话已显示" if shown else "当前无法显示气泡",
            last_spoken_at=time.time() if shown else self._monitor.snapshot().get("last_spoken_at"),
            last_decision_at=time.time(),
            last_action="say（点击回应）" if trigger == "click" else "say（自主搭话）",
            last_result="已显示" if shown else "气泡未显示",
        )
        # If a sleep click cancelled the plan, resume normal decisions after
        # the brief speech without waiting indefinitely for the periodic tick.
        if not self._plan_active and not self._busy:
            QTimer.singleShot(1500, self._tick)

    def queue_visual_observation(self, jpeg: bytes, *, process: str = "",
                                 activity: str = "") -> bool:
        """Accept one already-authorized window image; never capture again here.

        Invoked on Qt's thread, after the proactive watcher's window whitelist,
        foreground checks and cooldown have passed. Frames expire quickly and
        are never stored on disk or added to textual chat history.
        """
        # The legacy independent watcher must never feed autonomous decisions.
        return False
        self._pending_visual = (time.monotonic(), jpeg,
                                str(process)[:100], str(activity)[:100])
        self._monitor.publish(state="观察到窗口变化，等待统一 AI 决策")
        if not self._busy and not self._plan_active:
            QTimer.singleShot(0, self._tick)
        elif (self._plan_active and not self._sleep_held and not self._speech_busy
              and not self._busy and time.monotonic() >= self._speech_next_at
              and time.monotonic() - self._speech_last_at >= 120):
            # A multi-minute activity no longer swallows authorized window
            # changes: one optional speech decision, no animation replacement.
            QTimer.singleShot(0, lambda: self._start_speech("window"))
        return True

    def _tick(self, *, replan: bool = False):
        if not self._enabled:
            return
        if replan and not self._plan_active:
            return
        if self._plan_active and not replan:
            self._monitor.publish(
                state=("正在持续睡眠（现有动画中段循环，可叫醒）"
                       if self._sleep_held else
                       f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                       else f"正在执行活动：{self._plan_name}"),
                next_decision_at=None)
            return  # Finish the current local sequence before another API call.
        if not replan:
            self._monitor.publish(next_decision_at=time.time() + self._interval_seconds)
        if self._busy or self._speech_busy:
            return
        try:
            observed = self._observe()
        except Exception as exc:  # noqa: BLE001 - GUI observation callback boundary
            log.warning("自主决策：状态采集失败（%s）", type(exc).__name__)
            self._monitor.publish(state="桌宠状态采集失败", last_error=type(exc).__name__)
            if replan:
                self._schedule_replan(15_000)
            return
        if observed is None:
            self._monitor.publish(state="未获取到桌宠状态，下次重试")
            if replan:
                self._schedule_replan(15_000)
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
        self._life.advance(activity=self._plan_activity if self._plan_active else None)
        self._life.save()
        world["life_state"] = self._life.snapshot()
        self._monitor.publish(life_state=world["life_state"])
        if replan:
            world["ongoing_activity"] = {
                "activity": self._plan_activity,
                "remaining_seconds": max(0, int(self._plan_deadline - time.monotonic())),
                "current_animation": self._step_held_animation,
                "recent_animation_names": list(self._recent_clips),
                "goal": "choose next phase only; keep current activity and deadline",
            }
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
            provider = settings.autonomy_config
            provider.api_key = self._config.resolve_api_key(provider)
            if self._screen_enabled and not provider.vision_same_as_chat:
                from .chat.models import SecretStore
                provider.vision_api_key = (
                    SecretStore().get(provider.vision_api_key_ref)
                    or provider.vision_api_key or provider.api_key)
            if not provider.model or not provider.base_url:
                self._monitor.publish(state="模型尚未配置", last_error="缺少模型或 API 地址")
                if replan:
                    self._schedule_replan(15_000)
                return
        except (AttributeError, ImportError, KeyError, ValueError):
            log.warning("自主决策：模型配置不可用")
            self._monitor.publish(state="模型配置不可用", last_error="模型配置不可用")
            if replan:
                self._schedule_replan(15_000)
            return
        try:
            dialogue = tuple(self._get_dialogue())
        except Exception:  # Bad/unavailable history must not stop pet behavior.
            log.warning("自主决策：近期对话读取失败")
            dialogue = ()
        try:
            persona = str(self._get_persona() or "")[:1600]
        except Exception:
            persona = ""
        # The independent watcher is retired. This path is the *only*
        # autonomous image producer, and empty whitelist means text-only.
        take_own_screen = self._screen_enabled and bool(self._screen_whitelist)
        self._busy = True
        self._current_request_user = bool(user_message)
        self._current_request_replan = replan
        self._replan_request_revision = self._plan_revision if replan else -1
        if user_message:
            self._pending_user_text = ""
        self._request_serial += 1
        request_id = self._request_serial
        self._active_request = request_id
        self._request_animations = tuple(animations)
        generation = self._generation
        cancel = self._cancelled
        self._monitor.publish(
            busy=True, state=("正在理解聊天指令" if user_message else
                              f"AI 正在更新{self._plan_name}的下一阶段" if replan else
                              "正在等待模型决策"),
            model=str(provider.model),
            vision=bool(visual_frame is not None or take_own_screen), last_request_at=time.time(),
            requests=self._monitor.snapshot()["requests"] + 1, last_error="",
        )
        thread = threading.Thread(
            target=self._run_worker,
            args=(request_id, generation, cancel, provider, world,
                  tuple(animations), tuple(self._history),
                  tuple(self._recent_activities), take_own_screen,
                  dialogue, visual_frame, persona, self._screen_whitelist),
            name="pet-autonomy-llm", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._busy = False
            self._monitor.publish(
                busy=False, state="请求线程启动失败", last_error="RuntimeError")
            if replan:
                self._schedule_replan(15_000)

    def _run_worker(self, request_id, generation, cancel, provider, world,
                    animations, recent, recent_activities, vision,
                    dialogue=(), visual_frame=None, persona="", whitelist=()):
        intent = None
        error_kind = ""
        try:
            intent = _request_intent(provider, world, animations, recent,
                                     recent_activities, vision, cancel,
                                     recent_dialogue=dialogue,
                                     visual_frame=None, persona=persona,
                                     whitelist=whitelist)
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
        was_replan = self._current_request_replan
        replan_revision = self._replan_request_revision
        self._current_request_replan = False
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
        if was_replan and (not self._plan_active
                           or replan_revision != self._plan_revision):
            self._monitor.publish(busy=False, state="旧活动已结束，忽略过期规划")
            return
        finished_at = time.time()
        completed = self._monitor.snapshot()["completed"] + 1
        if error_kind:
            log.warning("自主决策：请求未完成（%s），保留当前动画", error_kind)
            self._monitor.publish(
                record=True, busy=False,
                state=(f"阶段更新失败，继续{self._plan_name}" if was_replan else "AI 请求失败"),
                last_action="—", last_result="请求失败", last_error=error_kind,
                last_decision_at=finished_at, completed=completed)
            if was_replan:
                self._schedule_replan()
            return
        if intent is None:
            self._monitor.publish(
                record=True, busy=False, state="本轮无可执行动作",
                last_action="—", last_result="无动作或无法解析",
                last_error="", last_decision_at=finished_at,
                completed=completed)
            if was_replan:
                self._schedule_replan()
            return
        if isinstance(intent, ActionIntent):  # Backward compatible worker result.
            intent = ActivityPlan("free", (intent,))
        if not isinstance(intent, ActivityPlan) or not intent.steps:
            self._monitor.publish(
                record=True, busy=False, state="模型决策格式错误",
                last_action="—", last_result="无法识别活动计划",
                last_error="InvalidActivityPlan", last_decision_at=finished_at,
                completed=completed)
            if was_replan:
                self._schedule_replan()
            return
        steps = list(activity_stage_steps(
            intent, available_animations=self._request_animations))
        if was_replan:
            # A phase refresh cannot change the long-term activity or its end
            # time. Keep the old plan if the model supplied an invalid stage.
            if (intent.activity != self._plan_activity or not steps
                    or not any(s.verb == "play" for s in steps)):
                self._monitor.publish(
                    record=True, busy=False, state=f"AI 阶段无效，继续{self._plan_name}",
                    last_action="—", last_result="无效阶段，旧动作继续",
                    last_error="InvalidActivityStage",
                    last_decision_at=finished_at, completed=completed)
                self._schedule_replan()
                return
            # The held animation is left in place; the next normal tick
            # hands over to the refreshed steps after the current clip.
            self._plan_steps = deque(steps)
            self._plan_total = len(steps)
            self._plan_done = 0
            self._plan_repeating = False
            self._accepted_repeats = []
            self._monitor.publish(
                record=True, busy=False,
                state=f"AI 已更新{self._plan_name}阶段（当前动画播完后切换）",
                last_action=f"plan：{self._plan_name}", last_result="阶段已更新",
                last_error="", last_decision_at=finished_at, completed=completed)
            self._schedule_replan()
            return
        self._cancel_plan()
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
        if intent.activity == "nap":
            hour = time.localtime().tm_hour
            self._plan_name = ("夜间睡眠" if is_night() else
                               "晨间补觉" if hour < 11 else
                               "午睡" if hour < 14 else "下午小憩" if hour < 18
                               else "晚间休息")
        self._plan_activity = intent.activity
        self._life_activity_started = False
        self._plan_available = frozenset(self._request_animations)
        self._plan_deadline = time.monotonic() + duration if duration else 0.0
        self._plan_deadline_wall = time.time() + duration if duration else 0.0
        self._plan_from_chat = self._current_request_user
        self._plan_active = True
        self._recent_activities.append(intent.activity)
        started_at = time.time()
        self._monitor.publish(
            busy=False, state=(f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                               else f"AI 已规划：{self._plan_name}"),
            activity_name=self._plan_name if duration else "",
            activity_started_at=started_at if duration else None,
            activity_ends_at=started_at + duration if duration else None,
            activity_total_seconds=duration,
            last_error="", last_decision_at=finished_at, completed=completed,
            next_decision_at=None)
        self._advance_plan()
        self._schedule_replan()

    def _activity_watch(self) -> None:
        """Watch a held sleep without waking the model or replacing user's click."""
        if not self._enabled or not self._plan_active or not self._sleep_held:
            self._activity_timer.stop()
            return
        if (time.monotonic() >= self._plan_deadline
                or (self._plan_deadline_wall and time.time() >= self._plan_deadline_wall)):
            self._cancel_plan()
            self._monitor.publish(state="休息结束，醒来继续活动",
                                  next_decision_at=time.time() + self._interval_seconds)
            return
        try:
            active = bool(self._sleep_loop_active(SLEEP_ANIMATION))
        except (RuntimeError, TypeError, ValueError):
            active = False
        if not active:
            # Both renderer backends consider an actively dragged sprite not
            # ready, even when its held sleep clip is still visually playing.
            # Dragging is a position edit, not a wake gesture.
            try:
                if self._sleep_dragging():
                    return
            except (RuntimeError, TypeError, ValueError):
                pass
            self._cancel_plan(awakened=True)
            self._monitor.publish(state="主人叫醒了小鲸鱼，准备回应",
                                  next_decision_at=None)
            QTimer.singleShot(1000, self._tick)

    def _advance_plan(self) -> None:
        """Execute one step on Qt's thread; an LLM refresh may replace future steps."""
        if not self._plan_active or not self._enabled:
            return
        if self._sleep_held:
            return  # 由 500ms 轻量时钟保持续播，绝不重复发 AI 请求。
        if not self._plan_steps and self._plan_deadline > time.monotonic():
            if self._accepted_repeats:
                self._plan_steps.extend(self._accepted_repeats)
                self._plan_total = len(self._accepted_repeats)
                self._plan_done = 0
                self._plan_repeating = True
            else:
                self._plan_deadline = 0.0  # One-off clip: don't stretch forever.
        keep_held = False
        if self._step_held_animation:
            previous = self._step_held_animation
            try:
                still_playing = self._sleep_loop_active(previous)
            except (RuntimeError, TypeError, ValueError):
                still_playing = False
            if not still_playing:
                self._cancel_plan(awakened=True)
                self._monitor.publish(state="用户打断了持续动画")
                return
            upcoming = self._plan_steps[0] if self._plan_steps else None
            keep_held = bool(upcoming and upcoming.verb == "play"
                             and upcoming.animation == previous
                             and self._plan_deadline > time.monotonic())
            if not keep_held:
                self._step_held_animation = ""
                self._end_sleep_loop(previous)
        if not self._plan_steps:
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
        sustained_step = (not sleep_step and intent.verb == "play"
                          and bool(intent.animation)
                          and self._plan_deadline > time.monotonic())
        try:
            if keep_held:
                # Same clip in next phase: leave its frame loop running without
                # a stop/restart seam at every 11.5-second planner tick.
                executed = True
                if intent.say:
                    self._execute(ActionIntent("say", say=intent.say,
                                               emotion=intent.emotion))
            elif sleep_step or sustained_step:
                executed = bool(self._begin_sleep_loop(intent.animation))
                if not executed and sustained_step:
                    executed = bool(self._execute(intent))
            else:
                executed = bool(self._execute(intent))
            if executed and sleep_step:
                self._sleep_held = True
                self._activity_timer.start()
                if intent.say:
                    # Sleep clip owns the picture; a separate short bubble may
                    # accompany it without replacing the animation.
                    self._execute(ActionIntent("say", say=intent.say,
                                               emotion=intent.emotion))
            elif executed and sustained_step:
                # Hold this single step, not the entire activity: the timer
                # releases it before executing the next AI-selected action.
                try:
                    held = bool(self._sleep_loop_active(intent.animation))
                except (RuntimeError, TypeError, ValueError):
                    held = False
                if held:
                    self._step_held_animation = intent.animation
                    if intent.say:
                        self._execute(ActionIntent("say", say=intent.say,
                                                   emotion=intent.emotion))
            if (executed and intent.verb == "play" and not sleep_step
                    and intent.animation not in ONE_SHOT_CLIPS):
                # Repeat the physical activity, never the sentence attached
                # to its first performance. Otherwise an AI breakfast plan
                # says exactly the same line every 11.5 seconds for minutes.
                replay = silent_play_replay(intent)
                if replay not in self._accepted_repeats:
                    self._accepted_repeats.append(replay)
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
        if executed and intent.verb == "play" and not self._plan_repeating:
            self._recent_clips.append(intent.animation)
            if not self._life_activity_started and (self._plan_activity != "nap" or sleep_step):
                self._life_activity_started = True
                self._life.start_activity(
                    self._plan_activity,
                    sleep_until_at=(time.time() + max(0.0, self._plan_deadline - time.monotonic())
                                    if sleep_step else None),
                )
                self._monitor.publish(life_state=self._life.snapshot())
        elif executed and intent.verb == "say" and self._plan_activity == "social" \
                and not self._life_activity_started:
            self._life_activity_started = True
            self._life.start_activity("social")
            self._monitor.publish(life_state=self._life.snapshot())
        if executed and intent.say and not self._plan_repeating:
            self._speech_last_at = time.monotonic()
        if executed and not self._plan_repeating:
            self._history.append(action)
        self._monitor.publish(
            record=not self._plan_repeating, busy=self._busy,
            state=(f"持续活动：{self._plan_name}（本地循环，无重复台词）"
                   if self._plan_repeating else
                   (f"聊天触发活动：{self._plan_name}" if self._plan_from_chat
                    else f"正在执行活动：{self._plan_name}")
                   + f"（{self._plan_done}/{self._plan_total}）"),
            last_action=action, last_result=result, last_error="",
            active_animation=(intent.animation if executed and intent.verb == "play" else ""),
            sustained=bool(executed and (sleep_step or sustained_step)),
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
