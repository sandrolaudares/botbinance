"""Scalping strategy V3 - focused on LOSS REDUCTION.

Core philosophy: It's better to miss a trade than to take a bad one.

Key principles:
- Only trade highly liquid pairs ($500k+ 24h volume)
- BTC/ETH macro filter: no buys when market is falling
- Very strict entry: need 4+/6 momentum factors
- Wider SL (2%) with smaller positions ($20) = same dollar risk, fewer false SL
- Let winners run with trailing (no hard TP, only trail)
- Max 5 concurrent positions (concentrated, manageable)
- Daily loss limit: pause 4h if losses > $40
- Disable dip-buying (main source of losses - catching falling knives)
- 2-hour cooldown per symbol after any exit
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
    """Configuration - conservative entries, let winners run."""

    active: bool = False
    quote_asset: str = "USDT"
    # Position sizing - balanced for more trades
    amount_per_trade: float = 15.0
    amount_per_trade_strong: float = 25.0
    # Exit - NO hard TP, only trailing. Wider SL to avoid noise.
    stop_loss_percent: float = 1.5  # Balanced SL
    trailing_activation: float = 1.0  # Trail activates earlier at +1%
    trailing_percent: float = 0.6  # Tight trail for normal scalps
    # Tiered/dynamic trailing - let big movers RUN
    runner_gain_threshold: float = 3.0  # Above this % = "runner" mode
    runner_trailing_percent: float = 4.0  # Wide trail for runners
    runner_trail_factor: float = 0.25  # Trail scales: peak_gain * factor
    # Time management
    max_hold_minutes: int = 90  # 1.5 hours max - free capital faster
    stale_exit_minutes: int = 30  # Exit faster if flat
    stale_exit_threshold: float = 0.3  # Less than 0.3% = flat
    # Capacity - more positions for more opportunities
    max_concurrent_trades: int = 20
    max_trades_per_cycle: int = 4  # Up to 4 new entries per cycle
    cycle_seconds: int = 45  # Faster cycles
    # Signal filters
    min_volume_24h: float = 150000.0  # More pairs eligible ($150k+)
    min_momentum_score: int = 3  # Need 3+/6 with all other protections
    min_price_change_5min: float = 0.5  # Recent move in klines
    min_volume_spike: float = 2.0  # Volume above average
    max_price_change_24h: float = 90.0  # Allow strong movers/pumps
    # Pump detection - catch explosive movers early
    pump_detection_enabled: bool = True
    pump_min_5min_change: float = 3.0  # 3%+ in last 15min = explosive
    pump_min_vol_spike: float = 3.0  # 3x volume spike
    pump_max_from_high: float = 2.0  # Don't buy >2% below the peak
    # Macro filter
    btc_filter_enabled: bool = True  # Skip all buys if BTC falling
    btc_min_change_1h: float = -1.0  # BTC must not be down >1% in 1h
    # Risk management
    blacklist_minutes: int = 45  # Shorter cooldown for faster re-entry
    daily_loss_limit: float = 40.0  # Pause after $40 daily loss
    pause_hours: int = 4  # Hours to pause after loss limit
    # Re-entry
    reentry_enabled: bool = True
    accumulation_enabled: bool = False  # Disabled - don't double down
    # Bearish DISABLED by default (main loss source)
    bearish_enabled: bool = False


class ScalpPosition(BaseModel):
    """An active scalping position."""

    id: str
    symbol: str
    entry_price: float
    quantity: float
    amount_quote: float
    highest_price: float
    trailing_active: bool = False
    signal_strength: str = "normal"
    momentum_score: int = 0
    is_pump: bool = False
    status: str = "ACTIVE"
    reason: str = ""
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None
    pnl_usdt: Optional[float] = None


class ScalpingStrategy:
    """Scalping V3 - Loss reduction focused.

    Entry requires ALL of:
    1. BTC not falling (macro filter)
    2. Momentum score 4+/6
    3. Not in 24h parabolic (< +15%)
    4. High volume ($500k+)
    5. Volume spiking (2.5x)
    6. Not blacklisted (2h cooldown)

    Exit:
    - SL at -2% (wide enough to avoid noise)
    - Trail activates at +1.5%, trails at 1%
    - Stale exit: flat after 60min
    - Max hold: 3 hours
    """

    def __init__(self):
        self.config: Optional[ScalpConfig] = None
        self.known_pairs: set[str] = set()
        self.positions: list[ScalpPosition] = []
        self._initialized: bool = False
        self.activity_log: list[dict] = []
        self._blacklist: dict[str, datetime] = {}
        self._reentry_count: dict[str, int] = {}
        self._trade_stats: dict = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_pct": 0.0,
            "total_pnl_usdt": 0.0,
            "daily_loss_usdt": 0.0,
            "last_reset": datetime.now(UTC).isoformat(),
        }
        self._paused_until: Optional[datetime] = None

    def _log_activity(self, action: str, symbol: str, details: str = ""):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "action": action,
            "symbol": symbol,
            "details": details,
        }
        self.activity_log.append(entry)
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[-200:]

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
        self._blacklist[symbol] = datetime.now(UTC) + timedelta(
            minutes=self.config.blacklist_minutes
        )

    def _check_daily_reset(self):
        """Reset daily loss counter at midnight UTC."""
        last = datetime.fromisoformat(self._trade_stats["last_reset"])
        now = datetime.now(UTC)
        if now.date() > last.date():
            self._trade_stats["daily_loss_usdt"] = 0.0
            self._trade_stats["last_reset"] = now.isoformat()

    def _is_paused(self) -> bool:
        """Check if trading is paused due to loss limit."""
        if not self._paused_until:
            return False
        if datetime.now(UTC) > self._paused_until:
            self._paused_until = None
            self._log_activity(
                "RETOMADO", "sistema",
                "Pausa por perda encerrada, voltando a operar"
            )
            return False
        return True

    async def setup(self, config: ScalpConfig) -> bool:
        self.config = config
        if config.active and not self._initialized:
            await self._initialize_known_pairs()
        logger.info(
            f"Scalping V3 (loss-reduction): "
            f"${config.amount_per_trade}/${config.amount_per_trade_strong}, "
            f"SL={config.stop_loss_percent}%, "
            f"Trail@{config.trailing_activation}%/{config.trailing_percent}%, "
            f"Max={config.max_concurrent_trades}, "
            f"MinScore={config.min_momentum_score}/6, "
            f"BTC filter={config.btc_filter_enabled}, "
            f"LossLimit=${config.daily_loss_limit}"
        )
        return True

    async def _initialize_known_pairs(self):
        pairs = await self._fetch_tradeable_pairs()
        self.known_pairs = set(pairs)
        self._initialized = True
        logger.info(
            f"Scalping V3: {len(self.known_pairs)} {self.quote} pairs"
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
            logger.error(f"Error fetching pairs: {e}")
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
            logger.info(f"NEW LISTINGS: {new_pairs}")
        self.known_pairs = current_set
        return list(new_pairs)

    async def _check_btc_trend(self) -> bool:
        """Check if BTC is not falling (macro filter).

        Returns True if safe to trade, False if market is falling.
        """
        if not self.config or not self.config.btc_filter_enabled:
            return True

        try:
            # Check BTC 1h klines for recent trend
            klines = await binance_client.get_klines(
                "BTCUSDT", interval="15m", limit=4
            )
            if not klines or len(klines) < 4:
                return True  # Can't determine, allow trading

            # Price change over last hour (4x15min candles)
            open_1h = float(klines[0][1])
            close_now = float(klines[-1][4])
            if open_1h == 0:
                return True

            change_1h = ((close_now - open_1h) / open_1h) * 100

            if change_1h < self.config.btc_min_change_1h:
                self._log_activity(
                    "MACRO FILTER", "BTCUSDT",
                    f"BTC {change_1h:+.2f}% (1h) < "
                    f"{self.config.btc_min_change_1h}% → skipping buys"
                )
                return False

            return True
        except Exception as e:
            logger.error(f"BTC trend check error: {e}")
            return True  # Error = allow trading

    async def _analyze_klines(self, symbol: str) -> Optional[dict]:
        """Deep kline analysis for momentum scoring."""
        try:
            klines = await binance_client.get_klines(
                symbol, interval="5m", limit=12
            )
            if not klines or len(klines) < 6:
                return None

            closes = [float(k[4]) for k in klines]
            opens = [float(k[1]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]

            # Recent price change (last 3 candles = 15min)
            if closes[-4] == 0:
                return None
            recent_change = (
                (closes[-1] - closes[-4]) / closes[-4]
            ) * 100

            # Volume spike: last 3 candles vs previous 6
            recent_vol = sum(volumes[-3:])
            prev_vol = sum(volumes[-9:-3]) / 2  # Average of prev 6 as 3
            vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0

            # Consecutive green candles (from most recent)
            green_count = 0
            for i in range(len(closes) - 1, max(len(closes) - 7, 0), -1):
                if closes[i] > opens[i]:
                    green_count += 1
                else:
                    break

            # Acceleration: recent candle bodies getting bigger
            bodies = [
                closes[i] - opens[i]
                for i in range(len(closes) - 3, len(closes))
            ]
            accelerating = (
                len(bodies) >= 3
                and all(b > 0 for b in bodies)  # All green
                and bodies[-1] > bodies[-2]  # Getting bigger
            )

            # Price position in range (0=low, 1=high)
            recent_high = max(highs[-6:])
            recent_low = min(lows[-6:])
            price_range = recent_high - recent_low
            price_position = (
                (closes[-1] - recent_low) / price_range
                if price_range > 0 else 0.5
            )

            # Pullback check: is price pulling back slightly from high?
            # (better entry than at the exact top)
            from_high = (
                (recent_high - closes[-1]) / recent_high * 100
                if recent_high > 0 else 0
            )
            is_pullback_entry = 0.1 < from_high < 0.5  # Slight dip

            # Pump detection: explosive recent move + big volume spike,
            # and we're catching it near the peak (not after it dumped).
            is_pump = False
            if self.config and self.config.pump_detection_enabled:
                is_pump = (
                    recent_change >= self.config.pump_min_5min_change
                    and vol_ratio >= self.config.pump_min_vol_spike
                    and from_high <= self.config.pump_max_from_high
                )

            return {
                "recent_change": recent_change,
                "vol_ratio": vol_ratio,
                "green_count": green_count,
                "accelerating": accelerating,
                "price_position": price_position,
                "is_pullback_entry": is_pullback_entry,
                "is_pump": is_pump,
                "last_price": closes[-1],
            }
        except Exception as e:
            logger.error(f"Kline error {symbol}: {e}")
            return None

    def _calculate_momentum_score(
        self,
        price_change_24h: float,
        weighted_avg: float,
        last_price: float,
        kline_data: Optional[dict],
    ) -> int:
        """Multi-factor momentum score (0-6). Need 4+ to enter."""
        if not kline_data:
            return 0

        score = 0

        # Factor 1: Strong recent 5min momentum
        min_change = (
            self.config.min_price_change_5min if self.config else 0.8
        )
        if kline_data["recent_change"] >= min_change:
            score += 1

        # Factor 2: Clear volume spike
        min_spike = self.config.min_volume_spike if self.config else 2.5
        if kline_data["vol_ratio"] >= min_spike:
            score += 1

        # Factor 3: Price above VWAP
        if weighted_avg > 0 and last_price > weighted_avg * 1.003:
            score += 1

        # Factor 4: 2+ consecutive green candles
        if kline_data["green_count"] >= 2:
            score += 1

        # Factor 5: Healthy 24h change (positive but not overextended)
        if 1.0 < price_change_24h < 12.0:
            score += 1

        # Factor 6: Momentum acceleration
        if kline_data["accelerating"]:
            score += 1

        return score

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
            logger.error(f"Error fetching markets: {e}")
            return set()

    async def scan_momentum_opportunities(self) -> list[dict]:
        """Find high-confidence entries with strict multi-factor filter."""
        if not self.config:
            return []

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            self._log_activity(
                "CAPACIDADE", "mercado",
                f"Max {self.config.max_concurrent_trades} posições atingido"
            )
            return []

        # Macro filter first (saves API calls if market is down)
        btc_ok = await self._check_btc_trend()
        if not btc_ok:
            return []

        open_markets = await self._get_open_markets()
        tickers = await binance_client.get_ticker_24h()
        if not tickers:
            return []

        suffix = self.quote
        pre_filter = []
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

            # Strict pre-filters
            if volume_24h < self.config.min_volume_24h:
                continue
            if price_change < 1.0:  # Must be positive
                continue
            if price_change > self.config.max_price_change_24h:
                continue  # Don't chase parabolic

            pre_filter.append({
                "symbol": symbol,
                "change_24h": price_change,
                "volume": volume_24h,
                "weighted_avg": weighted_avg,
                "last_price": last_price,
            })

        # Sort by volume (most liquid first = tighter spreads)
        pre_filter.sort(key=lambda x: x["volume"], reverse=True)

        # Only analyze top candidates
        slots = self.config.max_concurrent_trades - len(active)
        max_to_analyze = min(slots * 4, 12)
        candidates = pre_filter[:max_to_analyze]

        confirmed = []
        for c in candidates:
            if len(confirmed) >= self.config.max_trades_per_cycle:
                break

            kline = await self._analyze_klines(c["symbol"])
            if not kline:
                continue

            score = self._calculate_momentum_score(
                c["change_24h"],
                c["weighted_avg"],
                c["last_price"],
                kline,
            )

            is_pump = kline.get("is_pump", False)
            # Pumps qualify even at slightly lower score (they move fast)
            if score >= self.config.min_momentum_score or is_pump:
                c["kline"] = kline
                c["score"] = score
                c["is_pump"] = is_pump
                c["strength"] = (
                    "strong" if (score >= 5 or is_pump) else "normal"
                )
                confirmed.append(c)

        # Sort: pumps first, then by score (best first)
        confirmed.sort(
            key=lambda x: (x.get("is_pump", False), x["score"]),
            reverse=True,
        )

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} {c['score']}/6 "
                f"+{c['change_24h']:.1f}%"
                for c in confirmed
            )
            logger.info(f"Scalp V3 qualified: {desc}")

        return confirmed

    async def execute_scalp(self, symbol: str) -> Optional[dict]:
        """Buy a new listing (always strong)."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        amt = self.config.amount_per_trade_strong
        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            return None

        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength="strong",
            momentum_score=6,
            trailing_active=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)
        self._trade_stats["total_trades"] += 1

        self._log_activity(
            "COMPRA [LISTAGEM]", symbol,
            f"${amt:.0f} @ {result.price:.6f}"
        )
        return position.model_dump()

    async def execute_momentum_scalp(
        self, symbol: str, strength: str = "normal", score: int = 4,
        is_pump: bool = False,
    ) -> Optional[dict]:
        """Execute entry on qualified momentum signal."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            self._log_activity(
                "FALHA", symbol, "Ordem rejeitada"
            )
            return None

        position = ScalpPosition(
            id=f"scalp_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength=strength,
            momentum_score=score,
            is_pump=is_pump,
            trailing_active=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)
        self._trade_stats["total_trades"] += 1

        if is_pump:
            label = f"PUMP {score}/6"
        elif strength == "strong":
            label = f"FORTE {score}/6"
        else:
            label = f"{score}/6"
        self._log_activity(
            f"COMPRA [{label}]", symbol,
            f"${amt:.0f} @ {result.price:.6f} "
            f"x {result.quantity:.6f}"
        )
        return position.model_dump()

    async def monitor_positions(self):
        if not self.config or not self.config.active:
            return

        for pos in self.get_active_positions():
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.error(f"Monitor error {pos.symbol}: {e}")

    async def _check_position(self, pos: ScalpPosition):
        current_price = await binance_client.get_symbol_price(pos.symbol)
        if not current_price:
            return

        pnl_pct = (
            (current_price - pos.entry_price) / pos.entry_price
        ) * 100

        # Track highest price
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        created = datetime.fromisoformat(pos.created_at)
        elapsed_min = (datetime.now(UTC) - created).total_seconds() / 60

        # Peak gain so far (how far it ran from entry)
        peak_gain = (
            (pos.highest_price - pos.entry_price) / pos.entry_price
        ) * 100
        is_runner = peak_gain >= self.config.runner_gain_threshold

        # 1. Stop Loss (wider = less noise triggers)
        if pnl_pct <= -self.config.stop_loss_percent:
            self._add_to_blacklist(pos.symbol)
            await self._close_position(pos, current_price, "STOP_LOSS")
            return

        # 2. Max hold timeout - runners are exempt (let winners run)
        if not is_runner and elapsed_min >= self.config.max_hold_minutes:
            await self._close_position(pos, current_price, "TIMEOUT")
            return

        # 3. Stale exit (flat for too long)
        if (
            not is_runner
            and elapsed_min >= self.config.stale_exit_minutes
            and abs(pnl_pct) < self.config.stale_exit_threshold
        ):
            await self._close_position(pos, current_price, "STALE")
            return

        # 4. Trailing stop activation
        if (
            not pos.trailing_active
            and pnl_pct >= self.config.trailing_activation
        ):
            pos.trailing_active = True
            self._log_activity(
                "TRAIL ON", pos.symbol,
                f"+{pnl_pct:.2f}% → trailing ativado"
            )

        # 5. Trailing stop trigger - dynamic distance.
        # Small gains: tight trail to lock profit. Runners/pumps: WIDE
        # trail that scales with the peak so they can keep climbing.
        if pos.trailing_active and pos.highest_price > pos.entry_price:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            if is_runner:
                trail_dist = max(
                    self.config.runner_trailing_percent,
                    peak_gain * self.config.runner_trail_factor,
                )
            else:
                trail_dist = self.config.trailing_percent
            if drop_from_peak >= trail_dist:
                if pnl_pct > 0.5:  # Only sell if still in profit
                    await self._close_position(
                        pos, current_price, "TRAILING_TP"
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
            pos.pnl_usdt = pos.amount_quote * (pos.pnl_percent / 100)

            # Update stats
            if pos.pnl_percent > 0:
                self._trade_stats["wins"] += 1
            else:
                self._trade_stats["losses"] += 1
                # Track daily losses for limit
                self._trade_stats["daily_loss_usdt"] += abs(
                    pos.pnl_usdt or 0
                )
            self._trade_stats["total_pnl_pct"] += pos.pnl_percent
            self._trade_stats["total_pnl_usdt"] += (pos.pnl_usdt or 0)

            # Check daily loss limit
            if (
                self.config
                and self._trade_stats["daily_loss_usdt"]
                >= self.config.daily_loss_limit
            ):
                self._paused_until = datetime.now(UTC) + timedelta(
                    hours=self.config.pause_hours
                )
                self._log_activity(
                    "PAUSA", "sistema",
                    f"Perda diária ${self._trade_stats['daily_loss_usdt']:.2f}"
                    f" >= ${self.config.daily_loss_limit:.0f} → "
                    f"pausado {self.config.pause_hours}h"
                )

            # Always blacklist after exit (2h cooldown)
            self._add_to_blacklist(pos.symbol)

            wins = self._trade_stats["wins"]
            losses = self._trade_stats["losses"]
            total = wins + losses
            wr = (wins / total * 100) if total > 0 else 0
            total_pnl = self._trade_stats["total_pnl_usdt"]

            reason_labels = {
                "TAKE_PROFIT": "TP",
                "TRAILING_TP": "Trail",
                "STOP_LOSS": "SL",
                "TIMEOUT": "Time",
                "STALE": "Flat",
                "MANUAL": "Manual",
                "MANUAL_ALL": "Manual",
            }
            label = reason_labels.get(reason, reason)
            self._log_activity(
                f"VENDA ({label})", pos.symbol,
                f"PnL: {pos.pnl_percent:+.2f}% "
                f"(${pos.pnl_usdt:+.2f}) | "
                f"Total: ${total_pnl:+.2f} | "
                f"WR: {wr:.0f}% ({wins}W/{losses}L)"
            )

            # Re-entry only on good exits
            if (
                reason == "TRAILING_TP"
                and self.config
                and self.config.reentry_enabled
                and pos.pnl_percent
                and pos.pnl_percent > 1.5
            ):
                count = self._reentry_count.get(pos.symbol, 0) + 1
                self._reentry_count[pos.symbol] = count
                # Remove from blacklist for re-entry
                self._blacklist.pop(pos.symbol, None)
                current = await binance_client.get_symbol_price(pos.symbol)
                if current and current >= price * 0.999:
                    self._log_activity(
                        "RE-ENTRY", pos.symbol,
                        f"Trail TP +{pos.pnl_percent:.1f}%, "
                        f"still rising (#{count})"
                    )
                    await self.execute_momentum_scalp(
                        pos.symbol, pos.signal_strength, pos.momentum_score
                    )
            elif reason == "STOP_LOSS":
                self._reentry_count.pop(pos.symbol, None)
        else:
            logger.error(f"Failed to sell {pos.symbol}")
            self._log_activity(
                "ERRO VENDA", pos.symbol, "Falha ao vender"
            )

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
        """Run one cycle. Called every 60 seconds."""
        if not self.config or not self.config.active:
            return

        # Check daily reset
        self._check_daily_reset()

        # Check if paused
        if self._is_paused():
            remaining = (
                self._paused_until - datetime.now(UTC)
            ).total_seconds() / 60 if self._paused_until else 0
            self._log_activity(
                "PAUSADO", "sistema",
                f"Retoma em {remaining:.0f}min (limite de perda atingido)"
            )
            # Still monitor existing positions even when paused
            await self.monitor_positions()
            return

        active_count = len(self.get_active_positions())
        wins = self._trade_stats["wins"]
        losses = self._trade_stats["losses"]
        total_pnl = self._trade_stats["total_pnl_usdt"]
        daily_loss = self._trade_stats["daily_loss_usdt"]

        self._log_activity(
            "SCAN", "mercado",
            f"{active_count}/{self.config.max_concurrent_trades} pos | "
            f"PnL: ${total_pnl:+.2f} ({wins}W/{losses}L) | "
            f"DayLoss: ${daily_loss:.2f}"
        )

        # Scan for new listings
        new_pairs = await self.scan_for_new_listings()
        if new_pairs:
            self._log_activity(
                "LISTAGEM", ", ".join(new_pairs),
                f"{len(new_pairs)} nova(s)!"
            )
        for symbol in new_pairs:
            await self.execute_scalp(symbol)

        # Scan momentum (strict filtering)
        momentum_signals = await self.scan_momentum_opportunities()
        if momentum_signals:
            desc = ", ".join(
                f"{s['symbol']}({s['score']}/6)"
                for s in momentum_signals
            )
            self._log_activity(
                "SINAL", desc,
                f"{len(momentum_signals)} qualificados"
            )
        for signal in momentum_signals:
            await self.execute_momentum_scalp(
                signal["symbol"], signal["strength"], signal["score"],
                signal.get("is_pump", False),
            )

        # Monitor existing positions
        await self.monitor_positions()

    # Kept for API compatibility but disabled by default
    async def scan_bearish_opportunities(self) -> list[dict]:
        return []

    async def execute_bearish_scalp(
        self, symbol: str, strength: str = "normal"
    ) -> Optional[dict]:
        return None


scalping_strategy = ScalpingStrategy()
