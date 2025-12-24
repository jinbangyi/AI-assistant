"""
Database module for the Continuous Hyperliquid Trade Subscriber.

Uses a separate schema (hyperliquid_continuous) from the flow-based subscriber
to allow independent operation.
"""

import os
from datetime import datetime, timezone
from typing import Optional
from decimal import Decimal

from sqlalchemy import create_engine, String, BigInteger, DateTime, Numeric, JSON, text, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError
import logging


# PostgreSQL connection URL - can be overridden via environment
DEFAULT_DB_URL = "postgresql+psycopg2://prefect:prefect@postgres:5432/prefect"
DB_URL = os.environ.get("TRADES_DB_URL", DEFAULT_DB_URL)

# Schema name for continuous subscriber tables (separate from flow-based)
TRADES_SCHEMA = os.environ.get("TRADES_SCHEMA", "hyperliquid_continuous")

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
    # example: ["0xf5d889840ff183c72b0b57aac895303eee76f226", "0x5f60dfbf12fa7e9db11629e6a0f1c79009476738"], [buyer, seller]
    users: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<Trade(id={self.id}, coin={self.coin}, side={self.side}, price={self.price}, size={self.size})>"


class AddressTrade(Base):
    """
    Denormalized address-trade index for fast address-centric queries.

    Each trade creates multiple rows (one per address in the users array),
    enabling O(1) lookups for all trades involving a specific address.
    """
    __tablename__ = "address_trades"
    __table_args__ = (
        Index('idx_address_coin_timestamp', 'address', 'coin', 'timestamp'),
        UniqueConstraint('address', 'trade_id', name='uq_address_trade_id'),
        {'schema': TRADES_SCHEMA}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(255), index=True)
    coin: Mapped[str] = mapped_column(String(50), index=True)
    side: Mapped[str] = mapped_column(String(10))  # 'buy' or 'sell'
    price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    size: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    trade_id: Mapped[int] = mapped_column(BigInteger)  # Links to original trade
    tx_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<AddressTrade(id={self.id}, address={self.address}, coin={self.coin}, side={self.side})>"


class AddressFetchMetadata(Base):
    """
    Tracks fetch metadata for lazy backfill of historical user fills.

    Prevents duplicate API calls and tracks backfill status per (address, coin).
    """
    __tablename__ = "address_fetch_metadata"
    __table_args__ = (
        UniqueConstraint('address', 'coin', name='uq_address_coin_fetch'),
        {'schema': TRADES_SCHEMA}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(255), index=True)
    coin: Mapped[str] = mapped_column(String(50))
    last_fetch_timestamp: Mapped[datetime] = mapped_column(DateTime)
    oldest_trade_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_fills_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<AddressFetchMetadata(address={self.address}, coin={self.coin}, total_fills={self.total_fills_count})>"


def get_engine(db_url: str = DB_URL):
    """Get SQLAlchemy engine for PostgreSQL."""
    return create_engine(db_url, echo=False)


def init_db(db_url: str = DB_URL, schema: str = TRADES_SCHEMA) -> None:
    """
    Initialize database - create schema, tables, and triggers.

    Creates the auto-population trigger that fires on trades INSERT to
    denormalize addresses into the address_trades table.
    """
    engine = get_engine(db_url)

    # Create schema if it doesn't exist
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()

    # Create tables
    Base.metadata.create_all(engine)

    # Create trigger function for auto-populating address_trades
    # users is a JSON array of address strings: ["0x...", "0x..."]
    trigger_function_sql = f"""
    CREATE OR REPLACE FUNCTION {schema}.populate_address_trades()
    RETURNS TRIGGER AS $$
    DECLARE
        user_address text;
    BEGIN
        -- Extract each address from the users JSON array
        FOR user_address IN SELECT value::text FROM json_array_elements(NEW.users)
        LOOP
            -- Skip if address is null/empty
            IF user_address IS NOT NULL AND user_address != '' THEN
                INSERT INTO {schema}.address_trades
                    (address, coin, side, price, size, timestamp, trade_id, tx_hash, created_at)
                VALUES
                    (user_address, NEW.coin, NEW.side, NEW.price, NEW.size, NEW.timestamp, NEW.trade_id, NEW.tx_hash, NOW())
                ON CONFLICT (address, trade_id) DO NOTHING;
            END IF;
        END LOOP;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """

    # Create trigger that fires after INSERT on trades
    trigger_sql = f"""
    DROP TRIGGER IF EXISTS trades_insert_trigger ON {schema}.trades;
    CREATE TRIGGER trades_insert_trigger
    AFTER INSERT ON {schema}.trades
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.populate_address_trades();
    """

    with engine.connect() as conn:
        # Create trigger function
        conn.execute(text(trigger_function_sql))
        conn.commit()

        # Create trigger
        conn.execute(text(trigger_sql))
        conn.commit()

    logger.info(f"Database initialized: schema={schema}, tables and triggers created")


def get_session(db_url: str = DB_URL) -> Session:
    """Get a new database session."""
    engine = get_engine(db_url)
    return Session(engine)


def save_trade(trade_data: dict, db_url: str = DB_URL, schema: str = TRADES_SCHEMA) -> Trade:
    """
    Save a single trade to the database.

    Args:
        trade_data: Dictionary containing trade information
        db_url: Database connection URL
        schema: Schema name

    Returns:
        The created Trade object
    """
    # Import Trade with dynamic schema
    from sqlalchemy import Table, MetaData
    # ... (single insert not commonly used in continuous subscriber)

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


def save_trades(trades: list[dict], db_url: str = DB_URL, schema: str = TRADES_SCHEMA) -> dict:
    """
    Save multiple trades to the database with duplicate handling.

    Attempts bulk insert first. If IntegrityError occurs (duplicate trade_id),
    falls back to individual inserts with per-trade try-except to handle duplicates.

    Args:
        trades: List of trade dictionaries
        db_url: Database connection URL
        schema: Schema name

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
    schema: str = TRADES_SCHEMA,
) -> list[Trade]:
    """
    Retrieve trades from the database with optional filtering.

    Args:
        coin: Filter by coin symbol
        side: Filter by side ('buy' or 'sell')
        limit: Maximum number of trades to return
        db_url: Database connection URL
        schema: Schema name

    Returns:
        List of Trade objects
    """
    with get_session(db_url) as session:
        # Build query with schema-aware table
        query = session.query(Trade)

        if coin:
            query = query.filter(Trade.coin == coin)
        if side:
            query = query.filter(Trade.side == side)

        return query.order_by(Trade.timestamp.desc()).limit(limit).all()


def get_trade_stats(db_url: str = DB_URL, schema: str = TRADES_SCHEMA) -> dict:
    """
    Get trade statistics from the database.

    Args:
        db_url: Database connection URL
        schema: Schema name

    Returns:
        Dictionary with trade statistics
    """
    with get_session(db_url) as session:
        total_trades = session.query(Trade).count()

        trades_by_coin = {}
        for coin in session.query(Trade.coin).distinct():
            count = session.query(Trade).filter(Trade.coin == coin[0]).count()
            trades_by_coin[coin[0]] = count

        trades_by_side = {
            "buy": session.query(Trade).filter(Trade.side == "buy").count(),
            "sell": session.query(Trade).filter(Trade.side == "sell").count(),
        }

        return {
            "total_trades": total_trades,
            "trades_by_coin": trades_by_coin,
            "trades_by_side": trades_by_side,
        }


def get_address_trades(
    address: str,
    coin: Optional[str] = None,
    limit: int = 100,
    db_url: str = DB_URL,
    schema: str = TRADES_SCHEMA,
) -> list[AddressTrade]:
    """
    Retrieve address trades from the database with optional filtering.

    Args:
        address: Ethereum address to query
        coin: Optional coin filter
        limit: Maximum number of trades to return
        db_url: Database connection URL
        schema: Schema name

    Returns:
        List of AddressTrade objects ordered by timestamp descending
    """
    with get_session(db_url) as session:
        query = session.query(AddressTrade).filter(AddressTrade.address == address)

        if coin:
            query = query.filter(AddressTrade.coin == coin)

        return query.order_by(AddressTrade.timestamp.desc()).limit(limit).all()


def update_fetch_metadata(
    address: str,
    coin: str,
    last_fetch_timestamp: datetime,
    oldest_trade_timestamp: Optional[datetime],
    total_fills_count: int,
    db_url: str = DB_URL,
) -> None:
    """
    Update fetch metadata for an address/coin pair.

    Performs an upsert using ON CONFLICT.

    Args:
        address: Ethereum address
        coin: Coin symbol
        last_fetch_timestamp: When the fetch occurred
        oldest_trade_timestamp: Oldest trade timestamp in the batch
        total_fills_count: Total number of fills fetched
        db_url: Database connection URL
    """
    upsert_sql = f"""
    INSERT INTO {TRADES_SCHEMA}.address_fetch_metadata
        (address, coin, last_fetch_timestamp, oldest_trade_timestamp, total_fills_count, last_updated)
    VALUES
        (:address, :coin, :last_fetch_timestamp, :oldest_trade_timestamp, :total_fills_count, NOW())
    ON CONFLICT (address, coin)
    DO UPDATE SET
        last_fetch_timestamp = EXCLUDED.last_fetch_timestamp,
        oldest_trade_timestamp = LEAST(address_fetch_metadata.oldest_trade_timestamp, EXCLUDED.oldest_trade_timestamp),
        total_fills_count = address_fetch_metadata.total_fills_count + EXCLUDED.total_fills_count,
        last_updated = NOW();
    """

    with get_engine(db_url).connect() as conn:
        conn.execute(
            text(upsert_sql),
            {
                "address": address,
                "coin": coin,
                "last_fetch_timestamp": last_fetch_timestamp,
                "oldest_trade_timestamp": oldest_trade_timestamp,
                "total_fills_count": total_fills_count,
            }
        )
        conn.commit()


def query_address_trades(
    address: str,
    coin: str,
    min_trades: int = 100,
    db_url: str = DB_URL,
    schema: str = TRADES_SCHEMA,
) -> list[dict]:
    """
    Query address trades with lazy backfill from Hyperliquid API.

    This function provides a unified interface for querying address trades:
    1. First queries the local address_trades table
    2. If result count < min_trades, fetches from Hyperliquid REST API
    3. Inserts fetched trades to trades table (trigger populates address_trades)
    4. Updates fetch metadata
    5. Returns complete results sorted by timestamp DESC

    Args:
        address: Ethereum address (0x...)
        coin: Coin symbol (e.g., "ETH", "SOL")
        min_trades: Minimum trades desired; triggers API backfill if not met
        db_url: Database connection URL
        schema: Schema name

    Returns:
        List of trade dictionaries with keys:
        - address: The queried address
        - coin: Coin symbol
        - side: 'buy' or 'sell'
        - price: Decimal price
        - size: Decimal size
        - timestamp: ISO format timestamp
        - trade_id: Unique trade identifier
        - tx_hash: Transaction hash

    Raises:
        ValueError: On invalid address format
        HyperliquidAPIError: On API errors during backfill

    Example:
        >>> trades = query_address_trades("0x1234...", "ETH", min_trades=50)
        >>> print(f"Found {len(trades)} trades")
    """
    # Validate address
    if not address or not address.startswith("0x"):
        raise ValueError(f"Invalid address format: {address}")

    coin = coin.upper()

    # Step 1: Query local address_trades table
    logger.info(f"Querying local trades: address={address}, coin={coin}")
    local_trades = get_address_trades(address, coin, limit=min_trades * 2, db_url=db_url, schema=schema)

    if len(local_trades) >= min_trades:
        logger.info(f"Found {len(local_trades)} local trades, no backfill needed")
    else:
        logger.info(f"Only {len(local_trades)} local trades (need {min_trades}), triggering backfill")

        # Step 2: Fetch from Hyperliquid API
        try:
            # Import here to avoid circular dependency
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from lib.hyperliquid_api import HyperliquidClient

            with HyperliquidClient() as client:
                user_fills = client.fetch_user_fills(address, coin=coin)

                if not user_fills:
                    logger.info(f"No fills found for {address}/{coin} on Hyperliquid")
                else:
                    # Convert to trade dictionaries
                    trades_to_insert = [fill.to_trade_dict() for fill in user_fills]

                    # Step 3: Insert to trades table (trigger populates address_trades)
                    stats = save_trades(trades_to_insert, db_url=db_url, schema=schema)
                    logger.info(
                        f"Backfill complete: saved={stats['saved']}, "
                        f"duplicates={stats['duplicates']}, errors={stats['errors']}"
                    )

                    # Step 4: Update fetch metadata
                    oldest_timestamp = min(
                        (f.timestamp_ms for f in user_fills),
                        default=None
                    )
                    oldest_dt = datetime.fromtimestamp(oldest_timestamp / 1000, tz=timezone.utc) if oldest_timestamp else None

                    update_fetch_metadata(
                        address=address,
                        coin=coin,
                        last_fetch_timestamp=datetime.now(timezone.utc),
                        oldest_trade_timestamp=oldest_dt,
                        total_fills_count=stats["saved"],
                        db_url=db_url,
                    )

                    # Re-query local trades to get fresh results
                    local_trades = get_address_trades(address, coin, limit=min_trades * 2, db_url=db_url, schema=schema)

        except ImportError:
            logger.error("Failed to import HyperliquidClient, skipping backfill")
        except Exception as e:
            logger.error(f"Backfill failed: {e}")
            # Continue with local data

    # Convert AddressTrade objects to dictionaries
    result = []
    for at in local_trades:
        result.append({
            "address": at.address,
            "coin": at.coin,
            "side": at.side,
            "price": str(at.price),
            "size": str(at.size),
            "timestamp": at.timestamp.isoformat(),
            "trade_id": at.trade_id,
            "tx_hash": at.tx_hash,
        })

    # Sort by timestamp descending
    result.sort(key=lambda x: x["timestamp"], reverse=True)

    return result


def get_fetch_metadata(
    address: str,
    coin: Optional[str] = None,
    db_url: str = DB_URL,
) -> list[AddressFetchMetadata]:
    """
    Get fetch metadata for an address.

    Args:
        address: Ethereum address
        coin: Optional coin filter
        db_url: Database connection URL

    Returns:
        List of AddressFetchMetadata objects
    """
    with get_session(db_url) as session:
        query = session.query(AddressFetchMetadata).filter(AddressFetchMetadata.address == address)

        if coin:
            query = query.filter(AddressFetchMetadata.coin == coin)

        return query.order_by(AddressFetchMetadata.last_updated.desc()).all()


def save_address_trades(
    address_trades_list: list[dict],
    db_url: str = DB_URL,
    batch_size: int = 1000,
) -> dict:
    """
    Save multiple address_trades to the database with batch insert and duplicate handling.

    Attempts bulk insert first. If IntegrityError occurs (duplicate address, trade_id),
    falls back to individual inserts with per-record try-except to handle duplicates.

    Args:
        address_trades_list: List of address_trade dictionaries
        db_url: Database connection URL
        batch_size: Maximum records per batch insert

    Returns:
        Dictionary with insertion statistics:
        - saved: Number of address_trades saved
        - duplicates: Number of duplicate address_trades skipped
        - errors: Number of unexpected errors
    """
    if not address_trades_list:
        return {"saved": 0, "duplicates": 0, "errors": 0}

    stats = {"saved": 0, "duplicates": 0, "errors": 0}

    # Process in batches for efficiency
    for i in range(0, len(address_trades_list), batch_size):
        batch = address_trades_list[i:i + batch_size]

        # Try bulk insert first for efficiency
        try:
            with get_session(db_url) as session:
                address_trade_objects = []
                for at in batch:
                    address_trade = AddressTrade(
                        address=at["address"],
                        coin=at["coin"],
                        side=at["side"],
                        price=Decimal(str(at["price"])),
                        size=Decimal(str(at["size"])),
                        timestamp=at["timestamp"],
                        trade_id=at["trade_id"],
                        tx_hash=at["tx_hash"],
                        created_at=at.get("created_at", datetime.now(timezone.utc)),
                    )
                    address_trade_objects.append(address_trade)

                session.add_all(address_trade_objects)
                session.commit()
                stats["saved"] += len(address_trade_objects)
        except IntegrityError:
            # Batch had duplicates - fall back to individual inserts
            logger.info(f"Batch insert failed due to duplicates, attempting individual inserts for {len(batch)} records")

            # Fallback: individual inserts with per-record error handling
            for at in batch:
                try:
                    with get_session(db_url) as session:
                        address_trade = AddressTrade(
                            address=at["address"],
                            coin=at["coin"],
                            side=at["side"],
                            price=Decimal(str(at["price"])),
                            size=Decimal(str(at["size"])),
                            timestamp=at["timestamp"],
                            trade_id=at["trade_id"],
                            tx_hash=at["tx_hash"],
                            created_at=at.get("created_at", datetime.now(timezone.utc)),
                        )
                        session.add(address_trade)
                        session.commit()
                        stats["saved"] += 1
                except IntegrityError:
                    # Duplicate (address, trade_id) - skip
                    logger.debug(
                        f"Duplicate address_trade skipped: address={at['address']}, "
                        f"trade_id={at['trade_id']}, coin={at['coin']}"
                    )
                    stats["duplicates"] += 1
                except Exception as e:
                    logger.error(f"Error saving address_trade (address={at['address']}, trade_id={at['trade_id']}): {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Unexpected error in batch insert: {e}")
            stats["errors"] += len(batch)

    return stats


def populate_address_trades_from_existing(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db_url: str = DB_URL,
    batch_size: int = 5000,
) -> dict:
    """
    Populate address_trades table from existing trades data.

    This is useful for backfilling the address_trades table for trades that
    existed before the auto-population trigger was created.

    Args:
        start_time: Optional start time filter (inclusive)
        end_time: Optional end time filter (inclusive)
        db_url: Database connection URL
        batch_size: Number of address_trades to batch per insert

    Returns:
        Dictionary with statistics:
        - processed: Number of trades processed
        - inserted: Number of address_trades rows inserted
        - skipped: Number of duplicates skipped
        - errors: Number of errors

    Example:
        >>> from datetime import datetime, timedelta
        >>> start = datetime.now(timezone.utc) - timedelta(days=7)
        >>> result = populate_address_trades_from_existing(start_time=start)
        >>> print(f"Processed {result['processed']} trades")
    """
    logger.info(f"Populating address_trades from existing trades: start={start_time}, end={end_time}")

    stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

    with get_session(db_url) as session:
        # Build query for trades within time range
        query = session.query(Trade)

        if start_time:
            query = query.filter(Trade.timestamp >= start_time)
        if end_time:
            query = query.filter(Trade.timestamp <= end_time)

        trades = query.all()
        stats["processed"] = len(trades)

        logger.info(f"Found {len(trades)} trades to process")

        # Collect all address_trades to insert
        all_address_trades = []

        for trade in trades:
            # Extract addresses from users JSON array
            # users is a JSON array of address strings: ["0x...", "0x..."]
            users = trade.users if isinstance(trade.users, list) else []

            for address in users:
                # Skip invalid addresses (handle both strings and any unexpected types)
                if not isinstance(address, str):
                    continue
                address = address.strip()
                if not address or not address.startswith("0x"):
                    continue

                all_address_trades.append({
                    "address": address,
                    "coin": trade.coin,
                    "side": trade.side,
                    "price": str(trade.price),
                    "size": str(trade.size),
                    "timestamp": trade.timestamp,
                    "trade_id": trade.trade_id,
                    "tx_hash": trade.tx_hash,
                    "created_at": datetime.now(timezone.utc),
                })

        logger.info(f"Collected {len(all_address_trades)} address_trades to insert")

        # Batch insert all address_trades
        insert_stats = save_address_trades(all_address_trades, db_url=db_url, batch_size=batch_size)
        stats["inserted"] = insert_stats["saved"]
        stats["skipped"] = insert_stats["duplicates"]
        stats["errors"] = insert_stats["errors"]

    logger.info(
        f"Population complete: processed={stats['processed']}, "
        f"inserted={stats['inserted']}, skipped={stats['skipped']}, errors={stats['errors']}"
    )

    return stats


