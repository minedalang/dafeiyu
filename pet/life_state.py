"""Persistent, offline-capable needs for the autonomous whale.

All values and history are local, bounded, and independent of the LLM/Qt.
The model receives a *snapshot* and suggests actions; only a successfully
executed activity changes needs. Nothing here triggers keyboard/mouse actions.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path


log = logging.getLogger(__name__)
_FILENAME = "whale-life-state.json"
_VERSION = 1
# Offline simulation is limited to the most recent day; a week without the pet
# running must not credit a week of sleep or create an extreme hunger debt.
_MAX_OFFLINE_HOURS = 24
# 只保存最近 24 次关机补算，避免配置文件无限增长；不保存聊天或截图。
_MAX_OFFLINE_RECORDS = 24
_NIGHT_START = clock_time(22, 0)
_NIGHT_END = clock_time(7, 0)
_DEFAULTS = {"energy": 78.0, "hunger": 27.0,
             "sleepiness": 22.0, "mood": 72.0}
_MEALS = frozenset({"breakfast", "lunch", "dinner"})
_ACTIVITY_LABELS = {
    "breakfast": "吃早餐", "lunch": "吃午饭", "dinner": "吃晚饭",
    "nap": "睡觉休息", "work": "工作与学习", "games": "玩游戏",
    "music": "音乐与表演", "rest": "放松休息", "play": "开心玩耍",
    "sports": "活动筋骨", "imitation": "模仿变装", "social": "和主人说话",
}


def _clamp(value: object, fallback: float) -> float:
    """Reject malformed/non-finite saved values without poisoning the state."""
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return fallback
        return round(max(0.0, min(100.0, number)), 3)
    except (TypeError, ValueError, OverflowError):
        return fallback


class WhaleLife:
    """One whale's needs, short activity memory and meal accounting.

    Hunger / sleepiness rise when their needs are unmet; energy / mood increase
    when positive activities are actually performed. A chat event is counted
    once when queued, not on every animation replay or AI retry.
    """

    def __init__(self, directory: str | Path | None = None, *, now: float | None = None):
        self.path = Path(directory) / _FILENAME if directory else None
        self.values = dict(_DEFAULTS)
        self.events: list[dict] = []
        self.meals: dict[str, str] = {}
        self.offline_settlements: list[dict] = []
        # The end of an accepted nap, persisted to recover a nap interrupted by
        # shutdown. Night sleep is inferred independently from the local clock.
        self.sleep_until_at: float | None = None
        self.updated_at = float(time.time() if now is None else now)
        self._last_saved_at = 0.0
        self._load()
        # Restart after shutdown: simulate the real local night, even if the
        # app was closed before bedtime. Daytime only counts as sleep when an
        # *accepted* nap was in progress before the app exited unexpectedly.
        catchup = float(time.time() if now is None else now)
        if catchup > self.updated_at:
            self.reconcile_offline(now=catchup)
        elif self.sleep_until_at is not None:
            # A new process never inherits the *active* animation state.
            self.sleep_until_at = None
            self.save(force=True)

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            if not self.path.is_file() or self.path.stat().st_size > 32 * 1024:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != _VERSION:
                return
            raw = data.get("needs", {})
            if isinstance(raw, dict):
                for key, default in _DEFAULTS.items():
                    self.values[key] = _clamp(raw.get(key), default)
            timestamp = data.get("updated_at")
            if isinstance(timestamp, (int, float)) and 0 < timestamp <= self.updated_at + 60:
                self.updated_at = float(timestamp)
            pending_sleep = data.get("sleep_until_at")
            if (isinstance(pending_sleep, (int, float))
                    and not isinstance(pending_sleep, bool)
                    and self.updated_at < pending_sleep <= self.updated_at + 60 * 60 * 10):
                self.sleep_until_at = float(pending_sleep)
            raw_events = data.get("events", ())
            if isinstance(raw_events, list):
                self.events = [item for item in raw_events[-12:]
                               if isinstance(item, dict)
                               and isinstance(item.get("at"), (int, float))
                               and 0 < item["at"] <= self.updated_at + 60
                               and isinstance(item.get("description"), str)
                               and len(item["description"]) <= 64]
            raw_audit = data.get("offline_settlements", [])
            if isinstance(raw_audit, list):
                self.offline_settlements = [entry for entry in raw_audit[-_MAX_OFFLINE_RECORDS:]
                                            if isinstance(entry, dict)
                                            and isinstance(entry.get("started_at"), (float, int))
                                            and isinstance(entry.get("finished_at"), (float, int))
                                            and isinstance(entry.get("sleep_minutes"), (float, int))
                                            and isinstance(entry.get("before"), dict)
                                            and isinstance(entry.get("after"), dict)]
            raw_meals = data.get("meals", {})
            if isinstance(raw_meals, dict):
                self.meals = {key: str(value)[:10] for key, value in raw_meals.items()
                              if key in _MEALS and isinstance(value, str)}
        except (OSError, ValueError, UnicodeError, TypeError):
            log.warning("鲸鱼生命状态读取失败，使用安全默认值")

    def save(self, *, force: bool = False) -> None:
        if self.path is None:
            return
        if not force and self.updated_at - self._last_saved_at < 60:
            return
        target = self.path
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        payload = {"version": _VERSION, "updated_at": self.updated_at,
                   "needs": self.values, "events": self.events[-12:],
                   "meals": self.meals,
                   "sleep_until_at": self.sleep_until_at,
                   "offline_settlements": self.offline_settlements[-_MAX_OFFLINE_RECORDS:]}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, target)
            self._last_saved_at = self.updated_at
        except OSError:
            log.warning("无法保存鲸鱼生命状态，稍后重试")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def advance(self, *, now: float | None = None, activity: str | None = None,
                limit_minutes: float = 60) -> None:
        """Advance bounded elapsed time; never mistake replays for new meals."""
        stamp = float(time.time() if now is None else now)
        if stamp < self.updated_at:
            self.updated_at = stamp  # Wall clock was corrected backwards.
            return
        elapsed_minutes = max(0.0, (stamp - self.updated_at) / 60.0)
        # A suspended GUI may miss hours of Qt timers without ever exiting.
        # Route these gaps through the same calendar-aware settlement as restarts
        # instead of crediting 60 minutes and discarding everything else.
        if elapsed_minutes > limit_minutes and limit_minutes == 60:
            self.reconcile_offline(now=stamp)
            return
        minutes = min(elapsed_minutes, limit_minutes)
        self.updated_at = stamp
        if not minutes:
            return
        hour = datetime.fromtimestamp(stamp).hour
        night = hour >= 22 or hour < 7
        delta = {"energy": -0.055, "hunger": 0.075,
                 "sleepiness": 0.14 if night else 0.048, "mood": 0.0}
        if activity == "nap":
            delta.update(energy=0.53, hunger=0.045,
                         sleepiness=-0.9, mood=0.055)
        elif activity == "work":
            delta.update(energy=-0.16, hunger=0.09,
                         sleepiness=0.15 if night else 0.09, mood=-0.016)
        elif activity in ("games", "music", "play", "imitation"):
            delta.update(energy=-0.065, hunger=0.085, mood=0.12)
        elif activity == "sports":
            delta.update(energy=-0.24, hunger=0.12,
                         sleepiness=0.095, mood=0.16)
        elif activity == "rest":
            delta.update(energy=0.13, sleepiness=-0.075, mood=0.085)
        elif activity in _MEALS:
            delta.update(energy=0.075, mood=0.06)
        for key, rate in delta.items():
            self.values[key] = _clamp(self.values[key] + rate * minutes, _DEFAULTS[key])
        # Mood gently returns to equilibrium instead of becoming permanently
        # miserable from normal everyday work.
        self.values["mood"] = _clamp(
            self.values["mood"] + (68 - self.values["mood"]) * min(0.05, minutes / 600),
            _DEFAULTS["mood"])

    def reconcile_offline(self, *, now: float | None = None) -> float:
        """Settle a shutdown/suspend gap using *local* sleep hours.

        Only 22:00..07:00 is automatic night sleep. An explicitly accepted
        daytime nap continues only until its saved end time. The last 24h
        and one night are the limit; older gaps cannot farm arbitrary gains.
        The last-settled timestamp is saved immediately for idempotent restart.
        Returns the number of offline minutes credited as sleep.
        """
        end = float(time.time() if now is None else now)
        start = self.updated_at
        if end <= start:
            return 0.0
        gap_started_at = start
        before = {key: round(float(value), 3) for key, value in self.values.items()}
        # Don't simulate several missed days, even when the program was closed
        # for a week. The final day represents the whale's current rhythm.
        start = max(start, end - _MAX_OFFLINE_HOURS * 3600)
        self.updated_at = start
        # In local time, the nightly interval spans midnight. Constructing each
        # boundary as a local timestamp also handles UTC offset changes.
        first_day = datetime.fromtimestamp(start).date() - timedelta(days=1)
        last_day = datetime.fromtimestamp(end).date()
        sleep_intervals = []
        day = first_day
        while day <= last_day:
            bedtime = datetime.combine(day, _NIGHT_START).timestamp()
            wake_time = datetime.combine(day + timedelta(days=1), _NIGHT_END).timestamp()
            if wake_time > start and bedtime < end:
                sleep_intervals.append((max(start, bedtime), min(end, wake_time)))
            day += timedelta(days=1)
        night_intervals = tuple(sleep_intervals)
        if self.sleep_until_at is not None and self.sleep_until_at > start:
            sleep_intervals.append((start, min(end, self.sleep_until_at)))
        points = {start, end}
        for left, right in sleep_intervals:
            if left < right:
                points.add(left)
                points.add(right)
        sleep_minutes = 0.0
        night_minutes = 0.0
        daytime_nap_minutes = 0.0
        boundaries = sorted(points)
        for left, right in zip(boundaries, boundaries[1:]):
            if right <= left:
                continue
            sleeping = any(a <= left and right <= b for a, b in sleep_intervals)
            self.advance(now=right, activity="nap" if sleeping else None,
                         limit_minutes=_MAX_OFFLINE_HOURS * 60)
            if sleeping:
                part = (right - left) / 60
                sleep_minutes += part
                # Night hours take precedence if an accepted nap overlaps them.
                in_night = any(a <= left and right <= b for a, b in night_intervals)
                if in_night:
                    night_minutes += part
                else:
                    daytime_nap_minutes += part
        # Live suspend catch-up may be called while the animation plan is still
        # running. The planner will decide to end it when the wall deadline
        # passes, so keep the intent until then; startup explicitly clears it.
        self.sleep_until_at = None
        if sleep_minutes >= 1:
            hours, minutes = divmod(int(round(sleep_minutes)), 60)
            summary = f"离线睡眠 {hours} 小时 {minutes} 分钟，已恢复精力和困意"
            self._record(summary, now=end)
        self.offline_settlements.append({
            "started_at": gap_started_at,
            "settled_from_at": start,
            "finished_at": end,
            "sleep_minutes": round(sleep_minutes, 2),
            "night_sleep_minutes": round(night_minutes, 2),
            "daytime_nap_minutes": round(daytime_nap_minutes, 2),
            "discarded_minutes": round((start - gap_started_at) / 60, 2),
            "before": before,
            "after": {key: round(float(value), 3) for key, value in self.values.items()},
        })
        self.offline_settlements = self.offline_settlements[-_MAX_OFFLINE_RECORDS:]
        self.save(force=True)
        return sleep_minutes

    def end_activity(self, activity: str) -> None:
        """Clear nap intent only after the elapsed sleep was settled."""
        if activity == "nap":
            self.sleep_until_at = None

    def _record(self, description: str, *, now: float | None = None) -> None:
        stamp = float(time.time() if now is None else now)
        self.events.append({"at": stamp, "description": description})
        self.events = self.events[-12:]

    def start_activity(self, activity: str, *, now: float | None = None,
                       sleep_until_at: float | None = None) -> None:
        """Called once after an action is accepted, never on local loop replay."""
        stamp = float(time.time() if now is None else now)
        self.advance(now=stamp)
        if activity not in _ACTIVITY_LABELS:
            return
        if activity == "nap" and sleep_until_at is not None:
            # Online night sleep can last until morning (up to nine hours).
            # The saved deadline is also consulted after Windows suspend.
            self.sleep_until_at = min(stamp + 10 * 3600,
                                      max(stamp, float(sleep_until_at)))
        if activity in _MEALS:
            day = datetime.fromtimestamp(stamp).date().isoformat()
            # One credited meal per local date even if the AI reselects it.
            if self.meals.get(activity) != day:
                self.meals[activity] = day
                self.values["hunger"] = _clamp(self.values["hunger"] - 38, 0)
                self.values["energy"] = _clamp(self.values["energy"] + 7, 100)
                self.values["mood"] = _clamp(self.values["mood"] + 5, 100)
            else:
                return
        # Avoid a timeline flooded with identical work phases.
        if self.events and self.events[-1]["description"] == _ACTIVITY_LABELS[activity] \
                and stamp - self.events[-1]["at"] < 600:
            # Timeline deduplication must not suppress a new sleep deadline.
            self.save(force=True)
            return
        self._record(_ACTIVITY_LABELS[activity], now=stamp)
        self.save(force=True)

    def user_chatted(self, *, now: float | None = None) -> None:
        stamp = float(time.time() if now is None else now)
        self.advance(now=stamp)
        self.values["mood"] = _clamp(self.values["mood"] + 1.5, 100)
        self._record("主人来聊天了", now=stamp)
        self.save(force=True)

    def awakened(self, *, now: float | None = None) -> None:
        stamp = float(time.time() if now is None else now)
        self.advance(now=stamp)
        hour = datetime.fromtimestamp(stamp).hour
        night = hour >= 22 or hour < 7
        self.values["sleepiness"] = _clamp(self.values["sleepiness"] + (9 if night else 4), 100)
        self.values["mood"] = _clamp(self.values["mood"] - (9 if night else 2), 60)
        self._record("深夜被叫醒，有点不高兴" if night else "被主人叫醒", now=stamp)
        self.save(force=True)

    def snapshot(self, *, now: float | None = None) -> dict:
        """Small, privacy-safe world state for the model and settings panel."""
        stamp = float(time.time() if now is None else now)
        local = datetime.fromtimestamp(stamp)
        needs = {key: round(value, 1) for key, value in self.values.items()}
        meals_today = [meal for meal in ("breakfast", "lunch", "dinner")
                       if self.meals.get(meal) == local.date().isoformat()]
        # 精力与困意属于不同需求：低精力是身体疲劳，不自动等于需要睡觉。
        # 夜间节律仍由后面的时段规则与模型判断处理。
        if needs["sleepiness"] >= 70:
            if needs["energy"] <= 28:
                goal = "又累又困：先真正睡觉，补充睡眠与体力"
            else:
                goal = "睡眠需求很高：优先真正睡觉；即使精力不低也会困"
            suggested = ["nap", "rest"]
        elif needs["hunger"] >= 70:
            meal = "breakfast" if local.hour < 11 else "lunch" if local.hour < 16 else "dinner"
            if meal in meals_today:
                goal = "现在有点饿，但这一餐今天已吃过，先做其他舒缓活动"
                suggested = ["rest", "play"]
            else:
                goal = "有点饿，考虑用餐"
                suggested = [meal, "rest"]
        elif needs["energy"] <= 28:
            goal = "体力疲劳但不一定困：优先放松休息、减少消耗；不应仅因精力低就睡觉"
            suggested = ["rest", "music"]
        elif needs["mood"] <= 37:
            goal = "心情低落，考虑休息或轻松娱乐"
            suggested = ["rest", "games", "music"]
        elif local.hour >= 22 or local.hour < 7:
            goal = "已是夜间，合理安排安静活动与睡眠"
            suggested = ["nap", "rest"]
        elif 11 <= local.hour < 14:
            goal = "午饭已吃，可以午休" if "lunch" in meals_today else "午间生活：考虑午饭或午休"
            suggested = (["nap", "rest"] if "lunch" in meals_today
                         else ["lunch", "nap", "rest"])
        elif 17 <= local.hour < 20:
            goal = "晚饭已吃，可以娱乐" if "dinner" in meals_today else "傍晚生活：安排晚饭或娱乐"
            suggested = (["games", "rest"] if "dinner" in meals_today
                         else ["dinner", "games", "rest"])
        else:
            goal = "精力正常，可以工作、探索或娱乐"
            suggested = ["work", "play", "games"]
        fatigue = "exhausted" if needs["energy"] <= 28 else ("tired" if needs["energy"] <= 45 else "ok")
        sleep_drive = "sleepy" if needs["sleepiness"] >= 70 else ("drowsy" if needs["sleepiness"] >= 45 else "alert")
        return {**needs, "fatigue_level": fatigue, "sleep_drive_level": sleep_drive,
                "goal": goal, "suggested_activities": suggested,
                "recent_events": self.events[-6:],
                "meals_today": meals_today,
                "updated_at": self.updated_at}
