"""Prevent held-activity ticks from calling undefined BehaviorController helpers.

Uses AST so it can run on build machines without PySide6 installed.
"""

import ast
import unittest
from pathlib import Path


class BehaviorControllerContractTests(unittest.TestCase):
    def test_all_private_self_method_calls_exist(self):
        source = Path(__file__).resolve().parents[1] / "pet" / "sprite_behavior.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        controller = next(node for node in module.body
                          if isinstance(node, ast.ClassDef)
                          and node.name == "BehaviorController")
        methods = {node.name for node in controller.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        private_calls = {node.func.attr for node in ast.walk(controller)
                         if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute)
                         and isinstance(node.func.value, ast.Name)
                         and node.func.value.id == "self"
                         and node.func.attr.startswith("_")}
        self.assertIn("_bound_clip", methods)
        self.assertEqual(private_calls - methods, set())


if __name__ == "__main__":
    unittest.main()
