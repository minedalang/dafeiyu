"""Countdown UI and cross-process monitor contract without Qt runtime."""

import ast
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


class FakeButton(FakeLabel):
    def setEnabled(self, enabled):
        self.enabled = enabled


class ActivityClockTests(unittest.TestCase):
    def test_ui_countdown_and_finished_state(self):
        page_file = ROOT / "pet/chat/ai_settings_page.py"
        tree = ast.parse(page_file.read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "_AiSettingsPage")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_refresh_autonomy_monitor")
        isolated = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        namespace = {"time": types.SimpleNamespace(time=lambda: 500.0),
                     "datetime": datetime}
        exec(compile(isolated, str(page_file), "exec"), namespace)
        labels = {name: FakeLabel() for name in (
            "status", "activity_time", "life", "life_events", "model", "action", "result", "last",
            "next", "counts", "error", "history")}
        widget = types.SimpleNamespace(
            _autonomy_monitor_labels=labels,
            _edit_active_loop_button=FakeButton())
        snapshot = {
            "enabled": True, "state": "正在执行活动：工作与学习",
            "activity_name": "工作与学习", "activity_started_at": 400,
            "activity_ends_at": 700, "activity_total_seconds": 300,
        }
        namespace["_refresh_autonomy_monitor"](widget, snapshot)
        self.assertIn("已进行 01:40 / 05:00", labels["activity_time"].text)
        self.assertIn("剩余 03:20", labels["activity_time"].text)
        namespace["_refresh_autonomy_monitor"](widget, {"enabled": True})
        self.assertEqual(labels["activity_time"].text, "—（暂无持续活动）")
        namespace["_refresh_autonomy_monitor"](widget, {
            "enabled": True,
            "life_state": {"energy": 82, "hunger": 31, "sleepiness": 15,
                           "mood": 74, "goal": "准备好好工作",
                           "recent_events": [{"at": 440.0, "description": "吃早餐"}]},
        })
        self.assertIn("精力", labels["life"].text)
        self.assertIn("82/100", labels["life"].text)
        self.assertIn("准备好好工作", labels["life"].text)
        self.assertIn("吃早餐", labels["life_events"].text)

    def test_shared_monitor_keeps_activity_deadline(self):
        class FakeSignal:
            def __init__(self, *_):
                pass

            def emit(self, *_):
                pass

        qt = types.ModuleType("PySide6.QtCore")
        qt.QObject = type("QObject", (), {"__init__": lambda self, *_: None})
        qt.Signal = FakeSignal
        py_side = types.ModuleType("PySide6")
        py_side.QtCore = qt
        spec = importlib.util.spec_from_file_location(
            "pet._autonomy_monitor_time_test", ROOT / "pet/autonomy_monitor.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"PySide6": py_side, "PySide6.QtCore": qt}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            writer = module.AutonomyMonitor()
            writer.share_with(folder, writer=True)
            writer.publish(enabled=True, activity_name="工作与学习",
                           activity_started_at=100.0, activity_ends_at=580.0,
                           activity_total_seconds=480,
                           life_state={"energy": 72, "hunger": 40})
            reader = module.AutonomyMonitor()
            reader.share_with(folder)
            state = reader.snapshot()
            self.assertEqual(state["activity_name"], "工作与学习")
            self.assertEqual(state["activity_ends_at"], 580.0)
            self.assertEqual(state["activity_total_seconds"], 480)
            self.assertEqual(state["life_state"]["hunger"], 40)


if __name__ == "__main__":
    unittest.main()
