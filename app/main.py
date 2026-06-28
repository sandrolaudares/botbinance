import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.trading import AutoInvestConfig, DCAConfig, GridConfig
from app.services.binance_client import binance_client
from app.services.market_analysis import market_analyzer
from app.services.paper_trading import paper_engine
from app.services.trading_engine import trading_engine
from app.strategies.auto_invest import auto_invest_strategy
from app.strategies.dca import dca_strategy
from app.strategies.grid_trading import grid_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_grid_check():
    """Check all grid strategies periodically."""
    for symbol in list(grid_strategy.active_grids.keys()):
        await grid_strategy.check_and_execute(symbol)


async def scheduled_dca_check():
    """Check all DCA strategies periodically."""
    await dca_strategy.execute_all()


async def scheduled_auto_invest():
    """Run auto-invest rebalance and take profits."""
    if auto_invest_strategy.config and auto_invest_strategy.config.active:
        await auto_invest_strategy.take_profit()
        await auto_invest_strategy.rebalance()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Binance Trading Bot...")
    logger.info(f"Paper Trading: {'ENABLED' if settings.paper_trading else 'DISABLED'}")
    logger.info(f"Initial Balance: {settings.initial_balance_brl:.2f} BRL")

    # Start scheduler
    scheduler.add_job(scheduled_grid_check, "interval", seconds=30, id="grid_check")
    scheduler.add_job(scheduled_dca_check, "interval", minutes=1, id="dca_check")
    scheduler.add_job(scheduled_auto_invest, "interval", minutes=15, id="auto_invest")
    scheduler.start()

    yield

    # Cleanup
    scheduler.shutdown()
    await binance_client.close()
    logger.info("Bot stopped.")


app = FastAPI(
    title="Binance Trading Bot",
    description="Bot de trading automatizado com Grid Trading, DCA e Auto-Invest",
    version="1.0.0",
    lifespan=lifespan,
)

_BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


# ============ Web Dashboard Routes ============


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    return templates.TemplateResponse(request, "dashboard.html")


# ============ API Routes ============


@app.get("/api/portfolio")
async def get_portfolio():
    """Get current portfolio state."""
    portfolio = await trading_engine.get_portfolio()
    return portfolio.model_dump()


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    """Get trade history."""
    trades = trading_engine.get_trade_history(limit)
    return [t.model_dump() for t in trades]


@app.get("/api/stats")
async def get_stats():
    """Get trading statistics."""
    return trading_engine.get_stats()


@app.post("/api/reset")
async def reset_account():
    """Reset paper trading account."""
    trading_engine.reset()
    return {"message": "Account reset", "balance_brl": paper_engine.balance_brl}


@app.get("/api/mode")
async def get_trading_mode():
    """Get current trading mode info."""
    return {
        "paper_trading": settings.paper_trading,
        "has_credentials": binance_client.has_credentials,
        "is_live": trading_engine.is_live,
        "testnet": settings.binance_testnet,
    }


@app.post("/api/mode/live")
async def enable_live_trading():
    """Enable live trading (requires API credentials)."""
    if not binance_client.has_credentials:
        return {"error": "No API credentials configured", "status": "error"}
    settings.paper_trading = False
    return {"message": "Live trading enabled", "is_live": True}


@app.post("/api/mode/paper")
async def enable_paper_trading():
    """Switch back to paper trading."""
    settings.paper_trading = True
    return {"message": "Paper trading enabled", "is_live": False}


# ---- Grid Trading API ----


@app.post("/api/grid/setup")
async def setup_grid(config: GridConfig):
    """Set up a new grid trading strategy."""
    success = await grid_strategy.setup_grid(config)
    if success:
        return {"message": f"Grid setup for {config.symbol}", "status": "active"}
    return {"message": "Failed to setup grid", "status": "error"}


@app.delete("/api/grid/{symbol}")
async def remove_grid(symbol: str):
    """Remove a grid strategy."""
    grid_strategy.remove_grid(symbol)
    return {"message": f"Grid removed for {symbol}"}


@app.get("/api/grid/status")
async def get_grid_status():
    """Get all grid strategies status."""
    return grid_strategy.get_all_grids_status()


# ---- DCA API ----


@app.post("/api/dca/setup")
async def setup_dca(config: DCAConfig):
    """Set up a new DCA plan."""
    success = dca_strategy.setup_dca(config)
    if success:
        return {"message": f"DCA setup for {config.symbol}", "status": "active"}
    return {"message": "Failed to setup DCA", "status": "error"}


@app.delete("/api/dca/{symbol}")
async def remove_dca(symbol: str):
    """Remove a DCA plan."""
    dca_strategy.remove_dca(symbol)
    return {"message": f"DCA removed for {symbol}"}


@app.get("/api/dca/status")
async def get_dca_status():
    """Get all DCA plans status."""
    return dca_strategy.get_all_dca_status()


# ---- Auto-Invest API ----


@app.post("/api/auto-invest/setup")
async def setup_auto_invest(config: AutoInvestConfig):
    """Set up auto-invest strategy."""
    success = auto_invest_strategy.setup(config)
    if success:
        return {"message": "Auto-Invest configured", "status": "active"}
    return {"message": "Failed to setup Auto-Invest", "status": "error"}


@app.post("/api/auto-invest/rebalance")
async def trigger_rebalance():
    """Manually trigger a rebalance."""
    trades = await auto_invest_strategy.rebalance()
    return {"trades_executed": len(trades), "trades": [t.model_dump() for t in trades]}


@app.get("/api/auto-invest/status")
async def get_auto_invest_status():
    """Get auto-invest status."""
    return auto_invest_strategy.get_status()


@app.get("/api/auto-invest/analysis")
async def get_market_analysis():
    """Get latest market analysis."""
    return auto_invest_strategy.get_latest_analysis()


# ---- Market Data API ----


@app.get("/api/market/pairs")
async def get_brl_pairs():
    """Get available BRL trading pairs."""
    pairs = await binance_client.get_brl_pairs()
    return {"pairs": pairs}


@app.get("/api/market/top")
async def get_top_pairs(limit: int = 10):
    """Get top BRL pairs by volume."""
    top = await binance_client.get_top_brl_pairs_by_volume(limit)
    return top


@app.get("/api/market/analyze/{symbol}")
async def analyze_symbol(symbol: str):
    """Analyze a specific symbol."""
    analysis = await market_analyzer.analyze_symbol(symbol)
    if analysis:
        return analysis.model_dump()
    return {"error": f"Could not analyze {symbol}"}


@app.post("/api/market/scan")
async def scan_market():
    """Scan market for opportunities."""
    analyses = await auto_invest_strategy.analyze_market()
    return [a.model_dump() for a in analyses]


# ---- Manual Trading ----


@app.post("/api/trade/buy")
async def manual_buy(symbol: str, amount_brl: float):
    """Manually buy a symbol."""
    from app.models.trading import StrategyType

    trade = await paper_engine.buy_with_brl(symbol, amount_brl, StrategyType.GRID)
    if trade:
        return trade.model_dump()
    return {"error": "Buy failed"}


@app.post("/api/trade/sell")
async def manual_sell(symbol: str, percentage: float = 1.0):
    """Manually sell a position."""
    trade = await paper_engine.sell_position(symbol, percentage)
    if trade:
        return trade.model_dump()
    return {"error": "Sell failed"}
