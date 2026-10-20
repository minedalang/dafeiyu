"""No-Qt tests for sustained local activities and frame-only sleep loops."""

import json
import unittest
from datetime import datetime, timezone

from pet.activity_lifecycle import duration_seconds, sleep_pressure
from pet.animation_loops import advance_middle_loop, middle_frames
from pet.autonomy_intent import parse_activity_plan


class FakeClip:
    def __init__(self, current=0, frames=240, allow_seek=True):
        self.current = current
        self.frames = frames
        self.allow_seek = allow_seek
        self.seeks = []

    def frameCount(self):
        return self.frames

    def currentFrameNumber(self):
        return self.current

    def jumpToFrame(self, n):
        self.seeks.append(n)
        if self.allow_seek:
            self.current = n
        return self.allow_seek


class ActivityLifecycleTests(unittest.TestCase):
    def test_nap_duration_is_local_and_bounded(self):
        noon = datetime(2026, 10, 20, 13, 12, tzinfo=timezone.utc)
        night = datetime(2026, 10, 20, 23, 32, tzinfo=timezone.utc)
        self.assertEqual(duration_seconds("nap", now=noon), 20 * 60)
        self.assertEqual(duration_seconds("nap", 500, now=noon), 30 * 60)
        self.assertEqual(duration_seconds("nap", 1, now=night), 20 * 60)
        self.assertEqual(duration_seconds("nap", now=night), 30 * 60)
        self.assertEqual(duration_seconds("social", now=night), 0)

    def test_pressure_comes_from_clock_not_last_animation(self):
        morning = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        late = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
        self.assertGreater(sleep_pressure(late), sleep_pressure(morning))

    def test_duration_requested_by_model_is_only_a_hint(self):
        data = {"activity": "nap", "duration_minutes": 28, "steps": [
            {"verb": "play", "animation": "原地小憩沉眠"}]}
        plan = parse_activity_plan(json.dumps(data, ensure_ascii=False),
                                   available_animations=["原地小憩沉眠"])
        self.assertEqual(plan.duration_minutes, 28)
        data["duration_minutes"] = "forever"
        with self.assertRaises(ValueError):
            parse_activity_plan(json.dumps(data, ensure_ascii=False),
                                available_animations=["原地小憩沉眠"])

    def test_loop_only_middle_frames(self):
        self.assertEqual(middle_frames(10), None)
        clip = FakeClip(current=80)
        self.assertEqual(advance_middle_loop(clip), "waiting")
        self.assertEqual(clip.seeks, [])
        clip.current = 161
        self.assertEqual(advance_middle_loop(clip), "looped")
        self.assertEqual(clip.current, 84)
        self.assertEqual(clip.seeks, [84])

    def test_unseekable_clip_falls_back_to_whole_playback(self):
        clip = FakeClip(current=200, allow_seek=False)
        self.assertEqual(advance_middle_loop(clip), "unavailable")
        clip.jumpToFrame = lambda _n: None  # No-op implementation
        self.assertEqual(advance_middle_loop(clip), "unavailable")


if __name__ == "__main__":
    unittest.main()
