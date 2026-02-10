from __future__ import annotations

from pathlib import Path

import pyarrow.fs as fs


def resolve_filesystem_and_path(path_or_uri: str | Path) -> tuple[fs.FileSystem | None, str]:
    """Resolve a local path or URI into (filesystem, path).

    - For local filesystem paths, returns (None, <path_str>) so pyarrow defaults apply.
    - For URIs like s3://..., returns (filesystem, <path_without_scheme_and_bucket>).
    """

    s = str(path_or_uri)
    if "://" not in s:
        return None, s

    filesystem, path = fs.FileSystem.from_uri(s)
    return filesystem, path


def resolve_path(path_or_uri: str | Path) -> str:
    """Resolve a local path or URI into a path string usable with an explicit filesystem."""

    s = str(path_or_uri)
    if "://" not in s:
        return s

    # Avoid creating new filesystem objects if we only need the path part.
    if s.startswith("s3://"):
        return s[len("s3://") :]

    filesystem, path = fs.FileSystem.from_uri(s)
    return path
