"""In-process, GUI-thread-only status feed for autonomous pet decisions.

No prompts, screenshots, API keys or raw model responses are retained here.
The monitor is observability only: it never initiates a model request or changes
the decisions made by the pet.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, Signal


class AutonomyMonitor(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self._history = deque(maxlen=5)
        self._state = {
            "enabled": False,
            "state": "尚未启动",
            "model": "—",
            "vision": False,
            "interval_seconds": 40,
            "busy": False,
            "last_action": "—",
            "last_result": "尚无决策",
            "last_error": "",
            "last_request_at": None,
            "last_decision_at": None,
            "next_decision_at": None,
            "requests": 0,
            "completed": 0,
        }

    def snapshot(self) -> dict:
        return {**self._state, "history": tuple(self._history)}

    def publish(self, *, record: bool = False, **changes):
        """Called from the autonomy service's Qt GUI thread only."""
        self._state.update(changes)
        if record:
            self._history.appendleft({
                "at": changes.get("last_decision_at"),
                "action": self._state["last_action"],
                "result": self._state["last_result"],
            })
        self.changed.emit(self.snapshot())


_monitor: AutonomyMonitor | None = None


def get_autonomy_monitor() -> AutonomyMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AutonomyMonitor()
    return _monitor
