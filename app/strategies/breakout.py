"""Breakout Trading strategy for Binance.

Detects consolidation patterns then enters on breakout with volume
confirmation. Uses scaled take-profit (50% at TP1, trail rest to TP2).
"""

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from app.models.trading import StrategyType
from app.services.binance_client import binance_client
from app.services.trading_engine import trading_engine

logger = logging.getLogger(__name__)


class BreakoutConfig(BaseModel):
    """Configuration for breakout trading."""

    active: bool = False
    quote_asset: str = "USDT"
    amount_per_trade: float = 15.0  # Larger trades for breakouts
    amount_per_trade_strong: float = 25.0  # Very strong breakout
    max_concurrent_trades: int = 10
    # Consolidation detection
    consolidation_candles: int = 24  # 2h of 5min candles
    max_range_percent: float = 1.5  # Max range for consolidation
    min_volume_24h: float = 100000.0  # Higher volume for breakouts
    # Breakout entry
    breakout_threshold: float = 0.3  # % above resistance to confirm
    volume_multiplier: float = 2.0  # Volume must be 2x average
    # Take profit / Stop loss
    tp1_percent: float = 3.0  # Sell 50% at +3%
    tp2_percent: float = 8.0  # Trail rest up to +8%
    trailing_percent: float = 1.0  # Trailing for TP2 portion
    stop_loss_percent: float = 1.5  # Tight SL (failed breakout)
    max_hold_minutes: int = 120  # Max 2h hold
    max_trades_per_cycle: int = 3


class BreakoutPosition(BaseModel):
    """An active breakout position."""

    id: str
    symbol: str
    entry_price: float
    quantity: float
    remaining_quantity: float  # After partial sells
    amount_quote: float
    highest_price: float
    resistance_level: float  # Breakout level
    tp1_hit: bool = False  # First TP triggered
    trailing_active: bool = False
    signal_strength: str = "normal"
    status: str = "ACTIVE"
    reason: str = ""
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None


class BreakoutStrategy:
    """Breakout trading: detect consolidation -> buy on breakout.

    1. Scan for coins in tight consolidation (range < 1.5% over 2h)
    2. Detect breakout: price breaks above resistance + volume spike
    3. Buy with aggressive sizing
    4. Scaled exit: sell 50% at TP1 (+3%), trail rest to TP2 (+8%)
    5. Tight SL (-1.5%) for failed breakouts
    """

    def __init__(self):
        self.config: Optional[BreakoutConfig] = None
        self.positions: list[BreakoutPosition] = []
        self.activity_log: list[dict] = []
        self._blacklist: dict[str, datetime] = {}

    def _log_activity(self, action: str, symbol: str, details: str = ""):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "action": action,
            "symbol": symbol,
            "details": details,
        }
        self.activity_log.append(entry)
        if len(self.activity_log) > 100:
            self.activity_log = self.activity_log[-100:]

    def get_active_positions(self) -> list[BreakoutPosition]:
        return [p for p in self.positions if p.status == "ACTIVE"]

    def get_closed_positions(self) -> list[BreakoutPosition]:
        return [p for p in self.positions if p.status != "ACTIVE"]

    @property
    def quote(self) -> str:
        return self.config.quote_asset if self.config else "USDT"

    def _is_blacklisted(self, symbol: str) -> bool:
        until = self._blacklist.get(symbol)
        if not until:
            return False
        if datetime.now(UTC) > until:
            del self._blacklist[symbol]
            return False
        return True

    async def setup(self, config: BreakoutConfig) -> bool:
        self.config = config
        logger.info(
            f"Breakout configured: {config.amount_per_trade} "
            f"{config.quote_asset}/trade, "
            f"TP1={config.tp1_percent}%, TP2={config.tp2_percent}%, "
            f"SL={config.stop_loss_percent}%, "
            f"Max={config.max_concurrent_trades}"
        )
        return True

    async def _get_open_markets(self) -> set[str]:
        try:
            client = await binance_client._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()
            return {
                s["symbol"] for s in data.get("symbols", [])
                if s.get("quoteAsset") == self.quote
                and s.get("status") == "TRADING"
                and not s.get("symbol", "").startswith("LD")
            }
        except Exception as e:
            logger.error(f"Error fetching open markets: {e}")
            return set()

    async def _analyze_consolidation(
        self, symbol: str
    ) -> Optional[dict]:
        """Check if symbol is in consolidation then breaking out."""
        try:
            n = self.config.consolidation_candles if self.config else 24
            klines = await binance_client.get_klines(
                symbol, interval="5m", limit=n + 3
            )
            if not klines or len(klines) < n:
                return None

            # Consolidation zone: last N candles before the most recent 3
            consol_klines = klines[:-3]
            recent_klines = klines[-3:]

            highs = [float(k[2]) for k in consol_klines]
            lows = [float(k[3]) for k in consol_klines]
            volumes = [float(k[5]) for k in consol_klines]

            if not highs or not lows:
                return None

            resistance = max(highs)
            support = min(lows)

            if support == 0:
                return None

            range_pct = ((resistance - support) / support) * 100
            avg_volume = sum(volumes) / len(volumes) if volumes else 0

            # Recent data
            recent_closes = [float(k[4]) for k in recent_klines]
            recent_highs = [float(k[2]) for k in recent_klines]
            recent_volumes = [float(k[5]) for k in recent_klines]
            last_price = recent_closes[-1] if recent_closes else 0
            recent_avg_vol = (
                sum(recent_volumes) / len(recent_volumes)
                if recent_volumes else 0
            )

            return {
                "resistance": resistance,
                "support": support,
                "range_pct": range_pct,
                "avg_volume": avg_volume,
                "last_price": last_price,
                "recent_high": max(recent_highs) if recent_highs else 0,
                "recent_avg_vol": recent_avg_vol,
                "vol_ratio": (
                    recent_avg_vol / avg_volume if avg_volume > 0 else 0
                ),
            }
        except Exception as e:
            logger.error(f"Consolidation analysis error {symbol}: {e}")
            return None

    async def scan_breakout_opportunities(self) -> list[dict]:
        """Scan for breakout setups."""
        if not self.config or not self.config.active:
            return []

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return []

        open_markets = await self._get_open_markets()
        tickers = await binance_client.get_ticker_24h()
        if not tickers:
            return []

        suffix = self.quote
        active_symbols = {p.symbol for p in active}

        # Pre-filter: coins with volume and positive movement
        pre_candidates = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith(suffix):
                continue
            if symbol.startswith("LD") or "1MBB" in symbol:
                continue
            if symbol in active_symbols:
                continue
            if symbol not in open_markets:
                continue
            if self._is_blacklisted(symbol):
                continue

            try:
                volume_24h = float(t.get("quoteVolume", 0))
                price_change = float(t.get("priceChangePercent", 0))
            except (ValueError, TypeError):
                continue

            if volume_24h < self.config.min_volume_24h:
                continue
            # Breakout candidates: moderate positive change (not too high)
            if 0.5 <= price_change <= 8.0:
                pre_candidates.append({
                    "symbol": symbol,
                    "change": price_change,
                    "volume": volume_24h,
                })

        # Sort by volume (more liquid = better breakout)
        pre_candidates.sort(key=lambda x: x["volume"], reverse=True)

        # Analyze top candidates for consolidation + breakout
        confirmed = []
        check_limit = min(len(pre_candidates), 15)
        for c in pre_candidates[:check_limit]:
            if len(confirmed) >= self.config.max_trades_per_cycle:
                break

            analysis = await self._analyze_consolidation(c["symbol"])
            if not analysis:
                continue

            range_pct = analysis["range_pct"]
            last_price = analysis["last_price"]
            resistance = analysis["resistance"]
            vol_ratio = analysis["vol_ratio"]

            # Check consolidation: tight range
            if range_pct > self.config.max_range_percent:
                continue

            # Check breakout: price above resistance + volume spike
            breakout_pct = (
                (last_price - resistance) / resistance * 100
                if resistance > 0 else 0
            )
            if breakout_pct < self.config.breakout_threshold:
                continue
            if vol_ratio < self.config.volume_multiplier:
                continue

            # Classify strength
            strength = "normal"
            if vol_ratio > 3.0 and breakout_pct > 0.8:
                strength = "strong"

            c["analysis"] = analysis
            c["breakout_pct"] = breakout_pct
            c["vol_ratio"] = vol_ratio
            c["strength"] = strength
            confirmed.append(c)

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} brk+{c['breakout_pct']:.1f}% "
                f"vol{c['vol_ratio']:.1f}x ({c['strength']})"
                for c in confirmed
            )
            logger.info(f"BREAKOUT signals: {desc}")

        return confirmed

    async def execute_breakout(
        self, symbol: str, resistance: float, strength: str = "normal"
    ) -> Optional[dict]:
        """Enter a breakout trade."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Breakout: failed to buy {symbol}")
            self._log_activity(
                "FALHA", symbol, "Ordem rejeitada"
            )
            return None

        q = self.quote
        position = BreakoutPosition(
            id=f"brk_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            remaining_quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            resistance_level=resistance,
            signal_strength=strength,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        label = "FORTE" if strength == "strong" else "NORMAL"
        logger.info(
            f"BREAKOUT BUY [{label}]: {symbol} @ {result.price:.6f}, "
            f"qty={result.quantity:.8f}, {amt} {q}, "
            f"resistance={resistance:.6f}"
        )
        self._log_activity(
            f"COMPRA (Breakout {label})", symbol,
            f"{result.price:.6f} x {result.quantity:.6f} = "
            f"{amt:.2f} {q} | resist={resistance:.6f}"
        )
        return position.model_dump()

    async def monitor_positions(self):
        if not self.config or not self.config.active:
            return

        for pos in self.get_active_positions():
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(f"Breakout monitor error {pos.symbol}: {e}")

    async def _check_position(self, pos: BreakoutPosition):
        current_price = await binance_client.get_symbol_price(pos.symbol)
        if not current_price:
            return

        price_change_pct = (
            (current_price - pos.entry_price) / pos.entry_price
        ) * 100

        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Timeout
        created = datetime.fromisoformat(pos.created_at)
        elapsed = datetime.now(UTC) - created
        if elapsed > timedelta(minutes=self.config.max_hold_minutes):
            await self._close_position(
                pos, current_price, "TIMEOUT", pos.remaining_quantity
            )
            return

        # Stop Loss (failed breakout)
        if price_change_pct <= -self.config.stop_loss_percent:
            self._blacklist[pos.symbol] = (
                datetime.now(UTC) + timedelta(minutes=60)
            )
            await self._close_position(
                pos, current_price, "STOP_LOSS", pos.remaining_quantity
            )
            return

        # TP1: sell 50% at first target
        if (
            not pos.tp1_hit
            and price_change_pct >= self.config.tp1_percent
        ):
            sell_qty = pos.remaining_quantity * 0.5
            # Round down to avoid precision issues
            sell_qty = math.floor(sell_qty * 1e8) / 1e8
            if sell_qty > 0:
                pos.tp1_hit = True
                pos.trailing_active = True
                await self._partial_sell(
                    pos, current_price, sell_qty, "TP1"
                )
                return

        # TP2 trailing (after TP1 hit)
        if pos.tp1_hit and pos.trailing_active:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            gain_from_entry = (
                (pos.highest_price - pos.entry_price) / pos.entry_price
            ) * 100
            if (
                drop_from_peak >= self.config.trailing_percent
                and gain_from_entry >= self.config.tp1_percent
            ):
                await self._close_position(
                    pos, current_price, "TP2_TRAIL",
                    pos.remaining_quantity
                )

    async def _partial_sell(
        self, pos: BreakoutPosition, price: float,
        quantity: float, reason: str
    ):
        """Sell a portion of the position."""
        order = await binance_client.place_market_order(
            symbol=pos.symbol,
            side="SELL",
            quantity=quantity,
        )
        if order:
            pos.remaining_quantity -= quantity
            pnl = ((price - pos.entry_price) / pos.entry_price) * 100
            self._log_activity(
                f"VENDA PARCIAL ({reason})", pos.symbol,
                f"PnL: {pnl:+.2f}% | Vendeu {quantity:.6f}, "
                f"restante {pos.remaining_quantity:.6f}"
            )
            logger.info(
                f"Breakout PARTIAL SELL ({reason}): {pos.symbol} "
                f"qty={quantity:.6f} PnL={pnl:.2f}%"
            )
        else:
            logger.error(
                f"Breakout: partial sell failed {pos.symbol}"
            )

    async def _close_position(
        self, pos: BreakoutPosition, price: float,
        reason: str, quantity: float
    ):
        """Close remaining position."""
        if quantity <= 0:
            pos.status = "CLOSED"
            return

        order = await binance_client.place_market_order(
            symbol=pos.symbol,
            side="SELL",
            quantity=quantity,
        )
        if order:
            pos.status = "CLOSED"
            pos.reason = reason
            pos.close_price = price
            pos.closed_at = datetime.now(UTC).isoformat()
            pos.pnl_percent = (
                (price - pos.entry_price) / pos.entry_price
            ) * 100
            pos.remaining_quantity = 0

            reason_map = {
                "TP2_TRAIL": "VENDA (TP2 Trailing)",
                "STOP_LOSS": "VENDA (Stop Loss)",
                "TIMEOUT": "VENDA (Timeout)",
                "MANUAL": "VENDA (Manual)",
                "MANUAL_ALL": "VENDA (Fechar Todos)",
            }
            self._log_activity(
                reason_map.get(reason, f"VENDA ({reason})"),
                pos.symbol,
                f"PnL: {pos.pnl_percent:+.2f}% | "
                f"{pos.entry_price:.6f} -> {price:.6f}"
            )
            logger.info(
                f"Breakout CLOSED ({reason}): {pos.symbol} "
                f"PnL={pos.pnl_percent:.2f}%"
            )
        else:
            logger.error(f"Breakout: failed to sell {pos.symbol}")

    async def close_position_by_id(
        self, position_id: str
    ) -> Optional[dict]:
        for pos in self.positions:
            if pos.id == position_id and pos.status == "ACTIVE":
                price = await binance_client.get_symbol_price(pos.symbol)
                if price:
                    await self._close_position(
                        pos, price, "MANUAL", pos.remaining_quantity
                    )
                    return pos.model_dump()
        return None

    async def close_all(self) -> int:
        closed = 0
        for pos in self.get_active_positions():
            price = await binance_client.get_symbol_price(pos.symbol)
            if price:
                await self._close_position(
                    pos, price, "MANUAL_ALL", pos.remaining_quantity
                )
                closed += 1
        return closed

    async def run_cycle(self):
        """Run one breakout cycle. Called every 30s by scheduler."""
        if not self.config or not self.config.active:
            return

        active_count = len(self.get_active_positions())
        self._log_activity(
            "SCAN", "mercado",
            f"Buscando breakouts... ({active_count} posicoes)"
        )

        # Scan for breakout opportunities
        signals = await self.scan_breakout_opportunities()
        if signals:
            desc = ", ".join(
                f"{s['symbol']}({s['strength'][0].upper()})"
                for s in signals
            )
            self._log_activity(
                "BREAKOUT DETECTADO", desc,
                f"{len(signals)} sinais"
            )
        for signal in signals:
            resistance = signal["analysis"]["resistance"]
            await self.execute_breakout(
                signal["symbol"], resistance, signal["strength"]
            )

        # Monitor existing positions
        await self.monitor_positions()


breakout_strategy = BreakoutStrategy()
