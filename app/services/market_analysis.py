import logging
from typing import Optional

import numpy as np
import pandas as pd

from app.models.trading import MarketAnalysis
from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    """Technical analysis for market data."""

    def calculate_rsi(self, prices: list[float], period: int = 14) -> Optional[float]:
        """Calculate RSI (Relative Strength Index)."""
        if len(prices) < period + 1:
            return None

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def calculate_macd(
        self, prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Optional[dict]:
        """Calculate MACD (Moving Average Convergence Divergence)."""
        if len(prices) < slow + signal:
            return None

        series = pd.Series(prices)
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        return {
            "macd": round(float(macd_line.iloc[-1]), 4),
            "signal": round(float(signal_line.iloc[-1]), 4),
            "histogram": round(float(histogram.iloc[-1]), 4),
        }

    def calculate_bollinger_bands(
        self, prices: list[float], period: int = 20, std_dev: float = 2.0
    ) -> Optional[dict]:
        """Calculate Bollinger Bands."""
        if len(prices) < period:
            return None

        series = pd.Series(prices)
        sma = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()

        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)

        return {
            "upper": round(float(upper.iloc[-1]), 2),
            "middle": round(float(sma.iloc[-1]), 2),
            "lower": round(float(lower.iloc[-1]), 2),
            "current": prices[-1],
        }

    def calculate_ema(self, prices: list[float], period: int) -> Optional[float]:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return None
        series = pd.Series(prices)
        ema = series.ewm(span=period, adjust=False).mean()
        return round(float(ema.iloc[-1]), 4)

    async def analyze_symbol(self, symbol: str) -> Optional[MarketAnalysis]:
        """Perform full technical analysis on a symbol."""
        klines = await binance_client.get_klines(symbol, interval="1h", limit=100)
        if not klines:
            return None

        closes = [float(k[4]) for k in klines]

        current_price = closes[-1]
        ticker_24h = await binance_client.get_ticker_24h(symbol)

        change_24h = 0.0
        volume_24h = 0.0
        if ticker_24h:
            change_24h = float(ticker_24h[0].get("priceChangePercent", 0))
            volume_24h = float(ticker_24h[0].get("quoteVolume", 0))

        rsi = self.calculate_rsi(closes)
        macd_data = self.calculate_macd(closes)
        bb = self.calculate_bollinger_bands(closes)

        # Calculate score (0-100)
        score = self._calculate_score(rsi, macd_data, bb, change_24h, volume_24h)

        macd_signal = None
        if macd_data:
            if macd_data["histogram"] > 0:
                macd_signal = "BULLISH"
            else:
                macd_signal = "BEARISH"

        recommendation = self._get_recommendation(score, rsi)

        return MarketAnalysis(
            symbol=symbol,
            price=current_price,
            change_24h=change_24h,
            volume_24h=volume_24h,
            rsi=rsi,
            macd_signal=macd_signal,
            score=score,
            recommendation=recommendation,
        )

    def _calculate_score(
        self,
        rsi: Optional[float],
        macd_data: Optional[dict],
        bb: Optional[dict],
        change_24h: float,
        volume_24h: float,
    ) -> float:
        """Calculate a composite score for a symbol (0-100)."""
        score = 50.0  # Neutral starting point

        # RSI component (oversold = higher score for buying)
        if rsi is not None:
            if rsi < 30:
                score += 20  # Oversold - good buy signal
            elif rsi < 40:
                score += 10
            elif rsi > 70:
                score -= 20  # Overbought - sell signal
            elif rsi > 60:
                score -= 10

        # MACD component
        if macd_data is not None:
            if macd_data["histogram"] > 0:
                score += 10  # Bullish momentum
            else:
                score -= 10  # Bearish momentum

            # MACD crossover
            if macd_data["macd"] > macd_data["signal"] and macd_data["histogram"] > 0:
                score += 5

        # Bollinger Bands component
        if bb is not None and bb["upper"] != bb["lower"]:
            price_position = (bb["current"] - bb["lower"]) / (bb["upper"] - bb["lower"])
            if price_position < 0.2:
                score += 15  # Near lower band - potential bounce
            elif price_position > 0.8:
                score -= 10  # Near upper band - potential pullback

        # Momentum (24h change)
        if 0 < change_24h < 5:
            score += 5  # Positive momentum, not too extreme
        elif change_24h > 10:
            score -= 5  # Too much gain, might correct
        elif -5 < change_24h < 0:
            score += 3  # Small dip, possible entry

        return max(0.0, min(100.0, score))

    def _get_recommendation(self, score: float, rsi: Optional[float]) -> str:
        """Get trading recommendation based on score."""
        if score >= 70:
            return "STRONG_BUY"
        elif score >= 60:
            return "BUY"
        elif score >= 40:
            return "HOLD"
        elif score >= 30:
            return "SELL"
        else:
            return "STRONG_SELL"


market_analyzer = MarketAnalyzer()
