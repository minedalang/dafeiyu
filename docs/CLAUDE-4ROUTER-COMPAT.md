# Claude 与 4Router 接入及思考深度

## 适用范围

桌宠聊天窗口、AI 自主决策、主动搭话、点击回应共享所选 Provider，但调用链不同。本补丁修正自主决策和主动搭话直接向 Claude 原生 Messages API 发送 OpenAI `messages[0]` 系统角色的协议错误。

## 配置

大胖鱼设置 → AI 与对话 → API 列表，建议为 Claude 单独建立一条 Provider，避免切换 GPT 时保留不匹配的地址。

| 字段 | 4Router Claude 原生接口 |
| --- | --- |
| API 地址 | `https://4router.net` |
| 模型 | 选择你在 AppClaude 分组确实有权限的标识，如 `claude-sonnet-5` |
| API Key | 使用你自己的、支持 AppClaude 的 4Router Key |
| 深度思考 | 「跟随模型默认」最稳妥；启用后可直接输入 low / medium / high / xhigh / max 或其他模型要求的合法值 |

**安装显式协议选择补丁后**，请在「模型与连接 → API 请求协议」选择 **Anthropic / Claude Messages**。以 `https://4router.net/v1` 为根地址时，请求发往 `https://4router.net/v1/messages`，系统提示词以顶层 `system` 发送。详情见 [API 请求协议](API-PROTOCOL-SELECTION.md)。

不要把完整 API Key 贴给任何人，也不要在截图中展示它。

## 参数实际效果

| 模型/任务 | 请求特点 |
| --- | --- |
| Claude 5.x / 支持 effort 的 Claude 4.6 | `output_config.effort`；不发送 DeepSeek `thinking.type` |
| Claude 原生模型，「跟随默认」 | 不附加 effort |
| Claude 原生模型，「开启」+ 自定义输入 | 按原始字符串传入 `output_config.effort`，例如 `medium` 或 `xhigh`；合法性由服务端判断 |
| Claude 原生模型，「关闭」 | 尽量使用 `low`，不保证彻底禁止内部推理 |
| 不支持 effort 的旧 Claude | 「跟随默认」不附加 effort；手动输入则会传给服务端，可能返回 400 |
| AI 自主决策与独立搭话 | 为低延迟优先使用 `low`；独立聊天按设置的思考强度 |
| DeepSeek / 原来的 OpenAI 兼容调用 | 保留原来参数行为，无需迁移配置 |

## 本次 Bug 根因

Claude Messages 原生 API 不允许请求初始 `messages[0]` 携带有文字内容的 `system` 角色。之前 `pet/autonomy.py` 中两个非流式请求直接使用 OpenAI `_open()`，而聊天窗口已经有 `_to_anthropic_payload()` 的协议转换。

现在自主决策与搭话统一调用 `OpenAICompatibleProvider._complete_json()`；按 Provider 明确选定的协议转换并解析响应。Claude Messages 分支会提取顶层 `system`、转换角色/图片格式、读取 `content` 文本数组。

## 检查步骤

1. 关闭桌宠，安装补丁并重启。
2. 在「模型与连接」中检查 Base URL、模型 ID，点击「测试连接」。
3. 聊天发送「你好」，检查是否能正常回答。
4. 打开「AI 自主行为」，查看「AI 决策状态面板」是否出现模型决策记录；自主搭话如有冷却需要等待。
5. 若返回 `401/403` 检查 Key；`model_not_found` 检查模型/分组；若仍返回 `messages.0`，确认运行的是新补丁并检查 API 实际发送到了哪个地址。

本补丁不包含 API Key、不替换 `whale-life-state.json`，也不修改动画资源。网络/网关在线结果无法在离线测试环境保证。

官方协议： https://platform.claude.com/docs/en/api/messages/create
4Router 配置： https://docs.4router.net/guides/manual-config/


## 2026-10-21 自定义原始思考强度

在「AI 与对话 → 模型与连接 / 生成参数」中，选择“开启深度思考”，在「思考强度（原始参数）」文本框里直接填写模型要求的**字符串**。例如 Claude Haiku 5.5 支持 `low`、`medium`、`high`、`xhigh`、`max`，其中 `medium` 为官方默认。用户输入 `xhigh`，native Claude 请求发送：

```json
{"output_config": {"effort": "xhigh"}}
```

- 保存后原文保留；切换不同 Provider 时每个 Provider 分别保存自己的值。
- 「跟随模型默认」不发送额外强度；「开启」+ **留空**也不发送强度。
- 明确选择 Chat Completions 时使用 `reasoning_effort`，选择 Responses 时使用 `reasoning.effort`；DeepSeek 官方接口才额外保留 `thinking.type`。并非每个 OpenAI 兼容网关都支持此字段；遇到 400 请依网关官方文档调整。
- 旧 Claude 的自定义值也会发送，由服务端检查是否支持；不再静默降级成 high。
- **后台自主行为/主动搭话**之前为了减少延迟使用内置 `low` 低思考强度；此处设置首先影响聊天窗口，后台策略不在本次补丁中变更。
- 「关闭深度思考」不是自定义等级值；对于支持 effort 的 Claude 模型，这一模式沿用低强度策略。
- 官方参考：https://platform.claude.com/docs/en/build-with-claude/effort

**注意：**「测试连接」目前只测试 API 端点与 Key 是否可用，并不验证用户输入的 effort 值。保存后请再发一条正常聊天消息确认；若模型不支持该值，服务端会返回 HTTP 400。
