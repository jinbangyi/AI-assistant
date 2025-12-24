"""
Hyperliquid REST API Client for fetching historical user fills.

This module provides a client for querying the Hyperliquid REST API to fetch
historical trade fills for specific addresses. Supports both general user fills
and time-based queries with pagination handling.

IMPORTANT API LIMITATION:
    Hyperliquid API hard limit: only 10,000 most recent fills available,
    older data is permanently inaccessible via the API.

API Documentation:
    - Base URL: https://api.hyperliquid.xyz/info
    - Methods: userFills, userFillsByTime
    - Rate limit: Handle with exponential backoff retry
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

import httpx

# Configure logger
logger = logging.getLogger(__name__)

# Hyperliquid API endpoint
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"

# Maximum fills per response (Hyperliquid pagination limit)
FILLS_PER_PAGE = 2000

# Hyperliquid API hard limit: only 10,000 most recent fills available
MAX_API_FILLS_LIMIT = 10000

# Retry configuration
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # seconds


@dataclass
class UserFill:
    """
    Represents a single user fill from Hyperliquid API.

    Attributes:
        coin: Trading pair symbol (e.g., "ETH", "SOL")
        side: Trade side ("buy" or "sell")
        price: Execution price
        size: Position size
        timestamp_ms: Unix timestamp in milliseconds
        trade_id: Unique trade identifier
        tx_hash: Transaction hash
        start_time: Order start time (milliseconds)
        is_bid: True if bid (buy), False if ask (sell)
        user: User address
        oid: Order ID
        crossed_price: Price at which trade was crossed
        fee: Trading fee
        raw: Raw API response data
    """
    coin: str
    side: str
    price: Decimal
    size: Decimal
    timestamp_ms: int
    trade_id: int
    tx_hash: str
    start_time: int
    is_bid: bool
    user: str
    oid: int
    crossed_price: Decimal
    fee: dict
    raw: dict

    def to_trade_dict(self) -> dict:
        """Convert to trade dictionary format for database insertion."""
        from datetime import datetime, timezone

        return {
            "coin": self.coin,
            "side": self.side,
            "price": str(self.price),
            "size": str(self.size),
            "timestamp": datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "trade_id": self.trade_id,
            "tx_hash": self.tx_hash,
            # users is a JSON array of address strings: ["0x...", "0x..."]
            # Note: This only contains the user address for this fill, not the counterparty
            "users": [self.user],
        }


class HyperliquidAPIError(Exception):
    """Base exception for Hyperliquid API errors."""
    pass


class HyperliquidRateLimitError(HyperliquidAPIError):
    """Raised when rate limit is hit."""
    pass


class HyperliquidClient:
    """
    Client for Hyperliquid REST API.

    Handles fetching user fills with automatic retry logic and pagination.
    """

    def __init__(
        self,
        api_url: str = HYPERLIQUID_API_URL,
        timeout: float = 30.0,
        max_retries: int = MAX_RETRIES,
        initial_retry_delay: float = INITIAL_RETRY_DELAY,
    ):
        """
        Initialize Hyperliquid API client.

        Args:
            api_url: Base API URL
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            initial_retry_delay: Initial retry delay in seconds (exponential backoff)
        """
        self.api_url = api_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-init HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _make_request(self, payload: dict) -> dict:
        """
        Make a POST request to Hyperliquid API with retry logic.

        Args:
            payload: Request payload

        Returns:
            API response data

        Raises:
            HyperliquidAPIError: On API errors after retries exhausted
        """
        delay = self.initial_retry_delay

        for attempt in range(self.max_retries):
            try:
                response = self.client.post(self.api_url, json=payload)
                response.raise_for_status()

                data = response.json()

                # Check for API-level errors
                if isinstance(data, dict) and data.get("error"):
                    error_msg = data.get("error")
                    if "rate limit" in error_msg.lower():
                        raise HyperliquidRateLimitError(f"Rate limit hit: {error_msg}")
                    raise HyperliquidAPIError(f"API error: {error_msg}")

                return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limit - use exponential backoff
                    if attempt < self.max_retries - 1:
                        logger.warning(f"Rate limit hit, retrying in {delay}s (attempt {attempt + 1}/{self.max_retries})")
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise HyperliquidRateLimitError(f"Rate limit exceeded after {self.max_retries} retries")
                raise HyperliquidAPIError(f"HTTP error: {e}")

            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Request error, retrying in {delay}s (attempt {attempt + 1}/{self.max_retries}): {e}")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise HyperliquidAPIError(f"Request failed after {self.max_retries} retries: {e}")

            except HyperliquidRateLimitError:
                # Re-raise rate limit errors immediately for caller handling
                raise

            except Exception as e:
                raise HyperliquidAPIError(f"Unexpected error: {e}")

        raise HyperliquidAPIError("Max retries exceeded")

    def fetch_user_fills(
        self,
        address: str,
        coin: Optional[str] = None,
    ) -> List[UserFill]:
        """
        Fetch historical fills for a user address.

        This method fetches all available fills (up to 10,000) for a user.
        Results are ordered by time descending (most recent first).

        IMPORTANT: Hyperliquid API hard limit - only 10,000 most recent fills
        available. Older data is permanently inaccessible.

        Args:
            address: Ethereum address (0x...)
            coin: Optional coin filter (e.g., "ETH", "SOL"). If None, fetches all coins.

        Returns:
            List of UserFill objects, ordered by timestamp descending

        Raises:
            HyperliquidAPIError: On API errors
            ValueError: On invalid address format
        """
        # Validate address format
        if not address or not address.startswith("0x"):
            raise ValueError(f"Invalid address format: {address}")

        # Build request payload
        payload = {
            "type": "userFills",
            "user": address,
        }

        if coin:
            payload["coin"] = coin

        logger.info(f"Fetching user fills: address={address}, coin={coin or 'all'}")

        all_fills = []
        page = 0

        while True:
            # Set pagination - Hyperliquid returns batches of up to 2000
            if page > 0:
                # For pagination, use the oldest timestamp from previous batch
                oldest_timestamp = all_fills[-1].timestamp_ms
                payload["endTime"] = oldest_timestamp - 1  # Get fills before the oldest one

            data = self._make_request(payload)

            if not data:
                break

            fills = data if isinstance(data, list) else []
            if not fills:
                break

            # Parse fills
            for fill in fills:
                user_fill = UserFill(
                    coin=fill.get("coin", ""),
                    side="buy" if fill.get("side") == "B" else "sell",
                    price=Decimal(str(fill.get("px", "0"))),
                    size=Decimal(str(fill.get("sz", "0"))),
                    timestamp_ms=fill.get("time", 0),
                    trade_id=fill.get("tid", 0),
                    tx_hash=fill.get("hash", ""),
                    start_time=fill.get("startTime", 0),
                    is_bid=fill.get("side") == "B",
                    user=fill.get("user", address),
                    oid=fill.get("oid", 0),
                    crossed_price=Decimal(str(fill.get("crossedPx", "0"))),
                    fee=fill.get("fee", {}),
                    raw=fill,
                )
                all_fills.append(user_fill)

            logger.info(f"Fetched page {page + 1}: {len(fills)} fills (total: {len(all_fills)})")

            # Check if we've hit the API limit or received less than a full page
            if len(all_fills) >= MAX_API_FILLS_LIMIT:
                logger.warning(
                    f"Reached Hyperliquid API hard limit of {MAX_API_FILLS_LIMIT} fills. "
                    f"Older fills are permanently inaccessible."
                )
                break

            if len(fills) < FILLS_PER_PAGE:
                # Last page
                break

            page += 1

        logger.info(f"Total fills fetched: {len(all_fills)}")

        # Sort by timestamp descending (most recent first)
        all_fills.sort(key=lambda f: f.timestamp_ms, reverse=True)

        return all_fills

    def fetch_user_fills_by_time(
        self,
        address: str,
        start_time_ms: int,
        end_time_ms: int,
        coin: Optional[str] = None,
    ) -> List[UserFill]:
        """
        Fetch fills for a user within a specific time range.

        Args:
            address: Ethereum address (0x...)
            start_time_ms: Start time in milliseconds (Unix timestamp)
            end_time_ms: End time in milliseconds (Unix timestamp)
            coin: Optional coin filter

        Returns:
            List of UserFill objects

        Raises:
            HyperliquidAPIError: On API errors
            ValueError: On invalid address format or time range
        """
        if not address or not address.startswith("0x"):
            raise ValueError(f"Invalid address format: {address}")

        if start_time_ms >= end_time_ms:
            raise ValueError(f"Invalid time range: start_time must be before end_time")

        payload = {
            "type": "userFillsByTime",
            "user": address,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
        }

        if coin:
            payload["coin"] = coin

        logger.info(
            f"Fetching user fills by time: address={address}, "
            f"start={start_time_ms}, end={end_time_ms}, coin={coin or 'all'}"
        )

        data = self._make_request(payload)

        fills = data if isinstance(data, list) else []
        user_fills = []

        for fill in fills:
            user_fill = UserFill(
                coin=fill.get("coin", ""),
                side="buy" if fill.get("side") == "B" else "sell",
                price=Decimal(str(fill.get("px", "0"))),
                size=Decimal(str(fill.get("sz", "0"))),
                timestamp_ms=fill.get("time", 0),
                trade_id=fill.get("tid", 0),
                tx_hash=fill.get("hash", ""),
                start_time=fill.get("startTime", 0),
                is_bid=fill.get("side") == "B",
                user=fill.get("user", address),
                oid=fill.get("oid", 0),
                crossed_price=Decimal(str(fill.get("crossedPx", "0"))),
                fee=fill.get("fee", {}),
                raw=fill,
            )
            user_fills.append(user_fill)

        logger.info(f"Fetched {len(user_fills)} fills in time range")

        # Sort by timestamp descending
        user_fills.sort(key=lambda f: f.timestamp_ms, reverse=True)

        return user_fills


def fetch_user_fills(
    address: str,
    coin: Optional[str] = None,
) -> List[dict]:
    """
    Convenience function to fetch user fills.

    Returns a list of trade dictionaries ready for database insertion.

    Args:
        address: Ethereum address (0x...)
        coin: Optional coin filter

    Returns:
        List of trade dictionaries

    Example:
        >>> fills = fetch_user_fills("0x1234...", coin="ETH")
        >>> print(f"Fetched {len(fills)} fills")
    """
    with HyperliquidClient() as client:
        user_fills = client.fetch_user_fills(address, coin)
        return [fill.to_trade_dict() for fill in user_fills]


def fetch_user_fills_by_time(
    address: str,
    start_time_ms: int,
    end_time_ms: int,
    coin: Optional[str] = None,
) -> List[dict]:
    """
    Convenience function to fetch user fills by time range.

    Returns a list of trade dictionaries ready for database insertion.

    Args:
        address: Ethereum address (0x...)
        start_time_ms: Start time in milliseconds
        end_time_ms: End time in milliseconds
        coin: Optional coin filter

    Returns:
        List of trade dictionaries
    """
    with HyperliquidClient() as client:
        user_fills = client.fetch_user_fills_by_time(address, start_time_ms, end_time_ms, coin)
        return [fill.to_trade_dict() for fill in user_fills]
