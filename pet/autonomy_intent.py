"""Mochi-style LLM intent contract for the desktop pet.

The model chooses one *high-level* action.  This module neither imports Qt nor
executes model output; the existing movement/animation controllers own physics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


VERBS = frozenset({
    "idle", "walk", "approach_cursor", "retreat_cursor", "play", "say",
    "turn", "look_at_cursor",
})
EMOTIONS = frozenset({"neutral", "happy", "curious", "sleepy", "shy", "excited"})
_FIELDS = frozenset({"verb", "animation", "emotion", "say", "thought"})


@dataclass(frozen=True)
class ActionIntent:
    verb: str
    animation: str | None = None
    say: str | None = None
    emotion: str = "neutral"


@dataclass(frozen=True)
class ActivityPlan:
    """One LLM decision, several existing animations executed locally."""

    activity: str
    steps: tuple[ActionIntent, ...]


# Semantic activities are *aliases* for clips already in the active character
# pack, never new animation names. Missing clips automatically disappear.
ACTIVITY_CLIPS = {
    "breakfast": ("早餐", ("晨间刷牙", "吃早餐", "悠闲哼歌")),
    "lunch": ("午饭", ("吃午餐", "吃白饭", "工作状态-雀跃庆祝")),
    "dinner": ("晚饭", ("吃晚餐", "吃白饭", "超大伸懒腰")),
    "nap": ("午睡", ("哈欠连天", "原地小憩沉眠", "打瞌睡被惊醒")),
    "work": ("工作与学习", ("写代码", "工作状态-思考冒泡", "工作状态-忙碌点按",
                          "工作状态-清点归档", "轻快记录", "工作状态-雀跃庆祝")),
    "games": ("游戏与娱乐", ("下五子棋", "原地专心玩魔方", "玩游戏气急败坏",
                          "原地蹲下玩玩具汽车", "三球抛接", "扑克魔术")),
    "music": ("音乐与表演", ("小提琴演奏", "吹笛子", "悠闲哼歌", "可爱宅舞",
                          "轻快摇摆舞")),
    "rest": ("放松休息", ("超大伸懒腰", "哈欠连天", "原地小憩沉眠",
                          "摇扇纳凉", "碎碎念-发呆碎碎念")),
    "play": ("玩耍", ("撸猫", "抽陀螺", "放风筝", "吹气球", "原地跳跃抓碎头顶物品")),
    "social": ("主动陪伴与说话", ()),
}


def available_activities(animations: Iterable[str]) -> list[dict]:
    """Only advertise activities whose real animation clips are installed."""
    names = frozenset(animations)
    return [
        {"id": key, "description": label, "animations": [n for n in clips if n in names]}
        for key, (label, clips) in ACTIVITY_CLIPS.items()
        if key == "social" or any(n in names for n in clips)
    ]


def local_schedule(now: datetime | None = None) -> dict:
    """Use the user's *computer* local clock; never a server timezone."""
    now = now or datetime.now().astimezone()
    hour = now.hour + now.minute / 60
    if 6.5 <= hour < 9.5:
        suggestions = ["breakfast", "work"]
    elif 11.5 <= hour < 13:
        suggestions = ["lunch", "rest"]
    elif 13 <= hour < 14.5:
        suggestions = ["nap", "rest"]
    elif 18 <= hour < 20:
        suggestions = ["dinner", "rest"]
    elif hour >= 22 or hour < 6.5:
        suggestions = ["rest"]
    else:
        suggestions = ["work", "games", "music", "play"]
    return {"local_datetime": now.isoformat(timespec="minutes"),
            "weekday": "一二三四五六日"[now.weekday()],
            "suggested_activities": suggestions}


def parse_action_intent(response: str, *,
                        available_animations: Iterable[str] = ()) -> ActionIntent:
    """Validate the entire answer against a tiny allowlist (no commands/tools)."""
    raw = str(response or "").strip()
    if len(raw) > 12000:
        raise ValueError("model answer is too large")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lower() in ("```", "```json") and lines[-1] == "```":
            raw = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("model answer is not JSON") from exc
    if not isinstance(data, dict) or set(data) - _FIELDS:
        raise ValueError("model answer has unsupported fields")
    verb = data.get("verb")
    if not isinstance(verb, str) or verb not in VERBS:
        raise ValueError("model chose an unsupported action")
    emotion = data.get("emotion", "neutral")
    if not isinstance(emotion, str) or emotion not in EMOTIONS:
        raise ValueError("model chose an unsupported emotion")
    animation = data.get("animation")
    if animation is not None:
        if (not isinstance(animation, str)
                or animation not in frozenset(available_animations)
                or verb != "play"):
            raise ValueError("model chose an unsupported animation")
    if verb == "play" and animation is None:
        raise ValueError("play requires an available animation")
    say = data.get("say")
    if say is not None and not isinstance(say, str):
        raise ValueError("speech must be text")
    say = say.strip().replace("\x00", "")[:90] if say else None
    if verb == "say" and not say:
        raise ValueError("say requires speech")
    return ActionIntent(verb=verb, animation=animation, say=say, emotion=emotion)


def parse_activity_plan(response: str, *,
                        available_animations: Iterable[str] = ()) -> ActivityPlan:
    """Bounded, allowlisted multi-step plan; accept old single-step replies too."""
    raw = str(response or "").strip()
    if len(raw) > 12000:
        raise ValueError("model answer is too large")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lower() in ("```", "```json") and lines[-1] == "```":
            raw = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("model plan is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("model plan must be an object")
    if "steps" not in data:
        return ActivityPlan("free", (parse_action_intent(raw,
                            available_animations=available_animations),))
    if set(data) != {"activity", "steps"}:
        raise ValueError("model plan has unsupported fields")
    activity = data["activity"]
    if not isinstance(activity, str) or activity not in (*ACTIVITY_CLIPS, "free"):
        raise ValueError("model selected an unsupported activity")
    steps = data["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
        raise ValueError("model plan must have 1-4 steps")
    parsed = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("plan step must be an object")
        parsed.append(parse_action_intent(json.dumps(step, ensure_ascii=False),
                                          available_animations=available_animations))
    return ActivityPlan(activity, tuple(parsed))


_SYSTEM = (
    "你是桌面上活泼、有独立生活的大肥鱼（小鲸鱼），会玩耍、学习、用餐、休息和陪伴主人。"
    "结合电脑本地时间、当前桌面观察与最近的活动，自主选一项有意义的生活活动，"
    "而不是反复原地待机。午饭约11:30-13:00，午睡约13:00-14:30，晚饭约18:00-20:00；"
    "这是贴心建议，不是闹钟，不要连续多轮重复吃饭或午睡；优先考虑屏幕情境和最近活动。"
    "没有提供对应素材的活动绝不能假装做了（尤其不要凭空生成读书动画）。\n"
    "如果 observation 中有 user_message，则这是主人刚在 AI 聊天窗口对你说的话，"
    "必须优先响应主人的明确要求，选择实际可用的动画配合聊天回复；"
    "例如请求下棋优先选下五子棋，请求学习优先选择写代码或轻快记录。"
    "聊天窗口自身会给出文字答复，此处以可见动画为主，避免重复冗长的说辞。"
    "请求不存在的素材时，不要谎称已经执行，可用 say 简短说明。\n"
    "只输出一个 JSON 对象，不要 markdown 或解释："
    '{"activity":"work","steps":[{"verb":"play","animation":"写代码"},'
    '{"verb":"play","animation":"工作状态-思考冒泡"},'
    '{"verb":"say","say":"终于想明白啦！"}]}。'
    "activity 必须从 available_activities 的 id 或 free 选；steps 为1-4步。"
    "每步字段：verb（idle/walk/approach_cursor/retreat_cursor/play/say/turn/look_at_cursor），"
    "animation（只在play时必填，必须精确选自 available_play_animations），"
    "say（可选，90字以内的自然中文短句），emotion（可选：neutral/happy/curious/sleepy/shy/excited）。"
    "适当把动作和短台词组合，避免连续多次 say 或重复相同动画；用户忙碌或深夜尽量安静。"
    "不要发出任何键鼠、文件、网页、程序控制指令。所有结果仅用于现有桌宠动画和气泡。"
)


def build_decision_messages(observation: dict, *, animations: Iterable[str] = (),
                            recent_actions: Iterable[str] = (),
                            recent_activities: Iterable[str] = ()) -> list[dict]:
    """One compact decision prompt with local time and real clip affordances."""
    names = list(animations)[:128]
    activities = available_activities(names)
    schedule = local_schedule()
    schedule["suggested_activities"] = [
        key for key in schedule["suggested_activities"]
        if any(a["id"] == key for a in activities)
    ]
    snapshot = {
        "observation": observation,
        "local_time": schedule,
        "available_activities": activities,
        "available_play_animations": names,
        "recent_actions": list(recent_actions)[-8:],
        "recent_activities": list(recent_activities)[-8:],
    }
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
    ]
