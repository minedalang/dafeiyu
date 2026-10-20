"""Headless checks for shared dialogue and single-request visual decisions."""

import base64
import importlib
import json
import sys
import threading
import types
import unittest
from unittest.mock import patch

from pet.autonomy_intent import build_decision_messages


class UnifiedPromptTests(unittest.TestCase):
    def test_recent_speech_is_sent_to_the_same_planner(self):
        messages = build_decision_messages(
            {"pet": {"animation": "idle"}},
            recent_dialogue=[
                {"role": "assistant", "text": "你还在写代码吗？"},
                {"role": "user", "text": "马上就好"},
            ],
        )
        body = json.loads(messages[-1]["content"])
        self.assertEqual([m["text"] for m in body["recent_dialogue"]],
                         ["你还在写代码吗？", "马上就好"])

    def test_history_is_bounded_and_does_not_include_raw_screenshots(self):
        data = [{"role": "assistant", "text": "鲸" * 1000} for _ in range(30)]
        prompt = build_decision_messages({}, recent_dialogue=data)
        body = json.loads(prompt[-1]["content"])
        self.assertEqual(len(body["recent_dialogue"]), 12)
        self.assertTrue(all(len(m["text"]) <= 240 for m in body["recent_dialogue"]))


class SingleVisionRequestTests(unittest.TestCase):
    def test_existing_window_frame_does_not_capture_screen_again(self):
        qt = types.ModuleType("PySide6.QtCore")
        qt.QObject = type("QObject", (), {})
        qt.QTimer = type("QTimer", (), {})
        qt.Signal = lambda *args: object()
        py_side = types.ModuleType("PySide6")
        py_side.QtCore = qt

        captured = []
        provider_module = types.ModuleType("pet.chat.providers")
        vision_module = types.ModuleType("pet.vision")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                answer = '{"activity":"free","steps":[{"verb":"say","say":"辛苦啦"}]}'
                return json.dumps({"choices": [{"message": {"content": answer}}]}).encode()

        class Provider:
            def _open(self, payload, config, accept):
                captured.append(payload)
                return Response()

        provider_module.OpenAICompatibleProvider = Provider
        provider_module.thinking_payload = lambda *_args, **_kwargs: {}

        def forbidden_capture():
            raise AssertionError("already-authorized image must not be recaptured")

        vision_module.capture_screen_bytes = forbidden_capture
        vision_module.resolve_vision_model = lambda config: config.model

        with patch.dict(sys.modules, {
            "PySide6": py_side,
            "PySide6.QtCore": qt,
            "pet.chat.providers": provider_module,
            "pet.vision": vision_module,
        }):
            autonomy = importlib.import_module("pet.autonomy")
            config = types.SimpleNamespace(
                model="test", timeout=15, max_tokens=200,
                vision_same_as_chat=True,
            )
            answer = autonomy._request_intent(
                config, {"pet": {"animation": "idle"}}, (), (), (),
                False, threading.Event(),
                recent_dialogue=({"role": "assistant", "text": "之前说过的话"},),
                visual_frame=b"fake-jpeg-window",
            )

        self.assertEqual(answer.steps[0].say, "辛苦啦")
        self.assertEqual(len(captured), 1)
        messages = captured[0]["messages"]
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"],
                         "data:image/jpeg;base64," + base64.b64encode(b"fake-jpeg-window").decode())
        context = json.loads(messages[-1]["content"][0]["text"])
        self.assertEqual(context["recent_dialogue"][0]["text"], "之前说过的话")


if __name__ == "__main__":
    unittest.main()
