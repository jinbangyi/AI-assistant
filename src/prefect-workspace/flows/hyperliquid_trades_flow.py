"""
Prefect flow for subscribing to Hyperliquid trades.

This flow connects to the Hyperliquid WebSocket API and subscribes to trade
data for specified coins. It runs for a specified duration and saves trades
to a SQLite database.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List
import json
import os

from prefect import flow, task, get_run_logger
import websocket
import time

from db import init_db, save_trades, get_trade_stats


WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_COINS = ["ETH", "SOL"]
DEFAULT_DURATION_MINUTES = 5

# Database URL - can be overridden via environment
# Uses PostgreSQL (same as Prefect) with separate schema
DB_URL = os.environ.get(
    "TRADES_DB_URL",
    "postgresql+psycopg2://prefect:prefect@postgres:5432/prefect"
)


@task
def process_trade(trade_data: dict) -> dict:
    """
    Process a single trade from Hyperliquid WebSocket.

    Args:
        trade_data: Raw trade data from WebSocket

    Returns:
        Processed trade dictionary
    """
    ts_ms = trade_data["time"]
    dt = datetime.fromtimestamp(ts_ms / 1000)
    formatted = dt.strftime('%Y-%m-%dT%H:%M:%S')

    processed = {
        "coin": trade_data["coin"],
        "side": "buy" if trade_data["side"] == "B" else "sell",
        "price": Decimal(str(trade_data["px"])),
        "size": Decimal(str(trade_data["sz"])),
        "timestamp": formatted,
        "trade_id": trade_data["tid"],
        "tx_hash": trade_data["hash"],
        "users": trade_data["users"],
    }
    return processed


@task
def subscribe_to_trades(coins: List[str], duration_minutes: int = DEFAULT_DURATION_MINUTES) -> List[dict]:
    """
    Subscribe to Hyperliquid WebSocket for trade data.

    This task connects to the Hyperliquid WebSocket API, subscribes to
    trade updates for the specified coins, and collects trades for the
    specified duration.

    Args:
        coins: List of coin symbols to subscribe to (e.g., ["ETH", "SOL"])
        duration_minutes: How long to collect trades (default: 5 minutes)

    Returns:
        List of collected trades
    """
    logger = get_run_logger()
    collected_trades = []
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)

    # WebSocket callback functions with closure for state
    ws_app = None
    subscribed = {"done": False}

    def on_open(ws):
        logger.info(f"WebSocket connected to {WS_URL}")
        # Subscribe to trades for each coin
        for coin in coins:
            sub_msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "trades",
                    "coin": coin
                }
            }
            ws.send(json.dumps(sub_msg))
            logger.info(f"Subscribed to trades: {coin}")

    def on_message(ws, message):
        if time.time() >= end_time:
            logger.info(f"Duration elapsed ({duration_minutes} minutes), closing connection...")
            ws.close()
            return

        data = json.loads(message)
        # Trade pushes come on channel == "trades"
        if data.get("channel") == "trades":
            trades = data.get("data", [])
            for t in trades:
                processed = process_trade(t)
                collected_trades.append(processed)
                logger.info(f"Trade received: {processed}")

    def on_error(ws, error):
        logger.error(f"WebSocket error: {error}")

    def on_close(ws, close_status_code, close_msg):
        logger.info(f"WebSocket closed: {close_status_code}, {close_msg}")
        subscribed["done"] = True

    # Create and run WebSocket
    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    logger.info(f"Starting WebSocket connection for {duration_minutes} minutes...")
    ws_app.run_forever(ping_interval=20, ping_timeout=10)

    logger.info(f"Collected {len(collected_trades)} trades")
    return collected_trades


@task
def save_trades_to_db(trades: List[dict], db_url: str = DB_URL) -> int:
    """
    Save trades to the PostgreSQL database.

    Args:
        trades: List of processed trade dictionaries
        db_url: Database connection URL

    Returns:
        Number of trades saved
    """
    logger = get_run_logger()

    # Convert Decimal objects to strings for JSON serialization
    trades_serializable = []
    for t in trades:
        trade_dict = {
            "coin": t["coin"],
            "side": t["side"],
            "price": str(t["price"]),
            "size": str(t["size"]),
            "timestamp": t["timestamp"],
            "trade_id": t["trade_id"],
            "tx_hash": t["tx_hash"],
            "users": t["users"],
        }
        trades_serializable.append(trade_dict)

    count = save_trades(trades_serializable, db_url=db_url)
    logger.info(f"Saved {count} trades to database")
    return count


@flow(name="Hyperliquid Trades Subscriber")
def hyperliquid_trades_flow(
    coins: List[str] = DEFAULT_COINS,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    db_url: str = DB_URL,
) -> dict:
    """
    Prefect flow to subscribe to Hyperliquid trades and save to database.

    This flow collects trade data from Hyperliquid for the specified coins,
    saves them to PostgreSQL (visible in pgAdmin), and returns a summary.

    Args:
        coins: List of coin symbols to subscribe to
        duration_minutes: How long to collect trades
        db_url: Database connection URL (defaults to Prefect PostgreSQL)

    Returns:
        Summary dictionary with trade statistics
    """
    logger = get_run_logger()
    logger.info(f"Starting Hyperliquid trades flow for coins: {coins}")
    logger.info(f"Database URL: {db_url}")

    # Initialize database (auto-creates schema and tables if not exists)
    init_db(db_url=db_url)
    logger.info("Database initialized")

    # Subscribe to trades and collect data
    trades = subscribe_to_trades(coins=coins, duration_minutes=duration_minutes)

    # Save trades to database
    save_trades_to_db(trades, db_url=db_url)

    # Calculate summary statistics
    summary = {
        "total_trades": len(trades),
        "coins_subscribed": coins,
        "duration_minutes": duration_minutes,
        "trades_by_coin": {},
        "trades_by_side": {"buy": 0, "sell": 0},
    }

    for trade in trades:
        coin = trade["coin"]
        summary["trades_by_coin"][coin] = summary["trades_by_coin"].get(coin, 0) + 1
        summary["trades_by_side"][trade["side"]] += 1

    logger.info(f"Flow completed. Summary: {summary}")
    return summary


if __name__ == "__main__":
    # Run the flow directly for testing
    result = hyperliquid_trades_flow(
        coins=["ETH", "SOL"],
        duration_minutes=1,  # Run for 1 minute when testing directly
    )
    print(f"\nFinal result: {result}")
