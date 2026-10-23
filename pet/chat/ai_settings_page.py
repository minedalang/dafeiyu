# -*- coding: utf-8 -*-
"""Modern settings AI 设置页 _AiSettingsPage。

从 modern_settings_dialog.py 纯机械搬移。现位于 pet/chat/ 下，原 `from .chat.xxx`
相对导入改为 `from .xxx`（级别变化）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..settings_widgets import (
    ModernSelect,
    _line_edit,
    BrowserSpinBox,
    BrowserDoubleSpinBox,
    ToggleSwitch,
    ResourcePathPicker,
    ResponsiveActionRow,
    SettingsSection,
    SettingRow,
    SettingsCard,
)
from .themes import CHAT_UI_STYLE_LABELS, CHAT_UI_VIEW_ASPECT, theme_names
from .utils import _safe_emit
from ..autonomy_speech import speech_interval_bounds


class _AiSettingsPage(QWidget):
    test_done = Signal(bool, str)
    agent_test_done = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setObjectName("aiSettingsContent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # No-chat bundles exclude pet.chat and never construct this optional page.
        from .models import ProviderConfig, SecretStore, WEB_SEARCH_KEYED_ENGINES
        from .providers import test_connection
        from .web_search import ENGINE_LABELS

        self.config = config
        self._provider_config_type = ProviderConfig
        self._secret_store_type = SecretStore
        self._test_connection = test_connection
        self.settings = config.chat_settings()
        provider = self.settings.active_config
        self._test_thread = None
        self._provider_drafts: dict[str, dict] = {}
        self._deleted_provider_ids: set[str] = set()
        self._loading_provider = False
        self.test_done.connect(self._on_test_done)
        self.agent_test_done.connect(self._on_agent_test_done)

        self.provider_combo = ModernSelect(self, width=230)
        self.add_provider_btn = QPushButton("添加", self)
        self.delete_provider_btn = QPushButton("删除", self)
        for pid, provider_item in self.settings.providers.items():
            self.provider_combo.addItem(self._provider_label(provider_item), pid)
        self.provider_combo.setCurrentData(self.settings.active_provider)

        self.name = _line_edit(provider.name)
        self.url = _line_edit(provider.base_url)
        self.model = _line_edit(provider.model)
        self.api_protocol = ModernSelect(self, width=260)
        self.api_protocol.addItem('OpenAI Chat Completions', 'openai_chat')
        self.api_protocol.addItem('OpenAI Responses', 'openai_responses')
        self.api_protocol.addItem('Anthropic / Claude Messages', 'anthropic_messages')
        self.api_protocol.setCurrentData(provider.api_protocol)
        self.key = _line_edit(password=True)
        self.prompt = QPlainTextEdit(self.settings.default_system_prompt)
        self.prompt.setMinimumSize(240, 80)
        self.timeout = BrowserSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(provider.timeout))
        self.temperature = BrowserDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(provider.temperature)
        self.tokens = BrowserSpinBox()
        self.tokens.setRange(1, 131072)
        self.tokens.setValue(provider.max_tokens)
        self.skip_ssl = ToggleSwitch()
        self.skip_ssl.setChecked(not provider.verify_ssl)
        # 深度思考（按 provider 保存）
        self.thinking_mode = ModernSelect(self, width=230)
        self.thinking_mode.addItem("跟随模型默认", "auto")
        self.thinking_mode.addItem("开启深度思考", "enabled")
        self.thinking_mode.addItem("关闭深度思考", "disabled")
        self.thinking_mode.setCurrentData(provider.thinking)
        # 原始 API effort 参数：由用户直接填写，不限制成 low/high/max。
        # 不在 UI 中翻译等级，否则 medium/xhigh 会被错误地重置成 high。
        self.reasoning_effort = _line_edit(str(provider.reasoning_effort or ""))
        self.reasoning_effort.setMaxLength(64)
        self.reasoning_effort.setPlaceholderText("例如 low / medium / high / xhigh / max")
        # 联网搜索（全局，所有 provider 共用）
        self._web_keyed_engines = WEB_SEARCH_KEYED_ENGINES
        web = self.settings.web_search
        self.web_search_enabled = ToggleSwitch()
        self.web_search_enabled.setChecked(bool(web.enabled))
        self.web_search_engine = ModernSelect(self, width=230)
        for engine_id, engine_label in ENGINE_LABELS.items():
            self.web_search_engine.addItem(engine_label, engine_id)
        self.web_search_engine.setCurrentData(web.engine)
        self.web_search_key = _line_edit(password=True)
        self.web_search_key.setPlaceholderText("已保存则留空；输入新值将覆盖")
        self.web_search_results = BrowserSpinBox()
        self.web_search_results.setRange(1, 10)
        self.web_search_results.setSuffix(" 条")
        self.web_search_results.setValue(int(web.max_results))
        self.web_search_fetch = ToggleSwitch()
        self.web_search_fetch.setChecked(bool(web.fetch_pages))
        self.system_notify_check = ToggleSwitch()
        # 自主行为采用独立的 Provider/模型覆盖；旧配置默认跟随聊天模型。
        # 截图是另一项独立同意，不会因打开自主行为而自动上传屏幕。
        self.autonomy_enabled = ToggleSwitch()
        self.autonomy_enabled.setChecked(bool(self.settings.autonomy_enabled))
        self.autonomy_screen = ToggleSwitch()
        self.autonomy_screen.setChecked(bool(self.settings.autonomy_screen_enabled))
        self.autonomy_whitelist = QPlainTextEdit(self)
        self.autonomy_whitelist.setPlaceholderText("每行一个可识屏的程序名，如 code.exe、chrome.exe；不支持通配符。空白名单不截屏。")
        self.autonomy_whitelist.setPlainText("\n".join(self.settings.autonomy_screen_whitelist))
        self.autonomy_whitelist.setFixedHeight(90)
        self._autonomy_edited: set[str] = set()
        self.autonomy_provider_combo = ModernSelect(self, width=260)
        self._refresh_autonomy_provider_options(self.settings.autonomy_provider)
        self.autonomy_model = _line_edit(self.settings.autonomy_model)
        self.autonomy_model.setPlaceholderText("留空使用上方 Provider 自带的模型；填写后只影响自主行为")
        self.autonomy_thinking = ModernSelect(self, width=230)
        self.autonomy_thinking.addItem("沿用自主 Provider 的思考设置", "")
        self.autonomy_thinking.addItem("跟随模型默认", "auto")
        self.autonomy_thinking.addItem("开启深度思考", "enabled")
        self.autonomy_thinking.addItem("关闭深度思考", "disabled")
        self.autonomy_thinking.setCurrentData(self.settings.autonomy_thinking)
        self.autonomy_effort = _line_edit(self.settings.autonomy_reasoning_effort)
        self.autonomy_effort.setMaxLength(64)
        self.autonomy_effort.setPlaceholderText("留空沿用自主 Provider；例如 low、medium、high")
        # Desktop Agent: standalone operator Gateway, never reuses ordinary AI API key.
        from ..desktop_agent import normalized_settings
        agent = normalized_settings(config.get('desktop_agent', {}))
        self.agent_enabled = ToggleSwitch()
        self.agent_enabled.setChecked(agent['enabled'])
        self.agent_url = _line_edit(agent['gateway_url'])
        self.agent_url.setPlaceholderText('http://127.0.0.1:18789')
        self.agent_model = _line_edit(agent['agent_model'])
        self.agent_model.setPlaceholderText('openclaw/default')
        self.agent_backend_model = _line_edit(agent['backend_model'])
        self.agent_backend_model.setPlaceholderText('如 4router/模型ID；留空使用 OpenClaw 默认模型')
        self.agent_thinking = ModernSelect(self, width=240)
        for label, level in [('使用 OpenClaw 默认', 'default'), ('关闭思考', 'off'), ('Low', 'low'), ('Medium', 'medium'), ('High', 'high'), ('XHigh', 'xhigh'), ('Max', 'max'), ('Ultra', 'ultra')]:
            self.agent_thinking.addItem(label, level)
        self.agent_thinking.setCurrentData(agent['thinking_level'])
        self.agent_context_summary = ToggleSwitch()
        self.agent_context_summary.setChecked(agent['include_chat_context'])
        self.agent_key = _line_edit(password=True)
        self.agent_key.setPlaceholderText('已保存则留空；不会写入 config.json')
        self.agent_allowed_windows = QPlainTextEdit(self)
        self.agent_allowed_windows.setPlainText('\n'.join(agent['windows_allowlist']))
        self.agent_allowed_windows.setPlaceholderText('每行一个可操作程序名，如 notepad.exe。空白名单禁止全部 Windows 工具。')
        self.agent_allowed_windows.setFixedHeight(72)
        self.agent_test_button = QPushButton('测试 OpenClaw 连接', self)
        self.agent_test_result = QLabel('测试只读取 Agent 列表，不运行任务。', self)
        self.agent_test_result.setWordWrap(True)
        self._agent_test_thread = None
        self._agent_edited = False
        self.agent_enabled.toggled.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_url.textChanged.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_model.textChanged.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_backend_model.textChanged.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_thinking.currentIndexChanged.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_context_summary.toggled.connect(lambda _: setattr(self, '_agent_edited', True))
        self.agent_allowed_windows.textChanged.connect(lambda: setattr(self, '_agent_edited', True))
        self.agent_test_button.clicked.connect(self._run_agent_test)
        self.autonomy_interval = BrowserSpinBox()
        self.autonomy_interval.setRange(20, 300)
        self.autonomy_interval.setSuffix(" 秒")
        self.autonomy_interval.setValue(self.settings.autonomy_interval_seconds)
        speech_min, speech_max = speech_interval_bounds(
            config.get("autonomy_speech_min_minutes", 3),
            config.get("autonomy_speech_max_minutes", 6))
        self.autonomy_speech_min = BrowserSpinBox()
        self.autonomy_speech_min.setRange(1, 60)
        self.autonomy_speech_min.setSuffix(" 分钟")
        self.autonomy_speech_min.setValue(speech_min // 60)
        self.autonomy_speech_max = BrowserSpinBox()
        self.autonomy_speech_max.setRange(1, 60)
        self.autonomy_speech_max.setSuffix(" 分钟")
        self.autonomy_speech_max.setValue(speech_max // 60)
        # Live autonomy diagnostics. The monitor only retains action names,
        # execution results and timings; it does not store prompts or screenshots.
        from ..autonomy_monitor import get_autonomy_monitor
        self._autonomy_monitor = get_autonomy_monitor()
        # Most settings pages run under `python -m pet --settings`, a separate
        # process from the running pet. Read the pet's atomic local snapshot.
        self._autonomy_monitor.share_with(getattr(config, "dir", None))
        self._autonomy_monitor_labels = {}
        self._loop_editor = None
        self._edit_active_loop_button = QPushButton("调节当前持续动画的循环帧…", self)
        self._edit_active_loop_button.setEnabled(False)
        self._edit_active_loop_button.clicked.connect(self._open_activity_loop_editor)
        for name in ("status", "activity_time", "life", "life_events", "speech", "model",
                     "action", "result", "last", "next", "counts", "error", "history"):
            label = QLabel("—", self)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._autonomy_monitor_labels[name] = label
        self._autonomy_monitor.changed.connect(self._refresh_autonomy_monitor)
        self._autonomy_monitor_timer = QTimer(self)
        self._autonomy_monitor_timer.setInterval(1000)
        self._autonomy_monitor_timer.timeout.connect(self._refresh_autonomy_monitor)
        self._autonomy_monitor_timer.start()
        self._refresh_autonomy_monitor()
        # 系统通知键：构造期快照无条件回写会把打开期间外部（老聊天设置即存入口
        # settings_dialog.py:437）对该键的改动静默回滚，所以只在用户实际拨动过
        # （脏标记）时才写；toggled 连接晚于这里的 setChecked，构造期不会误标。
        self._system_notify_dirty = False
        self.system_notify_check.setChecked(bool(config.get("system_notifications_enabled", True)))
        self.chat_ui_style = ModernSelect(self, width=190)
        # 展示名与顺序（modern 在前 classic 在后）都取自 themes 的单一来源
        for style_id, style_label in CHAT_UI_STYLE_LABELS.items():
            self.chat_ui_style.addItem(style_label, style_id)
        self.chat_ui_style.setCurrentData(str(config.get("chat_ui_style", "modern")))
        self.vision_same = ToggleSwitch()
        self.vision_same.setChecked(bool(provider.vision_same_as_chat))
        self.vision_model = _line_edit(provider.vision_model)
        self.vision_url = _line_edit(provider.vision_base_url)
        self.vision_key = _line_edit(password=True)

        self._background_themes = list(theme_names())
        self._background_values = {
            "classic": str(config.get("chat_background", "") or ""),
            "modern": str(config.get("modern_chat_background", "") or ""),
        }
        self._background_display = {
            "classic": {
                "opacity": int(config.get("chat_background_opacity", 100) or 100),
                "fill": str(config.get("chat_background_fill", "cover") or "cover"),
            },
            "modern": {
                "opacity": int(config.get("modern_chat_background_opacity", 100) or 100),
                "fill": str(config.get("modern_chat_background_fill", "cover") or "cover"),
            },
        }
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self.background_select = ModernSelect(self, width=180)
        self.background_picker = ResourcePathPicker(
            "",
            parent=self,
        )
        self.background_opacity = BrowserSpinBox(self)
        self.background_opacity.setRange(10, 100)
        self.background_opacity.setSuffix(" %")
        self.message_card_opacity = BrowserSpinBox(self)
        self.message_card_opacity.setRange(10, 100)
        self.message_card_opacity.setSuffix(" %")
        self.message_card_opacity.setValue(
            int(config.get("modern_chat_card_opacity", 84) or 84)
        )
        self.background_fill = ModernSelect(self, width=160)
        self.background_fill.addItem("填充裁剪", "cover")
        self.background_fill.addItem("完整适应", "contain")
        self.background_fill.addItem("拉伸铺满", "stretch")
        # 裁切取景：编辑器结果按背景值记成编辑集（值为 None = 重置删除），save()
        # 时读磁盘最新配置后只合并编辑过的背景——整字典快照回写会把主设置窗打开
        # 期间外部（老聊天设置即存入口）对其他背景的裁切改动静默回滚。本页风格键
        # 的落盘粒度只到"按风格脏标记"（见下），裁切这一路才是按背景值合并。
        self._bg_crop_edits: dict[str, list | None] = {}
        # 背景风格键（两种对话风格各自的图片/不透明度/填充）同理：构造期快照无条件
        # 回写会把打开期间外部对另一风格的即存改动静默回滚，所以按风格脏标记只回写
        # 本窗口实际编辑过的风格；组内三键仍是整组回写，不承诺按键粒度。
        # _loading_background 挡住程序化赋值的误标。
        self._bg_edited_styles: set[str] = set()
        self._loading_background = False
        self.background_crop_btn = QPushButton("裁切取景…", self)
        self.background_crop_btn.clicked.connect(self._crop_background)
        self._populate_background_options(self._background_style)
        self.chat_ui_style.currentIndexChanged.connect(self._on_chat_ui_style_changed)
        self.background_select.currentIndexChanged.connect(self._on_background_edited)
        self.background_picker.edit.textChanged.connect(self._on_background_edited)
        # 路径变化要重新评估裁切按钮可用性：禁用后修好路径必须恢复可点
        self.background_picker.edit.textChanged.connect(self._update_background_visibility)
        self.background_opacity.valueChanged.connect(self._on_background_edited)
        self.background_fill.currentIndexChanged.connect(self._on_background_edited)
        # 填充方式改动要重估裁切行可见性：contain/stretch 下取景框不生效（themes.py 的 fill 语义）
        self.background_fill.currentIndexChanged.connect(self._update_background_visibility)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._run_test)
        self.test_result = QLabel("验证当前 Provider、API 地址和凭据是否可用。")
        self.test_result.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        provider_row = ResponsiveActionRow(
            self.provider_combo,
            [self.add_provider_btn, self.delete_provider_btn],
        )
        root.addWidget(SettingsSection("API 列表", [
            SettingRow(
                "provider_list", "API 列表",
                "选择要编辑/切换的模型服务；保存后当前选中项会作为 active_provider 生效。",
                provider_row,
            ),
        ], self))
        root.addWidget(SettingsSection("模型与连接", [
            SettingRow("provider_name", "Provider 名称", "用于区分当前使用的模型服务。", self.name),
            SettingRow("api_protocol", "API 请求协议",
                       "显式选择每个 Provider 使用的协议（无自动识别）；聊天、自主决策、主动搭话和联网检索共用。",
                       self.api_protocol),
            SettingRow("api_url", "API 地址", "当前协议的 API 根地址，如 https://4router.net/v1。", self.url),
            SettingRow("model", "模型", "发送请求时使用的模型标识。", self.model),
            SettingRow("api_key", "API Key", "凭据优先保存到系统钥匙串。", self.key),
            SettingRow("system_prompt", "小鲸鱼性格 / 说话风格 (System Prompt)",
                       "定义口吻、昵称、爱好、与主人的相处方式；对话、点击回应和自主搭话共用。"
                       "如果角色素材自带专属人设，这里的设置会补充并优先约束说话风格。",
                       self.prompt, stacked=True),
            SettingRow("connection_test", "连接测试", self.test_result.text(), self.test_button),
        ], self))
        thinking_web_rows = [
            SettingRow(
                "thinking_mode", "深度思考",
                "适配模型的思考设置：DeepSeek 使用 thinking 参数；Claude 原生接口使用 output_config.effort。"
                "「跟随模型默认」不额外发送参数。Claude 选择「关闭」会降至低强度，并非彻底关闭推理；"
                "不支持该参数的旧 Claude 模型自动使用服务端默认。复杂聊天建议将最大输出 Token 设为 8192 以上。",
                self.thinking_mode,
            ),
            SettingRow("reasoning_effort", "思考强度（原始参数）",
                       "开启深度思考后，原样发送此值（不自动翻译或降级）。Claude 原生使用 output_config.effort；"
                       "OpenAI 兼容 API 沿用 reasoning_effort；是否支持由模型 / 网关决定。"
                       "例如 low、medium、high、xhigh、max；留空则不发送强度。",
                       self.reasoning_effort),
            SettingRow(
                "web_search_enabled", "联网搜索",
                "回答前先由模型判断是否需要查资料，需要时自动搜索并阅读网页，再结合结果回答；闲聊不会触发搜索。",
                self.web_search_enabled,
            ),
            SettingRow(
                "web_search_engine", "搜索引擎",
                "百度适合国内直连，DuckDuckGo 通常需要代理，两者互为备用；免 Key 引擎搜索过于频繁会被要求人机验证。需要稳定联网请选博查或 Tavily（付费 API）。",
                self.web_search_engine,
            ),
            SettingRow("web_search_key", "搜索 API Key", "博查 / Tavily 的 API Key，优先保存到系统钥匙串。", self.web_search_key),
            SettingRow("web_search_max_results", "每次搜索结果数", "单次搜索交给模型的结果条数。", self.web_search_results),
            SettingRow("web_search_fetch_pages", "允许阅读网页正文", "搜索摘要不够时，允许模型打开网页读取正文（更准，但更慢）。", self.web_search_fetch),
        ]
        root.addWidget(SettingsSection("深度思考与联网", thinking_web_rows, self))
        root.addWidget(SettingsSection("电脑 Agent（OpenClaw，默认关闭）", [
            SettingRow('desktop_agent_enabled', '允许手动 Agent 任务',
                       '只有明确输入 /agent 加任务才会启动；自动聊天与自主决策不能操控电脑。',
                       self.agent_enabled, parent=self),
            SettingRow('desktop_agent_gateway', '本地 Gateway 地址',
                       '只能连接 127.0.0.1 / localhost，默认 http://127.0.0.1:18789。',
                       self.agent_url, parent=self),
            SettingRow('desktop_agent_model', 'Agent 标识',
                       'OpenClaw 的 Agent 入口（非 GPT 模型名），通常填写 openclaw/default。',
                       self.agent_model, parent=self),
            SettingRow('desktop_agent_backend_model', 'Agent 后端大模型',
                       '填 OpenClaw 中真正的 provider/model ID；留空则使用该 Agent 的默认模型，不更改全局配置。',
                       self.agent_backend_model, parent=self),
            SettingRow('desktop_agent_thinking', 'Agent 思考深度',
                       '每次任务单独指定 /think 等级；如果 OpenClaw 当前模型不支持，请选默认或该模型支持的值。',
                       self.agent_thinking, parent=self),
            SettingRow('desktop_agent_context_summary', '为 Agent 整理聊天背景',
                       '仅在明确输入 /agent 时生效：最近最多 20 条纯文本由大肥鱼当前聊天模型压缩，发给 OpenClaw 的只有短摘要，不发送完整聊天记录、附件或截图。摘要可能有误；本次任务始终优先。关闭后只发送 /agent 后的任务原文。',
                       self.agent_context_summary, parent=self),
            SettingRow('desktop_agent_token', 'Gateway Token',
                       '独立于 4Router API Key，保存在 Windows 系统凭据中；留空保留旧值。',
                       self.agent_key, parent=self),
            SettingRow('desktop_agent_allowed_windows', '允许操作的程序',
                       '仅约束本补丁的 Windows MCP 工具，与识屏白名单无关；OpenClaw 自带 exec 等工具需在其后台单独限制。',
                       self.agent_allowed_windows, stacked=True, parent=self),
            SettingRow('desktop_agent_test', '测试网关',
                       '只检测连接和凭据，不会启动 Agent。',
                       self.agent_test_button, parent=self),
            SettingRow('desktop_agent_test_result', '连接状态',
                       '需要先在 OpenClaw 配置中开启 chatCompletions 接口。',
                       self.agent_test_result, stacked=True, parent=self),
        ], self))
        root.addWidget(SettingsSection("AI 自主行为", [
            SettingRow("autonomy_provider", "自主行为使用的 API", "必须选择一个独立的 Provider。旧版跟随对话配置会在首次加载时固定为当时选择的 API，不再随对话切换。", self.autonomy_provider_combo, parent=self),
            SettingRow("autonomy_model", "自主决策模型标识", "只影响自主决策、主动搭话和点击回应，留空使用对应 Provider 的模型，与聊天窗口的模型选择互不影响。", self.autonomy_model, parent=self),
            SettingRow("autonomy_thinking", "自主行为深度思考", "可独立覆盖自主模型的思考开关；默认继承所选 Provider。", self.autonomy_thinking, parent=self),
            SettingRow("autonomy_reasoning_effort", "自主行为思考强度", "可独立设置 medium/high 等原始参数；留空继承所选 Provider。", self.autonomy_effort, parent=self),
            SettingRow(
                "autonomy_enabled", "让 AI 决定桌宠行为",
                "开启后，大肥鱼定期使用本节选择的自主模型决定散步、靠近鼠标、表演或说话；"
                "原有点击/拖拽/抛掷优先。关闭后恢复随机行为。每次决策会产生 API 用量。",
                self.autonomy_enabled, parent=self),
            SettingRow(
                "autonomy_screen_enabled", "允许自主识屏",
                "仅在开关开启且前台程序命中下方白名单时截取该窗口区域，绝不退化成全桌面截图；"
                "白名单为空、未命中或窗口识别失败则只发送文字状态；图像不落盘。",
                self.autonomy_screen, parent=self),
            SettingRow(
                "autonomy_screen_whitelist", "自主识屏程序白名单",
                "每行一个 Windows 进程文件名（如 code.exe），只在该程序窗口处于前台时截取其窗口区域。"
                "默认空白：即使打开识屏开关也不会发送任何截图；不会读取旧版主动识屏白名单。",
                self.autonomy_whitelist, stacked=True, parent=self),
            SettingRow(
                "autonomy_interval_seconds", "AI 决策间隔",
                "每 20–300 秒最多发起一次决策（单飞、不积压）；建议从 40 秒开始，"
                "模型响应慢时不会阻塞动画。",
                self.autonomy_interval, parent=self),
            SettingRow(
                "autonomy_speech_min_minutes", "自主搭话最短间隔",
                "持续活动期间，隔多久再次让 AI 判断是否主动说话。默认最短 3 分钟。"
                "需要实际 API 请求，模型仍可决定保持安静；睡觉时不主动打扰。",
                self.autonomy_speech_min, parent=self),
            SettingRow(
                "autonomy_speech_max_minutes", "自主搭话最长间隔",
                "默认最长 6 分钟。和最短间隔相同即为固定间隔；"
                "设置保存后，后台会重新安排下一次搭话判断。",
                self.autonomy_speech_max, parent=self),
        ], self))
        root.addWidget(SettingsSection("AI 决策状态面板", [
            SettingRow(
                "autonomy_monitor_status", "运行状态",
                "这里显示后台自主决策服务的真实状态；聊天连接成功不等于自主决策已启动。",
                self._autonomy_monitor_labels["status"], parent=self),
            SettingRow(
                "autonomy_monitor_activity_time", "当前活动计时",
                "显示持续活动的总时长、已进行时间、剩余时间和预计结束时刻；"
                "活动中的 AI 阶段更新不会重置倒计时。结束后自动清空。",
                self._autonomy_monitor_labels["activity_time"], parent=self),
            SettingRow(
                "autonomy_monitor_speech", "AI 搭话与点击回应",
                "持续活动时独立、低频判断要不要搭话；左键点击保留原动画并可 AI 回应。"
                "睡觉或免打扰时不会无故主动打扰，点击主动互动仍可回应。每次模型判断另计 API 用量。",
                self._autonomy_monitor_labels["speech"], parent=self),
            SettingRow(
                "autonomy_monitor_life", "小鲸鱼生命状态",
                "这些状态每 30 秒在本地更新，重启后也会保留；饥饿和困意越高越需要照顾，"
                "精力和心情则越高越好。模型读取此状态做决策，但不会每 30 秒调用 API。",
                self._autonomy_monitor_labels["life"], stacked=True, parent=self),
            SettingRow(
                "autonomy_monitor_life_events", "最近生活经历",
                "只记录活动名和聊天/叫醒事件，不保存聊天原文、屏幕截图或 API 内容。",
                self._autonomy_monitor_labels["life_events"], stacked=True, parent=self),
            SettingRow(
                "autonomy_monitor_model", "决策模型",
                "显示后台实际配置的模型，以及是否允许向模型发送桌面截图。",
                self._autonomy_monitor_labels["model"], parent=self),
            SettingRow(
                "autonomy_monitor_action", "上次 AI 选择",
                "显示解析后的动作意图（可能是英文动作名），并非随机动画。",
                self._autonomy_monitor_labels["action"], parent=self),
            SettingRow(
                "autonomy_monitor_result", "执行器反馈",
                "“执行器已接受”表示程序接收了动作，不保证肉眼一定能看到变化。",
                self._autonomy_monitor_labels["result"], parent=self),
            SettingRow(
                "autonomy_monitor_last", "最近完成时间",
                "模型完成返回或遇到错误的时间。",
                self._autonomy_monitor_labels["last"], parent=self),
            SettingRow(
                "autonomy_monitor_next", "下次决策",
                "根据定时器间隔估算；繁忙时会跳过，不会积压请求。",
                self._autonomy_monitor_labels["next"], parent=self),
            SettingRow(
                "autonomy_monitor_counts", "请求统计",
                "统计本次运行的自主动作规划、阶段更新与独立搭话/点击判断请求；不含普通 AI 聊天。",
                self._autonomy_monitor_labels["counts"], parent=self),
            SettingRow(
                "autonomy_monitor_error", "最近错误",
                "为保护 API Key 和屏幕隐私，只显示错误类型，不显示原始请求内容。",
                self._autonomy_monitor_labels["error"], parent=self),
            SettingRow(
                "autonomy_monitor_history", "最近五次记录",
                "包含 AI 动作意图和执行器反馈；不保存聊天内容和截图。",
                self._autonomy_monitor_labels["history"], stacked=True, parent=self),
            SettingRow(
                "autonomy_monitor_loop", "循环帧调节",
                "AI 正在执行持续活动时，可分别调整当前动画的开始/结束帧区间。"
                "松开滑块即保存，约 1 秒内同步到正在运行的桌宠；关闭设置不会停止动画。",
                self._edit_active_loop_button, parent=self),
        ], self))
        self.autonomy_provider_combo.currentIndexChanged.connect(
            lambda _: self._autonomy_edited.add("autonomy_provider"))
        self.autonomy_model.textChanged.connect(
            lambda _: self._autonomy_edited.add("autonomy_model"))
        self.autonomy_thinking.currentIndexChanged.connect(
            lambda _: self._autonomy_edited.add("autonomy_thinking"))
        self.autonomy_effort.textChanged.connect(
            lambda _: self._autonomy_edited.add("autonomy_reasoning_effort"))
        self.autonomy_enabled.toggled.connect(
            lambda _: self._autonomy_edited.add("autonomy_enabled"))
        self.autonomy_screen.toggled.connect(
            lambda _: self._autonomy_edited.add("autonomy_screen_enabled"))
        self.autonomy_whitelist.textChanged.connect(
            lambda: self._autonomy_edited.add("autonomy_screen_whitelist"))
        self.autonomy_interval.valueChanged.connect(
            lambda _: self._autonomy_edited.add("autonomy_interval_seconds"))
        self.autonomy_speech_min.valueChanged.connect(self._speech_min_changed)
        self.autonomy_speech_max.valueChanged.connect(self._speech_max_changed)
        self._reasoning_effort_row = thinking_web_rows[1]
        self._web_detail_rows = thinking_web_rows[3:]
        self._web_key_row = thinking_web_rows[4]
        self.thinking_mode.currentIndexChanged.connect(self._update_thinking_web_visibility)
        self.web_search_enabled.toggled.connect(self._update_thinking_web_visibility)
        self.web_search_engine.currentIndexChanged.connect(self._update_thinking_web_visibility)
        self._update_thinking_web_visibility()
        root.addWidget(SettingsSection("系统通知", [
            SettingRow(
                "system_notifications_enabled", "系统通知",
                "对话完成 / 生成失败 / 需要授权时，即使切走窗口也会在桌面右下角提醒。",
                self.system_notify_check,
            ),
        ], self))
        vision_rows = [
            SettingRow("vision_same", "视觉模型复用聊天模型", "开启后自动选择兼容的视觉模型，用于“看看屏幕”。", self.vision_same, parent=self),
            SettingRow("vision_model", "视觉模型", "关闭复用后使用的多模态模型标识；留空则自动推导。", self.vision_model, parent=self),
            SettingRow("vision_url", "视觉 API 地址", "留空复用聊天服务地址。", self.vision_url, parent=self),
            SettingRow("vision_key", "视觉 API Key", "留空复用聊天服务凭据。", self.vision_key, parent=self),
        ]
        root.addWidget(SettingsSection("视觉能力", vision_rows, self))
        self._vision_override_rows = vision_rows[1:]
        self.vision_same.toggled.connect(self._update_vision_visibility)
        self._update_vision_visibility(self.vision_same.isChecked())
        # 用户拨动系统通知开关才置脏；连接晚于构造期 setChecked，程序化赋值不置脏。
        self.system_notify_check.toggled.connect(self._on_system_notify_edited)
        self._test_row = self.findChild(SettingRow, "settingRow_connection_test")
        if self._test_row is not None:
            self.test_result = self._test_row.hint_label
        root.addWidget(SettingsSection("生成参数（高级）", [
            SettingRow("timeout", "请求超时", "等待模型服务响应的最长时间。", self.timeout),
            SettingRow("temperature", "Temperature", "数值越高，回答越随机。", self.temperature),
            SettingRow("max_tokens", "最大输出 Token", "限制模型单次回复的最大长度；开启深度思考时思考过程也计入，建议 8192 以上。", self.tokens),
            SettingRow("skip_ssl", "跳过 SSL 证书验证", "仅用于本地网关或自签名证书。", self.skip_ssl),
        ], self, advanced=True))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.add_provider_btn.clicked.connect(self._add_provider)
        self.delete_provider_btn.clicked.connect(self._delete_provider)
        self._update_provider_buttons()
        root.addStretch(1)

    def _refresh_autonomy_monitor(self, snapshot=None) -> None:
        """Render the live service snapshot and refresh the countdown locally."""
        if not isinstance(snapshot, dict):
            snapshot = self._autonomy_monitor.snapshot()
        labels = self._autonomy_monitor_labels
        enabled = bool(snapshot.get("enabled"))
        busy = bool(snapshot.get("busy"))
        if not enabled:
            icon = "○"
        elif busy:
            icon = "◉"
        else:
            icon = "●"
        labels["status"].setText(f'{icon} {snapshot.get("state") or "未知"}')
        activity_name = str(snapshot.get("activity_name") or "")
        started_at = snapshot.get("activity_started_at")
        ends_at = snapshot.get("activity_ends_at")
        total_seconds = snapshot.get("activity_total_seconds")
        if (enabled and activity_name and isinstance(started_at, (int, float))
                and isinstance(ends_at, (int, float))
                and isinstance(total_seconds, (int, float)) and total_seconds > 0
                and ends_at >= started_at):
            now = time.time()
            elapsed = max(0, min(int(total_seconds), int(now - started_at)))
            remaining = max(0, int(ends_at - now + 0.999))

            def clock(seconds):
                seconds = max(0, int(seconds))
                hours, remainder = divmod(seconds, 3600)
                minutes, secs = divmod(remainder, 60)
                return (f"{hours:d}:{minutes:02d}:{secs:02d}" if hours
                        else f"{minutes:02d}:{secs:02d}")

            time_text = (f"{activity_name} · 已进行 {clock(elapsed)} / {clock(total_seconds)}"
                         f" · 剩余 {clock(remaining)}")
            time_text += (f"\n预计 {datetime.fromtimestamp(ends_at):%H:%M:%S} 结束"
                          if remaining else "\n已到计划结束时间，等待当前动画收尾")
        else:
            time_text = "—（暂无持续活动）" if enabled else "—（未开启）"
        labels["activity_time"].setText(time_text)
        speech_state = str(snapshot.get("speech_status") or "待机")
        speech_at = snapshot.get("speech_next_at")
        if enabled and isinstance(speech_at, (int, float)):
            countdown = max(0, int(speech_at - time.time() + 0.999))
            speech_state += (f" · 约 {countdown} 秒后再判断" if countdown
                             else " · 等待合适时机")
        if "speech" in labels:  # Older test fixtures may omit newer monitor rows.
            labels["speech"].setText(speech_state)
        life = snapshot.get("life_state")
        if isinstance(life, dict) and life:
            def status_bar(key):
                try:
                    number = max(0.0, min(100.0, float(life.get(key, 0))))
                    count = max(0, min(10, round(number / 10)))
                    return f'{"█" * count}{"·" * (10-count)}  {number:.0f}/100'
                except (ValueError, TypeError, OverflowError):
                    return "—"

            labels["life"].setText(
                f'精力    {status_bar("energy")}\n'
                f'饥饿    {status_bar("hunger")}\n'
                f'困意    {status_bar("sleepiness")}\n'
                f'心情    {status_bar("mood")}\n'
                f'生活倾向：{str(life.get("goal") or "自然活动")[:100]}')
            events = life.get("recent_events")
            labels["life_events"].setText("\n".join(
                f'{datetime.fromtimestamp(item["at"]):%m-%d %H:%M}  {item["description"]}'
                for item in (events[-5:] if isinstance(events, list) else ())
                if isinstance(item, dict)
                and isinstance(item.get("at"), (int, float))
                and isinstance(item.get("description"), str)
            ) or "暂无生活记录")
        else:
            labels["life"].setText("—（等待桌宠生命系统启动）")
            labels["life_events"].setText("暂无生活记录")
        model = str(snapshot.get("model") or "—")
        vision = "识屏开启" if snapshot.get("vision") else "识屏关闭"
        labels["model"].setText(f"{model} · {vision}")
        labels["action"].setText(str(snapshot.get("last_action") or "—"))
        labels["result"].setText(str(snapshot.get("last_result") or "—"))

        def timestamp(value):
            if not isinstance(value, (int, float)) or value <= 0:
                return "—"
            return datetime.fromtimestamp(value).strftime("%H:%M:%S")

        labels["last"].setText(timestamp(snapshot.get("last_decision_at")))
        next_at = snapshot.get("next_decision_at")
        if not enabled:
            next_text = "—（未开启）"
        elif isinstance(next_at, (int, float)):
            remaining = max(0, int(next_at - time.time() + 0.999))
            next_text = f"约 {remaining} 秒后" if remaining else "即将进入下一周期"
            if busy:
                next_text += " · 当前请求进行中"
        else:
            next_text = "等待调度"
        labels["next"].setText(next_text)
        labels["counts"].setText(
            f'已发起 {snapshot.get("requests", 0)} 次 · 已完成 {snapshot.get("completed", 0)} 次')
        labels["error"].setText(str(snapshot.get("last_error") or "无"))
        history = snapshot.get("history") or ()
        labels["history"].setText("\n".join(
            f'{timestamp(item.get("at"))}  {item.get("action") or "—"}  ·  {item.get("result") or "—"}'
            for item in history if isinstance(item, dict)) or "暂无记录")
        current = str(snapshot.get("active_animation") or "")
        active = bool(snapshot.get("sustained")) and bool(current)
        self._edit_active_loop_button.setEnabled(active)
        self._edit_active_loop_button.setText(
            f"调节「{current}」循环帧…" if active else "等待 AI 开始持续动画…")

    def _open_activity_loop_editor(self):
        """Non-modal so changes can be judged against the live desktop pet."""
        snapshot = self._autonomy_monitor.snapshot()
        animation = snapshot.get("active_animation") if snapshot.get("sustained") else None
        if not isinstance(animation, str) or not animation:
            return
        from ..loop_editor import ActivityLoopEditor
        editor = ActivityLoopEditor(animation, self)
        self._loop_editor = editor  # Keep wrapper alive until closed.
        editor.show()

    def appearance_rows(self) -> list[SettingRow]:
        """Controls visually owned by the Appearance page, persisted with AI settings.

        行建在 ``_AiSettingsPage`` 上（``parent=self``）：下面那句
        ``_update_background_visibility`` 在返回之前就按当前背景值决定详情行显隐——
        无父控件的 ``setVisible(True)`` 会当场开出一个顶层窗口（用户看到的无标题
        小窗），进卡片之前无人回收。
        """
        rows = [
            SettingRow(
                "chat_ui_style", "对话窗口",
                "肥鱼版 DeepSeek 提供宽屏现代体验；肥鱼牌小手机保留紧凑经典体验。",
                self.chat_ui_style,
                parent=self,
            ),
            SettingRow(
                "chat_background", "对话背景",
                "肥鱼版 DeepSeek 与肥鱼牌小手机均支持纯色、内置主题或自定义图片。",
                self.background_select,
                parent=self,
            ),
            SettingRow("chat_background_file", "自定义背景图片", "支持常见图片格式，使用绝对路径。", self.background_picker, parent=self),
            SettingRow("chat_background_opacity", "图片不透明度", "调节背景图可见强度；消息卡片会独立保证正文可读。", self.background_opacity, parent=self),
            SettingRow("chat_background_fill", "填充方式", "选择裁剪铺满、完整显示或拉伸铺满窗口；仅「填充裁剪」支持自定义取景。", self.background_fill, parent=self),
            SettingRow("chat_bg_crops", "裁切取景", "拖拽移动 + 滚轮缩放选区，决定背景取哪一块；不裁则按主题默认主体取景。", self.background_crop_btn, parent=self),
            SettingRow(
                "modern_chat_card_opacity", "消息卡片不透明度",
                "调节肥鱼版 DeepSeek 消息卡片透出背景的程度。",
                self.message_card_opacity,
                parent=self,
            ),
        ]
        self._background_file_row = rows[-5]
        self._background_detail_rows = rows[-4:-1]
        self._background_crop_row = self._background_detail_rows[-1]
        self._message_card_opacity_row = rows[-1]
        self.background_select.currentIndexChanged.connect(self._update_background_visibility)
        self._update_background_visibility()
        return rows

    def _current_background_value(self) -> str:
        value = self.background_select.currentData()
        return self.background_picker.text() if value == "custom" else str(value or "")

    def _capture_background_value(self) -> None:
        self._background_values[self._background_style] = self._current_background_value()
        self._background_display[self._background_style] = {
            "opacity": self.background_opacity.value(),
            "fill": str(self.background_fill.currentData() or "cover"),
        }

    def _populate_background_options(self, style: str) -> None:
        self._loading_background = True  # 程序化赋值不得误标脏
        try:
            self.background_select.clear()
            self.background_select.addItem("纯色背景", "")
            # 内置主题两种对话窗口风格都可用（肥鱼版 DeepSeek 与肥鱼牌小手机一致）
            for key, label in self._background_themes:
                self.background_select.addItem(label, f"builtin:{key}")
            self.background_select.addItem("自定义图片", "custom")
            value = str(self._background_values.get(style, "") or "")
            if value.startswith("builtin:") and self.background_select.findData(value) >= 0:
                self.background_select.setCurrentData(value)
                self.background_picker.setText("")
            elif value:
                self.background_select.setCurrentData("custom")
                self.background_picker.setText(value)
            else:
                self.background_select.setCurrentData("")
                self.background_picker.setText("")
            display = self._background_display.get(style, {})
            self.background_opacity.setValue(int(display.get("opacity", 100)))
            self.background_fill.setCurrentData(str(display.get("fill", "cover")))
        finally:
            self._loading_background = False
        self._update_background_visibility()

    def _on_background_edited(self, *_args) -> None:
        """用户在当前风格上动了背景控件 → 该风格落盘脏标记（程序化赋值除外）。"""
        if not self._loading_background:
            self._bg_edited_styles.add(self._background_style)

    def _on_system_notify_edited(self, _checked: bool = False) -> None:
        """用户拨动系统通知开关 → 该键落盘脏标记（未拨动则保留外部即存值）。"""
        self._system_notify_dirty = True

    def _on_chat_ui_style_changed(self, _index: int = -1) -> None:
        self._capture_background_value()
        self._background_style = str(self.chat_ui_style.currentData() or "modern")
        self._populate_background_options(self._background_style)

    def _update_thinking_web_visibility(self, *_args) -> None:
        self._reasoning_effort_row.setVisible(self.thinking_mode.currentData() == "enabled")
        web_on = self.web_search_enabled.isChecked()
        for row in self._web_detail_rows:
            row.setVisible(web_on)
        self._web_key_row.setVisible(web_on and self.web_search_engine.currentData() in self._web_keyed_engines)
        card = self._reasoning_effort_row.parentWidget()
        if isinstance(card, SettingsCard):
            card.refresh_separators()

    def _update_vision_visibility(self, reuse_chat_model: bool) -> None:
        for row in self._vision_override_rows:
            row.setVisible(not reuse_chat_model)
        card = self._vision_override_rows[0].parentWidget() if self._vision_override_rows else None
        if isinstance(card, SettingsCard):
            card.refresh_separators()

    def _background_style_label(self) -> str:
        """当前对话窗口风格的展示名（裁切行标签、编辑器标题共用）。"""
        return CHAT_UI_STYLE_LABELS.get(self._background_style, self._background_style)

    def _background_fill_mode(self) -> str:
        return str(self.background_fill.currentData() or "cover")

    def _update_background_visibility(self, _index: int = -1) -> None:
        crop_row = getattr(self, "_background_crop_row", None)
        row = getattr(self, "_background_file_row", None)
        if row is not None:
            row.setVisible(self.background_select.currentData() == "custom")
            has_image = bool(self.background_select.currentData())
            for detail_row in getattr(self, "_background_detail_rows", []):
                detail_row.setVisible(has_image)
            # contain/stretch 下取景框被渲染路径整体忽略（见 themes.py 的 fill 语义），
            # 裁切入口一并隐藏，免得用户存下一个不生效的框。
            if crop_row is not None:
                crop_row.setVisible(has_image and self._background_fill_mode() == "cover")
            card = row.parentWidget()
            if isinstance(card, SettingsCard):
                card.refresh_separators()
        card_opacity_row = getattr(self, "_message_card_opacity_row", None)
        if card_opacity_row is not None:
            card_opacity_row.setVisible(self._background_style == "modern")
        if crop_row is not None:
            title = f"裁切取景（{self._background_style_label()}）"
            if crop_row.label.text() != title:
                crop_row.label.setText(title)
                crop_row.control.setAccessibleName(title)
        if getattr(self, "background_crop_btn", None) is not None:
            self.background_crop_btn.setText("裁切取景…")
            self.background_crop_btn.setEnabled(True)

    def _crop_background(self) -> None:
        """打开裁切取景编辑器：结果记入 _bg_crop_edits，save() 时按背景值合并落盘。"""
        from .crop_dialog import CropDialog
        from .themes import get_theme
        from .widgets import resolve_bg_pixmap

        self.config.reload()  # 打开期间外部可能即存改动：初始框必须读磁盘最新
        value = self._current_background_value()
        pix = resolve_bg_pixmap(value) if value else None
        if pix is None:
            self.background_crop_btn.setText("无可裁背景")
            self.background_crop_btn.setEnabled(False)
            return
        _unset = object()
        initial = self._bg_crop_edits.get(value, _unset)
        if initial is _unset:  # 会话编辑集里没有才读配置（上面已 reload 到磁盘最新）
            initial = (self.config.get('chat_bg_crops', {}) or {}).get(value)
        if initial is None and value.startswith("builtin:"):
            theme = get_theme(value[8:])
            initial = tuple(theme["focus"]) if theme else None
        aspect = CHAT_UI_VIEW_ASPECT.get(self._background_style, CHAT_UI_VIEW_ASPECT["classic"])
        dlg = CropDialog(pix, initial, self, self._background_style_label(), aspect)
        accepted = dlg.exec()
        reset, box = dlg.result_box() if accepted else (False, None)
        dlg.deleteLater()  # 延迟析构：对话框持有整张背景 QPixmap，不随使用次数累积
        if accepted:
            self._bg_crop_edits[value] = None if reset else [round(float(v), 4) for v in box]

    # ------------------------------------------------------------ API 列表管理
    @staticmethod
    def _provider_label(p) -> str:
        name = str(p.name or p.provider_id)
        model = str(p.model or '').strip()
        return f"{name} · {model}" if model else name

    def _capture_current_draft(self) -> None:
        pid = self.settings.active_provider
        if not pid or pid not in self.settings.providers:
            return
        existing = self._provider_drafts.get(pid, {})
        key_text = self.key.text()
        vkey_text = self.vision_key.text()
        self._provider_drafts[pid] = {
            "name": self.name.text().strip(),
            "base_url": self.url.text().strip(),
            "api_protocol": str(self.api_protocol.currentData() or "openai_chat"),
            "model": self.model.text().strip(),
            # 输入框为空表示“不修改/不覆盖”，保留草稿里已录入但尚未保存的 Key；
            # 否则 _load_provider_ui() 清空输入框后会把草稿 Key 覆盖成空。
            "key": key_text if key_text else existing.get("key", ""),
            "timeout": float(self.timeout.value()),
            "temperature": float(self.temperature.value()),
            "max_tokens": int(self.tokens.value()),
            "vision_model": self.vision_model.text().strip(),
            "vision_same_as_chat": self.vision_same.isChecked(),
            "vision_base_url": self.vision_url.text().strip(),
            "vision_key": vkey_text if vkey_text else existing.get("vision_key", ""),
            "verify_ssl": not self.skip_ssl.isChecked(),
            "thinking": str(self.thinking_mode.currentData() or "auto"),
            "reasoning_effort": str(self.reasoning_effort.text().strip()),
        }

    def _load_provider_ui(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        if p is None:
            return
        draft = self._provider_drafts.get(provider_id, {})
        self.settings.active_provider = provider_id
        self.name.setText(draft.get("name") if draft.get("name") is not None else p.name)
        self.url.setText(draft.get("base_url") if draft.get("base_url") is not None else p.base_url)
        self.model.setText(draft.get("model") if draft.get("model") is not None else p.model)
        self.api_protocol.setCurrentData(draft.get("api_protocol", p.api_protocol))
        self.key.clear()
        self.timeout.setValue(int(draft.get("timeout", p.timeout)))
        self.temperature.setValue(float(draft.get("temperature", p.temperature)))
        self.tokens.setValue(int(draft.get("max_tokens", p.max_tokens)))
        self.vision_model.setText(draft.get("vision_model", p.vision_model))
        self.vision_same.setChecked(bool(draft.get("vision_same_as_chat", p.vision_same_as_chat)))
        self.vision_url.setText(draft.get("vision_base_url", p.vision_base_url))
        self.vision_key.clear()
        self.skip_ssl.setChecked(not bool(draft.get("verify_ssl", p.verify_ssl)))
        self.thinking_mode.setCurrentData(draft.get("thinking", p.thinking))
        self.reasoning_effort.setText(str(draft.get("reasoning_effort", p.reasoning_effort) or ""))
        self._update_thinking_web_visibility()
        self._update_provider_buttons()

    def _on_provider_changed(self, _index: int = -1) -> None:
        if self._loading_provider:
            return
        self._capture_current_draft()
        pid = self.provider_combo.currentData()
        if pid and pid in self.settings.providers:
            self._load_provider_ui(pid)

    def _new_provider_id(self) -> str:
        # 避免复用已删除的 provider_id：否则保存合并时新建项会被删除集合过滤掉。
        used = set(self.settings.providers) | set(self._deleted_provider_ids)
        i = len(self.settings.providers) + 1
        while f"api-{i}" in used:
            i += 1
        return f"api-{i}"

    def _add_provider(self) -> None:
        self._capture_current_draft()
        base = self.settings.active_config
        new_id = self._new_provider_id()
        new = self._provider_config_type(
            new_id,
            name=f"{base.name} 副本",
            base_url=base.base_url,
            chat_path=base.chat_path,
            model=base.model,
            api_key_ref=f"provider/{new_id}",
            timeout=base.timeout,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
            vision_model=base.vision_model,
            vision_same_as_chat=base.vision_same_as_chat,
            vision_base_url=base.vision_base_url,
            vision_api_key_ref=f"provider/{new_id}/vision",
            verify_ssl=base.verify_ssl,
            thinking=base.thinking,
            reasoning_effort=base.reasoning_effort,
            api_protocol=base.api_protocol,
        )
        self.settings.providers[new_id] = new
        self._provider_drafts[new_id] = {
            "name": new.name,
            "base_url": new.base_url,
            "model": new.model,
            "key": "",
            "timeout": new.timeout,
            "temperature": new.temperature,
            "max_tokens": new.max_tokens,
            "vision_model": new.vision_model,
            "vision_same_as_chat": new.vision_same_as_chat,
            "vision_base_url": new.vision_base_url,
            "vision_key": "",
            "verify_ssl": new.verify_ssl,
            "thinking": new.thinking,
            "reasoning_effort": new.reasoning_effort,
            "api_protocol": new.api_protocol,
        }
        self._loading_provider = True
        try:
            self.provider_combo.addItem(self._provider_label(new), new_id)
            self.provider_combo.setCurrentIndex(self.provider_combo.count() - 1)
        finally:
            self._loading_provider = False
        self.settings.active_provider = new_id
        self._load_provider_ui(new_id)
        self._update_provider_buttons()
        self._refresh_autonomy_provider_options()

    def _refresh_autonomy_provider_options(self, value=None) -> None:
        """Refresh selectable API entries without silently selecting chat model."""
        combo = self.autonomy_provider_combo
        selected = str(combo.currentData() or '') if value is None else str(value or '')
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            # No inherited-chat option: autonomy must select a real provider.
            for pid, provider in self.settings.providers.items():
                combo.addItem(f"{provider.name} · {provider.model}", pid)
            combo.setCurrentData(selected if selected in self.settings.providers else self.settings.active_provider)
        finally:
            combo.blockSignals(blocked)

    def _delete_provider(self) -> None:
        if len(self.settings.providers) <= 1:
            return
        pid = self.provider_combo.currentData()
        if not pid or pid not in self.settings.providers:
            return
        self.settings.providers.pop(pid, None)
        self._provider_drafts.pop(pid, None)
        self._deleted_provider_ids.add(pid)
        self._loading_provider = True
        try:
            self.provider_combo.clear()
            for p in self.settings.providers.values():
                self.provider_combo.addItem(self._provider_label(p), p.provider_id)
            self.settings.active_provider = next(iter(self.settings.providers))
            self.provider_combo.setCurrentData(self.settings.active_provider)
        finally:
            self._loading_provider = False
        self._load_provider_ui(self.settings.active_provider)
        self._update_provider_buttons()
        self._refresh_autonomy_provider_options()

    def _update_provider_buttons(self) -> None:
        self.delete_provider_btn.setEnabled(len(self.settings.providers) > 1)

    def _apply_draft_to_provider(self, provider_id: str) -> None:
        p = self.settings.providers.get(provider_id)
        draft = self._provider_drafts.get(provider_id)
        if p is None or not draft:
            return
        if draft.get("name"):
            p.name = draft["name"]
        p.base_url = draft.get("base_url") or p.base_url
        p.model = draft.get("model") or p.model
        p.timeout = float(draft.get("timeout", p.timeout))
        p.temperature = float(draft.get("temperature", p.temperature))
        p.max_tokens = int(draft.get("max_tokens", p.max_tokens))
        p.vision_model = draft.get("vision_model", p.vision_model)
        p.vision_same_as_chat = bool(draft.get("vision_same_as_chat", p.vision_same_as_chat))
        p.vision_base_url = draft.get("vision_base_url", p.vision_base_url)
        p.verify_ssl = bool(draft.get("verify_ssl", p.verify_ssl))
        p.thinking = str(draft.get("thinking", p.thinking) or "auto")
        p.reasoning_effort = str(draft.get("reasoning_effort", p.reasoning_effort) or "").strip()
        p.api_protocol = str(draft.get("api_protocol", p.api_protocol) or "openai_chat")
        key = str(draft.get("key") or "")
        if key:
            p.api_key_ref = p.api_key_ref or f"provider/{provider_id}"
            if not self._secret_store_type().set(p.api_key_ref, key):
                p.api_key = key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")
        vkey = str(draft.get("vision_key") or "")
        if vkey:
            p.vision_api_key_ref = p.vision_api_key_ref or f"provider/{provider_id}/vision"
            if not self._secret_store_type().set(p.vision_api_key_ref, vkey):
                p.vision_api_key = vkey
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，Key 仅本次运行保留，重启需重输。")

    def _apply_web_search_settings(self) -> None:
        web = self.settings.web_search
        web.enabled = self.web_search_enabled.isChecked()
        web.engine = str(self.web_search_engine.currentData() or "baidu")
        web.max_results = int(self.web_search_results.value())
        web.fetch_pages = self.web_search_fetch.isChecked()
        key = self.web_search_key.text().strip()
        if key:
            web.api_key_ref = web.api_key_ref or "websearch/api"
            if self._secret_store_type().set(web.api_key_ref, key):
                web.api_key = ""
            else:
                web.api_key = key
                QMessageBox.warning(self, "安全存储不可用", "无法使用系统安全存储，搜索 API Key 仅本次运行保留，重启需重输。")
            self.web_search_key.clear()
        self.settings.attach_runtime()

    def provisional_config(self):
        provider = self.settings.active_config
        return self._provider_config_type(
            provider.provider_id,
            self.name.text().strip() or provider.name,
            self.url.text().strip(),
            provider.chat_path,
            self.model.text().strip(),
            provider.api_key_ref,
            # 表单未填时回退钥匙串：凭据默认存系统钥匙串，直接读 api_key 为空
            self.key.text() or provider.api_key or self._secret_store_type().get(provider.api_key_ref),
            float(self.timeout.value()),
            float(self.temperature.value()),
            int(self.tokens.value()),
            verify_ssl=not self.skip_ssl.isChecked(),
            thinking=str(self.thinking_mode.currentData() or "auto"),
            reasoning_effort=str(self.reasoning_effort.text().strip()),
            api_protocol=str(self.api_protocol.currentData() or "openai_chat"),
        )

    def _run_test(self) -> None:
        if self._test_thread is not None and self._test_thread.is_alive():
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.test_result.setText("正在连接模型服务…")
        self._test_thread = threading.Thread(
            target=self._run_test_worker,
            args=(self.provisional_config(),),
            daemon=True,
            name="pet-modern-settings-connection-test",
        )
        self._test_thread.start()

    def _run_test_worker(self, provider) -> None:
        result = self._test_connection(provider, timeout=10.0)
        # 对话框可能在测试在飞时被关闭销毁（WA_DeleteOnClose）：直接
        # self.test_done.emit 会对已删 C++ 对象 RuntimeError。
        _safe_emit(self, "test_done", *result)

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        self.test_result.setText(message)
        self.test_result.setStyleSheet("color: #16843b;" if ok else "color: #c9362b;")
        self._test_thread = None

    def _run_agent_test(self) -> None:
        if self._agent_test_thread is not None and self._agent_test_thread.is_alive():
            return
        from ..desktop_agent import normalized_settings
        settings = normalized_settings({'gateway_url': self.agent_url.text()})
        token = self.agent_key.text().strip()
        self.agent_test_button.setEnabled(False)
        self.agent_test_result.setText('正在检测本地 Gateway…')
        self._agent_test_thread = threading.Thread(
            target=self._agent_test_worker, args=(settings, token), daemon=True,
            name='pet-openclaw-connection-test')
        self._agent_test_thread.start()

    def _agent_test_worker(self, settings, token) -> None:
        from ..desktop_agent import test_gateway
        try:
            result = test_gateway(settings, token=token)
        except Exception as exc:
            result = False, '配置错误：' + str(exc)
        _safe_emit(self, 'agent_test_done', *result)

    def _on_agent_test_done(self, ok: bool, message: str) -> None:
        self.agent_test_button.setEnabled(True)
        self.agent_test_result.setText(message)
        self.agent_test_result.setStyleSheet('color: #16843b;' if ok else 'color: #c9362b;')
        self._agent_test_thread = None

    def _save_desktop_agent(self) -> None:
        from ..desktop_agent import TOKEN_REF, normalized_settings
        if self._agent_edited:
            settings = normalized_settings({
                'enabled': self.agent_enabled.isChecked(),
                'gateway_url': self.agent_url.text(),
                'agent_model': self.agent_model.text(),
                'backend_model': self.agent_backend_model.text(),
                'thinking_level': str(self.agent_thinking.currentData() or 'default'),
                'include_chat_context': self.agent_context_summary.isChecked(),
                'windows_allowlist': self.agent_allowed_windows.toPlainText(),
            })
            # Reject remote endpoints at save time, not only while dispatching.
            from ..desktop_agent import gateway_endpoint
            try:
                gateway_endpoint(settings['gateway_url'], '/v1/models')
            except ValueError as exc:
                QMessageBox.warning(self, '电脑 Agent 地址无效', str(exc))
                settings['enabled'] = False
            self.config.set('desktop_agent', settings)
            self._agent_edited = False
        token = self.agent_key.text().strip()
        if token:
            if not self._secret_store_type().set(TOKEN_REF, token):
                QMessageBox.warning(self, '安全存储不可用',
                    'OpenClaw Token 无法写入系统凭据；未保存明文。请修复 keyring 或通过 OPENCLAW_GATEWAY_TOKEN 环境变量提供。')
            self.agent_key.clear()

    def _speech_min_changed(self, _value) -> None:
        self._autonomy_edited.add("autonomy_speech_min_minutes")
        if self.autonomy_speech_min.value() > self.autonomy_speech_max.value():
            self.autonomy_speech_max.setValue(self.autonomy_speech_min.value())

    def _speech_max_changed(self, _value) -> None:
        self._autonomy_edited.add("autonomy_speech_max_minutes")
        if self.autonomy_speech_max.value() < self.autonomy_speech_min.value():
            self.autonomy_speech_min.setValue(self.autonomy_speech_max.value())

    def save(self) -> None:
        self._save_desktop_agent()
        # 保存前基于磁盘最新配置重取快照：另一个设置窗口（AI 设置/桌宠设置 AI 页）
        # 可能在本窗口打开期间改过 Provider 结构；先保留本窗口的 provider 草稿/
        # 新增/删除，再与磁盘快照合并，避免覆盖其它窗口新增的结构。
        old_settings = self.settings
        self._capture_current_draft()
        self.settings = self.config.chat_settings()
        for pid in list(self.settings.providers):
            if pid in self._deleted_provider_ids:
                self.settings.providers.pop(pid, None)
        for pid, provider in old_settings.providers.items():
            if pid not in self.settings.providers and pid not in self._deleted_provider_ids:
                self.settings.providers[pid] = provider
        active_pid = self.provider_combo.currentData()
        if active_pid in self.settings.providers:
            self.settings.active_provider = active_pid
        elif self.settings.providers:
            self.settings.active_provider = next(iter(self.settings.providers))
        for pid in list(self.settings.providers):
            self._apply_draft_to_provider(pid)
        self.settings.default_system_prompt = self.prompt.toPlainText().strip()
        if self.settings.autonomy_provider not in self.settings.providers:
            self.settings.autonomy_provider = self.settings.active_provider
        # 合并最新磁盘配置后，只回写实际编辑过的自主行为字段。
        if "autonomy_provider" in self._autonomy_edited:
            pid = str(self.autonomy_provider_combo.currentData() or "")
            self.settings.autonomy_provider = pid if pid in self.settings.providers else self.settings.active_provider
        if "autonomy_model" in self._autonomy_edited:
            self.settings.autonomy_model = self.autonomy_model.text().strip()
        if "autonomy_thinking" in self._autonomy_edited:
            self.settings.autonomy_thinking = str(self.autonomy_thinking.currentData() or "")
        if "autonomy_reasoning_effort" in self._autonomy_edited:
            self.settings.autonomy_reasoning_effort = self.autonomy_effort.text().strip()[:64]
        if "autonomy_enabled" in self._autonomy_edited:
            self.settings.autonomy_enabled = self.autonomy_enabled.isChecked()
        if "autonomy_screen_enabled" in self._autonomy_edited:
            self.settings.autonomy_screen_enabled = self.autonomy_screen.isChecked()
        if "autonomy_screen_whitelist" in self._autonomy_edited:
            from ..autonomy_vision import normalize_whitelist
            raw = [line.strip() for line in self.autonomy_whitelist.toPlainText().splitlines() if line.strip()]
            self.settings.autonomy_screen_whitelist = list(normalize_whitelist(raw))
        if "autonomy_interval_seconds" in self._autonomy_edited:
            self.settings.autonomy_interval_seconds = int(self.autonomy_interval.value())
        # Chat window owns web search preferences now. Never overwrite its newer
        # values with hidden settings widgets created when this page was opened.
        self.config.set("chat_ui_style", self.chat_ui_style.currentData())
        self._capture_background_value()
        # 按风格脏标记合并：只回写本窗口编辑过的风格，未触风格保持磁盘最新值
        # （宿主 save 前已 reload），打开期间外部对它们的即存改动不被快照回滚。
        # 粒度是风格而非按键：动过某风格即整组写回该风格的三个键。
        if "classic" in self._bg_edited_styles:
            self.config.set("chat_background", self._background_values["classic"])
            self.config.set("chat_background_opacity", self._background_display["classic"]["opacity"])
            self.config.set("chat_background_fill", self._background_display["classic"]["fill"])
        if "modern" in self._bg_edited_styles:
            self.config.set("modern_chat_background", self._background_values["modern"])
            self.config.set("modern_chat_background_opacity", self._background_display["modern"]["opacity"])
            self.config.set("modern_chat_background_fill", self._background_display["modern"]["fill"])
        if self._bg_crop_edits:
            # save() 跑在宿主 config.reload() 之后，这里读到的是磁盘最新：只覆盖
            # 本窗口编辑/重置过的键，外部对其他背景的即存改动原样保留。
            # 注意：此处绝不能再 reload()——本页 save 之前宿主已写入其它页的键，
            # reload 会把它们全部冲掉。
            crops = dict(self.config.get("chat_bg_crops", {}) or {})
            for key, box in self._bg_crop_edits.items():
                if box is None:
                    crops.pop(key, None)
                else:
                    crops[key] = box
            self.config.set("chat_bg_crops", crops)
            self._bg_crop_edits.clear()  # 落盘后编辑集失效：页面若复用，None 删除标记不得再次删外部新写入
        self.config.set("modern_chat_card_opacity", self.message_card_opacity.value())
        # 系统通知按脏标记合并：未拨动开关保持磁盘最新值（宿主 save 前已 reload），
        # 打开期间外部（老聊天设置即存）对该键的改动不被构造期快照回滚。
        if self._system_notify_dirty:
            self.config.set("system_notifications_enabled", self.system_notify_check.isChecked())
        self.config.set_chat_settings(self.settings)
        # These are global preferences, not ChatSettings fields. Saving after
        # set_chat_settings avoids losing them to ChatSettings.to_dict().
        if "autonomy_speech_min_minutes" in self._autonomy_edited:
            self.config.set("autonomy_speech_min_minutes", int(self.autonomy_speech_min.value()))
        if "autonomy_speech_max_minutes" in self._autonomy_edited:
            self.config.set("autonomy_speech_max_minutes", int(self.autonomy_speech_max.value()))
