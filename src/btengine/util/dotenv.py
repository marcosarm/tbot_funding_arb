from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DotenvResult:
    values: dict[str, str]
    path: Path


def load_dotenv(path: str | Path, *, override: bool = False) -> DotenvResult:
    """Load a minimal `.env` file into `os.environ`.

    This is intentionally tiny (no external deps) and supports:
    - `KEY=VALUE`
    - optional quotes: KEY="VALUE" / KEY='VALUE'
    - whitespace around KEY/`=`/VALUE
    - comments starting with `#` (full-line only)
    """

    import os

    p = Path(path)
    values: dict[str, str] = {}

    if not p.exists():
        return DotenvResult(values=values, path=p)

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()

        if not key:
            continue

        # Strip surrounding quotes.
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]

        values[key] = val

        if override or key not in os.environ:
            os.environ[key] = val

    return DotenvResult(values=values, path=p)

