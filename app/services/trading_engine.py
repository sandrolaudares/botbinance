"""Unified trading engine that routes between paper and real trading."""

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
from app.services.paper_trading import paper_engine

logger = logging.getLogger(__name__)


class TradingEngine:
    """Routes trading operations to paper or real execution."""

    @property
    def is_live(self) -> bool:
        """Check if we're in live trading mode."""
        return not settings.paper_trading and binance_client.has_credentials

    async def buy_with_brl(
        self,
        symbol: str,
        amount_brl: float,
        strategy: StrategyType,
        price: Optional[float] = None,
    ) -> Optional[Trade]:
        """Buy a symbol with BRL amount."""
        if not self.is_live:
            return await paper_engine.buy_with_brl(symbol, amount_brl, strategy, price)

        # Real trading via Binance API
        order = await binance_client.place_market_order(
            symbol=symbol,
            side="BUY",
            quote_order_qty=amount_brl,
        )
        if not order:
            return None

        # Convert Binance order response to Trade model
        executed_qty = float(order.get("executedQty", 0))
        cumulative_quote = float(order.get("cummulativeQuoteQty", 0))
        avg_price = cumulative_quote / executed_qty if executed_qty > 0 else 0

        trade = Trade(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=executed_qty,
            price=avg_price,
            total_brl=cumulative_quote,
            status=OrderStatus.FILLED,
            strategy=strategy,
            pnl=0.0,
        )
        logger.info(
            f"[LIVE] BUY {executed_qty:.6f} {symbol} @ {avg_price:.2f} BRL "
            f"(Total: {cumulative_quote:.2f} BRL)"
        )
        return trade

    async def sell(
        self,
        symbol: str,
        quantity: float,
        strategy: StrategyType,
        price: Optional[float] = None,
    ) -> Optional[Trade]:
        """Sell a quantity of a symbol."""
        if not self.is_live:
            return await paper_engine.execute_order(
                symbol, OrderSide.SELL, quantity, strategy, price
            )

        # Real trading via Binance API
        order = await binance_client.place_market_order(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
        )
        if not order:
            return None

        executed_qty = float(order.get("executedQty", 0))
        cumulative_quote = float(order.get("cummulativeQuoteQty", 0))
        avg_price = cumulative_quote / executed_qty if executed_qty > 0 else 0

        trade = Trade(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=executed_qty,
            price=avg_price,
            total_brl=cumulative_quote,
            status=OrderStatus.FILLED,
            strategy=strategy,
            pnl=0.0,  # PnL calculated separately for live trades
        )
        logger.info(
            f"[LIVE] SELL {executed_qty:.6f} {symbol} @ {avg_price:.2f} BRL "
            f"(Total: {cumulative_quote:.2f} BRL)"
        )
        return trade

    async def get_portfolio(self) -> PortfolioSnapshot:
        """Get portfolio - from paper engine or real account."""
        if not self.is_live:
            return await paper_engine.get_portfolio()

        # Live portfolio from Binance account
        account = await binance_client.get_account_info()
        if not account:
            return await paper_engine.get_portfolio()  # Fallback

        brl_balance = 0.0
        positions = []
        positions_value = 0.0

        for bal in account.get("balances", []):
            asset = bal["asset"]
            free = float(bal["free"])
            locked = float(bal["locked"])
            total = free + locked

            if asset == "BRL":
                brl_balance = free
            elif total > 0:
                symbol = f"{asset}BRL"
                price = await binance_client.get_ticker_price(symbol)
                if price and price > 0:
                    value = total * price
                    if value >= 1.0:  # Only show positions worth >= R$1
                        positions_value += value
                        positions.append(Position(
                            symbol=symbol,
                            quantity=total,
                            avg_entry_price=0,  # Not available from balance alone
                            current_price=price,
                            strategy=StrategyType.AUTO_INVEST,
                        ))

        total_balance = brl_balance + positions_value

        return PortfolioSnapshot(
            total_balance_brl=total_balance,
            available_brl=brl_balance,
            positions_value_brl=positions_value,
            total_pnl=0.0,
            total_pnl_percent=0.0,
            positions=positions,
        )

    def get_trade_history(self, limit: int = 50) -> list[Trade]:
        """Get trade history."""
        return paper_engine.get_trade_history(limit)

    def get_stats(self) -> dict:
        """Get trading stats."""
        return paper_engine.get_stats()

    def reset(self):
        """Reset (only works in paper trading mode)."""
        if not self.is_live:
            paper_engine.reset()


trading_engine = TradingEngine()
