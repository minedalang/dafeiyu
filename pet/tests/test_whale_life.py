"""Local whale life simulation: real needs, persistence and no token usage."""

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from pet.life_state import WhaleLife
from pet.autonomy_intent import build_decision_messages


def at(hour: int = 12) -> float:
    return datetime(2026, 10, 20, hour, 0).timestamp()


class WhaleLifeTests(unittest.TestCase):
    def test_work_consumes_energy_and_sleep_restores_it(self):
        life = WhaleLife(now=at(12))
        before = life.snapshot(now=at(12))
        life.advance(now=at(12) + 60 * 60, activity="work")
        during = life.snapshot(now=at(12) + 60 * 60)
        self.assertLess(during["energy"], before["energy"])
        self.assertGreater(during["hunger"], before["hunger"])
        self.assertGreater(during["sleepiness"], before["sleepiness"])
        life.advance(now=at(12) + 90 * 60, activity="nap")
        after = life.snapshot(now=at(12) + 90 * 60)
        self.assertGreater(after["energy"], during["energy"])
        self.assertLess(after["sleepiness"], during["sleepiness"])

    def test_meal_counts_once_per_day_across_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            life = WhaleLife(folder, now=at(12))
            before = life.values["hunger"]
            life.start_activity("lunch", now=at(12))
            self.assertLess(life.values["hunger"], before)
            after_first = life.values["hunger"]
            restored = WhaleLife(folder, now=at(12) + 50)
            self.assertIn("lunch", restored.snapshot(now=at(12) + 50)["meals_today"])
            restored.start_activity("lunch", now=at(12) + 60)
            self.assertGreaterEqual(restored.values["hunger"], after_first)
            self.assertNotIn("lunch", restored.snapshot(now=at(12) + 60)["suggested_activities"])
            self.assertEqual(len([e for e in restored.events
                                  if e["description"] == "吃午饭"]), 1)

    def test_user_chat_and_night_wakeup_change_mood_and_memory(self):
        life = WhaleLife(now=at(23))
        mood = life.values["mood"]
        life.user_chatted(now=at(23) + 10)
        self.assertGreater(life.values["mood"], mood)
        after_chat = life.values["mood"]
        life.awakened(now=at(23) + 20)
        self.assertLess(life.values["mood"], after_chat)
        self.assertGreater(life.values["sleepiness"], 22)
        self.assertIn("深夜被叫醒", life.events[-1]["description"])
        self.assertEqual(life.events[0]["description"], "主人来聊天了")

    def test_offline_night_rest_catches_up_without_wild_overflow(self):
        with tempfile.TemporaryDirectory() as folder:
            life = WhaleLife(folder, now=at(23))
            life.values.update(energy=10, sleepiness=90)
            life.save(force=True)
            next_morning = datetime(2026, 10, 21, 8).timestamp()
            restored = WhaleLife(folder, now=next_morning)
            self.assertGreater(restored.values["energy"], 80)
            self.assertLess(restored.values["sleepiness"], 20)
            self.assertTrue(all(0 <= x <= 100 for x in restored.values.values()))

    def test_night_shutdown_before_bedtime_only_counts_night_as_sleep(self):
        with tempfile.TemporaryDirectory() as folder:
            evening = datetime(2026, 10, 20, 19, 0).timestamp()
            morning = datetime(2026, 10, 21, 8, 0).timestamp()
            life = WhaleLife(folder, now=evening)
            life.values.update(energy=12, sleepiness=90)
            life.save(force=True)
            restored = WhaleLife(folder, now=morning)
            self.assertEqual(restored.updated_at, morning)
            self.assertLess(restored.values["sleepiness"], 10)
            self.assertGreater(restored.values["energy"], 90)
            self.assertTrue(any("离线睡眠 9 小时 0 分钟" in e["description"]
                                for e in restored.events))
            same_time = WhaleLife(folder, now=morning)
            self.assertEqual(same_time.values, restored.values)
            self.assertEqual(same_time.events, restored.events)

    def test_daytime_shutdown_does_not_grant_night_sleep(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 20, 12, 0).timestamp()
            end = datetime(2026, 10, 20, 16, 0).timestamp()
            life = WhaleLife(folder, now=start)
            life.values.update(energy=20, sleepiness=80)
            life.save(force=True)
            restored = WhaleLife(folder, now=end)
            self.assertGreater(restored.values["sleepiness"], 80)
            self.assertLess(restored.values["energy"], 20)
            self.assertFalse(any("离线睡眠" in e["description"]
                                 for e in restored.events))

    def test_nap_at_shutdown_only_counts_remaining_nap_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 20, 14, 0).timestamp()
            life = WhaleLife(folder, now=start)
            life.values.update(energy=35, sleepiness=85)
            life.start_activity("nap", now=start, sleep_until_at=start + 20 * 60)
            restarted = WhaleLife(folder, now=start + 3 * 3600)
            self.assertAlmostEqual(restarted.values["sleepiness"],
                                   85 - 20 * 0.9 + 160 * 0.048, delta=0.2)
            self.assertIsNone(restarted.sleep_until_at)
            self.assertTrue(any("离线睡眠 0 小时 20 分钟" in e["description"]
                                for e in restarted.events))

    def test_nap_closed_normally_is_not_credited_again(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 20, 14, 0).timestamp()
            life = WhaleLife(folder, now=start)
            life.start_activity("nap", now=start, sleep_until_at=start + 30 * 60)
            life.advance(now=start + 4 * 60, activity="nap")
            life.end_activity("nap")
            life.save(force=True)
            restored = WhaleLife(folder, now=start + 3600)
            self.assertFalse(any("离线睡眠" in e["description"]
                                 for e in restored.events))

    def test_week_long_shutdown_is_capped_to_most_recent_day(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 14, 20).timestamp()
            end = datetime(2026, 10, 21, 8).timestamp()
            life = WhaleLife(folder, now=start)
            life.values.update(energy=30, sleepiness=75)
            life.save(force=True)
            restored = WhaleLife(folder, now=end)
            self.assertEqual(restored.updated_at, end)
            night_entries = [e for e in restored.events if "离线睡眠" in e["description"]]
            self.assertEqual(len(night_entries), 1)
            self.assertIn("9 小时 0 分钟", night_entries[0]["description"])

    def test_partial_night_in_same_calendar_day_credits_sleep(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 20, 22, 30).timestamp()
            end = datetime(2026, 10, 20, 23, 30).timestamp()
            life = WhaleLife(folder, now=start)
            life.values["sleepiness"] = 80
            life.save(force=True)
            restored = WhaleLife(folder, now=end)
            self.assertLess(restored.values["sleepiness"], 30)
            self.assertTrue(any("离线睡眠 1 小时 0 分钟" in e["description"]
                                for e in restored.events))

    def test_corrupt_values_and_absurd_events_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "whale-life-state.json"
            path.write_text(json.dumps({
                "version": 1, "updated_at": at(12),
                "needs": {"energy": "NaN", "hunger": 300,
                          "sleepiness": -45, "mood": "invalid"},
                "events": [{"at": at(12), "description": "previous valid event"},
                           {"at": at(12), "description": "x" * 1000}],
            }), encoding="utf-8")
            life = WhaleLife(folder, now=at(12))
            self.assertEqual(life.values["energy"], 78.0)
            self.assertEqual(life.values["hunger"], 100)
            self.assertEqual(life.values["sleepiness"], 0)
            self.assertEqual(life.values["mood"], 72.0)
            self.assertEqual(len(life.events), 1)

    def test_snapshot_guides_but_does_not_force_model_actions(self):
        life = WhaleLife(now=at(12))
        life.values["hunger"] = 85
        world = life.snapshot(now=at(12))
        self.assertIn("lunch", world["suggested_activities"])
        self.assertEqual(world["hunger"], 85)
        self.assertNotIn("user_message", world)
        messages = build_decision_messages({"life_state": world})
        self.assertIn('"hunger": 85', messages[1]["content"])
        self.assertIn("只有实际执行成功", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
