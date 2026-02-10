from __future__ import annotations

from dataclasses import dataclass, field

from .types import Side


@dataclass(slots=True)
class Position:
    qty: float = 0.0  # base qty. +long / -short
    avg_price: float = 0.0  # average entry price for the open qty (USDT)


@dataclass(slots=True)
class Portfolio:
    """Simple position + realized PnL tracker (suitable for futures-style backtests)."""

    realized_pnl_usdt: float = 0.0
    fees_paid_usdt: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)

    def _pos(self, symbol: str) -> Position:
        p = self.positions.get(symbol)
        if p is None:
            p = Position()
            self.positions[symbol] = p
        return p

    def apply_fill(
        self,
        symbol: str,
        side: Side,
        qty: float,
        price: float,
        fee_usdt: float = 0.0,
    ) -> None:
        if qty <= 0:
            return

        pos = self._pos(symbol)

        signed = qty if side == "buy" else -qty
        new_qty = pos.qty + signed
        # Avoid tiny floating residuals becoming unintended flips / dust positions.
        if abs(new_qty) <= 1e-12:
            new_qty = 0.0

        # Fee is always a cost.
        self.fees_paid_usdt += float(fee_usdt)
        self.realized_pnl_usdt -= float(fee_usdt)

        # Full close (without flip): realize PnL and reset avg_price.
        if new_qty == 0.0 and pos.qty != 0.0:
            closed_qty = abs(pos.qty)
            pnl = closed_qty * (price - pos.avg_price) * (1.0 if pos.qty > 0 else -1.0)
            self.realized_pnl_usdt += pnl
            pos.qty = 0.0
            pos.avg_price = 0.0
            return

        # If position direction stays the same (or was flat), update avg_price.
        if pos.qty == 0.0 or (pos.qty > 0 and new_qty > 0) or (pos.qty < 0 and new_qty < 0):
            if new_qty == 0.0:
                pos.qty = 0.0
                pos.avg_price = 0.0
                return

            # Weighted average for increasing exposure.
            if pos.qty == 0.0:
                pos.avg_price = price
                pos.qty = new_qty
                return

            if abs(new_qty) > abs(pos.qty):
                # Increasing same-direction exposure.
                old_notional = abs(pos.qty) * pos.avg_price
                add_notional = abs(signed) * price
                pos.avg_price = (old_notional + add_notional) / abs(new_qty)
                pos.qty = new_qty
                return

            # Reducing without flipping: realize pnl for the reduced part.
            closed_qty = abs(signed)
            pnl = closed_qty * (price - pos.avg_price) * (1.0 if pos.qty > 0 else -1.0)
            self.realized_pnl_usdt += pnl
            pos.qty = new_qty
            if pos.qty == 0.0:
                pos.avg_price = 0.0
            return

        # Position flipped direction: close old fully, open new residual.
        closed_qty = abs(pos.qty)
        pnl = closed_qty * (price - pos.avg_price) * (1.0 if pos.qty > 0 else -1.0)
        self.realized_pnl_usdt += pnl

        pos.qty = new_qty
        pos.avg_price = price  # new direction entry

    def apply_funding(self, symbol: str, mark_price: float, funding_rate: float) -> float:
        """Apply funding payment for a perp position.

        Funding PnL (to the account) is:
          - position_notional * funding_rate
        where position_notional = qty * mark_price.
        This matches: positive funding => longs pay, shorts receive.
        """

        pos = self.positions.get(symbol)
        if pos is None or pos.qty == 0.0:
            return 0.0

        pnl = -(pos.qty * mark_price) * funding_rate
        self.realized_pnl_usdt += pnl
        return pnl
