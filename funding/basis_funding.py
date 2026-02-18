from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from btengine.engine import EngineContext
from btengine.execution.orders import Order
from btengine.marketdata import L2Book
from btengine.types import DepthUpdate, MarkPrice

StrategyState = Literal["flat", "standard", "reverse"]


def dynamic_z_threshold(vol_ratio: float) -> float:
    """Map volatility regime into dynamic z-entry threshold.

    Spec mapping:
    - vol_ratio < 0.8  => 1.5
    - vol_ratio > 1.5  => 3.0
    - otherwise        => 2.0
    """

    x = float(vol_ratio)
    if x < 0.8:
        return 1.5
    if x > 1.5:
        return 3.0
    return 2.0


def should_exit_mean_reversion(z: float, z_exit_eps: float) -> bool:
    return abs(float(z)) <= float(z_exit_eps)


def should_exit_hard_stop(z: float, z_hard_stop: float) -> bool:
    return abs(float(z)) >= float(z_hard_stop)


def basis_signal_mid(mid_perp: float, mid_future: float) -> float:
    if float(mid_perp) <= 0.0:
        return math.nan
    return (float(mid_future) - float(mid_perp)) / float(mid_perp)


def _std_population(values: list[float]) -> float:
    n = len(values)
    if n <= 1:
        return 0.0
    m = sum(values) / float(n)
    var = sum((v - m) ** 2 for v in values) / float(n)
    return math.sqrt(var)


def _safe_z(x: float, mean: float, std: float, *, eps: float = 1e-12) -> float:
    if abs(float(std)) <= float(eps):
        return 0.0
    return (float(x) - float(mean)) / float(std)


def side_liquidity_notional(book: L2Book, side: Literal["bid", "ask"], *, mid_price: float, depth_pct: float) -> float:
    """Notional liquidity around mid within a relative band.

    - bid side: [mid*(1-depth_pct), mid]
    - ask side: [mid, mid*(1+depth_pct)]
    """

    mid = float(mid_price)
    if mid <= 0.0:
        return 0.0

    lo = mid * (1.0 - float(depth_pct))
    hi = mid * (1.0 + float(depth_pct))

    total = 0.0
    if side == "bid":
        for p, q in book.bids.items():
            if p < lo or p > mid or q <= 0.0:
                continue
            total += float(p) * float(q)
        return total

    if side == "ask":
        for p, q in book.asks.items():
            if p < mid or p > hi or q <= 0.0:
                continue
            total += float(p) * float(q)
        return total

    raise ValueError(f"invalid side: {side!r}")


def has_min_liquidity(
    book: L2Book,
    side: Literal["bid", "ask"],
    *,
    mid_price: float,
    depth_pct: float,
    order_notional: float,
    min_ratio: float,
) -> bool:
    liq = side_liquidity_notional(book, side, mid_price=float(mid_price), depth_pct=float(depth_pct))
    need = float(order_notional) * float(min_ratio)
    return liq >= need


def execution_cost_std_rev(perp_book: L2Book, future_book: L2Book, *, impact_notional_usdt: float) -> tuple[float, float]:
    """Execution costs in basis space using impact prices.

    Returns `(cost_std, cost_rev)`:
    - standard entry: short perp / long future
      cost_std = (impact_ask_future - impact_bid_perp) / impact_bid_perp
    - reverse entry: long perp / short future
      cost_rev = (impact_bid_future - impact_ask_perp) / impact_ask_perp
    """

    n = float(impact_notional_usdt)
    if n <= 0.0:
        return math.nan, math.nan

    impact_bid_perp = perp_book.impact_vwap("sell", n)
    impact_ask_perp = perp_book.impact_vwap("buy", n)
    impact_bid_future = future_book.impact_vwap("sell", n)
    impact_ask_future = future_book.impact_vwap("buy", n)

    cost_std = math.nan
    cost_rev = math.nan

    if math.isfinite(impact_bid_perp) and impact_bid_perp > 0.0 and math.isfinite(impact_ask_future):
        cost_std = (float(impact_ask_future) - float(impact_bid_perp)) / float(impact_bid_perp)

    if math.isfinite(impact_ask_perp) and impact_ask_perp > 0.0 and math.isfinite(impact_bid_future):
        cost_rev = (float(impact_bid_future) - float(impact_ask_perp)) / float(impact_ask_perp)

    return cost_std, cost_rev


@dataclass(slots=True)
class BasisSnapshot:
    now_ms: int
    basis: float
    mean: float
    std: float
    z: float
    vol_ratio: float
    dynamic_z: float
    funding_proj: float
    cost_std: float
    cost_rev: float


@dataclass(slots=True)
class PendingLeg:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    maker_order_id: str
    maker_submit_ms: int
    maker_wait_ms: int
    taker_limit_px: float | None
    taker_order_id: str | None = None
    state: Literal["maker", "taker", "done"] = "maker"


@dataclass(slots=True)
class BasisFundingStrategy:
    """Basis+funding strategy (Perp x Quarterly) built on top of `btengine`.

    This lives in the project root (not inside the `btengine` package) to keep
    the engine reusable and exchange/dataset agnostic.
    """

    perp_symbol: str
    future_symbol: str

    impact_notional_usdt: float = 25_000.0
    funding_threshold: float = 0.0001
    max_slippage: float = 0.0005
    entry_safety_margin: float = 0.0002
    taker_fee_frac: float = 0.0005

    liquidity_min_ratio: float = 5.0
    liquidity_depth_pct: float = 0.001

    z_window: int = 1440
    vol_ratio_window: int = 60
    z_exit_eps: float = 0.2
    z_hard_stop: float = 4.0

    entry_cooldown_sec: int = 30
    maker_wait_sec: float = 5.0
    legging_check_delay_ms: int = 200
    asof_tolerance_ms: int = 100
    basis_sample_ms: int = 1000
    hedge_eps_base: float = 0.001
    allow_reverse: bool = True
    force_close_on_end: bool = True

    # State + diagnostics.
    state: StrategyState = "flat"
    last_snapshot: BasisSnapshot | None = None
    basis_history: deque[tuple[int, float]] = field(default_factory=deque)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    entries_standard: int = 0
    entries_reverse: int = 0
    exits_mean_reversion: int = 0
    exits_hard_stop: int = 0
    exits_funding_flip: int = 0
    liquidity_rejects: int = 0
    hedge_actions: int = 0

    _next_entry_allowed_ms: int = 0
    _pending_legs: list[PendingLeg] = field(default_factory=list)
    _order_fills: dict[str, float] = field(default_factory=dict)
    _fill_cursor: int = 0
    _pending_hedge_due_ms: int | None = None
    _last_basis_sample_ms: int = 0
    _last_eval_ms: int = 0
    _last_eval_symbol: str | None = None

    def __post_init__(self) -> None:
        if int(self.z_window) <= 1:
            raise ValueError("z_window must be > 1")
        if int(self.vol_ratio_window) <= 1:
            raise ValueError("vol_ratio_window must be > 1")
        if float(self.impact_notional_usdt) <= 0.0:
            raise ValueError("impact_notional_usdt must be > 0")
        if float(self.funding_threshold) < 0.0:
            raise ValueError("funding_threshold must be >= 0")
        if float(self.liquidity_min_ratio) <= 0.0:
            raise ValueError("liquidity_min_ratio must be > 0")
        if float(self.liquidity_depth_pct) <= 0.0:
            raise ValueError("liquidity_depth_pct must be > 0")
        if float(self.maker_wait_sec) < 0.0:
            raise ValueError("maker_wait_sec must be >= 0")
        if int(self.legging_check_delay_ms) < 0:
            raise ValueError("legging_check_delay_ms must be >= 0")
        if int(self.asof_tolerance_ms) < 0:
            raise ValueError("asof_tolerance_ms must be >= 0")
        if int(self.basis_sample_ms) <= 0:
            raise ValueError("basis_sample_ms must be > 0")

        self.basis_history = deque()

    def _position_qty(self, ctx: EngineContext, symbol: str) -> float:
        p = ctx.broker.portfolio.positions.get(symbol)
        return float(p.qty) if p is not None else 0.0

    def _book(self, ctx: EngineContext, symbol: str) -> L2Book | None:
        return ctx.books.get(symbol)

    def _book_mid(self, ctx: EngineContext, symbol: str) -> float | None:
        b = self._book(ctx, symbol)
        if b is None:
            return None
        m = b.mid_price()
        if m is None or m <= 0.0:
            return None
        return float(m)

    def _book_is_ready(self, ctx: EngineContext, symbol: str) -> bool:
        b = self._book(ctx, symbol)
        if b is None:
            return False
        return b.best_bid() is not None and b.best_ask() is not None

    def _funding_proj(self, ctx: EngineContext) -> float:
        mp = ctx.mark.get(self.perp_symbol)
        if mp is None:
            return float("nan")
        if abs(int(ctx.now_ms) - int(mp.event_time_ms)) > int(self.asof_tolerance_ms):
            return float("nan")
        return float(mp.funding_rate)

    def _target_leg_qtys(self, ctx: EngineContext) -> tuple[float, float] | None:
        mid_perp = self._book_mid(ctx, self.perp_symbol)
        mid_future = self._book_mid(ctx, self.future_symbol)
        if mid_perp is None or mid_future is None:
            return None

        qty_perp = float(self.impact_notional_usdt) / float(mid_perp)
        qty_future = float(self.impact_notional_usdt) / float(mid_future)
        if qty_perp <= 0.0 or qty_future <= 0.0:
            return None
        return qty_perp, qty_future

    def _submit_market(
        self, ctx: EngineContext, *, symbol: str, side: Literal["buy", "sell"], quantity: float, reason: str
    ) -> None:
        if quantity <= 0.0:
            return
        b = self._book(ctx, symbol)
        if b is None:
            return
        if b.best_bid() is None or b.best_ask() is None:
            return

        ctx.broker.submit(
            Order(
                id=f"bf_{reason}_{symbol}_{int(ctx.now_ms)}_{len(ctx.broker.fills)}",
                symbol=symbol,
                side=side,
                order_type="market",
                quantity=float(quantity),
            ),
            b,
            now_ms=int(ctx.now_ms),
        )

    def _maker_price(self, book: L2Book, side: Literal["buy", "sell"]) -> float | None:
        if side == "buy":
            return book.best_bid()
        return book.best_ask()

    def _taker_limit(self, book: L2Book, side: Literal["buy", "sell"]) -> float | None:
        if side == "buy":
            best = book.best_ask()
            if best is None:
                return None
            return float(best) * (1.0 + float(self.max_slippage))
        best = book.best_bid()
        if best is None:
            return None
        return float(best) * (1.0 - float(self.max_slippage))

    def _consume_new_fills(self, ctx: EngineContext) -> None:
        fills = ctx.broker.fills
        if self._fill_cursor >= len(fills):
            return
        for f in fills[self._fill_cursor :]:
            self._order_fills[f.order_id] = float(self._order_fills.get(f.order_id, 0.0)) + float(f.quantity)
        self._fill_cursor = len(fills)

    def _schedule_hedge(self, *, now_ms: int) -> None:
        self._pending_hedge_due_ms = int(now_ms) + int(self.legging_check_delay_ms)

    def _maybe_run_hedge(self, ctx: EngineContext) -> None:
        if self._pending_hedge_due_ms is None:
            return
        if int(ctx.now_ms) < int(self._pending_hedge_due_ms):
            return
        self._hedge_on_leg(ctx, reason="legging")
        self._pending_hedge_due_ms = None

    def _submit_entry_leg(
        self,
        ctx: EngineContext,
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: float,
        reason: str,
    ) -> PendingLeg | None:
        b = self._book(ctx, symbol)
        if b is None:
            return None
        if b.best_bid() is None or b.best_ask() is None:
            return None

        maker_px = self._maker_price(b, side)
        taker_limit_px = self._taker_limit(b, side)
        if maker_px is None or taker_limit_px is None:
            return None

        oid = f"bf_{reason}_{symbol}_{int(ctx.now_ms)}_{len(self._pending_legs)}"
        ctx.broker.submit(
            Order(
                id=oid,
                symbol=symbol,
                side=side,
                order_type="limit",
                quantity=float(quantity),
                price=float(maker_px),
                time_in_force="GTC",
                post_only=True,
            ),
            b,
            now_ms=int(ctx.now_ms),
        )
        return PendingLeg(
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            maker_order_id=oid,
            maker_submit_ms=int(ctx.now_ms),
            maker_wait_ms=int(float(self.maker_wait_sec) * 1000.0),
            taker_limit_px=float(taker_limit_px),
        )

    def _process_pending_legs(self, ctx: EngineContext) -> None:
        if not self._pending_legs:
            return

        now = int(ctx.now_ms)
        self._consume_new_fills(ctx)

        remaining: list[PendingLeg] = []
        for leg in self._pending_legs:
            filled = float(self._order_fills.get(leg.maker_order_id, 0.0))
            if leg.state == "maker":
                if filled >= float(leg.quantity) - 1e-12:
                    leg.state = "done"
                elif now - int(leg.maker_submit_ms) >= int(leg.maker_wait_ms):
                    ctx.broker.cancel(leg.maker_order_id, now_ms=now)
                    remaining_qty = float(leg.quantity) - filled
                    if remaining_qty > 1e-12:
                        b = self._book(ctx, leg.symbol)
                        if b is not None and b.best_bid() is not None and b.best_ask() is not None:
                            limit_px = leg.taker_limit_px
                            if limit_px is None:
                                limit_px = self._taker_limit(b, leg.side)
                            if limit_px is not None:
                                tid = f"{leg.maker_order_id}_taker"
                                ctx.broker.submit(
                                    Order(
                                        id=tid,
                                        symbol=leg.symbol,
                                        side=leg.side,
                                        order_type="limit",
                                        quantity=float(remaining_qty),
                                        price=float(limit_px),
                                        time_in_force="IOC",
                                        post_only=False,
                                    ),
                                    b,
                                    now_ms=now,
                                )
                                leg.taker_order_id = tid
                                leg.state = "taker"
                                self._consume_new_fills(ctx)
                            else:
                                leg.state = "done"
                        else:
                            leg.state = "done"
                    else:
                        leg.state = "done"
            if leg.state == "taker":
                # IOC executes immediately (or not). Nothing else to track here.
                leg.state = "done"

            if leg.state != "done":
                remaining.append(leg)

        self._pending_legs = remaining

    def _set_target_entry(self, ctx: EngineContext, *, perp_target_qty: float, future_target_qty: float, reason: str) -> None:
        cur_perp = self._position_qty(ctx, self.perp_symbol)
        cur_future = self._position_qty(ctx, self.future_symbol)

        d_perp = float(perp_target_qty) - cur_perp
        d_future = float(future_target_qty) - cur_future

        legs: list[PendingLeg] = []
        if abs(d_perp) > 1e-12:
            leg = self._submit_entry_leg(
                ctx,
                symbol=self.perp_symbol,
                side=("buy" if d_perp > 0.0 else "sell"),
                quantity=abs(d_perp),
                reason=f"{reason}_perp",
            )
            if leg is None:
                return
            legs.append(leg)

        if abs(d_future) > 1e-12:
            leg = self._submit_entry_leg(
                ctx,
                symbol=self.future_symbol,
                side=("buy" if d_future > 0.0 else "sell"),
                quantity=abs(d_future),
                reason=f"{reason}_future",
            )
            if leg is None:
                return
            legs.append(leg)

        if legs:
            self._pending_legs.extend(legs)
            self._schedule_hedge(now_ms=int(ctx.now_ms))
            self._process_pending_legs(ctx)

    def _hedge_on_leg(self, ctx: EngineContext, *, reason: str) -> None:
        q_perp = self._position_qty(ctx, self.perp_symbol)
        q_future = self._position_qty(ctx, self.future_symbol)

        abs_perp = abs(q_perp)
        abs_future = abs(q_future)
        diff = abs_perp - abs_future

        if abs(diff) <= float(self.hedge_eps_base):
            return

        if diff > 0.0:
            # Need to increase future abs exposure to match perp.
            if q_perp < 0.0:
                side = "buy"
            else:
                side = "sell"
            self._submit_market(ctx, symbol=self.future_symbol, side=side, quantity=abs(diff), reason=f"hedge_{reason}")
            self.hedge_actions += 1
            return

        # Need to increase perp abs exposure to match future.
        if q_future > 0.0:
            side = "sell"
        else:
            side = "buy"
        self._submit_market(ctx, symbol=self.perp_symbol, side=side, quantity=abs(diff), reason=f"hedge_{reason}")
        self.hedge_actions += 1

    def _flat_positions(self, ctx: EngineContext) -> bool:
        return (
            abs(self._position_qty(ctx, self.perp_symbol)) <= 1e-12
            and abs(self._position_qty(ctx, self.future_symbol)) <= 1e-12
        )

    def _flatten(self, ctx: EngineContext, *, reason: str) -> None:
        self._submit_market(
            ctx,
            symbol=self.perp_symbol,
            side=("buy" if self._position_qty(ctx, self.perp_symbol) < 0.0 else "sell"),
            quantity=abs(self._position_qty(ctx, self.perp_symbol)),
            reason=f"exit_{reason}_perp",
        )
        self._submit_market(
            ctx,
            symbol=self.future_symbol,
            side=("buy" if self._position_qty(ctx, self.future_symbol) < 0.0 else "sell"),
            quantity=abs(self._position_qty(ctx, self.future_symbol)),
            reason=f"exit_{reason}_future",
        )

    def _record_equity(self, ctx: EngineContext, t_ms: int) -> None:
        unreal = 0.0
        for sym in (self.perp_symbol, self.future_symbol):
            pos = ctx.broker.portfolio.positions.get(sym)
            if pos is None or pos.qty == 0.0:
                continue

            px = None
            mp = ctx.mark.get(sym)
            if mp is not None:
                px = float(mp.mark_price)
            else:
                m = self._book_mid(ctx, sym)
                if m is not None:
                    px = float(m)

            if px is None:
                continue
            unreal += float(pos.qty) * (float(px) - float(pos.avg_price))

        eq = float(ctx.broker.portfolio.realized_pnl_usdt) + float(unreal)
        self.equity_curve.append((int(t_ms), float(eq)))

    def _liquidity_ok_standard(self, ctx: EngineContext, mid_perp: float, mid_future: float) -> bool:
        b_perp = self._book(ctx, self.perp_symbol)
        b_fut = self._book(ctx, self.future_symbol)
        if b_perp is None or b_fut is None:
            return False

        ok_perp = has_min_liquidity(
            b_perp,
            "bid",
            mid_price=float(mid_perp),
            depth_pct=float(self.liquidity_depth_pct),
            order_notional=float(self.impact_notional_usdt),
            min_ratio=float(self.liquidity_min_ratio),
        )
        ok_fut = has_min_liquidity(
            b_fut,
            "ask",
            mid_price=float(mid_future),
            depth_pct=float(self.liquidity_depth_pct),
            order_notional=float(self.impact_notional_usdt),
            min_ratio=float(self.liquidity_min_ratio),
        )
        return bool(ok_perp and ok_fut)

    def _liquidity_ok_reverse(self, ctx: EngineContext, mid_perp: float, mid_future: float) -> bool:
        b_perp = self._book(ctx, self.perp_symbol)
        b_fut = self._book(ctx, self.future_symbol)
        if b_perp is None or b_fut is None:
            return False

        ok_perp = has_min_liquidity(
            b_perp,
            "ask",
            mid_price=float(mid_perp),
            depth_pct=float(self.liquidity_depth_pct),
            order_notional=float(self.impact_notional_usdt),
            min_ratio=float(self.liquidity_min_ratio),
        )
        ok_fut = has_min_liquidity(
            b_fut,
            "bid",
            mid_price=float(mid_future),
            depth_pct=float(self.liquidity_depth_pct),
            order_notional=float(self.impact_notional_usdt),
            min_ratio=float(self.liquidity_min_ratio),
        )
        return bool(ok_perp and ok_fut)

    def _evaluate(self, ctx: EngineContext, *, event_symbol: str | None = None) -> None:
        now = int(ctx.now_ms)
        if int(self._last_eval_ms) == now:
            if event_symbol is not None and self._last_eval_symbol == event_symbol:
                return
        else:
            if self._last_eval_ms and (now - int(self._last_eval_ms)) < int(self.basis_sample_ms):
                return
        self._last_eval_ms = now
        self._last_eval_symbol = event_symbol

        if not self._book_is_ready(ctx, self.perp_symbol) or not self._book_is_ready(ctx, self.future_symbol):
            return

        mid_perp = self._book_mid(ctx, self.perp_symbol)
        mid_future = self._book_mid(ctx, self.future_symbol)
        if mid_perp is None or mid_future is None:
            return

        basis = basis_signal_mid(mid_perp, mid_future)
        if not math.isfinite(basis):
            return

        if not self.basis_history or (now - int(self._last_basis_sample_ms)) >= int(self.basis_sample_ms):
            self.basis_history.append((now, float(basis)))
            self._last_basis_sample_ms = now
        elif self.basis_history and int(self.basis_history[-1][0]) == now:
            # Same timestamp: keep the latest basis value.
            self.basis_history[-1] = (now, float(basis))

        z_window_ms = int(self.z_window) * 60_000
        cutoff = now - z_window_ms
        # Keep one sample before the cutoff to preserve full-window span.
        while len(self.basis_history) > 1 and self.basis_history[1][0] < cutoff:
            self.basis_history.popleft()

        if not self.basis_history:
            return
        if now - int(self.basis_history[0][0]) < z_window_ms:
            return

        h = [v for _, v in self.basis_history]
        if len(h) < 2:
            return

        mean = sum(h) / float(len(h))
        std = _std_population(h)
        z = _safe_z(float(basis), mean, std)

        vol_window_ms = int(self.vol_ratio_window) * 60_000
        h_recent = [v for t, v in self.basis_history if t >= (now - vol_window_ms)]
        vol_now = _std_population(h_recent) if len(h_recent) >= 2 else 0.0
        vol_ref = _std_population(h)
        vol_ratio = 1.0 if vol_ref <= 1e-12 else float(vol_now) / float(vol_ref)
        dynamic_z = dynamic_z_threshold(vol_ratio)

        b_perp = self._book(ctx, self.perp_symbol)
        b_fut = self._book(ctx, self.future_symbol)
        assert b_perp is not None and b_fut is not None
        cost_std, cost_rev = execution_cost_std_rev(b_perp, b_fut, impact_notional_usdt=float(self.impact_notional_usdt))

        funding_proj = self._funding_proj(ctx)
        self.last_snapshot = BasisSnapshot(
            now_ms=int(ctx.now_ms),
            basis=float(basis),
            mean=float(mean),
            std=float(std),
            z=float(z),
            vol_ratio=float(vol_ratio),
            dynamic_z=float(dynamic_z),
            funding_proj=float(funding_proj),
            cost_std=float(cost_std),
            cost_rev=float(cost_rev),
        )

        if self.state != "flat":
            # Exit precedence: hard stop > funding flip > mean reversion.
            if should_exit_hard_stop(z, float(self.z_hard_stop)):
                self.exits_hard_stop += 1
                self._flatten(ctx, reason="hard_stop")
                return

            if math.isfinite(funding_proj) and self.state == "standard" and funding_proj < 0.0:
                self.exits_funding_flip += 1
                self._flatten(ctx, reason="funding_flip")
                return

            if math.isfinite(funding_proj) and self.state == "reverse" and funding_proj > 0.0:
                self.exits_funding_flip += 1
                self._flatten(ctx, reason="funding_flip")
                return

            if should_exit_mean_reversion(z, float(self.z_exit_eps)):
                self.exits_mean_reversion += 1
                self._flatten(ctx, reason="mean_reversion")
            return

        # Flat state: entry gating.
        if self._pending_legs:
            return
        if int(ctx.now_ms) < int(self._next_entry_allowed_ms):
            return

        cost_buffer = float(self.entry_safety_margin) + float(self.max_slippage) + 2.0 * float(self.taker_fee_frac)

        if math.isfinite(funding_proj) and funding_proj > float(self.funding_threshold) and z < -float(dynamic_z):
            if not self._liquidity_ok_standard(ctx, mid_perp, mid_future):
                self.liquidity_rejects += 1
                self._next_entry_allowed_ms = int(ctx.now_ms) + int(self.entry_cooldown_sec) * 1000
                return

            if math.isfinite(cost_std) and cost_std <= (mean - cost_buffer):
                q = self._target_leg_qtys(ctx)
                if q is None:
                    return
                q_perp, q_future = q
                self._set_target_entry(
                    ctx, perp_target_qty=-float(q_perp), future_target_qty=float(q_future), reason="entry_standard"
                )
            return

        if self.allow_reverse and math.isfinite(funding_proj) and funding_proj < -float(self.funding_threshold) and z > float(dynamic_z):
            if not self._liquidity_ok_reverse(ctx, mid_perp, mid_future):
                self.liquidity_rejects += 1
                self._next_entry_allowed_ms = int(ctx.now_ms) + int(self.entry_cooldown_sec) * 1000
                return

            if math.isfinite(cost_rev) and cost_rev >= (mean + cost_buffer):
                q = self._target_leg_qtys(ctx)
                if q is None:
                    return
                q_perp, q_future = q
                self._set_target_entry(
                    ctx, perp_target_qty=float(q_perp), future_target_qty=-float(q_future), reason="entry_reverse"
                )

    def on_event(self, event: object, ctx: EngineContext) -> None:
        self._consume_new_fills(ctx)
        self._process_pending_legs(ctx)
        self._maybe_run_hedge(ctx)

        if isinstance(event, MarkPrice):
            # Funding projection comes from perp mark stream.
            if event.symbol == self.perp_symbol:
                self._evaluate(ctx, event_symbol=event.symbol)
            self._record_equity(ctx, int(event.event_time_ms))
        elif isinstance(event, DepthUpdate) and (event.symbol == self.perp_symbol or event.symbol == self.future_symbol):
            self._evaluate(ctx, event_symbol=event.symbol)

        self._consume_new_fills(ctx)
        self._process_pending_legs(ctx)
        self._maybe_run_hedge(ctx)
        self._sync_state_from_positions(ctx)

    def _sync_state_from_positions(self, ctx: EngineContext) -> None:
        if self._flat_positions(ctx):
            if self.state != "flat":
                self.state = "flat"
            return

        perp_qty = self._position_qty(ctx, self.perp_symbol)
        new_state: StrategyState = "standard" if perp_qty < 0.0 else "reverse"
        if self.state != new_state:
            if self.state == "flat":
                if new_state == "standard":
                    self.entries_standard += 1
                else:
                    self.entries_reverse += 1
            self.state = new_state

    def on_end(self, ctx: EngineContext) -> None:
        if self.force_close_on_end and not self._flat_positions(ctx):
            self._flatten(ctx, reason="end")
