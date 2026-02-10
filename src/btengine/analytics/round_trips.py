from __future__ import annotations

from dataclasses import dataclass

from ..broker import Fill
from ..portfolio import Portfolio


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A simple "round-trip" trade reconstructed from fills.

    Definition:
    - A trade "opens" when the position for a symbol goes from 0 -> non-zero.
    - A trade "closes" when the position returns to 0 (or flips direction).

    Notes:
    - PnL is computed from fills only (funding is not included).
    - Fees are included in `net_pnl_usdt` (same semantics as `Portfolio`).
    """

    symbol: str
    direction: str  # "long" | "short"
    open_time_ms: int
    close_time_ms: int

    net_pnl_usdt: float
    gross_pnl_usdt: float
    fees_usdt: float

    max_abs_qty: float

    @property
    def duration_ms(self) -> int:
        return int(self.close_time_ms) - int(self.open_time_ms)


@dataclass(frozen=True, slots=True)
class RoundTripSummary:
    trades: int
    wins: int
    losses: int
    win_rate: float | None

    net_pnl_usdt: float
    gross_pnl_usdt: float
    fees_usdt: float

    avg_net_pnl_usdt: float | None
    avg_duration_ms: float | None
    max_duration_ms: int | None


@dataclass(slots=True)
class _OpenState:
    open_time_ms: int
    direction: str
    realized_start: float
    fees_start: float
    max_abs_qty: float


def round_trips_from_fills(fills: list[Fill]) -> list[RoundTrip]:
    """Reconstruct per-symbol round trips from a fill list."""

    # Keep it deterministic: stable sort by timestamp and insertion order.
    fills_sorted = sorted(enumerate(fills), key=lambda x: (int(x[1].event_time_ms), x[0]))

    pf = Portfolio()
    open_state: dict[str, _OpenState] = {}
    out: list[RoundTrip] = []

    for _, f in fills_sorted:
        sym = f.symbol

        pos_before = pf.positions.get(sym)
        qty_before = float(pos_before.qty) if pos_before is not None else 0.0

        realized_before = float(pf.realized_pnl_usdt)
        fees_before = float(pf.fees_paid_usdt)

        pf.apply_fill(sym, f.side, float(f.quantity), float(f.price), fee_usdt=float(f.fee_usdt))

        pos_after = pf.positions.get(sym)
        qty_after = float(pos_after.qty) if pos_after is not None else 0.0

        realized_after = float(pf.realized_pnl_usdt)
        fees_after = float(pf.fees_paid_usdt)

        # Open.
        if qty_before == 0.0 and qty_after != 0.0:
            open_state[sym] = _OpenState(
                open_time_ms=int(f.event_time_ms),
                direction="long" if qty_after > 0.0 else "short",
                realized_start=realized_before,
                fees_start=fees_before,
                max_abs_qty=abs(qty_after),
            )
            continue

        st = open_state.get(sym)
        if st is None:
            continue

        # Track peak exposure during the trade.
        st.max_abs_qty = max(float(st.max_abs_qty), abs(qty_after))

        flipped = (qty_before > 0.0 and qty_after < 0.0) or (qty_before < 0.0 and qty_after > 0.0)

        # Close (flat or flip).
        if qty_after == 0.0 or flipped:
            fees = fees_after - float(st.fees_start)
            net = realized_after - float(st.realized_start)
            gross = net + fees
            out.append(
                RoundTrip(
                    symbol=sym,
                    direction=st.direction,
                    open_time_ms=int(st.open_time_ms),
                    close_time_ms=int(f.event_time_ms),
                    net_pnl_usdt=float(net),
                    gross_pnl_usdt=float(gross),
                    fees_usdt=float(fees),
                    max_abs_qty=float(st.max_abs_qty),
                )
            )
            open_state.pop(sym, None)

            # If we flipped (still non-zero), immediately open a new trade
            # starting at this same fill timestamp.
            if flipped and qty_after != 0.0:
                open_state[sym] = _OpenState(
                    open_time_ms=int(f.event_time_ms),
                    direction="long" if qty_after > 0.0 else "short",
                    realized_start=realized_after,
                    fees_start=fees_after,
                    max_abs_qty=abs(qty_after),
                )

    return out


def summarize_round_trips(trades: list[RoundTrip]) -> RoundTripSummary:
    if not trades:
        return RoundTripSummary(
            trades=0,
            wins=0,
            losses=0,
            win_rate=None,
            net_pnl_usdt=0.0,
            gross_pnl_usdt=0.0,
            fees_usdt=0.0,
            avg_net_pnl_usdt=None,
            avg_duration_ms=None,
            max_duration_ms=None,
        )

    wins = sum(1 for t in trades if t.net_pnl_usdt > 0.0)
    losses = sum(1 for t in trades if t.net_pnl_usdt < 0.0)

    net = sum(t.net_pnl_usdt for t in trades)
    gross = sum(t.gross_pnl_usdt for t in trades)
    fees = sum(t.fees_usdt for t in trades)

    avg_net = net / float(len(trades))
    durations = [t.duration_ms for t in trades]
    avg_dur = sum(durations) / float(len(durations))

    return RoundTripSummary(
        trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=(wins / float(len(trades))) if trades else None,
        net_pnl_usdt=float(net),
        gross_pnl_usdt=float(gross),
        fees_usdt=float(fees),
        avg_net_pnl_usdt=float(avg_net),
        avg_duration_ms=float(avg_dur),
        max_duration_ms=int(max(durations)),
    )


def max_drawdown(equity_curve: list[tuple[int, float]]) -> float | None:
    """Compute max drawdown from an equity curve (time_ms, equity_pnl).

    Returns the minimum (most negative) drawdown value.
    """

    if not equity_curve:
        return None

    peak = float("-inf")
    mdd = 0.0
    for _, eq in equity_curve:
        x = float(eq)
        if x > peak:
            peak = x
        dd = x - peak
        if dd < mdd:
            mdd = dd
    return float(mdd)

