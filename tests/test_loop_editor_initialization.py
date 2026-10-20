"""Regression test for saved loop endpoints being overwritten on dialog open."""

import ast
import unittest
from pathlib import Path


class _FakeSlider:
    def __init__(self):
        self.value = 0
        self.blocked = False
        self.signals_emitted = 0

    def blockSignals(self, blocked):
        self.blocked = blocked

    def setValue(self, value):
        self.value = value
        if not self.blocked:
            self.signals_emitted += 1


class LoopEditorInitializationTests(unittest.TestCase):
    def test_two_saved_endpoints_loaded_without_intermediate_signal(self):
        file = Path(__file__).resolve().parents[1] / "pet" / "loop_editor.py"
        parsed = ast.parse(file.read_text(encoding="utf-8"))
        editor_cls = next(n for n in parsed.body if isinstance(n, ast.ClassDef)
                          and n.name == "ActivityLoopEditor")
        load = next(n for n in editor_cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_set_slider_range")
        ns = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[load], type_ignores=[])),
                     str(file), "exec"), ns)

        class EditorStub:
            def __init__(self):
                self._start = _FakeSlider()
                self._end = _FakeSlider()

            def _range_changed(self):
                self.labels_updated = True

        stub = EditorStub()
        ns["_set_slider_range"](stub, 35, 45)
        self.assertEqual((stub._start.value, stub._end.value), (35, 45))
        self.assertEqual(stub._start.signals_emitted + stub._end.signals_emitted, 0)
        self.assertTrue(stub.labels_updated)

        initializer = next(n for n in editor_cls.body
                           if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        method_text = ast.unparse(initializer)
        self.assertLess(method_text.index("self._set_slider_range(*current)"),
                        method_text.index("slider.valueChanged.connect"))


if __name__ == "__main__":
    unittest.main()
