# 大胖鱼 × OpenClaw：电脑 Agent 联动（首版）

> 这份补丁为**源码级**联动。默认关闭。OpenClaw 必须作为独立程序自行安装和运行，桌宠不会暗中启动、安装或提权。GUI / EXE 打包还需在 Windows 上验收。

## 交付能力

1. 现代聊天、经典聊天、快速聊天均可在开头输入 `/agent 你的任务` 把**这一条明确的任务**发送给 OpenClaw；普通聊天继续走原有 GPT / Claude/4Router。空 `/agent` 不会启动操作。
2. 共用原来的 `ChatService` 工作线程、流式输出、动作反馈、停止按钮、会话存档和完成气泡。Agent 使用 `dafeiyu:<对话 ID>` 作为独立的 OpenClaw 会话标识，后续同一对话的 `/agent` 请求可延续 Agent 上下文。
3. 默认启用“为 Agent 整理聊天背景”时，仅当用户明确输入 `/agent` 才会触发：最近最多 20 条纯文本由大肥鱼当前聊天模型压缩，发送给 OpenClaw 的只有短摘要，不发送完整聊天记录、附件、截图或 API 凭据。可在 AI 设置中关闭该选项；关闭后只发送 `/agent` 后的任务原文。摘要仅供参考，本次任务优先。Agent 内容在显示的聊天记录中保存（遵循桌宠原有聊天保存机制），请不要在任务文本中粘贴密码。
4. 电脑 Agent 设置：启用开关、本地 Gateway、Agent ID、专用凭据、单独的 Windows EXE 操作白名单，以及无副作用的连接测试。
5. 可选 Windows **受限 MCP 工具服务**：查看允许的窗口、激活窗口、打开系统记事本、向已授权前台窗口输入普通文本。它无法任意执行 shell、删除文件、点击任意屏幕坐标或直接保存文件。
6. 断开 OpenClaw 不影响正常聊天/生活系统。桌宠关闭或点击停止时关闭当前 HTTP 流并请求停止；**不能撤销 Agent 已完成的外部操作**。

## 准备 OpenClaw（只需配置一次）

确认你已在 **Windows 本机**安装 OpenClaw 并让 Gateway 运行。模型 Provider、4Router 网址和 4Router API Key 在 OpenClaw 内独立配置；桌宠这边填的是 **OpenClaw Gateway Token**，不是 4Router Key。

通过 OpenClaw 官方设置或其 CLI 开启 Chat Completions 接口：

```powershell
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
```

检查 Gateway 正在运行，并在需要时按 OpenClaw 提示重启 Gateway。默认端口为 `18789`。在 OpenClaw 中选择自己的 4Router 自定义 Provider / 模型。首次应在 OpenClaw 自己的 UI 中试通简单任务，确保 Agent 工具授权策略生效。

官方文档：
- https://docs.openclaw.ai/gateway/openai-http-api
- https://docs.openclaw.ai/gateway/local-models
- https://docs.openclaw.ai/tools/mcp
- https://docs.openclaw.ai/tools/exec-approvals

## 在大胖鱼启用

打开 **AI 设置 → 电脑 Agent（OpenClaw，默认关闭）**：

- 打开「允许手动 Agent 任务」。
- Gateway URL：`http://127.0.0.1:18789`，**只能连接本机**；其他机器/公网地址将被拒绝。
- Agent 标识：`openclaw/default`（这是 Agent ID，不是 GPT 模型 ID）。
- Gateway Token：填写 OpenClaw 的 Gateway Token，然后**保存**。只用 Windows keyring 系统凭据持久化；`config.json` 不存 Token。
- 「测试 OpenClaw 连接」会查询 `/v1/models`，不让 Agent 执行任何任务。
- 「允许操作的程序」一开始保持为空，先验证纯聊天任务。

在聊天窗输入：

```text
/agent 请告诉我你能使用哪些工具。先不要操作电脑。
```

必须显式加 `/agent`。普通聊天“你好”“帮我写诗”不会被桌宠自动代理到 OpenClaw。Agent 模式与自主识屏白名单相互独立。

> 安全存储不可用：Token 不会降级明文写盘。可改为启动桌宠前在自己的用户环境设置 `OPENCLAW_GATEWAY_TOKEN`，不要放进 Git 项目文件。分别运行的桌宠进程必须能访问相同用户环境。

## 可选：向 OpenClaw 注册 Windows MCP 工具

MCP 工具是**额外启用**的组件。只开启大胖鱼的 Agent，不会自动授予任何 Windows 桌面控制。

首先在大胖鱼 AI 设置的「允许操作的程序」加入：

```text
notepad.exe
```

保存设置。Windows MCP 默认从 `%APPDATA%\dsh-pet-standalone\config.json` 读取源码运行时的白名单；打包 EXE 通常保存在不同目录，例如：

```text
%APPDATA%\dsh-pet-standalone-webm-chat\config.json
```

因此 EXE 用户必须用 MCP 的 `--config` 参数，或设置 `DAFEIYU_AGENT_CONFIG` 为真实绝对配置文件路径。

推荐先在 Conda 中手动自检（不会主动打开任何窗口）：

```powershell
cd G:\WorkSpace\WorkspacePython\dafeiyu-ai-windows-conda-test
python -m unittest discover -s tests -p test_desktop_agent_bridge.py -v
```

Windows MCP 是 **stdio 服务**，真正执行任务应由 OpenClaw 启动，而不是直接双击。OpenClaw Control UI → Settings → MCP → Add server：

- 名称：`dafeiyu-windows`
- 传输：Stdio
- 命令：你已有 Python 环境的 `python.exe` 绝对路径
- 参数：`-m`, `pet.windows_agent_mcp`。如果是 EXE 配置再追加 `--config`, `<实际 config.json 的绝对路径>`
- Working directory：大胖鱼源代码根目录（包含 `pet/` 文件夹）

也可以按当前 OpenClaw CLI 文档使用：

```powershell
openclaw mcp add dafeiyu-windows --command "C:\path\to\python.exe" --arg=-m --arg=pet.windows_agent_mcp --cwd "G:\WorkSpace\WorkspacePython\dafeiyu-ai-windows-conda-test"
openclaw mcp doctor dafeiyu-windows --probe
```

> EXE 用户请自行将 `--arg --config --arg "C:\Users\...\AppData\Roaming\dsh-pet-standalone-webm-chat\config.json"` 加到 OpenClaw 的 MCP 命令，或用 UI 配置参数。无须修改 `.exe` 构建文件或在 EXE 内安装 Node.js。MCP 工具以普通 Windows 用户运行，不能控制管理员权限窗口。**不要赋予 OpenClaw 无限系统命令执行权限**。本补丁的 Windows 白名单只约束这四个新增 MCP 工具，并不约束 OpenClaw 原生的 `exec`、browser 等其他工具；必须在 OpenClaw 工具策略中独立禁用或要求审批。

测试：

```text
/agent 请只打开记事本并输入“你好，深深”。不要保存文件，不要运行 PowerShell。
```

工具限制：发送文本前再次核查前台窗口与 EXE 白名单（防止误打到刚切出的其他窗口）；不支持热键、任意点击、发送消息、写磁盘或自动确认授权。第二阶段才适合增加 Windows UI Automation 控件定位、保存前审阅与操作前二次确认。

## 隐私与权限边界

- OpenClaw HTTP Gateway Token 等同于操作员凭据，请只用本机、不要公开代理端口或上传 Token。
- 用户显式 `/agent` 仅代表发起这次任务；**它不是对任意高风险动作的一揽子批准**。删除、发送、外部转账、覆盖文件等工具应该在 OpenClaw 侧配置严格审批，或保持不可用。
- 电脑操作白名单**不等于**自主识屏白名单；给 `spotify.exe` 识屏授权不会授权输入/控制。
- Windows MCP 按 EXE 文件名而非数字签名授权；仅推荐给可信应用授权。
- 停止流式请求后，已执行的鼠标、键盘、外部操作不会自动回滚。
- 此版可以把任务交给 OpenClaw，并通过聊天流反馈**文本阶段**；不保证在桌宠里逐条显示外部工具的真实底层事件/审批交互。细粒度工具事件需另接 Gateway WebSocket Event/RPC，并做独立审批 UI。

## 排错

- `401/403`：确认 Gateway Token，不是 4Router Key；OpenClaw 的 Auth mode 需支持 Bearer。
- `404`：确认已在 OpenClaw 打开 Chat Completions HTTP 接口，而不是只启用 Responses。
- 连接测试通过但 `/agent` 失败：OpenClaw 默认模型/4Router 配置或 Agent 工具权限错误，请查 Gateway 日志。
- 无法发送文字：检查 `windows_allowlist` 是否包含目标 `.exe`，且窗口当前位于前台。
- MCP 没有工具：检查 `mcp doctor --probe`、Python 路径、工作目录和 `--config`。
- 网关打开了但桌宠仍无法使用：确认原有 EXE 打包对应代码与当前补丁一致；旧 EXE 不会自动包含这些新模块，需要重新构建。

## 自动化验收

在有 Python 3.11+ 的项目根目录运行：

```powershell
python -m unittest discover -s tests -p test_desktop_agent_bridge.py -v
```

这个测试用本机假的 HTTP Gateway 验证 `/agent` 路由、会话隔离、SSE 拼接、Token 不外泄、不允许远程网关，以及受限 MCP 的 JSON-RPC 协议。不需要联网、不需要真实 API Key。


## /agent 聊天背景摘要

“为 Agent 整理聊天背景”默认开启，仅在用户明确发送 `/agent ...` 时运行。它最多选择最近 20 条普通文本消息，交给大肥鱼当前聊天模型生成短摘要；发给 OpenClaw 的是“本次任务原文 + 历史摘要”，不是完整聊天记录。附件、截图、思考过程和凭据不会作为摘要输入。可在 AI 设置中关闭此选项，关闭后只发送任务原文。

摘要会优先保留用户最新纠正、明确约束、已确认事实和仍待核实的信息；旧摘要会被当作二手材料而不是事实。若摘要模型请求失败，任务仍会继续，但改用明确标记的近期对话摘录。历史摘要不能扩大本次授权，也不能覆盖本次 `/agent` 任务。
