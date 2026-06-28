import logging
from typing import Optional

from app.config import settings
from app.models.trading import (
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    StrategyType,
    Trade,
)
from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Simulated trading engine for paper trading."""

    def __init__(self):
        self.balance_brl: float = settings.initial_balance_brl
        self.positions: dict[str, Position] = {}  # symbol -> Position
        self.trades: list[Trade] = []
        self.total_realized_pnl: float = 0.0
        self._initial_balance: float = settings.initial_balance_brl

    def reset(self):
        """Reset the paper trading account."""
        self.balance_brl = settings.initial_balance_brl
        self.positions = {}
        self.trades = []
        self.total_realized_pnl = 0.0
        self._initial_balance = settings.initial_balance_brl

    async def execute_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        strategy: StrategyType,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Trade]:
        """Execute a simulated order."""
        if price is None:
            price = await binance_client.get_ticker_price(symbol)
            if price is None:
                logger.error(f"Cannot get price for {symbol}")
                return None

        total_brl = quantity * price

        if side == OrderSide.BUY:
            if total_brl > self.balance_brl:
                logger.warning(
                    f"Insufficient balance: need {total_brl:.2f} BRL, "
                    f"have {self.balance_brl:.2f} BRL"
                )
                return None

            self.balance_brl -= total_brl

            if symbol in self.positions:
                pos = self.positions[symbol]
                new_qty = pos.quantity + quantity
                pos.avg_entry_price = (
                    (pos.avg_entry_price * pos.quantity) + (price * quantity)
                ) / new_qty
                pos.quantity = new_qty
            else:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity,
                    avg_entry_price=price,
                    current_price=price,
                    strategy=strategy,
                )

        elif side == OrderSide.SELL:
            if symbol not in self.positions:
                logger.warning(f"No position in {symbol} to sell")
                return None

            pos = self.positions[symbol]
            if quantity > pos.quantity:
                logger.warning(
                    f"Cannot sell {quantity} {symbol}, only have {pos.quantity}"
                )
                return None

            pnl = (price - pos.avg_entry_price) * quantity
            self.total_realized_pnl += pnl
            self.balance_brl += total_brl

            pos.quantity -= quantity
            if pos.quantity <= 0.0001:  # Effectively zero
                del self.positions[symbol]

        trade = Trade(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            total_brl=total_brl,
            status=OrderStatus.FILLED,
            strategy=strategy,
            pnl=pnl if side == OrderSide.SELL else 0.0,
        )
        self.trades.append(trade)
        logger.info(
            f"[PAPER] {side.value} {quantity:.6f} {symbol} @ {price:.2f} BRL "
            f"(Total: {total_brl:.2f} BRL)"
        )
        return trade

    async def buy_with_brl(
        self,
        symbol: str,
        amount_brl: float,
        strategy: StrategyType,
        price: Optional[float] = None,
    ) -> Optional[Trade]:
        """Buy a specified BRL amount of a symbol."""
        if price is None:
            price = await binance_client.get_ticker_price(symbol)
            if price is None:
                return None

        quantity = amount_brl / price
        return await self.execute_order(symbol, OrderSide.BUY, quantity, strategy, price)

    async def sell_position(
        self,
        symbol: str,
        percentage: float = 1.0,
        strategy: Optional[StrategyType] = None,
    ) -> Optional[Trade]:
        """Sell a percentage of a position."""
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        quantity = pos.quantity * percentage
        strat = strategy or pos.strategy
        return await self.execute_order(symbol, OrderSide.SELL, quantity, strat)

    async def get_portfolio(self) -> PortfolioSnapshot:
        """Get current portfolio snapshot with updated prices."""
        positions_value = 0.0
        updated_positions = []

        if self.positions:
            symbols = list(self.positions.keys())
            prices = await binance_client.get_multiple_prices(symbols)

            for symbol, pos in self.positions.items():
                current_price = prices.get(symbol, pos.current_price)
                pos.current_price = current_price
                position_value = pos.quantity * current_price
                positions_value += position_value
                pos.unrealized_pnl = (current_price - pos.avg_entry_price) * pos.quantity
                if pos.avg_entry_price > 0:
                    pos.unrealized_pnl_percent = (
                        (current_price - pos.avg_entry_price) / pos.avg_entry_price
                    ) * 100
                updated_positions.append(pos.model_copy())

        total_balance = self.balance_brl + positions_value
        total_pnl = total_balance - self._initial_balance
        total_pnl_percent = (
            (total_pnl / self._initial_balance) * 100 if self._initial_balance > 0 else 0
        )

        return PortfolioSnapshot(
            total_balance_brl=total_balance,
            available_brl=self.balance_brl,
            positions_value_brl=positions_value,
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            positions=updated_positions,
        )

    def get_trade_history(self, limit: int = 50) -> list[Trade]:
        """Get recent trade history."""
        return sorted(self.trades, key=lambda t: t.timestamp, reverse=True)[:limit]

    def get_stats(self) -> dict:
        """Get trading statistics."""
        if not self.trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_realized_pnl": 0.0,
                "avg_trade_pnl": 0.0,
            }

        sell_trades = [t for t in self.trades if t.side == OrderSide.SELL]
        winning = [t for t in sell_trades if t.pnl > 0]
        losing = [t for t in sell_trades if t.pnl < 0]

        return {
            "total_trades": len(self.trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(sell_trades) * 100 if sell_trades else 0.0,
            "total_realized_pnl": self.total_realized_pnl,
            "avg_trade_pnl": self.total_realized_pnl / len(sell_trades) if sell_trades else 0.0,
        }


paper_engine = PaperTradingEngine()
