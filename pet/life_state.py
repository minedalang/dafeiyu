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
from datetime import datetime
from pathlib import Path


log = logging.getLogger(__name__)
_FILENAME = "whale-life-state.json"
_VERSION = 1
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
        self.updated_at = float(time.time() if now is None else now)
        self._last_saved_at = 0.0
        self._load()
        # Time since closing the program is passive rest, not a phantom work
        # activity. A night crossing permits recovering some lost sleep.
        catchup = float(time.time() if now is None else now)
        if catchup > self.updated_at:
            then, current = datetime.fromtimestamp(self.updated_at), datetime.fromtimestamp(catchup)
            overnight = then.date() < current.date() and then.hour >= 20 and current.hour <= 10
            self.advance(now=catchup, activity="nap" if overnight else None,
                         limit_minutes=240 if overnight else 120)

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
            raw_events = data.get("events", ())
            if isinstance(raw_events, list):
                self.events = [item for item in raw_events[-12:]
                               if isinstance(item, dict)
                               and isinstance(item.get("at"), (int, float))
                               and 0 < item["at"] <= self.updated_at + 60
                               and isinstance(item.get("description"), str)
                               and len(item["description"]) <= 64]
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
                   "meals": self.meals}
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
        minutes = min(max(0.0, (stamp - self.updated_at) / 60.0), limit_minutes)
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

    def _record(self, description: str, *, now: float | None = None) -> None:
        stamp = float(time.time() if now is None else now)
        self.events.append({"at": stamp, "description": description})
        self.events = self.events[-12:]

    def start_activity(self, activity: str, *, now: float | None = None) -> None:
        """Called once after an action is accepted, never on local loop replay."""
        stamp = float(time.time() if now is None else now)
        self.advance(now=stamp)
        if activity not in _ACTIVITY_LABELS:
            return
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
        if needs["sleepiness"] >= 70 or needs["energy"] <= 28:
            goal = "优先休息或睡觉；夜间被叫醒可能有点抱怨"
            suggested = ["nap", "rest"]
        elif needs["hunger"] >= 70:
            meal = "breakfast" if local.hour < 11 else "lunch" if local.hour < 16 else "dinner"
            if meal in meals_today:
                goal = "现在有点饿，但这一餐今天已吃过，先做其他舒缓活动"
                suggested = ["rest", "play"]
            else:
                goal = "有点饿，考虑用餐"
                suggested = [meal, "rest"]
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
        return {**needs, "goal": goal, "suggested_activities": suggested,
                "recent_events": self.events[-6:],
                "meals_today": meals_today,
                "updated_at": self.updated_at}
