"""Optional Pomodoro control window, with a process-level timer that survives hiding."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout)

from .pomodoro import PomodoroClock, PomodoroSettings


class PomodoroService(QObject):
    updated = Signal()
    transitioned = Signal(str)

    def __init__(self, directory=None, parent=None):
        super().__init__(parent)
        self.path = Path(directory) / 'pomodoro-settings.json' if directory else None
        self.clock = PomodoroClock(self._load_settings())
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def _load_settings(self):
        if self.path is not None:
            try:
                if self.path.is_file() and self.path.stat().st_size < 4096:
                    data = json.loads(self.path.read_text(encoding='utf-8'))
                    if isinstance(data, dict):
                        return PomodoroSettings(**{
                            key: data[key] for key in PomodoroSettings.__dataclass_fields__
                            if key in data}).normalized()
            except (OSError, ValueError, TypeError):
                pass
        return PomodoroSettings()

    def change_settings(self, settings):
        self.clock.set_settings(settings)
        if self.path is not None:
            target = self.path
            temporary = target.with_name(f'{target.name}.{os.getpid()}.tmp')
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(vars(self.clock.settings)), encoding='utf-8')
                os.replace(temporary, target)
            except OSError:
                pass
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self.updated.emit()

    def _tick(self):
        phase = self.clock.tick()
        if phase:
            self.transitioned.emit(phase)
        self.updated.emit()

    def toggle(self):
        self.clock.start_pause()
        if self.clock.running:
            self.timer.start()
        else:
            self.timer.stop()
        self.updated.emit()

    def skip(self):
        phase = self.clock.skip()
        self.transitioned.emit(phase)
        self.updated.emit()

    def reset(self):
        self.timer.stop()
        self.clock.reset()
        self.updated.emit()


class PomodoroDialog(QDialog):
    def __init__(self, service: PomodoroService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle('小鲸鱼 · 番茄钟')
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setMinimumWidth(310)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet('font-size: 16px; font-weight: bold')
        layout.addWidget(self.status)
        self.counter = QLabel()
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter.setStyleSheet('font-size: 44px; font-weight: bold')
        layout.addWidget(self.counter)
        self.rounds = QLabel()
        self.rounds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rounds)
        btns = QHBoxLayout()
        self.toggle_button = QPushButton('开始')
        self.toggle_button.clicked.connect(service.toggle)
        btns.addWidget(self.toggle_button)
        skip = QPushButton('跳过')
        skip.clicked.connect(service.skip)
        btns.addWidget(skip)
        reset = QPushButton('重置')
        reset.clicked.connect(service.reset)
        btns.addWidget(reset)
        layout.addLayout(btns)
        layout.addWidget(QLabel('计时设置（修改后在下一阶段生效）'))
        form = QFormLayout()
        self.spins = {}
        for key, label, maximum in (
            ('focus_minutes', '专注', 180), ('short_break_minutes', '短休息', 60),
            ('long_break_minutes', '长休息', 120), ('long_break_every', '长休息间隔（轮）', 12),
        ):
            spin = QSpinBox(self)
            spin.setRange(2 if key == 'long_break_every' else 1, maximum)
            if key != 'long_break_every':
                spin.setSuffix(' 分钟')
            spin.setValue(getattr(service.clock.settings, key))
            self.spins[key] = spin
            form.addRow(label, spin)
        layout.addLayout(form)
        save = QPushButton('保存时间设置')
        save.clicked.connect(self.save_settings)
        layout.addWidget(save)
        layout.addWidget(QLabel('关闭窗口后计时继续；不会自动叫醒或打断小鲸鱼。'))
        service.updated.connect(self.refresh)
        self.refresh()

    def save_settings(self):
        self.service.change_settings(PomodoroSettings(**{
            key: spin.value() for key, spin in self.spins.items()}))

    def refresh(self):
        snap = self.service.clock.snapshot()
        self.status.setText({'focus': '专注时间', 'short_break': '短休息',
                             'long_break': '长休息'}[snap['phase']])
        secs = snap['remaining_seconds']
        self.counter.setText(f'{secs // 60:02d}:{secs % 60:02d}')
        self.rounds.setText(f"已完成 {snap['completed_focus']} 轮专注")
        self.toggle_button.setText('暂停' if snap['running'] else '开始 / 继续')
