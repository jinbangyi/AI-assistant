"""
Database module for storing Hyperliquid trades.
Uses PostgreSQL (same as Prefect) with auto-creation of tables.
Data can be viewed in pgAdmin web UI.
"""

import os
from datetime import datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import create_engine, String, BigInteger, DateTime, Numeric, JSON, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError
import logging


# PostgreSQL connection URL - can be overridden via environment
# Defaults to the same PostgreSQL as Prefect
DEFAULT_DB_URL = "postgresql+psycopg2://prefect:prefect@postgres:5432/prefect"
DB_URL = os.environ.get("TRADES_DB_URL", DEFAULT_DB_URL)

# Schema name for trades tables (separate from Prefect tables)
TRADES_SCHEMA = "hyperliquid"

# Configure logger
logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for ORM models."""
    pass


class Trade(Base):
    """Trade model for storing Hyperliquid trades."""
    __tablename__ = "trades"
    __table_args__ = {'schema': TRADES_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coin: Mapped[str] = mapped_column(String(50), index=True)
    side: Mapped[str] = mapped_column(String(10), index=True)  # 'buy' or 'sell'
    price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    size: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    trade_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True)
    tx_hash: Mapped[str] = mapped_column(String(255))
    users: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade(id={self.id}, coin={self.coin}, side={self.side}, price={self.price}, size={self.size})>"


def get_engine(db_url: str = DB_URL):
    """Get SQLAlchemy engine for PostgreSQL."""
    return create_engine(db_url, echo=False)


def init_db(db_url: str = DB_URL) -> None:
    """Initialize database - create schema and tables."""
    engine = get_engine(db_url)

    # Create schema if it doesn't exist
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TRADES_SCHEMA}"))
        conn.commit()

    # Create tables
    Base.metadata.create_all(engine)


def get_session(db_url: str = DB_URL) -> Session:
    """Get a new database session."""
    engine = get_engine(db_url)
    return Session(engine)


def save_trade(trade_data: dict, db_url: str = DB_URL) -> Trade:
    """
    Save a single trade to the database.

    Args:
        trade_data: Dictionary containing trade information
        db_url: Database connection URL

    Returns:
        The created Trade object
    """
    with get_session(db_url) as session:
        trade = Trade(
            coin=trade_data["coin"],
            side=trade_data["side"],
            price=Decimal(str(trade_data["price"])),
            size=Decimal(str(trade_data["size"])),
            timestamp=datetime.fromisoformat(trade_data["timestamp"]),
            trade_id=trade_data["trade_id"],
            tx_hash=trade_data["tx_hash"],
            users=trade_data["users"],
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade


def save_trades(trades: list[dict], db_url: str = DB_URL) -> dict:
    """
    Save multiple trades to the database with duplicate handling.

    Attempts bulk insert first. If IntegrityError occurs (duplicate trade_id),
    falls back to individual inserts with per-trade try-except to handle duplicates.

    Args:
        trades: List of trade dictionaries
        db_url: Database connection URL

    Returns:
        Dictionary with insertion statistics:
        - saved: Number of trades saved
        - duplicates: Number of duplicate trades skipped
        - errors: Number of unexpected errors
    """
    if not trades:
        return {"saved": 0, "duplicates": 0, "errors": 0}

    stats = {"saved": 0, "duplicates": 0, "errors": 0}

    # Try bulk insert first for efficiency
    try:
        with get_session(db_url) as session:
            trade_objects = []
            for t in trades:
                trade = Trade(
                    coin=t["coin"],
                    side=t["side"],
                    price=Decimal(str(t["price"])),
                    size=Decimal(str(t["size"])),
                    timestamp=datetime.fromisoformat(t["timestamp"]),
                    trade_id=t["trade_id"],
                    tx_hash=t["tx_hash"],
                    users=t["users"],
                )
                trade_objects.append(trade)

            session.add_all(trade_objects)
            session.commit()
            stats["saved"] = len(trade_objects)
            return stats
    except IntegrityError:
        # Duplicate detected - fall back to individual inserts
        logger.info(f"Bulk insert failed due to duplicate, attempting individual inserts for {len(trades)} trades")

    # Fallback: individual inserts with per-trade error handling
    for t in trades:
        try:
            with get_session(db_url) as session:
                trade = Trade(
                    coin=t["coin"],
                    side=t["side"],
                    price=Decimal(str(t["price"])),
                    size=Decimal(str(t["size"])),
                    timestamp=datetime.fromisoformat(t["timestamp"]),
                    trade_id=t["trade_id"],
                    tx_hash=t["tx_hash"],
                    users=t["users"],
                )
                session.add(trade)
                session.commit()
                stats["saved"] += 1
        except IntegrityError:
            # Log duplicate with key identifiers
            logger.debug(
                f"Duplicate trade skipped: coin={t['coin']}, trade_id={t['trade_id']}, "
                f"tx_hash={t.get('tx_hash', 'N/A')}, timestamp={t.get('timestamp', 'N/A')}"
            )
            stats["duplicates"] += 1
        except Exception as e:
            logger.error(f"Error saving trade (coin={t['coin']}, trade_id={t['trade_id']}): {e}")
            stats["errors"] += 1

    return stats


def get_trades(
    coin: Optional[str] = None,
    side: Optional[str] = None,
    limit: int = 100,
    db_url: str = DB_URL,
) -> list[Trade]:
    """
    Retrieve trades from the database with optional filtering.

    Args:
        coin: Filter by coin symbol
        side: Filter by side ('buy' or 'sell')
        limit: Maximum number of trades to return
        db_url: Database connection URL

    Returns:
        List of Trade objects
    """
    with get_session(db_url) as session:
        query = session.query(Trade)

        if coin:
            query = query.filter(Trade.coin == coin)
        if side:
            query = query.filter(Trade.side == side)

        return query.order_by(Trade.timestamp.desc()).limit(limit).all()


def get_trade_stats(db_url: str = DB_URL) -> dict:
    """
    Get trade statistics from the database.

    Args:
        db_url: Database connection URL

    Returns:
        Dictionary with trade statistics
    """
    with get_session(db_url) as session:
        total_trades = session.query(Trade).count()

        trades_by_coin = {}
        for coin in session.query(Trade.coin).distinct():
            count = session.query(Trade).filter(Trade.coin == coin).count()
            trades_by_coin[coin] = count

        trades_by_side = {
            "buy": session.query(Trade).filter(Trade.side == "buy").count(),
            "sell": session.query(Trade).filter(Trade.side == "sell").count(),
        }

        return {
            "total_trades": total_trades,
            "trades_by_coin": trades_by_coin,
            "trades_by_side": trades_by_side,
        }
