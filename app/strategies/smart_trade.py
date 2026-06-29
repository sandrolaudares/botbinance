"""Native Smart Trade engine with take-profit, trailing stop, and stop-loss."""

import logging
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel

from app.models.trading import StrategyType
from app.services.binance_client import binance_client
from app.services.market_analysis import market_analyzer
from app.services.trading_engine import trading_engine

logger = logging.getLogger(__name__)


class SmartTradeConfig(BaseModel):
    """Configuration for a Smart Trade."""

    symbol: str  # e.g., "BTCBRL"
    amount_brl: float  # Amount to invest
    take_profit_percent: float = 3.0  # Take profit trigger (%)
    trailing_percent: float = 1.0  # Trailing deviation from peak (%)
    stop_loss_percent: float = 5.0  # Stop loss threshold (%)
    use_market_analysis: bool = False  # Auto-select coins


class SmartTradePosition(BaseModel):
    """An active Smart Trade position being monitored."""

    id: str
    symbol: str
    side: str = "BUY"
    entry_price: float
    quantity: float
    amount_brl: float
    take_profit_percent: float
    trailing_percent: float
    stop_loss_percent: float
    highest_price: float  # Peak price since entry (for trailing)
    trailing_active: bool = False  # Whether trailing has been activated
    status: str = "ACTIVE"  # ACTIVE, CLOSED, STOPPED
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None


class SmartTradeStrategy:
    """Native Smart Trade with take-profit + trailing stop + stop-loss.

    Logic:
    1. Buy at market price
    2. Monitor price continuously
    3. If price drops below entry - stop_loss_percent -> SELL (stop loss)
    4. If price rises above entry + take_profit_percent -> activate trailing
    5. Once trailing active, track highest price
    6. If price drops from highest by trailing_percent -> SELL (take profit)
    """

    def __init__(self):
        self.positions: list[SmartTradePosition] = []

    def get_active_positions(self) -> list[SmartTradePosition]:
        return [p for p in self.positions if p.status == "ACTIVE"]

    def get_closed_positions(self) -> list[SmartTradePosition]:
        return [p for p in self.positions if p.status != "ACTIVE"]

    async def create_trade(self, config: SmartTradeConfig) -> Optional[dict]:
        """Create a new Smart Trade: buy at market and start monitoring."""
        # Place market buy order via Binance
        result = await trading_engine.buy_with_brl(
            config.symbol, config.amount_brl, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Smart Trade: failed to buy {config.symbol}")
            return None

        entry_price = result.price
        quantity = result.quantity

        position = SmartTradePosition(
            id=f"st_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=config.symbol,
            entry_price=entry_price,
            quantity=quantity,
            amount_brl=config.amount_brl,
            take_profit_percent=config.take_profit_percent,
            trailing_percent=config.trailing_percent,
            stop_loss_percent=config.stop_loss_percent,
            highest_price=entry_price,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        logger.info(
            f"Smart Trade opened: {config.symbol} @ {entry_price:.2f} BRL, "
            f"qty={quantity:.8f}, TP={config.take_profit_percent}%, "
            f"Trail={config.trailing_percent}%, SL={config.stop_loss_percent}%"
        )
        return position.model_dump()

    async def create_auto_trades(
        self,
        total_amount_brl: float,
        top_n: int = 3,
        take_profit_percent: float = 3.0,
        trailing_percent: float = 1.0,
        stop_loss_percent: float = 5.0,
    ) -> list[dict]:
        """Auto-create Smart Trades from market analysis."""
        analyses = await market_analyzer.analyze_top_brl_pairs(top_n=10)
        if not analyses:
            logger.warning("No market analysis results")
            return []

        buy_signals = [
            a for a in analyses
            if a.recommendation in ("BUY", "STRONG_BUY")
        ][:top_n]

        if not buy_signals:
            logger.info("No BUY signals found")
            return []

        amount_per_trade = total_amount_brl / len(buy_signals)
        if amount_per_trade < 10:
            logger.warning(f"Amount per trade too small: R${amount_per_trade}")
            return []

        trades = []
        for analysis in buy_signals:
            config = SmartTradeConfig(
                symbol=analysis.symbol,
                amount_brl=amount_per_trade,
                take_profit_percent=take_profit_percent,
                trailing_percent=trailing_percent,
                stop_loss_percent=stop_loss_percent,
            )
            result = await self.create_trade(config)
            if result:
                trades.append(result)

        logger.info(
            f"Auto Smart Trades: {len(trades)}/{len(buy_signals)} created"
        )
        return trades

    async def monitor_positions(self):
        """Check all active positions and execute TP/SL if triggered.

        Called periodically by the scheduler.
        """
        active = self.get_active_positions()
        if not active:
            return

        for pos in active:
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(
                    f"Smart Trade monitor error {pos.symbol}: {e}"
                )

    async def _check_position(self, pos: SmartTradePosition):
        """Check a single position for TP/SL triggers."""
        # Get current price
        current_price = await binance_client.get_symbol_price(pos.symbol)
        if not current_price:
            return

        price_change_pct = (
            (current_price - pos.entry_price) / pos.entry_price
        ) * 100

        # Update highest price
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Check STOP LOSS
        if price_change_pct <= -pos.stop_loss_percent:
            logger.warning(
                f"Smart Trade STOP LOSS: {pos.symbol} "
                f"({price_change_pct:.2f}% < -{pos.stop_loss_percent}%)"
            )
            await self._close_position(pos, current_price, "STOP_LOSS")
            return

        # Check TAKE PROFIT activation
        if price_change_pct >= pos.take_profit_percent:
            if not pos.trailing_active:
                pos.trailing_active = True
                logger.info(
                    f"Smart Trade TRAILING ACTIVATED: {pos.symbol} "
                    f"({price_change_pct:.2f}% >= {pos.take_profit_percent}%)"
                )

        # Check TRAILING STOP (only if trailing is active)
        if pos.trailing_active:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            if drop_from_peak >= pos.trailing_percent:
                logger.info(
                    f"Smart Trade TRAILING SELL: {pos.symbol} "
                    f"(dropped {drop_from_peak:.2f}% from peak "
                    f"{pos.highest_price:.2f})"
                )
                await self._close_position(
                    pos, current_price, "TAKE_PROFIT"
                )

    async def _close_position(
        self, pos: SmartTradePosition, price: float, reason: str
    ):
        """Close a position by selling at market."""
        # Place sell order
        order = await binance_client.place_market_order(
            symbol=pos.symbol,
            side="SELL",
            quantity=pos.quantity,
        )

        if order:
            pos.status = "CLOSED"
            pos.close_price = price
            pos.closed_at = datetime.now(UTC).isoformat()
            pos.pnl_percent = (
                (price - pos.entry_price) / pos.entry_price
            ) * 100
            logger.info(
                f"Smart Trade CLOSED ({reason}): {pos.symbol} "
                f"entry={pos.entry_price:.2f} exit={price:.2f} "
                f"PnL={pos.pnl_percent:.2f}%"
            )
        else:
            logger.error(
                f"Smart Trade: failed to sell {pos.symbol} ({reason})"
            )

    async def close_position_by_id(self, trade_id: str) -> Optional[dict]:
        """Manually close a specific position."""
        for pos in self.positions:
            if pos.id == trade_id and pos.status == "ACTIVE":
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


smart_trade_strategy = SmartTradeStrategy()
