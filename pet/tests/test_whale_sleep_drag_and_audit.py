"""Regression tests for saved off-line simulations and animation-safe dragging.

These tests intentionally run without Qt / GUI: BehaviorController's drag entry
methods are loaded from their actual source AST with minimal behavioral doubles.
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import json
import tempfile
import unittest

from pet.life_state import WhaleLife

ROOT = Path(__file__).parents[1]


def stamp(day, hour):
    return datetime(2026, 10, day, hour).timestamp()


def load_behavior_handlers():
    module = ast.parse((ROOT / 'sprite_behavior.py').read_text(encoding='utf-8'))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef)
               and node.name == 'BehaviorController')
    names = {'on_drag_started', 'on_drag_released', '_tick_captured'}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in names]
    assert len(methods) == 3
    namespace = {'INTERACTION_NORMAL': 'normal',
                 'INTERACTION_DRAG': 'drag',
                 'INTERACTION_THROWN': 'thrown',
                 'STATE_HELD': 'held_activity',
                 'STATE_DRAG': 'drag',
                 'STATE_THROWN': 'thrown'}
    mod = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
                     + methods, type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), '<sprite_behavior_drag>', 'exec'), namespace)
    return namespace


class WhaleSleepDragTests(unittest.TestCase):
    def test_dragging_held_sleep_does_not_rebind_animation(self):
        handlers = load_behavior_handlers()
        class State:
            state = 'held_activity'
        class Sprite:
            interaction_state = 'drag'
        sprite = Sprite()
        state = State()
        class Controller:
            _states = {sprite: state}
            def __init__(self): self.events=[]
            def _ensure_hooks(self, s): pass
            def _enter_drag(self, *a): self.events.append('drag')
            def _enter_idle(self, *a): self.events.append('idle')
            def _enter_thrown(self, *a): self.events.append('thrown')
        ctrl = Controller()
        handlers['on_drag_started'](ctrl, sprite)
        handlers['_tick_captured'](ctrl, sprite, state)
        sprite.interaction_state = 'normal'
        handlers['on_drag_released'](ctrl, sprite)
        self.assertEqual(state.state, 'held_activity')
        self.assertEqual(ctrl.events, [])

    def test_normal_drag_still_uses_hanging_animation(self):
        handlers = load_behavior_handlers()
        class State:
            state = 'idle'
        class Sprite:
            interaction_state = 'drag'
        sprite = Sprite()
        state = State()
        class Controller:
            _states = {sprite: state}
            def __init__(self): self.events=[]
            def _ensure_hooks(self, s): pass
            def _enter_drag(self, *a): self.events.append('drag')
            def _enter_idle(self, *a): self.events.append('idle')
            def _enter_thrown(self, *a): self.events.append('thrown')
            def _categories(self, lib): return {}
        ctrl = Controller()
        handlers['on_drag_started'](ctrl, sprite)
        handlers['_tick_captured'](ctrl, sprite, state)
        sprite.interaction_state = 'normal'
        sprite.library = object()
        handlers['on_drag_released'](ctrl, sprite)
        self.assertEqual(ctrl.events, ['drag', 'drag', 'idle'])

    def test_legacy_protected_drag_checks_current_sleep_clip(self):
        source = (ROOT / 'window.py').read_text(encoding='utf-8')
        self.assertIn('if self.drag and not (sleep_clip and self.anim == sleep_clip):',source)
        self.assertIn('and not (sleep_clip and self.anim == sleep_clip)',source)


class OfflineAuditTests(unittest.TestCase):
    def test_night_records_before_after_and_prevents_double_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            life = WhaleLife(folder, now=stamp(20, 19))
            life.values['sleepiness'] = 90
            life.values['energy'] = 10
            life.save(force=True)
            morning = WhaleLife(folder, now=stamp(21, 8))
            record = morning.offline_settlements[-1]
            self.assertAlmostEqual(record['sleep_minutes'], 540)
            self.assertAlmostEqual(record['night_sleep_minutes'], 540)
            self.assertEqual(record['daytime_nap_minutes'], 0)
            self.assertEqual(record['before']['sleepiness'], 90)
            self.assertLess(record['after']['sleepiness'], 90)
            self.assertEqual(record['finished_at'], stamp(21,8))
            filedata=json.loads(Path(folder,'whale-life-state.json').read_text(encoding='utf-8'))
            self.assertEqual(filedata['offline_settlements'][-1],record)
            repeat=WhaleLife(folder,now=stamp(21,8))
            self.assertEqual(repeat.offline_settlements,morning.offline_settlements)
            self.assertEqual(repeat.values,morning.values)

    def test_daytime_does_not_invent_sleep_but_logs_calculation(self):
        with tempfile.TemporaryDirectory() as folder:
            life=WhaleLife(folder,now=stamp(20,12));life.save(force=True)
            later=WhaleLife(folder,now=stamp(20,16))
            row=later.offline_settlements[-1]
            self.assertEqual(row['sleep_minutes'],0)
            self.assertEqual(row['night_sleep_minutes'],0)
            self.assertEqual(row['daytime_nap_minutes'],0)

    def test_audit_window_trims_and_records_discarded_time(self):
        with tempfile.TemporaryDirectory() as folder:
            start=stamp(12,8);end=stamp(21,8)
            life=WhaleLife(folder,now=start);life.save(force=True)
            later=WhaleLife(folder,now=end)
            row=later.offline_settlements[-1]
            self.assertGreater(row['discarded_minutes'],0)
            self.assertGreaterEqual(row['night_sleep_minutes'],0)
            self.assertEqual(row['settled_from_at'],end-24*3600)
            self.assertLessEqual(len(later.offline_settlements),24)


if __name__ == '__main__': unittest.main()
