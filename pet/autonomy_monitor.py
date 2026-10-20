"""Status feed for autonomous decisions, also visible to standalone settings.

No prompts, screenshots, API keys or raw model responses are retained here.
The monitor is observability only: it never initiates a model request or changes
the decisions made by the pet.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)
_STATUS_FILENAME = "autonomy-decision-status.json"


class AutonomyMonitor(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self._shared_path: Path | None = None
        self._shared_writer = False
        self._history = deque(maxlen=5)
        self._state = {
            "enabled": False,
            "state": "尚未启动",
            "model": "—",
            "vision": False,
            "interval_seconds": 40,
            "busy": False,
            "last_action": "—",
            "active_animation": "",
            "sustained": False,
            "activity_name": "",
            "activity_started_at": None,
            "activity_ends_at": None,
            "activity_total_seconds": 0,
            "life_state": {},
            "speech_status": "待机",
            "speech_next_at": None,
            "last_spoken_at": None,
            "last_result": "尚无决策",
            "last_error": "",
            "last_request_at": None,
            "last_decision_at": None,
            "next_decision_at": None,
            "requests": 0,
            "completed": 0,
        }

    def share_with(self, data_dir, *, writer: bool = False) -> None:
        """Share non-sensitive diagnostics across pet and settings processes.

        Only the pet process may write. An in-process settings page must never
        downgrade an already-configured writer to a reader.
        """
        if self._shared_writer and not writer:
            return
        if data_dir is None:
            return
        self._shared_path = Path(data_dir) / _STATUS_FILENAME
        if writer:
            self._shared_writer = True

    def snapshot(self) -> dict:
        if self._shared_path is not None and not self._shared_writer:
            shared = self._read_shared()
            if shared is not None:
                return shared
        return {**self._state, "history": tuple(self._history)}

    def _read_shared(self) -> dict | None:
        """Read an atomic snapshot written by the pet's GUI process."""
        try:
            if self._shared_path.stat().st_size > 16 * 1024:
                return None
            payload = json.loads(self._shared_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        shared = payload.get("snapshot")
        if not isinstance(shared, dict):
            return None
        snapshot = {**self._state}
        for key in self._state:
            if key in shared:
                snapshot[key] = shared[key]
        history = shared.get("history", ())
        snapshot["history"] = tuple(item for item in history[:5] if isinstance(item, dict)) \
            if isinstance(history, (tuple, list)) else ()
        updated_at = payload.get("updated_at")
        # The decision interval can be up to five minutes. Do not classify a
        # long idle interval as a dead process merely because no model was called.
        seconds = snapshot.get("interval_seconds", 40)
        if not isinstance(seconds, (int, float)) or not 20 <= seconds <= 300:
            seconds = 40
        if (snapshot.get("enabled") and isinstance(updated_at, (int, float))
                and time.time() - updated_at > max(120, 2 * seconds + 45)):
            snapshot.update(enabled=False, busy=False, next_decision_at=None,
                            state="桌宠状态长时间未更新（可能已退出）")
        return snapshot

    def _write_shared(self) -> None:
        if self._shared_path is None or not self._shared_writer:
            return
        path = self._shared_path
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "snapshot": {**self._state, "history": list(self._history)},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            # Diagnostics must never prevent the pet from making decisions.
            log.debug("无法同步自主决策状态文件", exc_info=True)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def publish(self, *, record: bool = False, **changes):
        """Called from the autonomy service's Qt GUI thread only."""
        self._state.update(changes)
        if record:
            self._history.appendleft({
                "at": changes.get("last_decision_at"),
                "action": self._state["last_action"],
                "result": self._state["last_result"],
            })
        self._write_shared()
        self.changed.emit(self.snapshot())


_monitor: AutonomyMonitor | None = None


def get_autonomy_monitor() -> AutonomyMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AutonomyMonitor()
    return _monitor
