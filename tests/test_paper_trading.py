from unittest.mock import AsyncMock, patch

import pytest

from app.models.trading import OrderSide, OrderStatus, StrategyType
from app.services.paper_trading import PaperTradingEngine


@pytest.fixture
def engine():
    e = PaperTradingEngine()
    e.balance_brl = 1000.0
    e._initial_balance = 1000.0
    return e


@pytest.mark.asyncio
async def test_buy_reduces_balance(engine):
    trade = await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.BUY,
        quantity=0.001,
        strategy=StrategyType.GRID,
        price=500000.0,
    )
    assert trade is not None
    assert trade.status == OrderStatus.FILLED
    assert engine.balance_brl == pytest.approx(500.0)
    assert "BTCBRL" in engine.positions
    assert engine.positions["BTCBRL"].quantity == 0.001


@pytest.mark.asyncio
async def test_buy_insufficient_balance(engine):
    trade = await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.BUY,
        quantity=1.0,
        strategy=StrategyType.GRID,
        price=500000.0,
    )
    assert trade is None
    assert engine.balance_brl == 1000.0


@pytest.mark.asyncio
async def test_sell_increases_balance(engine):
    # Buy first
    await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.BUY,
        quantity=0.001,
        strategy=StrategyType.GRID,
        price=500000.0,
    )
    # Sell at higher price
    trade = await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.SELL,
        quantity=0.001,
        strategy=StrategyType.GRID,
        price=550000.0,
    )
    assert trade is not None
    assert trade.pnl == pytest.approx(50.0)
    assert engine.balance_brl == pytest.approx(1050.0)
    assert "BTCBRL" not in engine.positions


@pytest.mark.asyncio
async def test_sell_no_position(engine):
    trade = await engine.execute_order(
        symbol="ETHBRL",
        side=OrderSide.SELL,
        quantity=1.0,
        strategy=StrategyType.GRID,
        price=10000.0,
    )
    assert trade is None


@pytest.mark.asyncio
async def test_buy_with_brl(engine):
    with patch(
        "app.services.paper_trading.binance_client.get_ticker_price",
        new_callable=AsyncMock,
        return_value=500000.0,
    ):
        trade = await engine.buy_with_brl("BTCBRL", 100.0, StrategyType.DCA)
        assert trade is not None
        assert trade.quantity == pytest.approx(0.0002)
        assert engine.balance_brl == pytest.approx(900.0)


@pytest.mark.asyncio
async def test_get_portfolio(engine):
    await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.BUY,
        quantity=0.001,
        strategy=StrategyType.GRID,
        price=500000.0,
    )

    with patch(
        "app.services.paper_trading.binance_client.get_multiple_prices",
        new_callable=AsyncMock,
        return_value={"BTCBRL": 510000.0},
    ):
        portfolio = await engine.get_portfolio()
        assert portfolio.available_brl == pytest.approx(500.0)
        assert portfolio.positions_value_brl == pytest.approx(510.0)
        assert portfolio.total_pnl == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_get_stats_empty(engine):
    stats = engine.get_stats()
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0


@pytest.mark.asyncio
async def test_reset(engine):
    await engine.execute_order(
        symbol="BTCBRL",
        side=OrderSide.BUY,
        quantity=0.001,
        strategy=StrategyType.GRID,
        price=500000.0,
    )
    engine.reset()
    assert engine.balance_brl == 1000.0
    assert len(engine.positions) == 0
    assert len(engine.trades) == 0
