import asyncio
import hashlib
import hmac
import logging
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"
BINANCE_TESTNET_URL = "https://testnet.binance.vision"


class BinanceClient:
    """Client for Binance API - fetches market data and executes orders."""

    def __init__(self):
        self.base_url = BINANCE_TESTNET_URL if settings.binance_testnet else BINANCE_BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def has_credentials(self) -> bool:
        """Check if API credentials are configured."""
        return bool(settings.binance_api_key and settings.binance_api_secret)

    def _sign_params(self, params: dict) -> dict:
        """Sign request parameters with HMAC-SHA256."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.HMAC(
            settings.binance_api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

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

    # ========== Market Data (Public) ==========

    async def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        try:
            client = await self._get_client()
            response = await client.get(
                "/api/v3/ticker/price", params={"symbol": symbol}
            )
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    async def get_symbol_price(self, symbol: str) -> Optional[float]:
        """Alias for get_ticker_price."""
        return await self.get_ticker_price(symbol)

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
                t for t in tickers
                if t.get("symbol", "").endswith("BRL")
                and not t.get("symbol", "").startswith("LD")
                and "1MBB" not in t.get("symbol", "")
                and "ETHW" not in t.get("symbol", "")
            ]
            brl_tickers.sort(
                key=lambda x: float(x.get("quoteVolume", 0)),
                reverse=True,
            )
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

    # ========== Account (Authenticated) ==========

    async def get_account_info(self) -> Optional[dict]:
        """Get account information including balances."""
        if not self.has_credentials:
            return None
        try:
            client = await self._get_client()
            params = self._sign_params({})
            response = await client.get("/api/v3/account", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching account info: {e}")
            return None

    async def get_brl_balance(self) -> Optional[float]:
        """Get BRL balance from account."""
        account = await self.get_account_info()
        if not account:
            return None
        for balance in account.get("balances", []):
            if balance["asset"] == "BRL":
                return float(balance["free"])
        return 0.0

    async def get_asset_balance(self, asset: str) -> Optional[float]:
        """Get balance of a specific asset."""
        account = await self.get_account_info()
        if not account:
            return None
        for balance in account.get("balances", []):
            if balance["asset"] == asset:
                return float(balance["free"])
        return 0.0

    # ========== Trading (Authenticated) ==========

    async def place_market_order(
        self, symbol: str, side: str, quote_order_qty: Optional[float] = None,
        quantity: Optional[float] = None
    ) -> Optional[dict]:
        """Place a market order on Binance.

        Args:
            symbol: Trading pair (e.g., BTCBRL)
            side: BUY or SELL
            quote_order_qty: Amount in quote currency (BRL) for BUY orders
            quantity: Amount in base currency for SELL orders
        """
        if not self.has_credentials:
            logger.error("Cannot place real order: no API credentials")
            return None

        try:
            client = await self._get_client()
            params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
            }
            if quote_order_qty is not None:
                params["quoteOrderQty"] = f"{quote_order_qty:.2f}"
            elif quantity is not None:
                params["quantity"] = f"{quantity:.8f}"
            else:
                logger.error("Must specify either quote_order_qty or quantity")
                return None

            params = self._sign_params(params)
            response = await client.post("/api/v3/order", params=params)
            if response.status_code != 200:
                error_body = response.text
                logger.error(
                    f"Order rejected {side} {symbol}: "
                    f"HTTP {response.status_code} - {error_body}"
                )
                return None
            order = response.json()
            logger.info(
                f"Order placed: {side} {symbol} - "
                f"Status: {order.get('status')}, "
                f"Filled: {order.get('executedQty')} @ avg {order.get('cummulativeQuoteQty')}"
            )
            return order
        except Exception as e:
            logger.error(f"Error placing order {side} {symbol}: {e}")
            return None

    async def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
        time_in_force: str = "GTC"
    ) -> Optional[dict]:
        """Place a limit order on Binance."""
        if not self.has_credentials:
            logger.error("Cannot place real order: no API credentials")
            return None

        try:
            client = await self._get_client()
            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": time_in_force,
                "quantity": f"{quantity:.8f}",
                "price": f"{price:.2f}",
            }
            params = self._sign_params(params)
            response = await client.post("/api/v3/order", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error placing limit order {side} {symbol}: {e}")
            return None

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """Get open orders."""
        if not self.has_credentials:
            return []
        try:
            client = await self._get_client()
            params = {}
            if symbol:
                params["symbol"] = symbol
            params = self._sign_params(params)
            response = await client.get("/api/v3/openOrders", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []

    async def get_my_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Get recent trades for a symbol."""
        if not self.has_credentials:
            return []
        try:
            client = await self._get_client()
            params = {"symbol": symbol, "limit": limit}
            params = self._sign_params(params)
            response = await client.get("/api/v3/myTrades", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching trades for {symbol}: {e}")
            return []


binance_client = BinanceClient()
