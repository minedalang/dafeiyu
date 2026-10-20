"""No-Qt contract tests for the opt-in LLM daily activity planner."""

import json
import unittest
from datetime import datetime, timezone

from pet.autonomy_intent import (
    available_activities, build_decision_messages, local_schedule,
    parse_activity_plan,
)


CLIPS = ["写代码", "工作状态-思考冒泡", "吃午餐", "原地小憩沉眠", "下五子棋"]


class ActivityPlanTests(unittest.TestCase):
    def test_multiple_real_clips_and_talking(self):
        data = {"activity": "work", "steps": [
            {"verb": "play", "animation": "写代码"},
            {"verb": "play", "animation": "工作状态-思考冒泡"},
            {"verb": "say", "say": "忙完这件事就休息！"},
        ]}
        plan = parse_activity_plan(json.dumps(data, ensure_ascii=False),
                                   available_animations=CLIPS)
        self.assertEqual(plan.activity, "work")
        self.assertEqual([s.verb for s in plan.steps], ["play", "play", "say"])

    def test_reject_nonexistent_clip_and_oversized_plan(self):
        for data in (
            {"activity": "work", "steps": [{"verb": "play", "animation": "读书"}]},
            {"activity": "free", "steps": [{"verb": "idle"}] * 5},
            {"activity": "work", "steps": [{"verb": "launch_program"}]},
        ):
            with self.assertRaises(ValueError):
                parse_activity_plan(json.dumps(data, ensure_ascii=False),
                                    available_animations=CLIPS)

    def test_old_single_step_supported(self):
        plan = parse_activity_plan('{"verb":"look_at_cursor"}',
                                   available_animations=CLIPS)
        self.assertEqual(plan.activity, "free")
        self.assertEqual(plan.steps[0].verb, "look_at_cursor")

    def test_filter_missing_assets(self):
        activities = available_activities(["吃午餐"])
        self.assertEqual({a["id"] for a in activities}, {"lunch", "social"})

    def test_local_midday_schedule(self):
        at_noon = datetime(2026, 10, 20, 12, 5, tzinfo=timezone.utc)
        nap_time = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)
        self.assertIn("lunch", local_schedule(at_noon)["suggested_activities"])
        self.assertIn("nap", local_schedule(nap_time)["suggested_activities"])

    def test_user_message_reaches_planner_as_context(self):
        messages = build_decision_messages(
            {"pet": {"animation": "idle"}, "user_message": "陪我下五子棋吧"},
            animations=CLIPS,
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["observation"]["user_message"], "陪我下五子棋吧")
        self.assertIn("games", {a["id"] for a in payload["available_activities"]})


if __name__ == "__main__":
    unittest.main()
