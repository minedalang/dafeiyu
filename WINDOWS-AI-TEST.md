# 大肥鱼 AI：Windows 可运行测试包

这是**完整源码 + 角色动画 + Conda 启动脚本**的 Windows 测试版，基于
`MerZlin/dsh-pet-indesktop` 提交 `147170f`，并加入 Mochi 风格 AI 决策层。
它**不是已经编译好的 EXE**：当前构建环境是 Linux，无法替用户完成 Windows
PyInstaller 构建和真机验收。若需要免 Python 的 EXE，请在你的 Windows 电脑上
执行下面的第 3 步。

## 双击顺序

1. **`1-Install-Windows.bat`**：查找已安装的 Conda，创建独立的
   `dafeiyu-ai`（Python 3.11）环境，安装 `requirements.txt`。首次需要联网、
   可能需要几分钟。脚本会显示实际使用的 Python 路径。
2. **`2-Run-Windows.bat`**：先激活 `dafeiyu-ai`，再执行 `python -m pet`。
   命令窗口会留着用于显示报错；请不要关掉它。程序启动后到
   「设置 → AI 与对话」配置你自己的 API 地址、Key 和 Chat 模型，
   然后在「AI 自主行为」打开总开关。识屏另有单独开关，默认关闭。
3. **`3-Build-Windows-EXE.bat`（可选）**：在同一 Conda 环境中安装
   PyInstaller，调用项目自己的 `scripts/build_onedir.ps1`，生成带 AI 聊天的
   `webm-chat` Windows 打包版。输出位于 `dist-onedir/`；若预检发现构建所需
   组件缺失，会显示具体错误。该步骤比前两步耗时更长。

## 已有 Conda 环境怎么办？

默认使用新建的隔离环境，以免污染 `base`。如果你已经准备好了一个依赖齐全的环境，
先在 `cmd` 输入：

```cmd
set DAFEIYU_CONDA_ENV=你的环境名
2-Run-Windows.bat
```

如果安装的 Conda 路径特殊，脚本无法自动找到：

```cmd
set DAFEIYU_CONDA_BAT=D:\Miniconda3\condabin\conda.bat
1-Install-Windows.bat
```

上述路径仅是格式示例，需要换成你本机的实际路径。

## 排查

- `No module named PySide6`：运行 `1-Install-Windows.bat`，不要运行上游原版的
  `run.bat`（它直接用 PATH 上的 `pythonw`，可能找错 Python）。
- 桌宠出现、但 AI 不动：确认使用 Chat 版，已在 AI 设置保存 API 和自主开关；
  模型需要能访问 OpenAI 兼容的 `/chat/completions` 接口并返回 JSON。
- AI 动作比较慢：默认每 40 秒决策一次，第一次启用约 2.5 秒后会先尝试请求；
  看不到即时效果时请检查 API 是否能连通。
- 花费/API 隐私：默认不上传屏幕；打开识屏后会将压缩屏幕截图发给视觉模型。
- 想停用：在设置关闭「让 AI 决定桌宠行为」，恢复原有随机行为。

代码与 API 协议的细节见 [`docs/AI-AUTONOMY.md`](docs/AI-AUTONOMY.md)，
测试记录见 [`docs/PR-REPORT-AI-AUTONOMY-2026-10-20.md`](docs/PR-REPORT-AI-AUTONOMY-2026-10-20.md)。
