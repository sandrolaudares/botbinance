"""Scalping strategy for newly listed coins on Binance.

Detects new BRL trading pairs and buys immediately for short-term gains.
Uses aggressive take-profit with trailing stop for maximum capture.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from app.models.trading import StrategyType
from app.services.binance_client import binance_client
from app.services.trading_engine import trading_engine

logger = logging.getLogger(__name__)


class ScalpConfig(BaseModel):
    """Configuration for the scalping strategy."""

    active: bool = False
    amount_per_trade_brl: float = 25.0  # Smaller trades, more volume
    take_profit_percent: float = 8.0  # TP for new listings
    trailing_percent: float = 1.0  # Tight trailing
    stop_loss_percent: float = 2.0  # Quick exit on loss
    max_hold_hours: int = 2  # Short hold time
    max_concurrent_trades: int = 10  # More simultaneous positions
    min_volume_24h: float = 10000.0  # Lower volume threshold
    # Momentum scalping (aggressive mode)
    momentum_enabled: bool = True  # Scalp momentum spikes
    momentum_tp_percent: float = 2.0  # Quick TP for momentum
    momentum_sl_percent: float = 1.5  # Tight SL for momentum
    min_price_change_1h: float = 0.5  # Very low threshold
    min_volume_spike: float = 1.5  # Volume 1.5x average
    max_trades_per_cycle: int = 3  # Up to 3 buys per 60s cycle


class ScalpPosition(BaseModel):
    """An active scalping position."""

    id: str
    symbol: str
    entry_price: float
    quantity: float
    amount_brl: float
    highest_price: float
    trailing_active: bool = False
    status: str = "ACTIVE"  # ACTIVE, CLOSED
    reason: str = ""  # TP, SL, TIMEOUT, MANUAL
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None


class ScalpingStrategy:
    """Scalping strategy for new coin listings.

    Logic:
    1. Periodically fetch all BRL trading pairs from exchangeInfo
    2. Compare with known pairs to detect new listings
    3. When new pair detected: buy immediately at market
    4. Monitor with tight take-profit + trailing stop
    5. Force exit after max_hold_hours if no TP/SL triggered
    """

    def __init__(self):
        self.config: Optional[ScalpConfig] = None
        self.known_brl_pairs: set[str] = set()
        self.positions: list[ScalpPosition] = []
        self._initialized: bool = False
        self.activity_log: list[dict] = []  # Real-time activity feed

    def _log_activity(self, action: str, symbol: str, details: str = ""):
        """Add entry to real-time activity log (keeps last 50)."""
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "action": action,
            "symbol": symbol,
            "details": details,
        }
        self.activity_log.append(entry)
        if len(self.activity_log) > 50:
            self.activity_log = self.activity_log[-50:]

    def get_active_positions(self) -> list[ScalpPosition]:
        return [p for p in self.positions if p.status == "ACTIVE"]

    def get_closed_positions(self) -> list[ScalpPosition]:
        return [p for p in self.positions if p.status != "ACTIVE"]

    async def setup(self, config: ScalpConfig) -> bool:
        """Configure and activate scalping strategy."""
        self.config = config
        if config.active and not self._initialized:
            await self._initialize_known_pairs()
        logger.info(
            f"Scalping configured: R${config.amount_per_trade_brl}/trade, "
            f"TP={config.take_profit_percent}%, "
            f"Trail={config.trailing_percent}%, "
            f"SL={config.stop_loss_percent}%, "
            f"Max hold={config.max_hold_hours}h"
        )
        return True

    async def _initialize_known_pairs(self):
        """Load current BRL pairs as baseline (don't trade existing ones)."""
        pairs = await self._fetch_tradeable_brl_pairs()
        self.known_brl_pairs = set(pairs)
        self._initialized = True
        logger.info(
            f"Scalping initialized: {len(self.known_brl_pairs)} "
            f"existing BRL pairs tracked"
        )

    async def _fetch_tradeable_brl_pairs(self) -> list[str]:
        """Fetch all actively tradeable BRL pairs from Binance."""
        try:
            client = await binance_client._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()

            brl_pairs = []
            for symbol_info in data.get("symbols", []):
                if (
                    symbol_info.get("quoteAsset") == "BRL"
                    and symbol_info.get("status") == "TRADING"
                    and not symbol_info.get("symbol", "").startswith("LD")
                ):
                    brl_pairs.append(symbol_info["symbol"])
            return brl_pairs
        except Exception as e:
            logger.error(f"Error fetching BRL pairs: {e}")
            return []

    async def scan_for_new_listings(self) -> list[str]:
        """Check for newly listed BRL pairs."""
        if not self.config or not self.config.active:
            return []

        if not self._initialized:
            await self._initialize_known_pairs()
            return []

        current_pairs = await self._fetch_tradeable_brl_pairs()
        current_set = set(current_pairs)

        new_pairs = current_set - self.known_brl_pairs
        if new_pairs:
            logger.info(
                f"NEW LISTINGS DETECTED: {new_pairs}"
            )

        # Update known pairs
        self.known_brl_pairs = current_set
        return list(new_pairs)

    async def execute_scalp(self, symbol: str) -> Optional[dict]:
        """Buy a newly listed coin for scalping."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            logger.warning(
                f"Scalping: max concurrent trades reached "
                f"({self.config.max_concurrent_trades})"
            )
            return None

        # Check volume (skip if too low liquidity)
        ticker = await binance_client.get_ticker_24h(symbol)
        if ticker:
            volume = float(ticker[0].get("quoteVolume", 0))
            if volume < self.config.min_volume_24h:
                logger.info(
                    f"Scalping: skipping {symbol}, "
                    f"volume R${volume:.0f} < min R${self.config.min_volume_24h}"
                )
                return None

        # Place market buy
        result = await trading_engine.buy_with_brl(
            symbol, self.config.amount_per_trade_brl, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Scalping: failed to buy {symbol}")
            return None

        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_brl=self.config.amount_per_trade_brl,
            highest_price=result.price,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        logger.info(
            f"SCALP BUY: {symbol} @ R${result.price:.4f}, "
            f"qty={result.quantity:.8f}, "
            f"amount=R${self.config.amount_per_trade_brl:.2f}"
        )
        self._log_activity(
            "COMPRA (Nova Listagem)", symbol,
            f"R${result.price:.4f} x {result.quantity:.6f} = "
            f"R${self.config.amount_per_trade_brl:.2f}"
        )
        return position.model_dump()

    async def monitor_positions(self):
        """Monitor all scalping positions for TP/SL/timeout."""
        if not self.config or not self.config.active:
            return

        for pos in self.get_active_positions():
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(f"Scalping monitor error {pos.symbol}: {e}")

    async def _check_position(self, pos: ScalpPosition):
        """Check a position for exit conditions."""
        current_price = await binance_client.get_symbol_price(pos.symbol)
        if not current_price:
            return

        price_change_pct = (
            (current_price - pos.entry_price) / pos.entry_price
        ) * 100

        # Update highest price
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Check TIMEOUT (force sell after max hours)
        created = datetime.fromisoformat(pos.created_at)
        elapsed = datetime.now(UTC) - created
        if elapsed > timedelta(hours=self.config.max_hold_hours):
            logger.info(
                f"Scalp TIMEOUT: {pos.symbol} "
                f"(held {elapsed.total_seconds()/3600:.1f}h, "
                f"PnL={price_change_pct:.2f}%)"
            )
            await self._close_position(pos, current_price, "TIMEOUT")
            return

        # Check STOP LOSS
        if price_change_pct <= -self.config.stop_loss_percent:
            logger.warning(
                f"Scalp STOP LOSS: {pos.symbol} "
                f"({price_change_pct:.2f}%)"
            )
            await self._close_position(pos, current_price, "STOP_LOSS")
            return

        # Check TAKE PROFIT activation
        if price_change_pct >= self.config.take_profit_percent:
            if not pos.trailing_active:
                pos.trailing_active = True
                logger.info(
                    f"Scalp TRAILING ACTIVATED: {pos.symbol} "
                    f"({price_change_pct:.2f}%)"
                )

        # Check TRAILING STOP
        if pos.trailing_active:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            if drop_from_peak >= self.config.trailing_percent:
                logger.info(
                    f"Scalp TRAILING SELL: {pos.symbol} "
                    f"(peak R${pos.highest_price:.4f}, "
                    f"drop {drop_from_peak:.2f}%)"
                )
                await self._close_position(
                    pos, current_price, "TAKE_PROFIT"
                )

    async def _close_position(
        self, pos: ScalpPosition, price: float, reason: str
    ):
        """Close a scalping position."""
        order = await binance_client.place_market_order(
            symbol=pos.symbol,
            side="SELL",
            quantity=pos.quantity,
        )
        if order:
            pos.status = "CLOSED"
            pos.reason = reason
            pos.close_price = price
            pos.closed_at = datetime.now(UTC).isoformat()
            pos.pnl_percent = (
                (price - pos.entry_price) / pos.entry_price
            ) * 100
            logger.info(
                f"Scalp CLOSED ({reason}): {pos.symbol} "
                f"entry=R${pos.entry_price:.4f} exit=R${price:.4f} "
                f"PnL={pos.pnl_percent:.2f}%"
            )
            reason_map = {
                "TAKE_PROFIT": "VENDA (Take Profit)",
                "STOP_LOSS": "VENDA (Stop Loss)",
                "TIMEOUT": "VENDA (Timeout)",
                "MANUAL": "VENDA (Manual)",
                "MANUAL_ALL": "VENDA (Fechar Todos)",
            }
            self._log_activity(
                reason_map.get(reason, f"VENDA ({reason})"),
                pos.symbol,
                f"PnL: {pos.pnl_percent:+.2f}% | "
                f"R${pos.entry_price:.4f} → R${price:.4f}"
            )
        else:
            logger.error(f"Scalp: failed to sell {pos.symbol}")

    async def close_position_by_id(
        self, position_id: str
    ) -> Optional[dict]:
        """Manually close a position."""
        for pos in self.positions:
            if pos.id == position_id and pos.status == "ACTIVE":
                price = await binance_client.get_symbol_price(pos.symbol)
                if price:
                    await self._close_position(pos, price, "MANUAL")
                    return pos.model_dump()
        return None

    async def close_all(self) -> int:
        """Close all active positions."""
        closed = 0
        for pos in self.get_active_positions():
            price = await binance_client.get_symbol_price(pos.symbol)
            if price:
                await self._close_position(pos, price, "MANUAL_ALL")
                closed += 1
        return closed

    async def _get_open_markets(self) -> set[str]:
        """Get set of BRL symbols currently open for trading."""
        try:
            client = await binance_client._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()
            return {
                s["symbol"] for s in data.get("symbols", [])
                if s.get("quoteAsset") == "BRL"
                and s.get("status") == "TRADING"
                and not s.get("symbol", "").startswith("LD")
            }
        except Exception as e:
            logger.error(f"Error fetching open markets: {e}")
            return set()

    async def scan_momentum_opportunities(self) -> list[str]:
        """Find coins with sudden price/volume spikes for momentum scalping."""
        if not self.config or not self.config.momentum_enabled:
            return []

        # Already at max positions?
        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return []

        # Get open markets to avoid "Market is closed" errors
        open_markets = await self._get_open_markets()

        # Get all BRL tickers
        tickers = await binance_client.get_ticker_24h()
        if not tickers:
            return []

        # Filter BRL pairs with momentum signals
        candidates = []
        active_symbols = {p.symbol for p in active}

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("BRL"):
                continue
            if symbol.startswith("LD") or "1MBB" in symbol:
                continue
            if symbol in active_symbols:
                continue
            if symbol not in open_markets:
                continue

            try:
                price_change = float(t.get("priceChangePercent", 0))
                volume_24h = float(t.get("quoteVolume", 0))
                # Use weighted avg price vs last price for recent momentum
                weighted_avg = float(t.get("weightedAvgPrice", 0))
                last_price = float(t.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue

            # Skip low volume pairs
            if volume_24h < self.config.min_volume_24h:
                continue

            # Check momentum: price rising significantly
            if price_change < self.config.min_price_change_1h:
                continue

            # Confirm upward momentum (price above weighted avg)
            if weighted_avg > 0 and last_price > weighted_avg * 1.002:
                candidates.append({
                    "symbol": symbol,
                    "change": price_change,
                    "volume": volume_24h,
                })

        # Sort by price change (strongest momentum first)
        candidates.sort(key=lambda x: x["change"], reverse=True)

        # Take top candidates (leave room for max trades)
        slots = self.config.max_concurrent_trades - len(active)
        max_per_cycle = getattr(
            self.config, 'max_trades_per_cycle', 3
        )
        top = candidates[:min(slots, max_per_cycle)]

        if top:
            symbols = [c["symbol"] for c in top]
            desc = ", ".join(
                f"{c['symbol']} +{c['change']:.1f}%" for c in top
            )
            logger.info(f"Scalp MOMENTUM signals: {desc}")
            return symbols
        return []

    async def execute_momentum_scalp(self, symbol: str) -> Optional[dict]:
        """Execute a momentum-based scalp with tighter TP/SL."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        # Place market buy
        result = await trading_engine.buy_with_brl(
            symbol, self.config.amount_per_trade_brl, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Scalp momentum: failed to buy {symbol}")
            self._log_activity(
                "FALHA", symbol, "Mercado fechado ou ordem rejeitada"
            )
            return None

        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_brl=self.config.amount_per_trade_brl,
            highest_price=result.price,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        logger.info(
            f"SCALP MOMENTUM BUY: {symbol} @ R${result.price:.4f}, "
            f"qty={result.quantity:.8f}, R${self.config.amount_per_trade_brl}"
        )
        self._log_activity(
            "COMPRA (Momentum)", symbol,
            f"R${result.price:.4f} x {result.quantity:.6f} = "
            f"R${self.config.amount_per_trade_brl:.2f}"
        )
        return position.model_dump()

    async def run_cycle(self):
        """Run one full cycle: scan + momentum + monitor positions.

        Called by the scheduler every 60 seconds.
        """
        if not self.config or not self.config.active:
            return

        active_count = len(self.get_active_positions())
        self._log_activity(
            "SCAN", "mercado",
            f"Buscando oportunidades... "
            f"({active_count} posições ativas)"
        )

        # Scan for new listings
        new_pairs = await self.scan_for_new_listings()
        if new_pairs:
            self._log_activity(
                "NOVA LISTAGEM", ", ".join(new_pairs),
                f"{len(new_pairs)} nova(s) moeda(s) detectada(s)!"
            )
        for symbol in new_pairs:
            await self.execute_scalp(symbol)

        # Scan for momentum opportunities
        if self.config.momentum_enabled:
            momentum_symbols = await self.scan_momentum_opportunities()
            if momentum_symbols:
                self._log_activity(
                    "MOMENTUM DETECTADO",
                    ", ".join(momentum_symbols),
                    "Sinal de alta identificado"
                )
            for symbol in momentum_symbols:
                await self.execute_momentum_scalp(symbol)

        # Monitor existing positions
        await self.monitor_positions()


scalping_strategy = ScalpingStrategy()
