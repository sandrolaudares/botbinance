from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


class StrategyType(str, Enum):
    GRID = "GRID"
    DCA = "DCA"
    AUTO_INVEST = "AUTO_INVEST"
    SMART_TRADE = "SMART_TRADE"


class Trade(BaseModel):
    id: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float
    total_brl: float
    status: OrderStatus = OrderStatus.PENDING
    strategy: StrategyType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pnl: float = 0.0


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_percent: float = 0.0
    strategy: StrategyType
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PortfolioSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_balance_brl: float
    available_brl: float
    positions_value_brl: float
    total_pnl: float
    total_pnl_percent: float
    positions: list[Position] = []


class GridConfig(BaseModel):
    symbol: str
    upper_price: float
    lower_price: float
    levels: int = 10
    investment_brl: float = 500.0
    active: bool = True


class DCAConfig(BaseModel):
    symbol: str
    amount_brl: float = 50.0
    interval_minutes: int = 60
    active: bool = True


class AutoInvestConfig(BaseModel):
    top_n_coins: int = 5
    total_investment_brl: float = 500.0
    rebalance_hours: int = 4
    min_volume_24h: float = 1_000_000.0
    active: bool = True


class MarketAnalysis(BaseModel):
    symbol: str
    price: float
    change_24h: float
    volume_24h: float
    rsi: Optional[float] = None
    macd_signal: Optional[str] = None
    score: float = 0.0
    recommendation: str = "HOLD"
