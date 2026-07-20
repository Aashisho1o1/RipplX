"""Keep every emitted finding-drop code understandable in the audit UI."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

from finwatch.llm.harness import _SKEPTIC_CODES

ROOT = Path(__file__).parents[1]


def test_every_drop_code_has_a_user_facing_label():
    tree = ast.parse((ROOT / "src/finwatch/verify/compiler.py").read_text())
    emitted: set[str] = set(get_args(_SKEPTIC_CODES))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "CompilerIssue":
            for keyword in node.keywords:
                if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                    emitted.add(keyword.value.value)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "run_errors"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            emitted.add(node.args[0].value)

    source = (ROOT / "web/src/components/ProvenancePanel.tsx").read_text()
    block = source.split("export const DROP_CODE_LABEL", 1)[1].split("};", 1)[0]
    labelled = set(re.findall(r"^\s*([A-Z][A-Z0-9_]*):", block, re.MULTILINE))

    # The compiler and harness currently emit 17 finding-level codes. Keep the
    # count explicit so a new public certificate code requires a deliberate UI
    # label review, rather than silently reaching users as an opaque enum.
    assert len(emitted) == 17
    assert emitted <= labelled
