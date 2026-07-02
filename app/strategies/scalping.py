"""Aggressive scalping strategy for Binance - V2 (redesigned for effectiveness).

Key changes from V1:
- Larger position sizes ($30-50) for meaningful profits
- Better signal quality: volume spike + kline acceleration required
- Proper risk/reward: 2.5% TP vs 1.2% SL (~2:1 ratio)
- Smarter trailing: activates after 1% profit, 0.8% distance
- Fewer concurrent trades with higher confidence
- Multi-factor momentum scoring
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
    # Position sizing - meaningful amounts for real profit
    amount_per_trade: float = 30.0  # Normal signal
    amount_per_trade_strong: float = 50.0  # Strong signal (multi-factor)
    # Exit parameters - 2:1 risk/reward ratio
    take_profit_percent: float = 2.5  # Hard TP
    trailing_activation: float = 1.2  # Activate trail after this % gain
    trailing_percent: float = 0.8  # Trail distance once activated
    stop_loss_percent: float = 1.2  # Max loss per trade
    # Time management
    max_hold_minutes: int = 120  # 2 hours max
    stale_exit_minutes: int = 45  # Exit at breakeven if flat after 45min
    stale_exit_threshold: float = 0.15  # "Flat" = less than 0.15% movement
    # Capacity
    max_concurrent_trades: int = 15
    max_trades_per_cycle: int = 3
    cycle_seconds: int = 45
    # Signal filters - quality over quantity
    min_volume_24h: float = 100000.0  # Only liquid pairs ($100k+)
    min_momentum_score: int = 3  # Need 3+ factors to enter (out of 6)
    min_price_change_5min: float = 0.5  # Min 0.5% move in last 15min
    min_volume_spike: float = 2.0  # Volume must be 2x recent average
    # Re-entry and blacklist
    reentry_enabled: bool = True
    blacklist_minutes: int = 60  # Longer cooldown after SL
    accumulation_enabled: bool = True
    # Bearish/dip-buy
    bearish_enabled: bool = True
    bearish_drop_threshold: float = -4.0  # Deeper dip required
    bearish_bounce_min: float = 1.0  # Stronger bounce confirmation
    bearish_tp_percent: float = 2.0
    bearish_sl_percent: float = 1.0


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
    status: str = "ACTIVE"
    reason: str = ""
    created_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    pnl_percent: Optional[float] = None


class ScalpingStrategy:
    """Aggressive scalping V2 - quality signals, proper risk/reward.

    Entry conditions (multi-factor momentum score):
    1. 5min kline shows >0.5% move in last 15min
    2. Volume spike: recent 3 candles > 2x previous 3
    3. Price above VWAP (weighted avg price)
    4. 2+ consecutive green candles
    5. 24h change positive (>1%)
    6. Kline acceleration (each candle bigger than last)

    Need 3+ factors (configurable) to enter.
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
        }

    def _log_activity(self, action: str, symbol: str, details: str = ""):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "action": action,
            "symbol": symbol,
            "details": details,
        }
        self.activity_log.append(entry)
        if len(self.activity_log) > 150:
            self.activity_log = self.activity_log[-150:]

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

    async def setup(self, config: ScalpConfig) -> bool:
        self.config = config
        if config.active and not self._initialized:
            await self._initialize_known_pairs()
        logger.info(
            f"Scalping V2: ${config.amount_per_trade}/"
            f"${config.amount_per_trade_strong} per trade, "
            f"TP={config.take_profit_percent}%, "
            f"SL={config.stop_loss_percent}%, "
            f"Trail@{config.trailing_activation}%/{config.trailing_percent}%, "
            f"MaxHold={config.max_hold_minutes}min, "
            f"Max={config.max_concurrent_trades}, "
            f"MinScore={config.min_momentum_score}/6"
        )
        return True

    async def _initialize_known_pairs(self):
        pairs = await self._fetch_tradeable_pairs()
        self.known_pairs = set(pairs)
        self._initialized = True
        logger.info(
            f"Scalping V2 initialized: {len(self.known_pairs)} "
            f"{self.quote} pairs tracked"
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

            # 1. Recent price change (last 3 candles = 15min)
            if closes[-4] == 0:
                return None
            recent_change = (
                (closes[-1] - closes[-4]) / closes[-4]
            ) * 100

            # 2. Volume spike: last 3 candles vs previous 3
            recent_vol = sum(volumes[-3:])
            prev_vol = sum(volumes[-6:-3])
            vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0

            # 3. Consecutive green candles
            green_count = 0
            for i in range(len(closes) - 1, max(len(closes) - 7, 0), -1):
                if closes[i] > opens[i]:
                    green_count += 1
                else:
                    break

            # 4. Acceleration: each recent candle body bigger than previous
            bodies = [
                abs(closes[i] - opens[i])
                for i in range(len(closes) - 3, len(closes))
            ]
            accelerating = (
                len(bodies) >= 3
                and bodies[-1] > bodies[-2] > bodies[-3]
                and closes[-1] > opens[-1]  # Last candle is green
            )

            # 5. Price position vs range (higher = stronger)
            recent_high = max(highs[-6:])
            recent_low = min(lows[-6:])
            price_range = recent_high - recent_low
            if price_range > 0:
                price_position = (
                    (closes[-1] - recent_low) / price_range
                )
            else:
                price_position = 0.5

            # 6. Average candle size for volatility assessment
            avg_body = sum(
                abs(closes[i] - opens[i]) for i in range(-6, 0)
            ) / 6
            volatility = (
                (avg_body / closes[-1]) * 100 if closes[-1] > 0 else 0
            )

            return {
                "recent_change": recent_change,
                "vol_ratio": vol_ratio,
                "green_count": green_count,
                "accelerating": accelerating,
                "price_position": price_position,
                "last_price": closes[-1],
                "volatility": volatility,
            }
        except Exception as e:
            logger.error(f"Kline analysis error {symbol}: {e}")
            return None

    def _calculate_momentum_score(
        self,
        price_change_24h: float,
        weighted_avg: float,
        last_price: float,
        kline_data: Optional[dict],
    ) -> int:
        """Calculate multi-factor momentum score (0-6)."""
        if not kline_data:
            return 0

        score = 0

        # Factor 1: Recent 5min momentum > threshold
        if kline_data["recent_change"] >= (
            self.config.min_price_change_5min if self.config else 0.5
        ):
            score += 1

        # Factor 2: Volume spike
        min_spike = self.config.min_volume_spike if self.config else 2.0
        if kline_data["vol_ratio"] >= min_spike:
            score += 1

        # Factor 3: Price above VWAP (above weighted avg)
        if weighted_avg > 0 and last_price > weighted_avg * 1.002:
            score += 1

        # Factor 4: Consecutive green candles (2+)
        if kline_data["green_count"] >= 2:
            score += 1

        # Factor 5: 24h change positive and strong
        if price_change_24h > 1.0:
            score += 1

        # Factor 6: Kline acceleration (momentum building)
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
            logger.error(f"Error fetching open markets: {e}")
            return set()

    async def scan_momentum_opportunities(self) -> list[dict]:
        """Find high-quality momentum entries using multi-factor scoring."""
        if not self.config:
            return []

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
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

            # Pre-filter: decent volume and some positive movement
            if volume_24h < self.config.min_volume_24h:
                continue
            if price_change < 0.5:  # At least slightly positive 24h
                continue

            pre_filter.append({
                "symbol": symbol,
                "change_24h": price_change,
                "volume": volume_24h,
                "weighted_avg": weighted_avg,
                "last_price": last_price,
            })

        # Sort by 24h change - top movers first
        pre_filter.sort(key=lambda x: x["change_24h"], reverse=True)

        # Analyze top candidates with klines (limit API calls)
        slots = self.config.max_concurrent_trades - len(active)
        max_to_analyze = min(slots * 3, 15)
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

            if score >= self.config.min_momentum_score:
                c["kline"] = kline
                c["score"] = score
                c["strength"] = "strong" if score >= 5 else "normal"
                confirmed.append(c)

        # Sort confirmed by score (best first)
        confirmed.sort(key=lambda x: x["score"], reverse=True)

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} score={c['score']}/6 "
                f"+{c['change_24h']:.1f}%"
                for c in confirmed
            )
            logger.info(f"Scalp V2 signals: {desc}")

        return confirmed

    async def execute_scalp(self, symbol: str) -> Optional[dict]:
        """Buy a newly listed coin (strong sizing, high priority)."""
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
            logger.error(f"Scalping: failed to buy {symbol}")
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

        self._log_activity(
            "COMPRA (Nova Listagem)", symbol,
            f"${amt:.0f} @ {result.price:.6f} "
            f"x {result.quantity:.6f} [STRONG]"
        )
        return position.model_dump()

    async def execute_momentum_scalp(
        self, symbol: str, strength: str = "normal", score: int = 3
    ) -> Optional[dict]:
        """Execute a momentum scalp with proper sizing."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        # Dynamic sizing based on signal strength
        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        # Accumulation on 3rd+ re-entry
        reentry_n = self._reentry_count.get(symbol, 0)
        if self.config.accumulation_enabled and reentry_n >= 2:
            amt = min(amt * 1.5, 75.0)  # Cap at $75

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            self._log_activity(
                "FALHA", symbol, "Ordem rejeitada (saldo/min notional)"
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
            trailing_active=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        label = f"FORTE {score}/6" if strength == "strong" else f"{score}/6"
        self._log_activity(
            f"COMPRA [{label}]", symbol,
            f"${amt:.0f} @ {result.price:.6f} "
            f"x {result.quantity:.6f}"
        )
        self._trade_stats["total_trades"] += 1
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
        elapsed = datetime.now(UTC) - created
        elapsed_min = elapsed.total_seconds() / 60

        # 1. Hard Stop Loss
        if pnl_pct <= -self.config.stop_loss_percent:
            self._add_to_blacklist(pos.symbol)
            await self._close_position(pos, current_price, "STOP_LOSS")
            return

        # 2. Max hold timeout
        if elapsed_min >= self.config.max_hold_minutes:
            await self._close_position(pos, current_price, "TIMEOUT")
            return

        # 3. Stale position exit (flat after N minutes -> exit near breakeven)
        if (
            elapsed_min >= self.config.stale_exit_minutes
            and abs(pnl_pct) < self.config.stale_exit_threshold
        ):
            await self._close_position(pos, current_price, "STALE")
            return

        # 4. Hard Take Profit
        if pnl_pct >= self.config.take_profit_percent:
            await self._close_position(pos, current_price, "TAKE_PROFIT")
            return

        # 5. Trailing stop logic - activate after threshold
        if (
            not pos.trailing_active
            and pnl_pct >= self.config.trailing_activation
        ):
            pos.trailing_active = True
            self._log_activity(
                "TRAIL ATIVO", pos.symbol,
                f"Lucro +{pnl_pct:.2f}% > "
                f"{self.config.trailing_activation}%"
            )

        # 6. Trailing stop trigger
        if pos.trailing_active and pos.highest_price > pos.entry_price:
            drop_from_peak = (
                (pos.highest_price - current_price) / pos.highest_price
            ) * 100
            if drop_from_peak >= self.config.trailing_percent:
                # Only sell if we're in profit
                if pnl_pct > 0.3:
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

            # Update stats
            if pos.pnl_percent > 0:
                self._trade_stats["wins"] += 1
            else:
                self._trade_stats["losses"] += 1
            self._trade_stats["total_pnl_pct"] += pos.pnl_percent

            reason_map = {
                "TAKE_PROFIT": "VENDA (TP)",
                "TRAILING_TP": "VENDA (Trail)",
                "STOP_LOSS": "VENDA (SL)",
                "TIMEOUT": "VENDA (Timeout)",
                "STALE": "VENDA (Flat)",
                "MANUAL": "VENDA (Manual)",
                "MANUAL_ALL": "VENDA (Fechar Todos)",
            }
            wins = self._trade_stats["wins"]
            losses = self._trade_stats["losses"]
            total = wins + losses
            wr = (wins / total * 100) if total > 0 else 0
            self._log_activity(
                reason_map.get(reason, f"VENDA ({reason})"),
                pos.symbol,
                f"PnL: {pos.pnl_percent:+.2f}% | "
                f"${pos.amount_quote:.0f} | "
                f"WR: {wr:.0f}% ({wins}W/{losses}L)"
            )

            # Re-entry logic
            if (
                reason in ("TAKE_PROFIT", "TRAILING_TP")
                and self.config
                and self.config.reentry_enabled
                and pos.pnl_percent > 0.8
            ):
                count = self._reentry_count.get(pos.symbol, 0) + 1
                self._reentry_count[pos.symbol] = count
                # Check if still rising
                current = await binance_client.get_symbol_price(pos.symbol)
                if current and current >= price * 0.999:
                    self._log_activity(
                        "RE-ENTRY", pos.symbol,
                        f"Ainda subindo (#{count})"
                    )
                    await self.execute_momentum_scalp(
                        pos.symbol, pos.signal_strength, pos.momentum_score
                    )
            elif reason == "STOP_LOSS":
                self._reentry_count.pop(pos.symbol, None)
        else:
            logger.error(f"Failed to sell {pos.symbol}")
            self._log_activity(
                "ERRO VENDA", pos.symbol, "Falha ao executar venda"
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
        """Run one full cycle: scan + buy + monitor.

        Called by the scheduler every ~45 seconds.
        """
        if not self.config or not self.config.active:
            return

        active_count = len(self.get_active_positions())
        bl_count = len(self._blacklist)
        wins = self._trade_stats["wins"]
        losses = self._trade_stats["losses"]
        total_pnl = self._trade_stats["total_pnl_pct"]

        self._log_activity(
            "SCAN", "mercado",
            f"{active_count} pos | {bl_count} BL | "
            f"PnL: {total_pnl:+.2f}% ({wins}W/{losses}L)"
        )

        # Scan for new listings (always)
        new_pairs = await self.scan_for_new_listings()
        if new_pairs:
            self._log_activity(
                "NOVA LISTAGEM", ", ".join(new_pairs),
                f"{len(new_pairs)} nova(s)!"
            )
        for symbol in new_pairs:
            await self.execute_scalp(symbol)

        # Scan momentum (multi-factor scoring)
        momentum_signals = await self.scan_momentum_opportunities()
        if momentum_signals:
            desc = ", ".join(
                f"{s['symbol']}({s['score']}/6)"
                for s in momentum_signals
            )
            self._log_activity(
                "MOMENTUM", desc,
                f"{len(momentum_signals)} sinais qualificados"
            )
        for signal in momentum_signals:
            await self.execute_momentum_scalp(
                signal["symbol"], signal["strength"], signal["score"]
            )

        # Bearish dip-buy
        if self.config.bearish_enabled:
            bearish_signals = await self.scan_bearish_opportunities()
            if bearish_signals:
                desc = ", ".join(
                    f"{s['symbol']}({s['drop']:.0f}%)"
                    for s in bearish_signals
                )
                self._log_activity(
                    "DIP-BUY", desc,
                    f"{len(bearish_signals)} bounces"
                )
            for signal in bearish_signals:
                await self.execute_bearish_scalp(
                    signal["symbol"], signal["strength"]
                )

        # Monitor existing positions
        await self.monitor_positions()

    async def scan_bearish_opportunities(self) -> list[dict]:
        """Find deep dips showing strong bounce (confirmed reversal)."""
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
            # Only coins that dropped significantly
            if price_change > self.config.bearish_drop_threshold:
                continue

            # Bounce from low must be strong
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

        # Sort by bounce strength
        candidates.sort(key=lambda x: x["bounce"], reverse=True)

        slots = self.config.max_concurrent_trades - len(active)
        max_per_cycle = self.config.max_trades_per_cycle
        pre_candidates = candidates[:min(slots, max_per_cycle * 2)]

        # Confirm with klines: recent recovery in 5min timeframe
        confirmed = []
        for c in pre_candidates:
            if len(confirmed) >= max_per_cycle:
                break
            kline = await self._analyze_klines(c["symbol"])
            if kline and kline["recent_change"] > 0.3:
                c["kline"] = kline
                c["strength"] = (
                    "strong" if c["bounce"] >= 2.0 else "normal"
                )
                confirmed.append(c)

        if confirmed:
            desc = ", ".join(
                f"{c['symbol']} {c['drop']:.1f}% "
                f"bounce+{c['bounce']:.1f}%"
                for c in confirmed
            )
            logger.info(f"Bearish dip-buy V2: {desc}")

        return confirmed

    async def execute_bearish_scalp(
        self, symbol: str, strength: str = "normal"
    ) -> Optional[dict]:
        """Buy a confirmed dip bounce."""
        if not self.config:
            return None

        active = self.get_active_positions()
        if len(active) >= self.config.max_concurrent_trades:
            return None

        if strength == "strong":
            amt = self.config.amount_per_trade_strong
        else:
            amt = self.config.amount_per_trade

        # Accumulation
        reentry_n = self._reentry_count.get(symbol, 0)
        if self.config.accumulation_enabled and reentry_n >= 2:
            amt = min(amt * 1.5, 75.0)

        result = await trading_engine.buy_with_quote(
            symbol, amt, StrategyType.SMART_TRADE
        )
        if not result:
            self._log_activity(
                "FALHA DIP", symbol, "Ordem rejeitada"
            )
            return None

        position = ScalpPosition(
            id=f"bear_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            symbol=symbol,
            entry_price=result.price,
            quantity=result.quantity,
            amount_quote=amt,
            highest_price=result.price,
            signal_strength=strength,
            momentum_score=0,
            trailing_active=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.positions.append(position)

        label = "FORTE" if strength == "strong" else "NORMAL"
        self._log_activity(
            f"COMPRA DIP [{label}]", symbol,
            f"${amt:.0f} @ {result.price:.6f}"
        )
        self._trade_stats["total_trades"] += 1
        return position.model_dump()


scalping_strategy = ScalpingStrategy()
