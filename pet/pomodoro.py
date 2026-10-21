"""Manual, deterministic Pomodoro clock (no Qt or AI/API dependencies).

A session is a user-owned timer; it never wakes a sleeping pet or cancels an
AI-held activity. The GUI calls tick() once a second using *wall* timestamps so
brief GUI stalls do not lengthen a phase. After sleep/suspend, at most one phase
is advanced: missed sessions are never fabricated.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass
class PomodoroSettings:
    focus_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_every: int = 4

    def normalized(self):
        def bounded(value, low, high, default):
            try:
                if isinstance(value, bool):
                    return default
                return max(low, min(high, int(value)))
            except (ValueError, TypeError, OverflowError):
                return default
        return PomodoroSettings(
            bounded(self.focus_minutes, 1, 180, 25),
            bounded(self.short_break_minutes, 1, 60, 5),
            bounded(self.long_break_minutes, 1, 120, 15),
            bounded(self.long_break_every, 2, 12, 4),
        )


class PomodoroClock:
    def __init__(self, settings: PomodoroSettings | None = None):
        self.settings = (settings or PomodoroSettings()).normalized()
        self.phase = "focus"
        self.completed_focus = 0
        self.ever_started = False
        self.running = False
        self.deadline = None
        self.remaining_seconds = self.settings.focus_minutes * 60

    def _phase_seconds(self):
        mins = {
            'focus': self.settings.focus_minutes,
            'short_break': self.settings.short_break_minutes,
            'long_break': self.settings.long_break_minutes,
        }[self.phase]
        return mins * 60

    def start_pause(self, now=None):
        stamp = time.time() if now is None else float(now)
        if self.running:
            self.remaining_seconds = self.remaining(now=stamp)
            self.running = False
            self.deadline = None
        else:
            self.ever_started = True
            self.running = True
            self.deadline = stamp + self.remaining_seconds

    def remaining(self, now=None):
        if self.running and self.deadline is not None:
            stamp = time.time() if now is None else float(now)
            return max(0, int(math.ceil(self.deadline - stamp)))
        return max(0, int(self.remaining_seconds))

    def next_phase(self, *, now=None):
        stamp = time.time() if now is None else float(now)
        if self.phase == 'focus':
            self.completed_focus += 1
            self.phase = ('long_break' if self.completed_focus % self.settings.long_break_every == 0
                          else 'short_break')
        else:
            self.phase = 'focus'
        self.remaining_seconds = self._phase_seconds()
        if self.running:
            self.deadline = stamp + self.remaining_seconds
        else:
            self.deadline = None
        return self.phase

    def tick(self, now=None):
        if not self.running or self.remaining(now=now) > 0:
            return None
        # Advance once only. Never credit arbitrary focus sessions during
        # a Windows standby/hibernate interval.
        return self.next_phase(now=now)

    def skip(self, now=None):
        return self.next_phase(now=now)

    def reset(self):
        self.phase = 'focus'
        self.completed_focus = 0
        self.ever_started = False
        self.running = False
        self.deadline = None
        self.remaining_seconds = self.settings.focus_minutes * 60

    def set_settings(self, settings: PomodoroSettings):
        self.settings = settings.normalized()
        if not self.ever_started:
            self.remaining_seconds = self._phase_seconds()

    def snapshot(self, now=None):
        return {
            'phase': self.phase,
            'running': self.running,
            'remaining_seconds': self.remaining(now=now),
            'completed_focus': self.completed_focus,
            'quiet_focus': self.running and self.phase == 'focus',
        }
