# API 请求协议：手动选择、迁移与请求格式

> 基线：2026-10-21。适用于「显式协议选择」补丁；旧程序没有这个设置项。

## 设置位置与支持范围

**桌宠设置 → AI 与对话 → 模型与连接 → API 请求协议。** 每个 Provider 分别保存 `api_protocol`。不设「自动识别」。修改后点「保存并退出」并重启桌宠。

| 选项 | 配置值 | 请求路径（当 API 根地址为 `https://4router.net/v1` 时） | 思考强度 JSON |
| --- | --- | --- | --- |
| OpenAI Chat Completions | `openai_chat` | `/v1/chat/completions` | `reasoning_effort: "high"` |
| OpenAI Responses | `openai_responses` | `/v1/responses` | `reasoning: {"effort":"high"}` |
| Anthropic / Claude Messages | `anthropic_messages` | `/v1/messages` | `output_config: {"effort":"high"}` |

上表的思考参数只有在「深度思考 = 开启」且输入框不为空时才按需发送。各模型支持的实际取值由模型及网关负责校验；例如某服务未提供 `/v1/responses` 时，手动选择它就会返回相应 404/400，不会自动回退到其他协议。

**重要：协议不是模型名。** Claude 模型也可能由某些网关通过 OpenAI Chat Completions 兼容接口提供；应以该网关的实际 API 文档和地址为准。不能单凭模型名认定协议。

## 原有配置升级

早期配置没有 `api_protocol` 字段。第一次读入旧 Provider 时仅为兼容历史行为做一次迁移：

- `4router.net` / `api.anthropic.com` 上以 `claude-` 开头的模型，或旧地址明确以 `/v1/messages` 结尾 → 迁移为 **Anthropic / Claude Messages**。
- 其他旧 Provider → 迁移为 **OpenAI Chat Completions**。

程序保存后，配置中含 `api_protocol` 字段，之后**不会因模型名或域名变化而擅自切换**。截图中的 `https://4router.net/v1` + `claude-haiku-5-5` 属于第一种迁移情况，安装后应显示 Anthropic / Claude Messages。升级后请仍先打开设置确认选项，再测试连接。

## Provider 选择与协议隔离

API 请求协议属于每个 Provider 配置项。现代 AI 对话窗口另有「聊天 API」选择器，选择某个已配置 Provider 后，会同时使用该 Provider 的 `base_url`、`api_protocol`、API Key、超时和生成参数。它不读取设置页 API 列表当前的 `active_provider` 作为请求路由。

聊天窗口的模型下拉框只列出当前聊天 Provider 的默认模型，避免把另一个 Provider 的模型名套在当前 Provider 的协议上。若要使用 Claude Messages API，请在聊天窗口的「聊天 API」中明确选择配置了 `anthropic_messages` 的 Provider；仅输入 `claude-*` 模型名不会自动改变协议。

## 一致性

- 普通聊天（含流式回答）、快速聊天、聊天相关的屏幕问答与文件解读，使用聊天窗口选定 Provider 的请求适配器。
- AI 自主决策与主动搭话等自主行为流程使用独立的自主 Provider 配置；不跟随聊天窗口的 Provider。
- 联网搜索的工具调用会按所选协议转换：Chat Completions `tool_calls`、Responses `function_call`、Anthropic `tool_use`。
- System Prompt 在 Anthropic 请求中转到顶层 `system`；在 OpenAI Responses/Chat 中使用对应的消息格式。
- 响应解析支持 Chat Completions `choices`、Responses `output` 和 Anthropic `content`。
- **自主决策和主动搭话**保留原有低延迟、短输出策略，不必完全跟随常规聊天的用户思考强度。
- DeepSeek 官方接口此前的 `thinking.type` 行为保留；一般 GPT Chat Completions 不应额外发送这个 DeepSeek 专用字段。

## 注意与限制

- 「测试连接」只验证连接、权限与基本端点，不验证你的全部思考等级、流式输出和联网工具调用；保存后还需发一条实际消息、观察 AI 决策记录。
- 对于使用另一套独立接口的视觉翻译/专门识屏服务，此次不承诺自动适配；本补丁主要覆盖聊天、自主决策、主动搭话与它们的联网辅助调用。
- 本补丁不包含 API Key；不会覆写你本地的生命状态、动画或 `frameseq` 文件。
- 已通过本地无 Qt/无网络的单元测试；Windows GUI 与真实 4Router 服务仍需实机验证。

## 排错

`messages.0: use the top-level 'system' ...`：Claude Messages 收到了错误的系统提示词格式；先确认对应 Provider 明确选择 Anthropic / Claude Messages、重启程序，并确保安装补丁时覆盖了 `pet/chat/providers.py` 和 `pet/autonomy.py`。

`404` 或 `400 model does not support`：核对你选中的协议与网关支持的路径是否相符，不能仅因模型名字是 Claude 就假定网关提供 Claude 原生接口。

`400 reasoning / effort invalid`：查看「生成参数（高级）」中的原始强度字符串，按服务端文档填写，例如 `low`、`medium`、`high`；无需附带整个 JSON 对象。
