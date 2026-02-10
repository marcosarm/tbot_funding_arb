from __future__ import annotations

from dataclasses import dataclass

import pyarrow.fs as fs


@dataclass(frozen=True, slots=True)
class S3Config:
    region: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    session_token: str | None = None


def make_s3_filesystem(cfg: S3Config) -> fs.S3FileSystem:
    """Create an Arrow S3 filesystem.

    If access_key/secret_key are None, Arrow/AWS SDK's default credential chain
    will be used (env vars, profiles, instance roles, etc.).
    """

    kwargs: dict[str, object] = {}
    if cfg.region:
        kwargs["region"] = cfg.region
    if cfg.access_key:
        kwargs["access_key"] = cfg.access_key
    if cfg.secret_key:
        kwargs["secret_key"] = cfg.secret_key
    if cfg.session_token:
        kwargs["session_token"] = cfg.session_token

    return fs.S3FileSystem(**kwargs)

