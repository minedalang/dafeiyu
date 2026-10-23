# 大胖鱼 AI / OpenClaw 设置页面修复（v2）

## 问题根因

先前 OpenClaw v1 在 `pet/chat/ai_settings_page.py` 创建了七个控制字段，但真正展示的设置界面由 `pet/modern_settings_dialog.py::_rebuild_domain_navigation()` 再次重组，未将 `desktop_agent_*` 键登记为展示行。界面无法看到这些设置，与 Token 或 OpenClaw 版本无关。

## 更新后的设置页面

桌宠右键打开 **设置 → AI 与对话**（侧栏）：

1. **AI 对话 · 模型与连接**：聊天用的 Provider、API 协议、模型与 Key。
2. **AI 对话 · 深度思考与联网**：普通对话请求的思考参数及联网配置。
3. **电脑 Agent · OpenClaw**：手动 `/agent` 开关、本机地址（默认 `http://127.0.0.1:18789`）、`openclaw/default` Agent 标识、Gateway Token、可操作程序白名单、连接测试按钮及状态。
4. **AI 自主行为**：独立选择自主 Provider、自主模型名、思考开关及思考强度，另外保留自主开关、识屏白名单与决策/搭话间隔。
5. **AI 决策状态面板**：当前后台使用的模型以及最近决策状态。

### 自主行为的 Provider 与模型配置

- `自主行为使用的 API` 默认 **跟随 AI 对话（兼容旧设置）**。可改选 API 列表中任意已保存的 Provider，沿用它的 API 地址、协议与 Windows 凭据中的 Key。
- `自主决策与搭话模型` 留空 = 使用上述 Provider 的默认模型；填写具体模型名 = **仅覆盖自主决策与主动搭话/点击响应**，不更改该 Provider 的聊天模型。
- `自主行为深度思考` 与 `自主行为思考强度` 都可独立覆盖；不填写或选继承时沿用该 Provider。
- 如果你想让自主行为使用完全不同的 Key/地址/协议，先在“API 列表”添加第二个 Provider，再回“AI 自主行为”选择它。
- 老配置不会丢失：未设置独立模型的桌宠依旧使用原先的聊天 Provider。
- 如果删除了自主专用 Provider，运行时自动回退到当前聊天 Provider，不会因为旧 Provider ID 而崩溃。

### OpenClaw

OpenClaw Gateway 和大胖鱼使用**独立模型/鉴权配置**；设置里的“Agent 标识”是 OpenClaw 模型入口名，不是你的 GPT 模型名。

- `/agent 你好` 才会进入外部 Agent；普通聊天不会自动调用 OpenClaw。
- OpenClaw Gateway 需自行在本机运行，且要启用 Chat Completions HTTP 入口。
- Gateway Token 使用系统凭据存储；不要填写 4Router API Key。
- “允许操作的程序”是本补丁 Windows MCP 工具白名单，不等于限制 OpenClaw 自身所有其他工具；那些工具还须在 OpenClaw 中单独限权。

## 覆盖方式

- 已覆盖过 OpenClaw v1：下载 **v2 热修补丁**，把 `pet` 目录覆盖到项目根目录即可。
- 尚未安装 v1 或不确定哪些文件已覆盖：使用 **v2 累计补丁**，它包含 v1 全部 Agent 源码和本次修复。
- 两个补丁都只含源码/文档/测试，不含脚本或用户密钥；不修改 `scripts/build_onedir.ps1`。
- 使用源码版重新启动桌宠与设置窗口，验证后再重新 build EXE。**已经 build 出的旧 EXE 不会自动读取你刚覆盖的 Python 源码。**

## 测试范围

离线 Python 自动化验证 ChatSettings 新字段的读写、模型选择隔离、删除 Provider 回退以及现代设置域中全部行键的登记；上一版 OpenClaw 桥接的七项测试也重新执行。Linux 环境不具备 PySide6，因此**尚未在真实 Windows 窗口中打开设置界面**；也未连接你本机的 OpenClaw Gateway 或实际构建新的 EXE。
