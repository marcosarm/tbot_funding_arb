"""Analytics helpers for btengine.

These helpers are intentionally lightweight (no external deps) so they can be
used in scripts/tests and exported into other projects.
"""

from .round_trips import RoundTrip, RoundTripSummary, max_drawdown, round_trips_from_fills, summarize_round_trips

__all__ = [
    "RoundTrip",
    "RoundTripSummary",
    "round_trips_from_fills",
    "summarize_round_trips",
    "max_drawdown",
]

