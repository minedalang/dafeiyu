# v4 · 大肥鱼三路 AI 配置与 OpenClaw 执行权限

## 界面

- **AI 与对话 → API 列表、AI 对话 · 模型与连接**：管理 Provider、API 地址、协议、Key、角色 Prompt；旧的「AI 对话 · 深度思考与联网」分区不再在设置页显示。
- **AI 聊天窗口顶部**：模型 ID（可编辑）、思考强度（默认/关闭/Low/Medium/High/XHigh/Max）、联网搜索开关，以及「搜索设置…」对话框（引擎、Key、条数、正文抓取）。
- **AI 自主行为**：明确选择一个已经存在的 API Provider，模型标识、思考设置独立，不能再选择跟随聊天。旧配置无自主 Provider 时，首次读取将当前活跃 Provider 固定为自主 Provider。更换对话 Provider 不影响自主行为。
- **电脑 Agent · OpenClaw**：Agent ID、后端 `provider/model`、Agent 思考深度，及 Gateway URL/Token/受限 Windows 工具白名单。

## OpenClaw Agent 模型与推理

- HTTP `model=openclaw/default` 选择 Agent ID；独立的「后端大模型」通过 `x-openclaw-model` HTTP 请求头覆盖，仅本次请求生效。
- 「Agent 思考深度」使用 OpenClaw `/think:<level>` **行内指令**，不修改 Gateway 或其他会话的默认级别。OpenClaw 会校验当前模型可用等级；如果你使用自定义 4Router Provider，出现只支持 default/off/ultra，选择 High/Medium 仍可能被拒绝。必须在 OpenClaw 配置中正确声明支持等级或换用受支持模型。
- Gateway Token 不是大模型 API Key；大肥鱼不对 OpenClaw 配置或轮换该 Key。

## 默认执行权限（务必在 OpenClaw 设置）

执行权限属于 OpenClaw 的**工具执行安全策略**，不是模型思考深度。建议在 Gateway 控制面板「Nodes → Exec approvals」选择 `Gateway / Defaults`，使用 `ask`（白名单自动放行，其他命令需确认），而不是 `full`。亦可在 PowerShell 设置：

```powershell
openclaw config set tools.exec.mode ask
openclaw gateway restart
openclaw exec-policy show
openclaw approvals get
```

注意 OpenClaw 的执行权限是两层：`tools.exec.mode` 和宿主机审批规则，后者可以更严格；在没有可用审批界面的情况下，默认拒绝需要确认的动作。网页聊天的会话级权限不保证传到桌宠的 `/agent` HTTP 会话，因此不要仅在浏览器聊天窗口授权。

`windows_allowlist` **只约束大肥鱼自带的四个 Windows MCP 工具**，不对 OpenClaw 自己的 `exec`、browser、文件工具生效；OpenClaw 后端的白名单/审批也需单独配置。

## 覆盖源码

此补丁覆盖 `pet/chat/models.py`、`pet/chat/widgets.py`、`pet/chat/legacy_widgets.py`、`pet/quick_chat.py`、`pet/chat/modern_styles.qss`、`pet/chat/ai_settings_page.py`、`pet/modern_settings_dialog.py`、`pet/desktop_agent.py`，无安装脚本。先运行源码版（`2-Run-Windows.bat`）测试；已打包的 EXE 需重新构建才有新界面。

本环境验证了纯 Python 模型/传输测试、源码语法；没有 Windows 图形环境、真实 OpenClaw Gateway 或 EXE 打包环境。推荐在你的 Windows 机上实际检查主对话窗设置与 OpenClaw `/agent` 请求。

官方：
- https://docs.openclaw.ai/tools/exec-approvals
- https://docs.openclaw.ai/gateway/openai-http-api
- https://docs.openclaw.ai/slash-commands
