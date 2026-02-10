"""btengine: generic, event-driven backtest components.

The core engine is intentionally exchange-agnostic. Dataset/exchange specific
adapters live under `btengine.data.*`.
"""

from .engine import BacktestEngine, EngineConfig, Strategy
from .types import DepthUpdate, Liquidation, MarkPrice, OpenInterest, Side, Ticker, Trade

__all__ = [
    "BacktestEngine",
    "EngineConfig",
    "Strategy",
    "DepthUpdate",
    "Trade",
    "MarkPrice",
    "Ticker",
    "OpenInterest",
    "Liquidation",
    "Side",
]
