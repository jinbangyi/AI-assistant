"""
Continuous Hyperliquid Trade Subscriber with Batch Processing.

A never-stop multi-coin trade subscriber with:
- Time-based batch writes (10s or 10,000 trades)
- Trade_id uniqueness enforcement with partial batch retry
- Graceful shutdown via SIGTERM/SIGINT
- Automatic reconnection with exponential backoff
- Health monitoring endpoint

Environment Variables:
- HYPERLIQUID_COINS: Comma-separated list of coins (default: "ETH,SOL")
- BATCH_FLUSH_INTERVAL_SECONDS: Batch flush interval in seconds (default: 10)
- BATCH_SIZE: Maximum trades per batch (default: 10000)
- HEALTH_CHECK_PORT: Health check HTTP server port (default: 8080)
- TRADES_DB_URL: Database connection URL (default: prefect PostgreSQL)
"""

import argparse
import json
import logging
import os
import queue
import signal
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List

import websocket

from db import init_db, save_trades


# ============== Configuration ==============
WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_COINS = ["ETH", "SOL"]
DEFAULT_BATCH_INTERVAL_SECONDS = 10
DEFAULT_BATCH_SIZE = 10000
DEFAULT_HEALTH_CHECK_PORT = 8080

# Reconnection backoff: 1s -> 2s -> 5s -> 10s -> 30s max
RECONNECT_DELAYS = [1, 2, 5, 10, 30]

# Environment configuration
COINS = os.environ.get("HYPERLIQUID_COINS", "ETH,SOL").split(",")
BATCH_FLUSH_INTERVAL_SECONDS = int(os.environ.get("BATCH_FLUSH_INTERVAL_SECONDS", str(DEFAULT_BATCH_INTERVAL_SECONDS)))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
HEALTH_CHECK_PORT = int(os.environ.get("HEALTH_CHECK_PORT", str(DEFAULT_HEALTH_CHECK_PORT)))
TRADES_DB_URL = os.environ.get(
    "TRADES_DB_URL",
    "postgresql+psycopg2://prefect:prefect@postgres:5432/prefect"
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============== Shared State ==============
@dataclass
class SubscriberState:
    """Thread-safe shared state for the subscriber."""
    shutdown_flag: bool = False
    total_trades: int = 0
    error_count: int = 0
    last_trade_time: str = ""
    start_time: float = field(default_factory=time.time)
    last_flush_time: float = field(default_factory=time.time)
    queue_depth: int = 0
    write_stats: dict = field(default_factory=lambda: {"saved": 0, "duplicates": 0, "errors": 0})
    reconnect_count: int = 0
    is_connected: bool = False


# Global state
state = SubscriberState()
trade_queue: queue.Queue = queue.Queue()
state_lock = threading.Lock()


# ============== Trade Processing ==============
def process_trade(trade_data: dict) -> dict:
    """Process a single trade from Hyperliquid WebSocket."""
    ts_ms = trade_data["time"]
    dt = datetime.fromtimestamp(ts_ms / 1000)
    formatted = dt.strftime('%Y-%m-%dT%H:%M:%S')

    return {
        "coin": trade_data["coin"],
        "side": "buy" if trade_data["side"] == "B" else "sell",
        "price": Decimal(str(trade_data["px"])),
        "size": Decimal(str(trade_data["sz"])),
        "timestamp": formatted,
        "trade_id": trade_data["tid"],
        "tx_hash": trade_data["hash"],
        "users": trade_data["users"],
    }


# ============== Batch Writer ==============
class BatchWriter(threading.Thread):
    """Background thread that flushes trades to database based on time or size triggers."""

    def __init__(self, queue_: queue.Queue, state_: SubscriberState, lock_: threading.Lock):
        super().__init__(daemon=True)
        self.queue = queue_
        self.state = state_
        self.lock = lock_
        self.running = True

    def run(self):
        """Main batch writer loop."""
        logger.info(f"BatchWriter started: interval={BATCH_FLUSH_INTERVAL_SECONDS}s, size={BATCH_SIZE}")

        while self.running:
            try:
                # Wait for next flush or shutdown
                self._wait_for_flush_condition()

                if not self.running:
                    # Final flush before exit
                    self._flush()
                    break

                # Check if we should flush (time trigger or size trigger)
                now = time.time()
                time_elapsed = now - self.state.last_flush_time
                should_flush = (
                    time_elapsed >= BATCH_FLUSH_INTERVAL_SECONDS or
                    self.state.queue_depth >= BATCH_SIZE
                )

                if should_flush:
                    self._flush()

            except Exception as e:
                logger.error(f"Error in BatchWriter: {e}")
                with self.lock:
                    self.state.error_count += 1

        logger.info("BatchWriter stopped")

    def _wait_for_flush_condition(self):
        """Wait until it's time to flush or shutdown is requested."""
        while self.running:
            now = time.time()
            time_elapsed = now - self.state.last_flush_time
            queue_size = self.state.queue_depth

            # Flush triggers: time elapsed OR queue size reached
            if time_elapsed >= BATCH_FLUSH_INTERVAL_SECONDS or queue_size >= BATCH_SIZE:
                return

            # Sleep for a short interval before checking again
            time.sleep(0.1)

    def _flush(self):
        """Flush all pending trades to database."""
        if self.queue.empty():
            return

        batch = []
        while not self.queue.empty() and len(batch) < BATCH_SIZE * 2:  # Allow some overflow
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if not batch:
            return

        # Convert Decimal objects to strings for serialization
        trades_serializable = []
        for t in batch:
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

        # Save to database
        try:
            stats = save_trades(trades_serializable, db_url=TRADES_DB_URL)
            with self.lock:
                self.state.write_stats["saved"] += stats["saved"]
                self.state.write_stats["duplicates"] += stats["duplicates"]
                self.state.write_stats["errors"] += stats["errors"]
                self.state.last_flush_time = time.time()

            logger.info(
                f"Flushed {len(batch)} trades: saved={stats['saved']}, "
                f"duplicates={stats['duplicates']}, errors={stats['errors']}"
            )
        except Exception as e:
            logger.error(f"Failed to flush trades: {e}")
            with self.lock:
                self.state.error_count += 1

    def stop(self):
        """Signal the batch writer to stop after final flush."""
        self.running = False


# ============== WebSocket Subscriber ==============
class TradeSubscriber:
    """WebSocket subscriber with automatic reconnection."""

    def __init__(self, coins: List[str]):
        self.coins = coins
        self.ws_app = None
        self.running = True

    def connect(self):
        """Connect to Hyperliquid WebSocket with subscription."""
        logger.info(f"Connecting to {WS_URL}...")

        def on_open(ws):
            logger.info("WebSocket connected")
            with state_lock:
                state.is_connected = True
                state.reconnect_count = 0  # Reset on successful connection

            # Subscribe to trades for each coin
            for coin in self.coins:
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
            data = json.loads(message)
            if data.get("channel") == "trades":
                trades = data.get("data", [])
                for t in trades:
                    processed = process_trade(t)
                    trade_queue.put(processed)

                    with state_lock:
                        state.total_trades += 1
                        state.last_trade_time = processed["timestamp"]
                        state.queue_depth = trade_queue.qsize()

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
            with state_lock:
                state.error_count += 1

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket closed: {close_status_code}, {close_msg}")
            with state_lock:
                state.is_connected = False

        self.ws_app = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

    def run(self):
        """Run the WebSocket connection with auto-reconnect."""
        retry_count = 0

        while self.running and not state.shutdown_flag:
            try:
                self.connect()
                logger.info("Starting WebSocket connection...")
                self.ws_app.run_forever(ping_interval=20, ping_timeout=10)

                # If we're here, connection closed
                if state.shutdown_flag:
                    logger.info("Shutdown requested, not reconnecting")
                    break

                # Exponential backoff reconnection
                retry_count += 1
                delay_idx = min(retry_count - 1, len(RECONNECT_DELAYS) - 1)
                delay = RECONNECT_DELAYS[delay_idx]

                with state_lock:
                    state.reconnect_count += 1

                logger.warning(f"Connection lost, reconnecting in {delay}s (attempt {retry_count})...")
                time.sleep(delay)

            except Exception as e:
                logger.error(f"Error in WebSocket loop: {e}")
                with state_lock:
                    state.error_count += 1
                time.sleep(5)

        logger.info("TradeSubscriber stopped")

    def stop(self):
        """Stop the subscriber."""
        self.running = False
        if self.ws_app:
            self.ws_app.close()


# ============== Health Check Server ==============
class HealthCheckServer(threading.Thread):
    """HTTP server for health check endpoint."""

    def __init__(self, port: int, state_: SubscriberState, lock_: threading.Lock):
        super().__init__(daemon=True)
        self.port = port
        self.state = state_
        self.lock = lock_
        self.running = True

    def run(self):
        """Run the health check HTTP server."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", self.port))
        server_socket.listen(1)
        server_socket.settimeout(1.0)  # Allow periodic checks for shutdown

        logger.info(f"Health check server listening on port {self.port}")

        while self.running:
            try:
                connection, client_address = server_socket.accept()
                self._handle_request(connection)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Health check server error: {e}")

        server_socket.close()
        logger.info("Health check server stopped")

    def _handle_request(self, connection):
        """Handle a single HTTP request."""
        try:
            request = connection.recv(1024).decode("utf-8")

            if not request.strip():
                return

            # Parse request line
            request_line = request.split("\n")[0]
            method, path, _ = request_line.split(" ")

            if method == "GET" and path == "/health":
                with self.lock:
                    uptime = time.time() - self.state.start_time
                    response_data = {
                        "status": "healthy" if self.state.is_connected else "reconnecting",
                        "uptime_seconds": round(uptime, 2),
                        "total_trades": self.state.total_trades,
                        "queue_depth": self.state.queue_depth,
                        "last_trade_timestamp": self.state.last_trade_time,
                        "error_count": self.state.error_count,
                        "reconnect_count": self.state.reconnect_count,
                        "is_connected": self.state.is_connected,
                        "write_stats": self.state.write_stats,
                    }

                response_body = json.dumps(response_data, indent=2)
                response = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(response_body)}\r\n"
                    f"\r\n"
                    f"{response_body}"
                )
                connection.sendall(response.encode("utf-8"))
            else:
                # 404 for other paths
                response = (
                    f"HTTP/1.1 404 Not Found\r\n"
                    f"Content-Length: 0\r\n"
                    f"\r\n"
                )
                connection.sendall(response.encode("utf-8"))
        except Exception as e:
            logger.error(f"Error handling request: {e}")
        finally:
            connection.close()

    def stop(self):
        """Stop the health check server."""
        self.running = False


# ============== Signal Handlers ==============
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    signal_name = signal.Signals(signum).name
    logger.info(f"Received signal {signal_name}, initiating graceful shutdown...")

    with state_lock:
        state.shutdown_flag = True

    # Signal all components to stop
    logger.info("Flushing remaining trades before shutdown...")


# ============== Main ==============
def main():
    """Main entry point for the continuous trade subscriber."""
    parser = argparse.ArgumentParser(description="Continuous Hyperliquid Trade Subscriber")
    parser.add_argument(
        "--coins",
        type=str,
        default=",".join(COINS),
        help=f"Comma-separated list of coins (default: {','.join(COINS)})"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=BATCH_FLUSH_INTERVAL_SECONDS,
        help=f"Batch flush interval in seconds (default: {BATCH_FLUSH_INTERVAL_SECONDS})"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Maximum trades per batch (default: {BATCH_SIZE})"
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=HEALTH_CHECK_PORT,
        help=f"Health check port (default: {HEALTH_CHECK_PORT})"
    )
    args = parser.parse_args()

    # Apply CLI overrides
    coins_list = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    batch_interval = args.interval
    batch_size = args.batch_size
    health_port = args.health_port

    logger.info("=" * 60)
    logger.info("Starting Continuous Hyperliquid Trade Subscriber")
    logger.info("=" * 60)
    logger.info(f"Configuration:")
    logger.info(f"  Coins: {coins_list}")
    logger.info(f"  Batch interval: {batch_interval}s")
    logger.info(f"  Batch size: {batch_size}")
    logger.info(f"  Health check port: {health_port}")
    logger.info(f"  Database URL: {TRADES_DB_URL}")
    logger.info("=" * 60)

    # Initialize database
    logger.info("Initializing database...")
    init_db(db_url=TRADES_DB_URL)

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start batch writer thread
    batch_writer = BatchWriter(trade_queue, state, state_lock)
    batch_writer.start()

    # Start health check server
    health_server = HealthCheckServer(health_port, state, state_lock)
    health_server.start()

    # Start WebSocket subscriber (main thread)
    subscriber = TradeSubscriber(coins_list)

    try:
        subscriber.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        # Graceful shutdown
        logger.info("Shutting down...")

        with state_lock:
            state.shutdown_flag = True

        # Stop components
        subscriber.stop()
        batch_writer.stop()
        health_server.stop()

        # Wait for batch writer to finish final flush
        batch_writer.join(timeout=30)

        logger.info("=" * 60)
        logger.info("Shutdown complete. Final statistics:")
        logger.info(f"  Total trades received: {state.total_trades}")
        logger.info(f"  Trades saved: {state.write_stats['saved']}")
        logger.info(f"  Duplicates skipped: {state.write_stats['duplicates']}")
        logger.info(f"  Errors: {state.write_stats['errors']}")
        logger.info(f"  Uptime: {time.time() - state.start_time:.2f} seconds")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
