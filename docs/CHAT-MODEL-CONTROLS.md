# AI 对话 API / 模型选择与自主决策隔离

## AI 对话窗口

现代 AI 对话窗口顶部有独立的 **聊天 API**、**模型 ID** 和 **思考深度** 控件。

- **聊天 API**：选择一个已在「桌宠设置 → AI 与对话 → 模型与连接」配置好的 Provider。
- **模型 ID**：默认只列出当前聊天 API 的模型；也可以手动输入该 API 服务商支持的模型 ID。
- **思考深度**：仅覆盖普通 AI 对话的 thinking / reasoning effort，不改写 Provider 的保存值。
- 当前聊天 API 的 `base_url`、`api_protocol`、API Key 引用、超时、温度、最大输出等参数都从该 Provider 读取。
- 聊天窗口选择与设置页 API 列表的 `active_provider` 完全分开。设置页切换当前编辑/激活项，不会改变聊天窗口的 Provider。
- 聊天窗口切换 Provider 时会清除之前的模型覆盖，改用新 Provider 配置的默认模型，避免把 Claude 模型名带到 GPT API 上。
- 模型下拉框不会列出其他 Provider 的模型，防止只换模型字符串却仍沿用另一 API 的协议、URL 和凭据。手动输入模型 ID 时，请确认该模型由当前聊天 API 提供。
- 联网搜索设置仍是聊天设置中的独立选项；OpenClaw 有自己独立的工具和联网配置。

## 旧配置迁移

- 旧配置没有 `chat_provider` 时，如果 `chat_model` 与某一个 Provider 的默认模型完全匹配，会自动绑定该 Provider。
- 如果没有唯一精确匹配，则使用原 `active_provider`，以保留原有行为。模型名本身不用于猜测协议，因为第三方网关也可能通过 OpenAI 兼容协议提供 Claude 模型。
- 迁移后的 `chat_provider` 会保存到配置中。后续切换设置页 API 列表不会覆盖它。
- `chat_model` 若等于该 Provider 的默认模型，会规范化为空覆盖值；这样修改 Provider 默认模型后，聊天窗口能自然跟随。

## AI 自主行为与 OpenClaw

- **AI 自主行为**使用独立的 `autonomy_provider`、`autonomy_model` 和对应思考参数。
- **OpenClaw Agent** 使用 OpenClaw 自身配置或其专用设置；不会因为切换普通聊天 API 而修改 OpenClaw 的全局默认模型。
- 普通聊天、快速聊天、聊天相关的「看看屏幕」和文件解读使用聊天窗口选择的 Provider。
- API 列表当前选中项仍用于设置页编辑、余额查询等 Provider 管理功能。

## 使用步骤

1. 在设置页配置好 GPT 和 Claude 两个 Provider，并分别保存正确的 API 地址、Key 和 API 请求协议。
2. 在 AI 对话窗口顶部的「聊天 API」中选 Claude，再选/输入 `claude-haiku-5-5`。
3. 设置页的 API 列表即使仍选中 GPT，也不会改变聊天请求的 Claude Messages 协议、地址或凭据。
4. 反向测试：聊天 API 选 GPT，而设置页 API 列表选 Claude；聊天请求仍应走 GPT Provider 的配置。
