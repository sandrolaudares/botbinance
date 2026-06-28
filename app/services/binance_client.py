import asyncio
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"
BINANCE_TESTNET_URL = "https://testnet.binance.vision"


class BinanceClient:
    """Client for Binance API - fetches market data."""

    def __init__(self):
        self.base_url = BINANCE_TESTNET_URL if settings.binance_testnet else BINANCE_BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"X-MBX-APIKEY": settings.binance_api_key},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        try:
            client = await self._get_client()
            response = await client.get("/api/v3/ticker/price", params={"symbol": symbol})
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    async def get_ticker_24h(self, symbol: Optional[str] = None) -> list[dict]:
        """Get 24h ticker stats."""
        try:
            client = await self._get_client()
            params = {}
            if symbol:
                params["symbol"] = symbol
            response = await client.get("/api/v3/ticker/24hr", params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return [data]
            return data
        except Exception as e:
            logger.error(f"Error fetching 24h ticker: {e}")
            return []

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[list]:
        """Get candlestick/kline data."""
        try:
            client = await self._get_client()
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            response = await client.get("/api/v3/klines", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return []

    async def get_brl_pairs(self) -> list[str]:
        """Get all trading pairs with BRL."""
        try:
            client = await self._get_client()
            response = await client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()
            brl_pairs = [
                s["symbol"]
                for s in data.get("symbols", [])
                if s.get("quoteAsset") == "BRL" and s.get("status") == "TRADING"
            ]
            return brl_pairs
        except Exception as e:
            logger.error(f"Error fetching BRL pairs: {e}")
            return self._get_default_brl_pairs()

    def _get_default_brl_pairs(self) -> list[str]:
        """Default BRL pairs for paper trading when API is unavailable."""
        return [
            "BTCBRL",
            "ETHBRL",
            "BNBBRL",
            "SOLBRL",
            "XRPBRL",
            "ADABRL",
            "DOTBRL",
            "MATICBRL",
            "LINKBRL",
            "AVAXBRL",
        ]

    async def get_top_brl_pairs_by_volume(self, top_n: int = 10) -> list[dict]:
        """Get top N BRL pairs by 24h volume."""
        try:
            tickers = await self.get_ticker_24h()
            brl_tickers = [
                t for t in tickers if t.get("symbol", "").endswith("BRL")
            ]
            brl_tickers.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            return brl_tickers[:top_n]
        except Exception as e:
            logger.error(f"Error getting top BRL pairs: {e}")
            return []

    async def get_multiple_prices(self, symbols: list[str]) -> dict[str, float]:
        """Get prices for multiple symbols concurrently."""
        tasks = [self.get_ticker_price(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        prices = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, float):
                prices[symbol] = result
        return prices


binance_client = BinanceClient()
