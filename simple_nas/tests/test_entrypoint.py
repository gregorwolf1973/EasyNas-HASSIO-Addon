#!/usr/bin/env python3
"""Everything the __main__ block calls must be defined above it.

Regression for 3.2.3: serve() was defined below the block and the add-on died
with NameError on start. An import-based test cannot catch this, so this one
inspects the source order.
"""
import ast
import builtins
import os
import unittest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "app.py")


class EntrypointOrderTest(unittest.TestCase):
    def test_main_block_only_uses_names_defined_above_it(self):
        with open(APP, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        main_idx = next(i for i, n in enumerate(tree.body)
                        if isinstance(n, ast.If) and getattr(n.test.left, "id", "") == "__name__")
        defined_above = set()
        for n in tree.body[:main_idx]:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                defined_above.add(n.name)
            elif isinstance(n, ast.Assign):
                defined_above.update(t.id for t in n.targets if isinstance(t, ast.Name))
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                defined_above.update((a.asname or a.name).split(".")[0] for a in n.names)
        called = {c.func.id for c in ast.walk(tree.body[main_idx])
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        missing = called - defined_above - set(dir(builtins))
        self.assertEqual(missing, set(), f"im Startblock benutzt, aber erst darunter definiert: {missing}")


if __name__ == "__main__":
    unittest.main()
