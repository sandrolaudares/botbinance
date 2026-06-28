from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Binance API
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False  # Use real API when keys are provided

    # Trading
    paper_trading: bool = True  # Paper trading enabled by default
    initial_balance_brl: float = 1000.0  # Initial simulated balance in BRL
    max_risk_per_trade: float = 0.02  # 2% max risk per trade
    max_open_positions: int = 5

    # Grid Trading
    grid_levels: int = 10
    grid_spread_percent: float = 1.0  # 1% between grid levels

    # DCA
    dca_interval_minutes: int = 60  # Buy every hour
    dca_amount_brl: float = 50.0  # Amount per DCA buy

    # Auto-Invest
    auto_invest_enabled: bool = False
    auto_invest_top_n: int = 5  # Top N coins to analyze
    auto_invest_rebalance_hours: int = 4

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env"}


settings = Settings()
