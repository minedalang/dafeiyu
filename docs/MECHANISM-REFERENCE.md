# 机制速查、配置字段与维护清单

> 本页是跨模块的“真相索引”，帮助以后继续迭代而不忘记同步机制文档。参数以 `2026-10-21` 这份源码为准；**表格不是运行时可编辑项清单**，标记“代码常量”的需修改代码才会改变。

## 1. 核心参数总表

| 机制 | 当前值 | 设置/来源 | 相关源码 |
| --- | --- | --- | --- |
| 自主行为总开关 | 默认关闭 | `chat.autonomy_enabled` | `chat/models.py`、`autonomy.py` |
| 自主行为识屏 | 默认关闭 | `chat.autonomy_screen_enabled` | `chat/models.py`、`autonomy.py` |
| 普通自主决策间隔 | 默认 40 秒，20～300 秒 | `chat.autonomy_interval_seconds` | `chat/models.py`、`autonomy.py` |
| 单次行为计划长度 | 1～4 步 | 代码校验 | `autonomy_intent.py` |
| 单次行为决策超时上限 | 35 秒 | 代码常量 | `autonomy.py:_request_intent` |
| 单次行为决策输出上限 | 720 tokens | 代码常量 | `autonomy.py:_request_intent` |
| 独立搭话检查 | 15 秒 | 代码常量 | `autonomy.py` |
| 自主搭话随机间隔 | 默认 3～6 分钟，各 1～60 分钟 | 根配置 `autonomy_speech_min_minutes/max_minutes` | `autonomy_speech.py`、`autonomy.py` |
| 搭话回复长度 | 最多 90 字 | 代码校验 | `autonomy_speech.py` |
| 搭话超时 / 输出预算 | 25 秒 / 180 tokens | 代码常量 | `autonomy.py:_request_speech` |
| 自主搭话最近发言冷却 | 45 秒 | 代码常量 | `autonomy.py:_speech_tick` |
| 左键点击请求限频 | 12 秒 | 代码常量 | `autonomy.py:queue_user_click` |
| 双击中断判定 | 0.55 秒 / 36 像素以内 | 代码常量 | `autonomy.py:allow_user_click_interrupt` |
| 非睡眠持续活动阶段更新 | 约 90 秒 | 代码常量，有触发条件 | `autonomy.py:_schedule_replan` |
| 在线生命状态刷新 | 30 秒 | 代码常量 | `autonomy.py:_life_timer` |
| 在线睡眠动画检查 | 500 毫秒 | 代码常量 | `autonomy.py:_activity_timer` |
| 在线白天午睡 | 15～30 分钟，默认 20 | 代码边界 | `activity_lifecycle.py` |
| 在线夜间长睡眠 | 22:00～07:00，最多 9 小时，按开始时间到 07:00 | 代码规则 | `activity_lifecycle.py` |
| 工作活动 | 5～15 分钟，默认 8 | 代码边界 | `activity_lifecycle.py` |
| 番茄钟 | 默认专注 25 / 短休 5 / 长休 15 分钟，每 4 轮长休 | 右键菜单 / 托盘，`pomodoro-settings.json` | `pomodoro.py`、`pomodoro_dialog.py` |
| 自动离线夜间睡眠 | 22:00～次日 07:00 | 电脑本地时间，代码常量 | `life_state.py` |
| 离线补算上限 | 最近 24 小时 | 代码常量 | `life_state.py` |
| 睡眠困意下降速率 | 每分钟 -0.90 | 代码常量 | `life_state.py:advance` |
| 睡眠精力恢复速率 | 每分钟 +0.53 | 代码常量 | `life_state.py:advance` |
| 状态取值边界 | 0～100 | 代码校验 | `life_state.py:_clamp` |
| 最近生活事件 | 最多 12 条 | 本地状态文件 | `life_state.py` |
| 离线审计明细 | 最近 24 条 | 本地状态文件 | `life_state.py` |
| AI 决策面板历史 | 最近 5 条 | 跨进程诊断快照 | `autonomy_monitor.py` |
| 睡眠动画默认中段 | 35%～67% | `sleep-loop.json` / 动画自定义覆盖 | `animation_loops.py` |

## 2. 两套配置结构

| 配置入口 | JSON 键 | 说明 |
| --- | --- | --- |
| AI 设置 → AI 自主行为 | `chat.autonomy_enabled` | 自主决策总开关 |
| 同上 | `chat.autonomy_screen_enabled` | 自主服务主动截屏授权 |
| 同上 | `chat.autonomy_interval_seconds` | 普通自主决策间隔 |
| 同上 | `autonomy_speech_min_minutes` | **根配置字段**，不要错误地放入 `chat` 子对象 |
| 同上 | `autonomy_speech_max_minutes` | **根配置字段**，保存后重新安排独立搭话 |
| AI 设置 → 模型与连接 | `chat.default_system_prompt` | 用户自定义系统提示词，与角色人设合成有效个性 |
| AI 设置 → Provider | `chat.providers`、`chat.active_provider` | 模型、端点、参数和凭据引用 |

`pet/chat/ai_settings_page.py` 同时负责 ChatSettings 字段和根配置字段。编辑设置时要区分保存位置，避免“设置看似改了但自主调度仍读取旧值”。API 密钥优先由安全存储管理，不应复制进文档、测试快照或提交记录。

## 3. 各模块职责

| 源文件 | 当前职责 |
| --- | --- |
| `pet/autonomy.py` | 自主任务调度、独立搭话、持续活动执行、双击打断、生命系统时钟接入 |
| `pet/autonomy_intent.py` | 意图 JSON 白名单、活动类别与动画映射、时间语义建议、动作计划校验 |
| `pet/autonomy_speech.py` | 人设整合辅助、短句提示词、搭话间隔验证与返回校验 |
| `pet/activity_lifecycle.py` | 多分钟活动时长上限、白天/夜间睡眠策略 |
| `pet/life_state.py` | 四项生命值、用餐去重、在线更新、离线夜间与午睡补算、审计持久化 |
| `pet/animation_loops.py` | 按动画名选择循环百分比、读取与原子保存配置 |
| `pet/loop_editor.py` | 循环帧可视化调节界面 |
| `pet/autonomy_monitor.py` | 跨进程监控快照与最近决策结果 |
| `pet/app.py` | 主程序、角色提示词、原版识屏/聊天/点击钩子对接 |
| `pet/window.py` | 传统窗口交互、动画循环/拖动/抛掷 |
| `pet/sprite_behavior.py` | Overlay 路径中的受保护动画与拖拽交互 |
| `pet/context_menu_dismiss.py` | Windows 不抢焦点右键菜单的第一次外部点击关闭逻辑 |
| `pet/chat/ai_settings_page.py` | AI 设置 UI、状态倒计时、保存配置 |
| `pet/chat/prompt.py` | 正常聊天侧角色提示词合成 |

## 4. 修改前后都应检查

| 改动类型 | 必须同步更新的文档与验证 |
| --- | --- |
| 决策频率/AI 动作类型/JSON 格式 | `AI-AUTONOMY.md`、本页；检查正常计划、无效响应、忙碌跳过 |
| 人设/搭话/点击回复 | `AI-SPEECH-AND-PERSONA.md`、本页；检查上下界、静默、DND、API 限频 |
| 活动时长或 90 秒阶段更新 | `ACTIVITIES-AND-ANIMATION.md`、本页；检查截止时间不延长、没有素材时降级 |
| 单击/双击/睡觉拖动 | `INTERACTIONS.md`、`ACTIVITIES-AND-ANIMATION.md`；分别验收传统与 Overlay |
| 睡眠速率、晚间时间段、离线补算 | `WHALE-LIFE-MECHANICS.md`、`DIAGNOSTICS-AND-DATA.md`、本页；覆盖跨午夜、长时离线、重复启动、DST/本地时间 |
| 识屏授权和请求复用 | `VISION-AND-PRIVACY.md`、`AI-AUTONOMY.md`；检查开关四种组合及数据出境 |
| 状态面板和本地 JSON 字段 | `DIAGNOSTICS-AND-DATA.md`、本页；检查读取旧数据及字段上下限 |

可参考当前仓库专项测试：`pet/tests/test_whale_life.py`、`pet/tests/test_whale_sleep_drag_and_audit.py`、`pet/tests/test_ai_activity_plan.py`、`pet/tests/test_autonomy_activity_time.py`。**本次更新已修改源码并运行无 Qt 测试；Windows 实机交互仍待验收。**

## 5. 尚未实现 / 不要写成已实现

- **夜间睡眠安排**：已可从入睡时持续到当地 07:00，具体时长取决于开始时间；详见 [睡眠与番茄钟更新](SLEEP-POMODORO-2026-10-21.md)。
- **睡眠快结束时 AI 判断是否赖床并延长当前计划**：未加入；当前不自动续期。
- **按入睡时刻切换自主活动显示名称**：已区分夜间睡眠、晨间补觉、午睡、下午小憩、晚间休息。
- **主动搭话失败后 30 秒自动重试/指数退避**：目前并未实现；失败后仍按普通间隔等待。
- **真正检测用户本人睡眠、从关机时运行模型**：未实现，也不应这样表述。关机后没有程序运行，下次启动只是按本地时钟推算小鲸鱼的离线生活。


### 精力与困意判断规则（2026-10-21）

- 困意 `sleepiness ≥ 70`：优先 `nap`，与精力是否充足无关。
- 饥饿 `hunger ≥ 70`：未触发严重困倦时优先考虑进餐。
- 精力 `energy ≤ 28` 且困意不高、无严重饥饿：优先 `rest`，不自动建议睡觉。
- 夜间 22:00–07:00 仍综合昼夜节律决定睡眠，不单靠数值。
- 可选实验 WebM（若放进本机角色素材文件夹）：`补充体力-喝热茶`、`恢复元气-轻轻伸展` 属于 `rest`，`番茄钟-专注读书` 属于 `work`。
