"""Predicates the AST sweeps share.

Each sweep kept its own copy and they drifted: two of them pinned ``str(...)``
alone for a while, so the same value reached the same sink spelled as an
f-string.
"""

from __future__ import annotations

import ast


def renders_a_value_as_text(node: ast.expr) -> bool:
    """Python's four ways of interpolating a value into a string.

    Pinning ``str(...)`` alone left ``f"{error}"`` and ``"%s" % error`` reaching
    the same sink with the class name already gone.
    """
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.Mod)
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "format"
    return isinstance(node.func, ast.Name) and node.func.id == "str"
