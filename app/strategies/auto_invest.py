import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from app.models.trading import AutoInvestConfig, MarketAnalysis, StrategyType, Trade
from app.services.binance_client import binance_client
from app.services.market_analysis import market_analyzer
from app.services.paper_trading import paper_engine

logger = logging.getLogger(__name__)


class AutoInvestStrategy:
    """
    Auto-Invest Strategy.

    Automatically analyzes the market and allocates investment to the
    top-scoring cryptocurrencies based on technical analysis. Rebalances
    periodically to capture short-term opportunities.
    """

    def __init__(self):
        self.config: Optional[AutoInvestConfig] = None
        self.last_rebalance: Optional[datetime] = None
        self.current_allocations: dict[str, float] = {}  # symbol -> percentage
        self.analysis_cache: list[MarketAnalysis] = []
        self.trade_history: list[Trade] = []

    def setup(self, config: AutoInvestConfig) -> bool:
        """Set up auto-invest configuration."""
        self.config = config
        logger.info(
            f"Auto-Invest setup: top {config.top_n_coins} coins, "
            f"{config.total_investment_brl:.2f} BRL, "
            f"rebalance every {config.rebalance_hours}h"
        )
        return True

    async def analyze_market(self) -> list[MarketAnalysis]:
        """Analyze top BRL pairs and rank them."""
        top_pairs = await binance_client.get_top_brl_pairs_by_volume(top_n=20)

        if not top_pairs:
            # Use default pairs
            default_pairs = binance_client._get_default_brl_pairs()
            symbols = default_pairs[:10]
        else:
            symbols = [
                t["symbol"]
                for t in top_pairs
                if float(t.get("quoteVolume", 0))
                >= (self.config.min_volume_24h if self.config else 0)
            ]

        # Analyze each symbol
        analyses = []
        tasks = [market_analyzer.analyze_symbol(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, MarketAnalysis):
                analyses.append(result)

        # Sort by score (highest first)
        analyses.sort(key=lambda a: a.score, reverse=True)
        self.analysis_cache = analyses
        return analyses

    async def calculate_allocations(self) -> dict[str, float]:
        """Calculate optimal allocation percentages based on analysis."""
        if not self.config:
            return {}

        analyses = await self.analyze_market()
        if not analyses:
            return {}

        # Select top N coins with BUY or STRONG_BUY recommendation
        top_coins = [
            a for a in analyses
            if a.recommendation in ("STRONG_BUY", "BUY")
        ][:self.config.top_n_coins]

        if not top_coins:
            # If no strong signals, use top scored ones above 50
            top_coins = [a for a in analyses if a.score > 50][:self.config.top_n_coins]

        if not top_coins:
            logger.info("No suitable coins found for auto-invest")
            return {}

        # Allocate proportional to score
        total_score = sum(a.score for a in top_coins)
        allocations = {}
        for analysis in top_coins:
            pct = (analysis.score / total_score) * 100 if total_score > 0 else 0
            allocations[analysis.symbol] = pct

        self.current_allocations = allocations
        return allocations

    async def rebalance(self) -> list[Trade]:
        """Rebalance portfolio according to calculated allocations."""
        if not self.config or not self.config.active:
            return []

        now = datetime.now(UTC)
        if self.last_rebalance:
            next_rebalance = self.last_rebalance + timedelta(hours=self.config.rebalance_hours)
            if now < next_rebalance:
                return []

        allocations = await self.calculate_allocations()
        if not allocations:
            return []

        trades = []
        investment = self.config.total_investment_brl

        # First, sell positions that are no longer in allocations
        for symbol in list(paper_engine.positions.keys()):
            pos = paper_engine.positions[symbol]
            if pos.strategy == StrategyType.AUTO_INVEST and symbol not in allocations:
                trade = await paper_engine.sell_position(
                    symbol, percentage=1.0, strategy=StrategyType.AUTO_INVEST
                )
                if trade:
                    trades.append(trade)
                    logger.info(f"Auto-Invest: sold {symbol} (no longer in top picks)")

        # Buy according to new allocations
        available = min(paper_engine.balance_brl, investment)
        for symbol, pct in allocations.items():
            amount = available * (pct / 100)
            if amount < 10:  # Minimum BRL amount
                continue

            # Check if we already have a position
            if symbol in paper_engine.positions:
                pos = paper_engine.positions[symbol]
                if pos.strategy == StrategyType.AUTO_INVEST:
                    current_value = pos.quantity * pos.current_price
                    target_value = available * (pct / 100)
                    if abs(current_value - target_value) / target_value < 0.1:
                        continue  # Within 10% of target, skip

            trade = await paper_engine.buy_with_brl(
                symbol=symbol,
                amount_brl=amount,
                strategy=StrategyType.AUTO_INVEST,
            )
            if trade:
                trades.append(trade)
                logger.info(
                    f"Auto-Invest: bought {amount:.2f} BRL of {symbol} "
                    f"(allocation: {pct:.1f}%)"
                )

        self.last_rebalance = now
        self.trade_history.extend(trades)
        return trades

    async def take_profit(self, min_profit_percent: float = 2.0) -> list[Trade]:
        """Take profit on positions that exceeded target."""
        trades = []
        for symbol, pos in list(paper_engine.positions.items()):
            if pos.strategy != StrategyType.AUTO_INVEST:
                continue

            if pos.unrealized_pnl_percent >= min_profit_percent:
                # Sell 50% of profitable position
                trade = await paper_engine.sell_position(
                    symbol, percentage=0.5, strategy=StrategyType.AUTO_INVEST
                )
                if trade:
                    trades.append(trade)
                    logger.info(
                        f"Auto-Invest: took profit on {symbol} "
                        f"(+{pos.unrealized_pnl_percent:.1f}%)"
                    )

        return trades

    def get_status(self) -> dict:
        """Get auto-invest status."""
        return {
            "active": self.config.active if self.config else False,
            "top_n_coins": self.config.top_n_coins if self.config else 0,
            "total_investment_brl": self.config.total_investment_brl if self.config else 0,
            "rebalance_hours": self.config.rebalance_hours if self.config else 0,
            "last_rebalance": self.last_rebalance.isoformat() if self.last_rebalance else None,
            "current_allocations": self.current_allocations,
            "total_trades": len(self.trade_history),
            "analysis_count": len(self.analysis_cache),
        }

    def get_latest_analysis(self) -> list[dict]:
        """Get cached market analysis."""
        return [a.model_dump() for a in self.analysis_cache]


auto_invest_strategy = AutoInvestStrategy()
