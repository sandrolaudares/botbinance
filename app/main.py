import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.binance_client import binance_client
from app.services.trading_engine import trading_engine
from app.strategies.breakout import BreakoutConfig, breakout_strategy
from app.strategies.scalping import ScalpConfig, scalping_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_scalping_cycle():
    """Run scalping cycle: detect new listings + monitor positions."""
    await scalping_strategy.run_cycle()


async def scheduled_breakout_cycle():
    """Run breakout cycle: detect consolidation breakouts."""
    await breakout_strategy.run_cycle()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Binance Trading Bot...")

    # Auto-activate live trading + scalping on boot
    if binance_client.has_credentials:
        settings.paper_trading = False
        logger.info("Live trading auto-activated")
        scalp_config = ScalpConfig(
            active=True, quote_asset="USDT",
            amount_per_trade=5.0, amount_per_trade_strong=12.0,
            max_concurrent_trades=25, bearish_enabled=True,
        )
        await scalping_strategy.setup(scalp_config)
        logger.info(
            f"Scalping auto-started: {len(scalping_strategy.known_pairs)} pairs"
        )
        brk_config = BreakoutConfig(
            active=True, quote_asset="USDT",
            amount_per_trade=15.0, amount_per_trade_strong=25.0,
            max_concurrent_trades=10,
        )
        await breakout_strategy.setup(brk_config)
        logger.info("Breakout auto-started")

    # Start scheduler (scalping + breakout only)
    scheduler.add_job(
        scheduled_scalping_cycle, "interval", seconds=30, id="scalping"
    )
    scheduler.add_job(
        scheduled_breakout_cycle, "interval", seconds=30, id="breakout"
    )
    scheduler.start()

    yield

    # Cleanup
    scheduler.shutdown()
    await binance_client.close()
    logger.info("Bot stopped.")


app = FastAPI(
    title="Binance Trading Bot",
    description="Bot de scalping e breakout automatizado para Binance",
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


# ---- Market Data API ----


@app.get("/api/market/top")
async def get_top_pairs(limit: int = 10):
    """Get top USDT pairs by volume."""
    tickers = await binance_client.get_ticker_24h()
    if not tickers:
        return []
    usdt_pairs = [
        t for t in tickers
        if t.get("symbol", "").endswith("USDT")
        and not t.get("symbol", "").startswith("LD")
    ]
    usdt_pairs.sort(
        key=lambda x: float(x.get("quoteVolume", 0)), reverse=True
    )
    return usdt_pairs[:limit]


# ---- Scalping (New Coin Listings + Momentum) ----


@app.post("/api/scalping/activate")
async def activate_scalping(config: ScalpConfig):
    """Activate the new coin scalping strategy."""
    config.active = True
    await scalping_strategy.setup(config)
    return {
        "status": "activated",
        "config": config.model_dump(),
        "known_pairs": len(scalping_strategy.known_pairs),
    }


@app.post("/api/scalping/deactivate")
async def deactivate_scalping():
    """Deactivate scalping strategy."""
    if scalping_strategy.config:
        scalping_strategy.config.active = False
    return {"status": "deactivated"}


@app.get("/api/scalping/status")
async def get_scalping_status():
    """Get scalping strategy status and positions."""
    active_positions = scalping_strategy.get_active_positions()
    closed_positions = scalping_strategy.get_closed_positions()
    return {
        "active": bool(
            scalping_strategy.config and scalping_strategy.config.active
        ),
        "quote_asset": scalping_strategy.quote,
        "known_pairs": len(scalping_strategy.known_pairs),
        "active_positions": len(active_positions),
        "closed_positions": len(closed_positions),
        "positions": [p.model_dump() for p in active_positions],
        "history": [p.model_dump() for p in closed_positions[-10:]],
        "activity_log": scalping_strategy.activity_log[-50:],
    }


@app.post("/api/scalping/{position_id}/close")
async def close_scalp_position(position_id: str):
    """Close a specific scalping position."""
    result = await scalping_strategy.close_position_by_id(position_id)
    if result:
        return {"status": "closed", "position": result}
    return {"error": "Position not found", "status": "error"}


@app.post("/api/scalping/close-all")
async def close_all_scalp_positions():
    """Close all scalping positions."""
    closed = await scalping_strategy.close_all()
    return {"status": "closed", "positions_closed": closed}


# ---- Breakout Trading ----


@app.post("/api/breakout/activate")
async def activate_breakout(config: BreakoutConfig):
    """Activate the breakout trading strategy."""
    config.active = True
    await breakout_strategy.setup(config)
    return {
        "status": "activated",
        "config": config.model_dump(),
    }


@app.post("/api/breakout/deactivate")
async def deactivate_breakout():
    """Deactivate breakout strategy."""
    if breakout_strategy.config:
        breakout_strategy.config.active = False
    return {"status": "deactivated"}


@app.get("/api/breakout/status")
async def get_breakout_status():
    """Get breakout strategy status and positions."""
    active_positions = breakout_strategy.get_active_positions()
    closed_positions = breakout_strategy.get_closed_positions()
    return {
        "active": bool(
            breakout_strategy.config and breakout_strategy.config.active
        ),
        "quote_asset": breakout_strategy.quote,
        "active_positions": len(active_positions),
        "closed_positions": len(closed_positions),
        "positions": [p.model_dump() for p in active_positions],
        "history": [p.model_dump() for p in closed_positions[-10:]],
        "activity_log": breakout_strategy.activity_log[-20:],
    }


@app.post("/api/breakout/{position_id}/close")
async def close_breakout_position(position_id: str):
    """Close a specific breakout position."""
    result = await breakout_strategy.close_position_by_id(position_id)
    if result:
        return {"status": "closed", "position": result}
    return {"error": "Position not found", "status": "error"}


@app.post("/api/breakout/close-all")
async def close_all_breakout_positions():
    """Close all breakout positions."""
    closed = await breakout_strategy.close_all()
    return {"status": "closed", "positions_closed": closed}
