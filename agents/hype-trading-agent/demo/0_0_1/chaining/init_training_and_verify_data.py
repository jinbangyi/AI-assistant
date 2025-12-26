from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import time
from typing import Optional
from dataclasses import dataclass
import json

from hyperliquid.info import Info
from hyperliquid.utils import constants
from loguru import logger
from sqlalchemy import create_engine, String, BigInteger, DateTime, Numeric, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

DEFAULT_DB_URL = "postgresql+psycopg2://prefect:prefect@localhost:5435/prefect"
HOUR = 3600
DAY = 24 * HOUR

DB_URL = os.environ.get("TRADES_DB_URL", DEFAULT_DB_URL)
TRADES_SCHEMA = os.environ.get("TRADES_SCHEMA", "hyperliquid_continuous")
# coin_list = ["BTC", "ETH", "SOL"]
coin_list = ["BTC"]
# data_window_size = 3 * DAY  # 3 days in seconds
data_window_size = 2 * DAY
kline_intervals = ["1m", "5m", "15m", "1h"]

info = Info(constants.MAINNET_API_URL, skip_ws=True)
engine = create_engine(DB_URL, echo=True)

"""
data class
[
    {
        T: int,
        c: float string,
        h: float string,
        i: str,
        l: float string,
        n: int,
        o: float string,
        s: string,
        t: int,
        v: float string
    },
    ...
]
"""


@dataclass
class Candle:
    T: int  # timestamp in ms
    c: str  # close price
    h: str  # high price
    i: str  # interval
    l: str  # low price
    n: int  # number of trades
    o: str  # open price
    s: str  # symbol
    t: int  # timestamp in ms
    v: str  # volume


class Base(DeclarativeBase):
    """Base class for ORM models."""

    pass


class Trade(Base):
    """Trade model for storing Hyperliquid trades."""

    __tablename__ = "trades"
    __table_args__ = {"schema": TRADES_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coin: Mapped[str] = mapped_column(String(50), index=True)
    # Bid = buy, Ask = Short = sell
    side: Mapped[str] = mapped_column(String(10), index=True)  # 'buy' or 'sell'
    price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    size: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    trade_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True)
    tx_hash: Mapped[str] = mapped_column(String(255))
    # example: ["0xf5d889840ff183c72b0b57aac895303eee76f226", "0x5f60dfbf12fa7e9db11629e6a0f1c79009476738"], [taker, maker]
    users: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<Trade(id={self.id}, coin={self.coin}, side={self.side}, price={self.price}, size={self.size})>"


def init_training_klines(end_time: Optional[int] = None):
    candle_dir = Path("./temp-data/candles/")
    candle_dir.mkdir(parents=True, exist_ok=True)

    if end_time is None:
        # last 10 hour's integral point of hour in timestamp
        end_time = int((time.time() - (10 * HOUR)) // HOUR * HOUR)
    start_time = end_time - data_window_size

    logger.info(
        f"Initializing klines from {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(start_time))}"
    )

    for coin in coin_list:
        for interval in kline_intervals:
            # fetch candles and save into temp-data folder
            candles = info.candles_snapshot(
                coin,
                interval=interval,
                startTime=start_time * 1000,
                endTime=end_time * 1000,
            )
            candle_path = (
                candle_dir / f"{coin}_{interval}_candles_{start_time}_{end_time}.json"
            )
            with candle_path.open("w") as f:
                json.dump(candles, f, indent=4)

            logger.debug(
                f"Fetched {len(candles)} candles for {coin.upper()} at interval {interval}"
            )


def init_verify_klines(end_time: Optional[int] = None):
    candle_dir = Path("./temp-data/verify-candles/")
    candle_dir.mkdir(parents=True, exist_ok=True)

    if end_time is None:
        # last 10 hour's integral point of hour in timestamp
        end_time = int(time.time() // HOUR * HOUR)
    start_time = end_time
    end_time = start_time + HOUR * 10

    logger.info(
        f"Initializing verify klines from {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(start_time))}"
    )

    for coin in coin_list:
        for interval in kline_intervals:
            # fetch candles and save into temp-data folder
            candles = info.candles_snapshot(
                coin,
                interval=interval,
                startTime=start_time * 1000,
                endTime=end_time * 1000,
            )
            candle_path = (
                candle_dir / f"{coin}_{interval}_candles_{start_time}_{end_time}.json"
            )
            with candle_path.open("w") as f:
                json.dump(candles, f, indent=4)

            logger.debug(
                f"Fetched {len(candles)} verify candles for {coin.upper()} at interval {interval}"
            )


def init_trades(end_time: Optional[int] = None):
    trades_dir = Path("./temp-data/trades/")
    trades_dir.mkdir(parents=True, exist_ok=True)
    if end_time is None:
        # last 10 hour's integral point of hour in timestamp
        end_time = int((time.time() - (10 * HOUR)) // HOUR * HOUR)
    start_time = end_time - data_window_size
    logger.info(
        f"Initializing trades from {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(start_time))}"
    )

    for coin in coin_list:
        # read from postgres
        with Session(engine) as session:
            # Build query with schema-aware table
            query = session.query(Trade)
            query = query.filter(
                Trade.coin == coin,
                Trade.timestamp >= datetime.fromtimestamp(start_time, tz=timezone.utc),
                Trade.timestamp < datetime.fromtimestamp(end_time, tz=timezone.utc),
            )
            trades = [
                {
                    "id": q.id,
                    "coin": q.coin,
                    "side": q.side,
                    "price": float(q.price) if q.price is not None else None,
                    "size": float(q.size) if q.size is not None else None,
                    "timestamp": q.timestamp.timestamp() if q.timestamp else None,
                    "trade_id": q.trade_id,
                    "tx_hash": q.tx_hash,
                    "taker": q.users[0] if q.users and len(q.users) > 0 else None,
                    "maker": q.users[1] if q.users and len(q.users) > 1 else None,
                }
                for q in query.all()
            ]

        trades_path = trades_dir / f"{coin}_trades_{start_time}_{end_time}.json"
        with trades_path.open("w") as f:
            json.dump(trades, f, indent=4)

        logger.debug(f"Fetched {len(trades)} trades for {coin.upper()}")


def init():
    end_time = int((time.time() - (10 * HOUR)) // HOUR * HOUR)
    # 2025-12-24 21:00:00, 1766581200
    # end_time = 1766581200
    init_training_klines(end_time)
    init_verify_klines(end_time)
    init_trades(end_time)


if __name__ == "__main__":
    init()
