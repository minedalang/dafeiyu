"""Mochi-style LLM intent contract for the desktop pet.

The model chooses one *high-level* action.  This module neither imports Qt nor
executes model output; the existing movement/animation controllers own physics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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


_SYSTEM = (
    "你是一只名为大肥鱼的活泼桌宠的大脑。你的唯一任务是依据周围环境、"
    "当前动作和最近的行为，选择下一步自然的小动作。不要当聊天机器人连续说话，"
    "大多数时候用动作表达。偶尔可以用 say 说一句短中文。\n"
    "只输出一个 JSON 对象，不要 markdown、解释或额外字段。字段："
    "verb（必填：idle, walk, approach_cursor, retreat_cursor, play, say, turn, "
    "look_at_cursor）；animation（只有 play 时必填，必须从提供的候选动画中选）；"
    "emotion（可选：neutral, happy, curious, sleepy, shy, excited）；"
    "say（可选中文台词，不超过 90 字）；thought（可选简短想法，不展示）。\n"
    "选择 walk 表示随意散步，approach_cursor/retreat_cursor 表示向鼠标靠近/远离，"
    "look_at_cursor 表示看向鼠标。没有合适动作时选择 idle。"
    "你不能操作电脑、键盘、文件、网页或其他程序；所有输出仅用于桌宠动画。"
)


def build_decision_messages(observation: dict, *, animations: Iterable[str] = (),
                            recent_actions: Iterable[str] = ()) -> list[dict]:
    """Create one concise, stateless, model-independent decision prompt."""
    snapshot = {
        "observation": observation,
        "available_play_animations": list(animations)[:128],
        "recent_actions": list(recent_actions)[-8:],
    }
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
    ]
