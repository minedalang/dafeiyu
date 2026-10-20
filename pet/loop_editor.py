"""Small non-modal editor for the currently sustained pet animation.

The editor never edits or regenerates sprites. Preferences are persisted per
animation and picked up by the running pet's frame loop within about a second.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
)

from .animation_loops import (
    LOOP_SETTINGS_PATH, SLEEP_ANIMATION_NAME,
    read_clip_loop_settings, read_loop_percentages, save_clip_loop,
)


class ActivityLoopEditor(QDialog):
    def __init__(self, animation: str, parent=None):
        super().__init__(parent)
        self.animation = animation
        self.setWindowTitle("持续动画 · 循环帧调节")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(410)

        root = QVBoxLayout(self)
        title = QLabel(f"当前持续动画：{animation}", self)
        title.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(QLabel("拖动后松开滑块即可保存；在桌面观察小鲸鱼是否接得自然。", self))

        self._start_label = QLabel(self)
        self._end_label = QLabel(self)
        self._start = QSlider(Qt.Orientation.Horizontal, self)
        self._end = QSlider(Qt.Orientation.Horizontal, self)
        for slider in (self._start, self._end):
            slider.setRange(0, 100)
            slider.setSingleStep(1)
            slider.setPageStep(1)
        root.addWidget(self._start_label)
        root.addWidget(self._start)
        root.addWidget(self._end_label)
        root.addWidget(self._end)

        # Read the persisted per-clip override directly, even if the player
        # process has a one-second hot-reload cache. Initialize *both* sliders
        # before connecting valueChanged: the end slider starts at zero, and
        # the validation handler would otherwise clamp a saved start to 0%.
        saved = read_clip_loop_settings()
        current = saved.get(animation) or (
            read_loop_percentages(LOOP_SETTINGS_PATH)
            if animation == SLEEP_ANIMATION_NAME else (35.0, 67.0))
        self._set_slider_range(*current)
        for slider in (self._start, self._end):
            slider.valueChanged.connect(self._range_changed)
            slider.sliderReleased.connect(self._save)
        self._note = QLabel("", self)
        self._note.setWordWrap(True)
        root.addWidget(self._note)
        if animation not in saved:
            self._note.setText("当前尚未设置独立循环区间；按“应用”后立即启用。")

        buttons = QHBoxLayout()
        apply_button = QPushButton("应用当前范围", self)
        apply_button.clicked.connect(self._save)
        buttons.addWidget(apply_button)
        reset_button = QPushButton("恢复默认", self)
        reset_button.clicked.connect(self._reset)
        buttons.addWidget(reset_button)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def _set_slider_range(self, start: float, end: float):
        """Apply both endpoints atomically, without intermediate validation."""
        for slider in (self._start, self._end):
            slider.blockSignals(True)
        try:
            self._start.setValue(round(start))
            self._end.setValue(round(end))
        finally:
            for slider in (self._start, self._end):
                slider.blockSignals(False)
        self._range_changed()

    def _range_changed(self, *_):
        first, last = self._start.value(), self._end.value()
        if last - first < 2:
            # Block recursive valueChanged emission while enforcing the
            # minimum two-percentage-point range.
            moved = self.sender()
            slider = self._start if moved is self._start else self._end
            slider.blockSignals(True)
            if moved is self._start:
                slider.setValue(max(0, last - 2))
            else:
                slider.setValue(min(100, first + 2))
            slider.blockSignals(False)
        self._start_label.setText(f"循环开始：{self._start.value()}%")
        self._end_label.setText(f"循环结束：{self._end.value()}%")

    def _save(self):
        try:
            save_clip_loop(self.animation, self._start.value(), self._end.value())
        except (OSError, ValueError) as exc:
            self._note.setText(f"保存失败：{type(exc).__name__}。请检查程序目录写权限。")
        else:
            self._note.setText("已保存。正在播放的持续动画约 1 秒内读取新位置。")

    def _reset(self):
        try:
            save_clip_loop(self.animation, None, None)
        except (OSError, ValueError) as exc:
            self._note.setText(f"恢复失败：{type(exc).__name__}")
            return
        percentages = (read_loop_percentages(LOOP_SETTINGS_PATH)
                       if self.animation == SLEEP_ANIMATION_NAME else (35.0, 67.0))
        self._set_slider_range(*percentages)
        if self.animation == SLEEP_ANIMATION_NAME:
            self._note.setText("已恢复 sleep-loop.json 中的睡眠默认区间。")
        else:
            self._note.setText("已取消独立区间；下一次将完整播放这段动画。")
