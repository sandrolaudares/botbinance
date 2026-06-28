import logging
from typing import Optional

from app.models.trading import GridConfig, OrderSide, StrategyType, Trade
from app.services.binance_client import binance_client
from app.services.paper_trading import paper_engine

logger = logging.getLogger(__name__)


class GridTradingStrategy:
    """
    Grid Trading Strategy.

    Places buy and sell orders at regular intervals above and below a set price,
    creating a grid of orders. Profits from price oscillations within the grid range.
    """

    def __init__(self):
        self.active_grids: dict[str, GridConfig] = {}
        self.grid_orders: dict[str, list[dict]] = {}  # symbol -> grid levels

    async def setup_grid(self, config: GridConfig) -> bool:
        """Set up a new grid for a symbol."""
        current_price = await binance_client.get_ticker_price(config.symbol)
        if current_price is None:
            logger.error(f"Cannot get price for {config.symbol}")
            return False

        # Auto-calculate grid bounds if not set
        if config.upper_price == 0 or config.lower_price == 0:
            spread = current_price * 0.05  # 5% spread
            config.upper_price = current_price + spread
            config.lower_price = current_price - spread

        # Calculate grid levels
        price_range = config.upper_price - config.lower_price
        step = price_range / config.levels
        amount_per_level = config.investment_brl / config.levels

        grid_levels = []
        for i in range(config.levels + 1):
            level_price = config.lower_price + (step * i)
            grid_levels.append({
                "price": level_price,
                "amount_brl": amount_per_level,
                "side": OrderSide.BUY if level_price < current_price else OrderSide.SELL,
                "filled": False,
            })

        self.active_grids[config.symbol] = config
        self.grid_orders[config.symbol] = grid_levels

        logger.info(
            f"Grid setup for {config.symbol}: "
            f"{config.lower_price:.2f} - {config.upper_price:.2f} BRL, "
            f"{config.levels} levels, {amount_per_level:.2f} BRL/level"
        )
        return True

    async def check_and_execute(self, symbol: str) -> list[Trade]:
        """Check grid levels and execute orders if price crosses levels."""
        if symbol not in self.active_grids or not self.active_grids[symbol].active:
            return []

        current_price = await binance_client.get_ticker_price(symbol)
        if current_price is None:
            return []

        executed_trades = []
        grid_levels = self.grid_orders.get(symbol, [])

        for level in grid_levels:
            if level["filled"]:
                continue

            level_price = level["price"]

            # Buy when price drops to or below grid level
            if level["side"] == OrderSide.BUY and current_price <= level_price:
                trade = await paper_engine.buy_with_brl(
                    symbol=symbol,
                    amount_brl=level["amount_brl"],
                    strategy=StrategyType.GRID,
                    price=current_price,
                )
                if trade:
                    level["filled"] = False
                    level["side"] = OrderSide.SELL  # Flip to sell
                    executed_trades.append(trade)
                    logger.info(f"Grid BUY {symbol} at {current_price:.2f}")

            # Sell when price rises to or above grid level
            elif level["side"] == OrderSide.SELL and current_price >= level_price:
                if symbol in paper_engine.positions:
                    # Sell proportional amount
                    pos = paper_engine.positions[symbol]
                    sell_qty = level["amount_brl"] / current_price
                    sell_qty = min(sell_qty, pos.quantity)

                    if sell_qty > 0:
                        trade = await paper_engine.execute_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=sell_qty,
                            strategy=StrategyType.GRID,
                            price=current_price,
                        )
                        if trade:
                            level["filled"] = False
                            level["side"] = OrderSide.BUY  # Flip to buy
                            executed_trades.append(trade)
                            logger.info(f"Grid SELL {symbol} at {current_price:.2f}")

        return executed_trades

    def remove_grid(self, symbol: str):
        """Remove an active grid."""
        self.active_grids.pop(symbol, None)
        self.grid_orders.pop(symbol, None)

    def get_grid_status(self, symbol: str) -> Optional[dict]:
        """Get status of a grid."""
        if symbol not in self.active_grids:
            return None

        config = self.active_grids[symbol]
        levels = self.grid_orders.get(symbol, [])
        filled_count = sum(1 for level in levels if level["filled"])

        return {
            "symbol": symbol,
            "active": config.active,
            "upper_price": config.upper_price,
            "lower_price": config.lower_price,
            "total_levels": len(levels),
            "filled_levels": filled_count,
            "investment_brl": config.investment_brl,
        }

    def get_all_grids_status(self) -> list[dict]:
        """Get status of all active grids."""
        statuses = []
        for symbol in self.active_grids:
            status = self.get_grid_status(symbol)
            if status:
                statuses.append(status)
        return statuses


grid_strategy = GridTradingStrategy()
