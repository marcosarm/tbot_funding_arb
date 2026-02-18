"""Pytest configuration.

`btengine` is expected to be installed as an external dependency (repo separado).
We intentionally do not prepend `ROOT/src` to `sys.path`, to avoid importing a
vendored copy by accident.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ensure the local `funding/` package is importable when tests run
# without installing the project.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
