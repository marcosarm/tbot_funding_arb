from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class CryptoHftLayout:
    """Path builder for the CryptoHFTData S3 layout."""

    bucket: str
    prefix: str

    def _base(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}".rstrip("/")

    def _d(self, d: date) -> str:
        return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"

    def trades(self, *, exchange: str, symbol: str, day: date) -> str:
        return f"{self._base()}/trades/{exchange}/{symbol}/{self._d(day)}/trades.parquet"

    def ticker(self, *, exchange: str, symbol: str, day: date) -> str:
        return f"{self._base()}/ticker/{exchange}/{symbol}/{self._d(day)}/ticker.parquet"

    def mark_price(self, *, exchange: str, symbol: str, day: date) -> str:
        return f"{self._base()}/mark_price/{exchange}/{symbol}/{self._d(day)}/mark_price.parquet"

    def open_interest(self, *, exchange: str, symbol: str, day: date) -> str:
        return f"{self._base()}/open_interest/{exchange}/{symbol}/{self._d(day)}/open_interest.parquet"

    def liquidations(self, *, exchange: str, symbol: str, day: date) -> str:
        return f"{self._base()}/liquidations/{exchange}/{symbol}/{self._d(day)}/liquidations.parquet"

    def orderbook(self, *, exchange: str, symbol: str, day: date, hour: int) -> str:
        if not (0 <= hour <= 23):
            raise ValueError("hour must be 0..23")
        return (
            f"{self._base()}/orderbook/{exchange}/{symbol}/{self._d(day)}/"
            f"orderbook_{hour:02d}.parquet"
        )

