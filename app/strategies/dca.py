import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from app.models.trading import DCAConfig, StrategyType, Trade
from app.services.paper_trading import paper_engine

logger = logging.getLogger(__name__)


class DCAStrategy:
    """
    Dollar Cost Averaging (DCA) Strategy.

    Buys a fixed BRL amount of a cryptocurrency at regular intervals,
    regardless of price. Reduces impact of volatility over time.
    """

    def __init__(self):
        self.active_dcas: dict[str, DCAConfig] = {}
        self.last_execution: dict[str, datetime] = {}
        self.dca_history: dict[str, list[Trade]] = {}

    def setup_dca(self, config: DCAConfig) -> bool:
        """Set up a new DCA plan for a symbol."""
        self.active_dcas[config.symbol] = config
        self.dca_history.setdefault(config.symbol, [])
        logger.info(
            f"DCA setup for {config.symbol}: "
            f"{config.amount_brl:.2f} BRL every {config.interval_minutes} min"
        )
        return True

    async def check_and_execute(self, symbol: str) -> Optional[Trade]:
        """Check if it's time to execute DCA and buy."""
        if symbol not in self.active_dcas:
            return None

        config = self.active_dcas[symbol]
        if not config.active:
            return None

        now = datetime.now(UTC)
        last = self.last_execution.get(symbol)

        if last is not None:
            next_execution = last + timedelta(minutes=config.interval_minutes)
            if now < next_execution:
                return None

        # Execute DCA buy
        trade = await paper_engine.buy_with_brl(
            symbol=symbol,
            amount_brl=config.amount_brl,
            strategy=StrategyType.DCA,
        )

        if trade:
            self.last_execution[symbol] = now
            self.dca_history[symbol].append(trade)
            logger.info(
                f"DCA executed: bought {config.amount_brl:.2f} BRL of {symbol}"
            )

        return trade

    async def execute_all(self) -> list[Trade]:
        """Execute DCA for all configured symbols."""
        trades = []
        for symbol in list(self.active_dcas.keys()):
            trade = await self.check_and_execute(symbol)
            if trade:
                trades.append(trade)
        return trades

    def remove_dca(self, symbol: str):
        """Remove a DCA plan."""
        self.active_dcas.pop(symbol, None)
        self.last_execution.pop(symbol, None)

    def get_dca_status(self, symbol: str) -> Optional[dict]:
        """Get DCA status for a symbol."""
        if symbol not in self.active_dcas:
            return None

        config = self.active_dcas[symbol]
        last = self.last_execution.get(symbol)
        history = self.dca_history.get(symbol, [])
        total_invested = sum(t.total_brl for t in history)

        return {
            "symbol": symbol,
            "active": config.active,
            "amount_brl": config.amount_brl,
            "interval_minutes": config.interval_minutes,
            "last_execution": last.isoformat() if last else None,
            "total_executions": len(history),
            "total_invested_brl": total_invested,
        }

    def get_all_dca_status(self) -> list[dict]:
        """Get status of all DCA plans."""
        statuses = []
        for symbol in self.active_dcas:
            status = self.get_dca_status(symbol)
            if status:
                statuses.append(status)
        return statuses


dca_strategy = DCAStrategy()
