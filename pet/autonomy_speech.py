"""Small, independently scheduled speech decisions for the desktop whale.

Pure data helpers: the caller handles API calls, GUI presentation, and consent.
No generated content here can trigger an animation or a computer operation.
"""

from __future__ import annotations

import json


def speech_interval_bounds(minimum_minutes=3, maximum_minutes=6) -> tuple[int, int]:
    """Validate the editable quiet interval and return seconds for the scheduler.

    Configs from older releases may omit these keys; corrupt values should not
    crash the pet or accidentally trigger an API request every few milliseconds.
    """
    def minutes(value, fallback):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            number = fallback
        return max(1, min(60, number))

    minimum = minutes(minimum_minutes, 3)
    maximum = max(minimum, minutes(maximum_minutes, 6))
    return minimum * 60, maximum * 60


def combine_persona(character_prompt: str, user_prompt: str) -> str:
    """Keep a character's identity while honoring the editable AI settings."""
    character = str(character_prompt or "").strip()
    user = str(user_prompt or "").strip()
    if character and user:
        return (character + "\n\n【用户设置的个性与说话风格，优先遵守】\n" + user)
    return user or character


def build_speech_messages(observation: dict, *, trigger: str,
                          persona: str = "", recent_dialogue=(),
                          recent_speech=()) -> list[dict]:
    """Return a bounded JSON-only request; the model may elect silence."""
    if trigger not in ("click", "ambient", "window"):
        raise ValueError("unsupported speech trigger")
    prompt = (
        "你是桌面上的小鲸鱼。你只决定此刻是否说一句话，不负责切换任何动画或操作电脑。"
        "请根据本地时间、自己正在做的活动、情绪和最近对话，决定是否开口。"
        "不要机械重复问候，不要复读自己刚说过的句子，也不要编造没有观察到的屏幕内容。"
        "自然、简短、有性格，有时不说话也是正确选择。"
        "只输出 JSON 对象：{\"say\":\"自然中文短句\"} 或 {\"say\":\"\"}。"
        "say 最多 90 字，不要 Markdown、表情堆砌、指令、工具调用或其他键。"
    )
    if trigger == "click":
        prompt += (
            "主人刚刚左键点了你，这是亲自发起的互动。优先给予贴合情绪的回应，"
            "但可根据情境决定安静。原本的点击动画正在播放，你的回答不能改变动作。"
            "若刚从睡梦中被叫醒，可以困倦地抱怨几句。"
        )
    elif trigger == "window":
        prompt += "你看到的是授权的前台窗口观察；只有确实有话想说才开口，别报幕。"
    else:
        prompt += "这是一次低频自主搭话判断；主人没有叫你，正专注时优先安静。"
    if persona:
        prompt += "\n【小鲸鱼个人性格设定，仅影响说话风格】\n" + str(persona)[:1600]
    context = {
        "trigger": trigger,
        "observation": observation,
        "recent_dialogue": [
            {"role": m["role"], "text": str(m["text"])[:240]}
            for m in list(recent_dialogue)[-10:]
            if isinstance(m, dict) and m.get("role") in ("assistant", "user")
            and str(m.get("text", "")).strip()
        ],
        "recent_spontaneous_lines": list(recent_speech)[-5:],
    }
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def parse_speech_response(raw: str) -> str:
    """Accept only a single short line or silence; ignore unsupported fields."""
    response = str(raw or "").strip()
    if len(response) > 8192:
        raise ValueError("speech response too large")
    if response.startswith("```"):
        lines = response.splitlines()
        if len(lines) >= 3 and lines[0].lower() in ("```", "```json") and lines[-1] == "```":
            response = "\n".join(lines[1:-1]).strip()
    try:
        answer = json.loads(response)
    except (TypeError, ValueError) as exc:
        raise ValueError("speech response is not JSON") from exc
    if not isinstance(answer, dict) or set(answer) != {"say"}:
        raise ValueError("speech response has unsupported fields")
    line = answer["say"]
    if not isinstance(line, str):
        raise ValueError("speech line is not text")
    return " ".join(line.split())[:90]
