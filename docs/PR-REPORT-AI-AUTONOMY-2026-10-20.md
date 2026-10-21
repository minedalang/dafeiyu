> **历史开发报告（非最新运行机制）**：本文件保留早期提交记录。当前默认值、持续活动、独立搭话、双击和离线睡眠请从 [机制文档索引](README.md) 查阅。

# PR 报告：大肥鱼 AI 自主行为（2026-10-20，工作区修改版）

基线：`MerZlin/dsh-pet-indesktop` 的本次本地克隆（`147170f`）；参考 `NatBrian/mochi-llm-pet` 的「环境摘要 → 模型选择高层动作 → 有界执行器执行」理念。未提交上游 PR，未声明 Windows 已实机验收。用户操作文档见 [`AI-AUTONOMY.md`](AI-AUTONOMY.md)，该历史报告曾引用 `SETTINGS-CHANGE-GATES.md`（当前测试源码包未包含该文件）；现行配置可看 [机制速查](MECHANISM-REFERENCE.md)。

## 修改文件说明

| 文件 | 用途 |
|---|---|
| `pet/autonomy_intent.py`（新） | 定义 8 类允许的动作、动画名校验、受限 JSON 解析和环境摘要提示词，不允许模型给出系统命令 |
| `pet/autonomy.py`（新） | Qt 单飞后台调用、定时器、独立识屏授权、失败与过期结果丢弃；复用原 Chat API 配置 |
| `pet/app.py` | 两种显示拓扑的状态采样、模型意图派发、设置刷新与停机收口 |
| `pet/sprite_behavior.py` | AI 模式暂停自动随机选行为；执行已有动画、水平靠近/远离和转身；本地交互继续优先 |
| `pet/window.py` | 旧式窗口的 AI 方向移动及随机动作互斥，提供公开动作入口 |
| `pet/overlay_shell.py` | 合成层每个宠物同步 AI 自主状态 |
| `pet/config.py`, `pet/chat/models.py` | 默认关闭的三项安全配置及旧文件读取兼容 |
| `pet/chat/ai_settings_page.py` | 自主总开关、独立识屏授权和 20–300 秒频率项；沿用原保存流程 |
| `tests/test_autonomy_intent.py`（新） | 白名单协议、动作与动画验证测试 |
| `tests/test_autonomy_service.py`（新） | 关闭时零调用、单飞、迟到结果、截图隐私、合成层执行测试 |
| `tests/test_autonomy_settings.py`（新） | Qt 离屏设置页与配置持久化、非法布尔字段拒绝测试 |
| `tests/test_sprite_behavior.py` | 原控制器随机暂停和光标定向行为回归测试 |
| `README.md`, `docs/AI-AUTONOMY.md`（新）, `docs/INDEX.md`, `docs/SETTINGS-CHANGE-GATES.md` | 用户入口、文档索引、设置契约与故障恢复说明 |
| `windows/_conda_setup.bat`, `1-Install-Windows.bat`, `2-Run-Windows.bat`, `3-Build-Windows-EXE.bat`, `WINDOWS-AI-TEST.md`（新） | Windows 独立 Conda 环境初始化、固定解释器启动、调用原版 onedir Chat 变体打包脚本；解决原版 `run.bat` 绕过 Conda 的入口问题 |

没有改动现有动画资源，也没有把文本模型输出作为代码执行。与 Mochi 并非逐行相同：模型控制高层动作，本地保持碰撞、点击拖拽、渲染与动作边界。

## 性能分析

- 定时决策范围 **20–300 秒**，默认 40 秒；模型请求只容许一个在途，超过 18 秒超时；Qt 主线程只执行简短采样、显示与动作派发。
- 通过 CPython `timeit`（Linux 本工作区，`20000` 次，`3` 轮，best）：解析 `{"verb":"idle"}` 为 **1.9 µs/次**。此值仅覆盖纯 JSON 校验，不是完整模型调用或帧渲染数据。
- 单次截屏仅在单独授权下开始，JPEG 长边限制 768px；未打开识屏时不执行截屏，也不会发送图片。
- 40 秒间隔理想极限是 **2160 次/天**；请求实际频率、token 与 API 费用随网络与服务商变化。长时运行的实际 CPU、内存、帧率和费用**未测量**，不得从解析微基准推断。

## 实机运行记录

- 当前环境：**Linux，`QT_QPA_PLATFORM=offscreen`**；已构造真实 `QApplication` 和设置页并保存、重读配置，非 Windows 可见桌面。
- 新增的协议、服务、设置与原行为控制器专项用例已跑通；`tests/test_architecture.py` **16 passed**；设置页及配置迁移族 **58 passed / 2 skipped**；原聊天族在受限沙箱执行 **113 passed / 1 skipped / 1 socket 权限失败**，该失败用例按允许联网的环境单独重跑 **1 passed**。
- 最后一次联合回归（协议、服务、设置、行为、配置模式、架构和设置迁移）**212 passed / 2 skipped / 3 subtests passed**；`ruff check pet/ tests/` 和 `git diff --check` 均成功。
- 另一个原 overlay 生命周期用例在 Linux/offscreen 持续未返回，已停止，**不声称此用例通过**。Windows 上的桌面移动、动画观感、跨屏、打包和长时费用待用户实机复验。
- 修改版范围含 Chat 变体；上游已发布的 EXE 不包含本次代码。回退方法是在设置里关闭自主模式，恢复原有随机行为。
