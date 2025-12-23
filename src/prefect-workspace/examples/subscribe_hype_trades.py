from datetime import datetime
from decimal import Decimal
import json
import websocket
import threading
import time

WS_URL = "wss://api.hyperliquid.xyz/ws"

# Coins you want to watch
COINS = ["ETH", "SOL"]


def on_open(ws):
    print("WebSocket connected")

    # Subscribe to trades for each coin
    for coin in COINS:
        sub_msg = {
            "method": "subscribe",
            "subscription": {
                "type": "trades",
                "coin": coin
            }
        }
        ws.send(json.dumps(sub_msg))
        print(f"Subscribed to trades: {coin}")


def on_message(ws, message):
    data = json.loads(message)

    # Trade pushes come on channel == "trades"
    if data.get("channel") == "trades":
        trades = data.get("data", [])
        for t in trades:
            print_trade(t)


def print_trade(t):
    """
    Typical trade object:
    {'coin': 'BTC', 'side': 'sell', 'price': Decimal('88967.0'), 'size': Decimal('0.00012'), 'timestamp': '2025-12-22 07:02:40', 'trade_id': 826910409700179, 'tx_hash': '0xb14aab3a175a8471b2c40431ddc45d020184001fb25da3435513568cd65e5e5c', 'users': ['0xb80efe792bda2ff806174773064657e8ae4f86e7', '0x718a9b47d8991347a818ab19b0f87012a0e84eea']}
    """
    ts_ms = t["time"]
    dt = datetime.fromtimestamp(ts_ms / 1000)
    formatted = dt.strftime('%Y-%m-%dT%H:%M:%S')
    trade = {
        "coin": t["coin"],
        "side": "buy" if t["side"] == "B" else "sell",
        "price": Decimal(t["px"]),
        "size": Decimal(t["sz"]),
        "timestamp": formatted,
        "trade_id": t["tid"],
        "tx_hash": t["hash"],
        "users": t["users"],
    }
    print(trade)


def on_error(ws, error):
    print("WebSocket error:", error)


def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed:", close_status_code, close_msg)


def run():
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    # Run forever (with automatic reconnect logic outside if needed)
    ws.run_forever(ping_interval=20, ping_timeout=10)


if __name__ == "__main__":
    run()
