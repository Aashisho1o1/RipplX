"""Ensure the src-layout package is importable during test collection.

Redundant with pyproject's ``[tool.pytest.ini_options] pythonpath = ["src"]``, but
bulletproof against editable-install quirks: on some Python builds uv's editable
``.pth`` is not honored by ``site``. Tests always run against live source in ``src/``.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
