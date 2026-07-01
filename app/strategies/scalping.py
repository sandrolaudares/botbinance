"""Aggressive scalping strategy for Binance.

Detects momentum via 5min klines + 24h ticker, buys with dynamic sizing,
trails from entry, re-enters after TP, blacklists recent SL symbols.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from app.models.trading import StrategyType
from app.services.binance_client import binance_client
from app.services.trading_engine import trading_engine

logger = logging.getLogger(__name__)


class ScalpConfig(BaseModel):
    """Configuration for the scalping strategy."""

    active: bool = False
    quote_asset: str = "USDT"
    amount_per_trade: float = 5.0  # Base amount in quote currency
    amount_per_trade_strong: float = 12.0  # Larger for strong signals
    take_profit_percent: float = 5.0  # TP for new listings
    trailing_percent: float = 0.5  # Tight trailing
    stop_loss_percent: float = 0.8  # Quick exit on loss
    max_hold_minutes: int = 30  # Force sell after N minutes
    max_concurrent_trades: int = 25
    min_volume_24h: float = 20000.0  # Lower volume threshold
    # Momentum scalping (aggressive mode)
    momentum_enabled: bool = True
    momentum_tp_percent: float = 1.0  # Quick TP for momentum
    momentum_sl_percent: float = 0.8  # Tight SL for momentum
    min_price_change_24h: float = 0.3  # Very low 24h threshold
    min_volume_spike: float = 1.3  # Volume 1.3x average
    max_trades_per_cycle: int = 5  # Up to 5 buys per cycle
    # Advanced features
    trail_from_entry: bool = True  # Start trailing immediately
    reentry_enabled: bool = True  # Re-enter after TP if still rising
    blacklist_minutes: int = 30  # Cooldown after SL
    accumulation_enabled: bool = True  # Double size on 3rd+ re-entry
    # Bearish scalping (dip-buying)
    bearish_enabled: bool = True  # Buy oversold dips for bounce profit
    bearish_drop_threshold: float = -2.0  # Min 24h drop % to consider
    bearish_bounce_min: float = 0.3  # Min bounce from low to confirm reversal
    bearish_tp_percent: float = 1.5  # TP for bearish scalps
    bearish_sl_percent: float = 1.0  # SL for bearish scalps


class ScalpPosition(BaseModel):
    """An active scalping position."""

    id: str
    symbol: str
    entry_price: float
    quantity: float
    amount_quote: float
    highest_price: float
    trailing_active: bool = False
    signal_strength: str = "normal"  # normal or strong
    status: str = "ACTIVE"
    reason: str = ""
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None


class ScalpingStrategy:
    """Aggressive scalping strategy with momentum detection.

    Features:
    1. 5min kline analysis for precise momentum detection
    2. Trailing from entry (immediate trailing)
    3. Re-entry after TP if coin keeps rising
    4. Blacklist recently SL'd coins
    5. Dynamic position sizing (normal vs strong signals)
    """

    def __init__(self):
        self.config: Optional[ScalpConfig] = None
        self.known_pairs: set[str] = set()
        self.positions: list[ScalpPosition] = []
        self._initialized: bool = False
        self.activity_log: list[dict] = []
        self._blacklist: dict[str, datetime] = {}  # symbol -> blacklist_until
        self._reentry_count: dict[str, int] = {}  # symbol -> re-entry count

    def _log_activity(self, action: str, symbol: str, details: str = ""):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "action": action,
            "symbol": symbol,
            "details": details,
        }
        self.activity_log.append(entry)
        if len(self.activity_log) > 100:
            self.activity_log = self.activity_log[-100:]

    def get_active_positions(self) -> list[ScalpPosition]:
        return [p for p in self.positions if p.status == "ACTIVE"]

    def get_closed_positions(self) -> list[ScalpPosition]:
        return [p for p in self.positions if p.status != "ACTIVE"]

    @property
    def quote(self) -> str:
        return self.config.quote_asset if self.config else "USDT"

    def _is_blacklisted(self, symbol: str) -> bool:
        until = self._blacklist.get(symbol)
        if not until:
            return False
        if datetime.now(UTC) > until:
            del self._blacklist[symbol]
            return False
        return True

    def _add_to_blacklist(self, symbol: str):
        if not self.config:
            return
        mins = self.config.blacklist_minutes
        self._blacklist[symbol] = datetime.now(UTC) + timedelta(minutes=mins)

    async def setup(self, config: ScalpConfig) -> bool:
        self.config = config
        if config.active and not self._initialized:
            await self._initialize_known_pairs()
        logger.info(
            f"Scalping configured: {config.amount_per_trade} "
            f"{config.quote_asset}/trade "
            f"(strong: {config.amount_per_trade_strong}), "
            f"TP={config.momentum_tp_percent}%, "
            f"SL={config.momentum_sl_percent}%, "
            f"Trail={config.trailing_percent}%, "
            f"Hold={config.max_hold_minutes}min, "
            f"Max={config.max_concurrent_trades}"
        )
        return True

    async def _initialize_known_pairs(self):
        pairs = await self._fetch_tradeable_pairs()
        self.known_pairs = set(pairs)
        self._initialized = True
        logger.info(
            f"Scalping initialized: {len(self.known_pairs)} "
            f"existing {self.quote} pairs tracked"
        )

    async def _fetch_tradeable_pairs(self) -> list[str]:
        try:
            client = await binance_client._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()

            pairs = []
            for symbol_info in data.get("symbols", []):
                sym = symbol_info.get("symbol", "")
                if (
                    symbol_info.get("quoteAsset") == self.quote
                    and symbol_info.get("status") == "TRADING"
                    and not sym.startswith("LD")
                ):
                    pairs.append(sym)
            return pairs
        except Exception as e:
            logger.error(f"Error fetching {self.quote} pairs: {e}")
            return []

    async def scan_for_new_listings(self) -> list[str]:
        if not self.config or not self.config.active:
            return []
        if not self._initialized:
            await self._initialize_known_pairs()
            return []

        current_pairs = await self._fetch_tradeable_pairs()
        current_set = set(current_pairs)
        new_pairs = current_set - self.known_pairs
        if new_pairs:
            logger.info(f"NEW LISTINGS DETECTED: {new_pairs}")
        self.known_pairs = current_set
        return list(new_pairs)

    async def _analyze_5min_klines(self, symbol: str) -> Optional[dict]:
        """Analyze last 5min candles for recent momentum."""
        try:
            klines = await binance_client.get_klines(
                symbol, interval="5m", limit=6
            )
            if not klines or len(klines) < 3:
                return None

            # Each kline: [open_time, open, high, low, close, volume, ...]
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]

            # Recent price change (last 3 candles = 15min)
            if closes[-3] == 0:
                return None
            recent_change = (
                (closes[-1] - closes[-3]) / closes[-3]
            ) * 100

            # Volume trend (last 3 vs previous 3)
            recent_vol = sum(volumes[-3:])
            prev_vol = sum(volumes[:3])
            vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0

            # Consecutive green candles
            green_count = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes[i] > closes[i - 1]:
                    green_count += 1
                else:
                    break

            return {
                "recent_change": recent_change,
                "vol_ratio": vol_ratio,
                "green_count": green_count,
                "last_price": closes[-1],
            }
        except Exception as e:
            logger.error(f"Kline analysis error {symbol}: {e}")
            return None

    def _classify_signal(
        self, price_change_24h: float, kline_data: Optional[dict]
    ) -> str:
        """Classify signal strength: 'strong' or 'normal'."""
        if not kline_data:
            return "normal"

        strong_conditions = 0
        if price_change_24h > 3.0:
            strong_conditions += 1
        if kline_data["recent_change"] > 1.0:
            strong_conditions += 1
        if kline_data["vol_ratio"] > 2.0:
            strong_conditions += 1
        if kline_data["green_count"] >= 3:
            strong_conditions += 1

        return "strong" if strong_conditions >= 2 else "normal"

    async def _get_open_markets(self) -> set[str]:
        try:
            client = await binance_client._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()
            return {
                s["symbol"] for s in data.get("symbols", [])
                if s.get("quoteAsset") == self.quote
                and s.get("status") == "TRADING"
                and not s.get("symbol", "").startswith("LD")
            }
        except Exception as e:
            logger.error(f"Error fetching open markets: {e}")
            return set()

    async def scan_momentum_opportunities(self) -> list[dict]:
        """Find coins with momentum via 24h ticker + 5min kline confirmation."""
        if not self.config or not self.config.momentum_enabled:
            return []

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return []

        open_markets = await self._get_open_markets()
        tickers = await binance_client.get_ticker_24h()
        if not tickers:
            return []

        suffix = self.quote
        candidates = []
        active_symbols = {p.symbol for p in active}

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith(suffix):
                continue
            if symbol.startswith("LD") or "1MBB" in symbol:
                continue
            if symbol in active_symbols:
                continue
            if symbol not in open_markets:
                continue
            if self._is_blacklisted(symbol):
                continue

            try:
                price_change = float(t.get("priceChangePercent", 0))
                volume_24h = float(t.get("quoteVolume", 0))
                weighted_avg = float(t.get("weightedAvgPrice", 0))
                last_price = float(t.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue

            if volume_24h < self.config.min_volume_24h:
                continue
            if price_change < self.config.min_price_change_24h:
                continue

            # Confirm upward momentum
            if weighted_avg > 0 and last_price > weighted_avg * 1.001:
                candidates.append({
                    "symbol": symbol,
                    "change": price_change,
                    "volume": volume_24h,
                })

        # Sort by price change (strongest first)
        candidates.sort(key=lambda x: x["change"], reverse=True)

        slots = self.config.max_concurrent_trades - len(active)
        max_per_cycle = self.config.max_trades_per_cycle
        pre_candidates = candidates[:min(slots, max_per_cycle * 2)]

        # Confirm with 5min kline analysis (top candidates only)
        confirmed = []
        for c in pre_candidates:
            if len(confirmed) >= max_per_cycle:
                break
            kline = await self._analyze_5min_klines(c["symbol"])
            if kline and kline["recent_change"] > 0.1:
                c["kline"] = kline
                c["strength"] = self._classify_signal(
                    c["change"], kline
                )
                confirmed.append(c)
            elif kline is None:
                # Kline fetch failed; still accept based on 24h data
                c["kline"] = None
                c["strength"] = "normal"
                confirmed.append(c)

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} +{c['change']:.1f}% "
                f"({c['strength']})"
                for c in confirmed
            )
            logger.info(f"Scalp MOMENTUM signals: {desc}")

        return confirmed

    async def execute_scalp(self, symbol: str) -> Optional[dict]:
        """Buy a newly listed coin for scalping."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        ticker = await binance_client.get_ticker_24h(symbol)
        if ticker:
            volume = float(ticker[0].get("quoteVolume", 0))
            if volume < self.config.min_volume_24h:
                return None

        # New listings get strong sizing
        amt = self.config.amount_per_trade_strong
        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Scalping: failed to buy {symbol}")
            return None

        q = self.quote
        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength="strong",
            trailing_active=self.config.trail_from_entry,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        logger.info(
            f"SCALP BUY: {symbol} @ {result.price:.6f}, "
            f"qty={result.quantity:.8f}, {amt:.2f} {q}"
        )
        self._log_activity(
            "COMPRA (Nova Listagem)", symbol,
            f"{result.price:.6f} x {result.quantity:.6f} = "
            f"{amt:.2f} {q} [STRONG]"
        )
        return position.model_dump()

    async def execute_momentum_scalp(
        self, symbol: str, strength: str = "normal"
    ) -> Optional[dict]:
        """Execute a momentum-based scalp with dynamic sizing."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        # Dynamic position sizing + accumulation
        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        # Accumulation: double size on 3rd+ re-entry for same symbol
        reentry_n = self._reentry_count.get(symbol, 0)
        if self.config.accumulation_enabled and reentry_n >= 2:
            amt = amt * 2
            logger.info(
                f"ACCUMULATION: {symbol} re-entry #{reentry_n+1}, "
                f"doubling to {amt} {self.quote}"
            )

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            logger.error(f"Scalp momentum: failed to buy {symbol}")
            self._log_activity(
                "FALHA", symbol, "Mercado fechado ou ordem rejeitada"
            )
            return None

        q = self.quote
        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength=strength,
            trailing_active=self.config.trail_from_entry,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        label = "FORTE" if strength == "strong" else "NORMAL"
        if reentry_n >= 2:
            label += " ACUM"
        logger.info(
            f"SCALP MOMENTUM BUY [{label}]: {symbol} @ {result.price:.6f}, "
            f"qty={result.quantity:.8f}, {amt} {q}"
        )
        self._log_activity(
            f"COMPRA (Momentum {label})", symbol,
            f"{result.price:.6f} x {result.quantity:.6f} = "
            f"{amt:.2f} {q}"
        )
        return position.model_dump()

    async def monitor_positions(self):
        if not self.config or not self.config.active:
            return

        for pos in self.get_active_positions():
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(f"Scalping monitor error {pos.symbol}: {e}")

    async def _check_position(self, pos: ScalpPosition):
        current_price = await binance_client.get_symbol_price(pos.symbol)
        if not current_price:
            return

        price_change_pct = (
            (current_price - pos.entry_price) / pos.entry_price
        ) * 100

        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Use minutes for timeout
        created = datetime.fromisoformat(pos.created_at)
        elapsed = datetime.now(UTC) - created
        max_minutes = self.config.max_hold_minutes
        if elapsed > timedelta(minutes=max_minutes):
            logger.info(
                f"Scalp TIMEOUT: {pos.symbol} "
                f"(held {elapsed.total_seconds()/60:.0f}min, "
                f"PnL={price_change_pct:.2f}%)"
            )
            await self._close_position(pos, current_price, "TIMEOUT")
            return

        # Stop Loss
        sl = self.config.momentum_sl_percent
        if price_change_pct <= -sl:
            logger.warning(
                f"Scalp STOP LOSS: {pos.symbol} ({price_change_pct:.2f}%)"
            )
            self._add_to_blacklist(pos.symbol)
            await self._close_position(pos, current_price, "STOP_LOSS")
            return

        # Trailing from entry: always active
        if self.config.trail_from_entry:
            pos.trailing_active = True
        else:
            # Traditional: activate trailing after TP threshold
            tp = self.config.momentum_tp_percent
            if price_change_pct >= tp and not pos.trailing_active:
                pos.trailing_active = True
                logger.info(
                    f"Scalp TRAILING ACTIVATED: {pos.symbol} "
                    f"({price_change_pct:.2f}%)"
                )

        # Trailing stop check
        if pos.trailing_active and pos.highest_price > pos.entry_price:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            # Only sell via trailing if we're in profit
            gain_from_entry = (
                (pos.highest_price - pos.entry_price) / pos.entry_price
            ) * 100
            if (
                drop_from_peak >= self.config.trailing_percent
                and gain_from_entry >= 0.3
            ):
                logger.info(
                    f"Scalp TRAILING SELL: {pos.symbol} "
                    f"(peak {pos.highest_price:.6f}, "
                    f"drop {drop_from_peak:.2f}%, "
                    f"gain {gain_from_entry:.2f}%)"
                )
                await self._close_position(
                    pos, current_price, "TAKE_PROFIT"
                )

    async def _close_position(
        self, pos: ScalpPosition, price: float, reason: str
    ):
        order = await binance_client.place_market_order(
            symbol=pos.symbol,
            side="SELL",
            quantity=pos.quantity,
        )
        if order:
            pos.status = "CLOSED"
            pos.reason = reason
            pos.close_price = price
            pos.closed_at = datetime.now(UTC).isoformat()
            pos.pnl_percent = (
                (price - pos.entry_price) / pos.entry_price
            ) * 100
            logger.info(
                f"Scalp CLOSED ({reason}): {pos.symbol} "
                f"entry={pos.entry_price:.6f} exit={price:.6f} "
                f"PnL={pos.pnl_percent:.2f}%"
            )
            reason_map = {
                "TAKE_PROFIT": "VENDA (Take Profit)",
                "STOP_LOSS": "VENDA (Stop Loss)",
                "TIMEOUT": "VENDA (Timeout)",
                "MANUAL": "VENDA (Manual)",
                "MANUAL_ALL": "VENDA (Fechar Todos)",
            }
            self._log_activity(
                reason_map.get(reason, f"VENDA ({reason})"),
                pos.symbol,
                f"PnL: {pos.pnl_percent:+.2f}% | "
                f"{pos.entry_price:.6f} -> {price:.6f}"
            )

            # Re-entry logic: if closed at TP and still rising
            if (
                reason == "TAKE_PROFIT"
                and self.config
                and self.config.reentry_enabled
                and pos.pnl_percent
                and pos.pnl_percent > 0.5
            ):
                # Track re-entry count for accumulation
                count = self._reentry_count.get(pos.symbol, 0) + 1
                self._reentry_count[pos.symbol] = count
                current = await binance_client.get_symbol_price(pos.symbol)
                if current and current >= price * 0.998:
                    self._log_activity(
                        "RE-ENTRY SINAL", pos.symbol,
                        f"Ainda subindo apos TP "
                        f"(+{pos.pnl_percent:.1f}%, "
                        f"re-entry #{count})"
                    )
                    await self.execute_momentum_scalp(
                        pos.symbol, pos.signal_strength
                    )
            elif reason == "STOP_LOSS":
                # Reset re-entry count on SL
                self._reentry_count.pop(pos.symbol, None)
        else:
            logger.error(f"Scalp: failed to sell {pos.symbol}")

    async def close_position_by_id(
        self, position_id: str
    ) -> Optional[dict]:
        for pos in self.positions:
            if pos.id == position_id and pos.status == "ACTIVE":
                price = await binance_client.get_symbol_price(pos.symbol)
                if price:
                    await self._close_position(pos, price, "MANUAL")
                    return pos.model_dump()
        return None

    async def close_all(self) -> int:
        closed = 0
        for pos in self.get_active_positions():
            price = await binance_client.get_symbol_price(pos.symbol)
            if price:
                await self._close_position(pos, price, "MANUAL_ALL")
                closed += 1
        return closed

    async def run_cycle(self):
        """Run one full cycle: scan + momentum + monitor positions.

        Called by the scheduler every 30 seconds.
        """
        if not self.config or not self.config.active:
            return

        active_count = len(self.get_active_positions())
        bl_count = len(self._blacklist)
        self._log_activity(
            "SCAN", "mercado",
            f"Buscando oportunidades... "
            f"({active_count} posicoes, {bl_count} blacklist)"
        )

        # Scan for new listings
        new_pairs = await self.scan_for_new_listings()
        if new_pairs:
            self._log_activity(
                "NOVA LISTAGEM", ", ".join(new_pairs),
                f"{len(new_pairs)} nova(s) moeda(s) detectada(s)!"
            )
        for symbol in new_pairs:
            await self.execute_scalp(symbol)

        # Scan for momentum opportunities (now returns dicts with strength)
        if self.config.momentum_enabled:
            momentum_signals = await self.scan_momentum_opportunities()
            if momentum_signals:
                desc = ", ".join(
                    f"{s['symbol']}({s['strength'][0].upper()})"
                    for s in momentum_signals
                )
                self._log_activity(
                    "MOMENTUM DETECTADO", desc,
                    f"{len(momentum_signals)} sinais"
                )
            for signal in momentum_signals:
                await self.execute_momentum_scalp(
                    signal["symbol"], signal["strength"]
                )

        # Scan for bearish (dip-buying) opportunities
        if self.config.bearish_enabled:
            bearish_signals = await self.scan_bearish_opportunities()
            if bearish_signals:
                desc = ", ".join(
                    f"{s['symbol']}({s['drop']:.1f}%)"
                    for s in bearish_signals
                )
                self._log_activity(
                    "BEARISH DETECTADO", desc,
                    f"{len(bearish_signals)} dips para bounce"
                )
            for signal in bearish_signals:
                await self.execute_bearish_scalp(
                    signal["symbol"], signal["strength"]
                )

        # Monitor existing positions
        await self.monitor_positions()

    async def scan_bearish_opportunities(self) -> list[dict]:
        """Find coins with sharp drops showing bounce signals (dip-buying)."""
        if not self.config or not self.config.bearish_enabled:
            return []

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return []

        open_markets = await self._get_open_markets()
        tickers = await binance_client.get_ticker_24h()
        if not tickers:
            return []

        suffix = self.quote
        candidates = []
        active_symbols = {p.symbol for p in active}

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith(suffix):
                continue
            if symbol.startswith("LD") or "1MBB" in symbol:
                continue
            if symbol in active_symbols:
                continue
            if symbol not in open_markets:
                continue
            if self._is_blacklisted(symbol):
                continue

            try:
                price_change = float(t.get("priceChangePercent", 0))
                volume_24h = float(t.get("quoteVolume", 0))
                last_price = float(t.get("lastPrice", 0))
                low_price = float(t.get("lowPrice", 0))
            except (ValueError, TypeError):
                continue

            if volume_24h < self.config.min_volume_24h:
                continue
            # Only consider coins that dropped significantly
            if price_change > self.config.bearish_drop_threshold:
                continue

            # Check bounce from low: price recovered from the 24h low
            if low_price > 0 and last_price > low_price:
                bounce_pct = (
                    (last_price - low_price) / low_price
                ) * 100
                if bounce_pct >= self.config.bearish_bounce_min:
                    candidates.append({
                        "symbol": symbol,
                        "drop": price_change,
                        "bounce": bounce_pct,
                        "volume": volume_24h,
                    })

        # Sort by bounce strength (strongest bounce from low first)
        candidates.sort(key=lambda x: x["bounce"], reverse=True)

        slots = self.config.max_concurrent_trades - len(active)
        max_per_cycle = self.config.max_trades_per_cycle
        pre_candidates = candidates[:min(slots, max_per_cycle * 2)]

        # Confirm with 5min kline: recent candles should show recovery
        confirmed = []
        for c in pre_candidates:
            if len(confirmed) >= max_per_cycle:
                break
            kline = await self._analyze_5min_klines(c["symbol"])
            if kline and kline["recent_change"] > 0:
                c["kline"] = kline
                c["strength"] = (
                    "strong" if c["bounce"] >= 1.0 else "normal"
                )
                confirmed.append(c)
            elif kline is None:
                c["kline"] = None
                c["strength"] = "normal"
                confirmed.append(c)

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} {c['drop']:.1f}% bounce+{c['bounce']:.1f}%"
                for c in confirmed
            )
            logger.info(f"Scalp BEARISH dip-buy signals: {desc}")

        return confirmed

    async def execute_bearish_scalp(
        self, symbol: str, strength: str = "normal"
    ) -> Optional[dict]:
        """Buy a dip for bounce profit (bearish market scalping)."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        # Accumulation on re-entries
        reentry_n = self._reentry_count.get(symbol, 0)
        if self.config.accumulation_enabled and reentry_n >= 2:
            amt = amt * 2

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            self._log_activity(
                "FALHA BEARISH", symbol,
                "Ordem rejeitada ou mercado fechado"
            )
            return None

        q = self.quote
        position = ScalpPosition(
            id=f"bear_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength=strength,
            trailing_active=self.config.trail_from_entry,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        label = "FORTE" if strength == "strong" else "NORMAL"
        logger.info(
            f"BEARISH DIP-BUY [{label}]: {symbol} @ {result.price:.6f}, "
            f"qty={result.quantity:.8f}, {amt} {q}"
        )
        self._log_activity(
            f"COMPRA BEARISH ({label})", symbol,
            f"Dip-buy @ {result.price:.6f} x {result.quantity:.6f} = "
            f"{amt:.2f} {q}"
        )
        return position.model_dump()


scalping_strategy = ScalpingStrategy()
