import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.trading import AutoInvestConfig
from app.services.binance_client import binance_client
from app.services.market_analysis import market_analyzer
from app.services.paper_trading import paper_engine
from app.services.trading_engine import trading_engine
from app.strategies.auto_invest import auto_invest_strategy
from app.strategies.scalping import ScalpConfig, scalping_strategy
from app.strategies.smart_trade import SmartTradeConfig, smart_trade_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_auto_invest():
    """Run auto-invest rebalance and take profits."""
    if auto_invest_strategy.config and auto_invest_strategy.config.active:
        await auto_invest_strategy.take_profit()
        await auto_invest_strategy.rebalance()


async def scheduled_smart_trade_monitor():
    """Monitor Smart Trade positions for TP/SL triggers."""
    await smart_trade_strategy.monitor_positions()


async def scheduled_scalping_cycle():
    """Run scalping cycle: detect new listings + monitor positions."""
    await scalping_strategy.run_cycle()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Binance Trading Bot...")
    logger.info(f"Paper Trading: {'ENABLED' if settings.paper_trading else 'DISABLED'}")
    logger.info(f"Initial Balance: {settings.initial_balance_brl:.2f} BRL")

    # Start scheduler
    scheduler.add_job(scheduled_auto_invest, "interval", minutes=15, id="auto_invest")
    scheduler.add_job(
        scheduled_smart_trade_monitor, "interval", seconds=30, id="smart_trade"
    )
    scheduler.add_job(
        scheduled_scalping_cycle, "interval", seconds=30, id="scalping"
    )
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


# ---- Smart Trades (Take Profit + Trailing Stop) ----


@app.post("/api/smart-trade/create")
async def create_smart_trade(config: SmartTradeConfig):
    """Create a Smart Trade with take-profit and trailing stop."""
    if not trading_engine.is_live:
        return {"error": "Ative o Live Trading primeiro", "status": "error"}
    result = await smart_trade_strategy.create_trade(config)
    if result:
        return {"status": "created", "trade_id": result.get("id"), "trade": result}
    return {"error": "Failed to create Smart Trade", "status": "error"}


@app.post("/api/smart-trade/auto")
async def auto_smart_trades(
    total_amount_brl: float = 300,
    top_n: int = 3,
    take_profit_percent: float = 3.0,
    trailing_percent: float = 1.0,
    stop_loss_percent: float = 5.0,
):
    """Auto-create Smart Trades based on market analysis."""
    if not trading_engine.is_live:
        return {"error": "Ative o Live Trading primeiro", "status": "error"}
    trades = await smart_trade_strategy.create_auto_trades(
        total_amount_brl=total_amount_brl,
        top_n=top_n,
        take_profit_percent=take_profit_percent,
        trailing_percent=trailing_percent,
        stop_loss_percent=stop_loss_percent,
    )
    return {
        "status": "created",
        "trades_created": len(trades),
        "trades": trades,
    }


@app.get("/api/smart-trade/active")
async def get_active_smart_trades():
    """Get all active Smart Trades."""
    positions = smart_trade_strategy.get_active_positions()
    return {
        "active_trades": len(positions),
        "trades": [p.model_dump() for p in positions],
    }


@app.get("/api/smart-trade/history")
async def get_smart_trade_history():
    """Get closed Smart Trades."""
    closed = smart_trade_strategy.get_closed_positions()
    return {
        "closed_trades": len(closed),
        "trades": [p.model_dump() for p in closed],
    }


@app.post("/api/smart-trade/{trade_id}/close")
async def close_smart_trade(trade_id: str):
    """Close a specific Smart Trade at market price."""
    result = await smart_trade_strategy.close_position_by_id(trade_id)
    if result:
        return {"status": "closed", "trade": result}
    return {"error": "Trade not found or already closed", "status": "error"}


@app.post("/api/smart-trade/close-all")
async def close_all_smart_trades():
    """Close all active Smart Trades."""
    closed = await smart_trade_strategy.close_all()
    return {"status": "closed", "trades_closed": closed}


# ---- Currency Conversion ----


@app.post("/api/convert-to-usdt")
async def convert_brl_to_usdt():
    """Convert all available BRL to USDT."""
    if not trading_engine.is_live:
        return {"error": "Ative o Live Trading primeiro", "status": "error"}

    brl_balance = await binance_client.get_brl_balance()
    if brl_balance is None:
        return {"error": "Não foi possível consultar saldo", "status": "error"}
    if brl_balance < 10:
        return {
            "error": f"Saldo BRL insuficiente: R${brl_balance:.2f}",
            "status": "error",
        }

    # Use 99% of balance to avoid rounding/fee issues
    amount = round(brl_balance * 0.99, 2)
    order = await binance_client.place_market_order(
        symbol="USDTBRL",
        side="BUY",
        quote_order_qty=amount,
    )
    if not order:
        return {"error": "Falha ao comprar USDT", "status": "error"}

    executed_qty = float(order.get("executedQty", 0))
    cumulative_quote = float(order.get("cummulativeQuoteQty", 0))

    usdt_balance = await binance_client.get_asset_balance("USDT")
    return {
        "status": "success",
        "usdt_bought": executed_qty,
        "brl_spent": cumulative_quote,
        "usdt_balance": usdt_balance,
    }


@app.post("/api/sell-asset-for-usdt")
async def sell_asset_for_usdt(asset: str):
    """Sell all of an asset for USDT."""
    import math

    if not trading_engine.is_live:
        return {"error": "Ative o Live Trading primeiro", "status": "error"}

    balance = await binance_client.get_asset_balance(asset.upper())
    if balance is None or balance <= 0:
        return {
            "error": f"Sem saldo de {asset.upper()}",
            "status": "error",
        }

    symbol = f"{asset.upper()}USDT"

    # Get LOT_SIZE step to truncate quantity properly
    try:
        client = await binance_client._get_client()
        resp = await client.get(
            "/api/v3/exchangeInfo", params={"symbol": symbol}
        )
        resp.raise_for_status()
        info = resp.json()
        step_size = 0.00001
        for f in info["symbols"][0].get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        # Truncate balance to valid step size
        if step_size > 0:
            precision = int(round(-math.log10(step_size)))
            factor = 10 ** precision
            balance = math.floor(balance * factor) / factor
    except Exception as e:
        logger.warning(f"Could not fetch LOT_SIZE for {symbol}: {e}")

    order = await binance_client.place_market_order(
        symbol=symbol,
        side="SELL",
        quantity=balance,
    )
    if not order:
        return {"error": f"Falha ao vender {asset.upper()}", "status": "error"}

    executed_qty = float(order.get("executedQty", 0))
    cumulative_quote = float(order.get("cummulativeQuoteQty", 0))
    usdt_balance = await binance_client.get_asset_balance("USDT")

    return {
        "status": "success",
        "asset": asset.upper(),
        "sold_qty": executed_qty,
        "usdt_received": cumulative_quote,
        "usdt_balance": usdt_balance,
    }


# ---- Scalping (New Coin Listings + Momentum) ----


@app.post("/api/scalping/activate")
async def activate_scalping(config: ScalpConfig):
    """Activate the new coin scalping strategy."""
    if not trading_engine.is_live:
        return {"error": "Ative o Live Trading primeiro", "status": "error"}
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
        "activity_log": scalping_strategy.activity_log[-20:],
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
