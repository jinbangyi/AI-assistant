"""
Prefect Flows for Backfilling Hyperliquid Address Trade History.

This module provides two flows:
1. backfill_address_flow - Fetch historical fills from Hyperliquid REST API
2. populate_address_trades_flow - Populate address_trades from existing trades

IMPORTANT API LIMITATION:
    Hyperliquid API hard limit: only 10,000 most recent fills available,
    older data is permanently inaccessible via the API.

Usage:
    # Fetch from Hyperliquid API
    python flows/backfill_address_history.py fetch --address 0x1234... --coin ETH

    # Populate from existing trades (by time range)
    python flows/backfill_address_history.py populate --start-days 7

    # Deploy to Prefect
    prefect deploy flows/backfill_address_history.py:backfill_address_flow \
        --name backfill-address-history --pool hyperliquid-pool
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import argparse
from typing import Optional

# Prefect imports
from prefect import flow, task, get_run_logger
from prefect.artifacts import create_markdown_artifact

# Local imports
from db_continuous import save_trades, update_fetch_metadata, TRADES_SCHEMA, DB_URL
from lib.hyperliquid_api import HyperliquidClient, UserFill

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    """Result of a backfill operation."""
    address: str
    coin: str
    total_fetched: int
    saved: int
    duplicates: int
    errors: int
    oldest_timestamp_ms: Optional[int]
    newest_timestamp_ms: Optional[int]
    duration_seconds: float


@task(name="fetch_user_fills")
def fetch_user_fills_task(
    address: str,
    coin: Optional[str] = None,
) -> list[UserFill]:
    """
    Fetch user fills from Hyperliquid API.

    Args:
        address: Ethereum address (0x...)
        coin: Optional coin filter

    Returns:
        List of UserFill objects

    Raises:
        ValueError: On invalid address format
        HyperliquidAPIError: On API errors
    """
    task_logger = get_run_logger()
    task_logger.info(f"Fetching fills for address={address}, coin={coin or 'all'}")

    with HyperliquidClient() as client:
        fills = client.fetch_user_fills(address, coin=coin)

        task_logger.info(f"Fetched {len(fills)} fills from Hyperliquid API")
        if len(fills) >= 10000:
            task_logger.warning(
                "Reached Hyperliquid API hard limit of 10,000 fills. "
                "Older fills are permanently inaccessible."
            )

        return fills


@task(name="save_trades_batch")
def save_trades_batch_task(
    fills: list[UserFill],
    db_url: str = DB_URL,
    db_schema: str = TRADES_SCHEMA,
) -> dict:
    """
    Save a batch of fills to the database.

    Args:
        fills: List of UserFill objects
        db_url: Database connection URL
        db_schema: Schema name

    Returns:
        Dictionary with save statistics
    """
    task_logger = get_run_logger()

    if not fills:
        return {"saved": 0, "duplicates": 0, "errors": 0}

    # Convert to trade dictionaries
    trades = [fill.to_trade_dict() for fill in fills]

    task_logger.info(f"Saving {len(trades)} trades to database")

    stats = save_trades(trades, db_url=db_url, schema=db_schema)

    task_logger.info(
        f"Save complete: saved={stats['saved']}, "
        f"duplicates={stats['duplicates']}, errors={stats['errors']}"
    )

    return stats


@task(name="update_fetch_metadata")
def update_fetch_metadata_task(
    address: str,
    coin: str,
    total_saved: int,
    oldest_timestamp_ms: Optional[int],
    db_url: str = DB_URL,
) -> None:
    """
    Update fetch metadata for an address/coin pair.

    Args:
        address: Ethereum address
        coin: Coin symbol
        total_saved: Number of new fills saved
        oldest_timestamp_ms: Oldest trade timestamp in milliseconds
        db_url: Database connection URL
    """
    task_logger = get_run_logger()

    oldest_dt = None
    if oldest_timestamp_ms:
        oldest_dt = datetime.fromtimestamp(oldest_timestamp_ms / 1000, tz=timezone.utc)

    update_fetch_metadata(
        address=address,
        coin=coin,
        last_fetch_timestamp=datetime.now(timezone.utc),
        oldest_trade_timestamp=oldest_dt,
        total_fills_count=total_saved,
        db_url=db_url,
    )

    task_logger.info(f"Updated metadata for {address}/{coin}")


@task(name="create_backfill_report")
def create_backfill_report_task(result: BackfillResult) -> str:
    """
    Create a markdown report artifact for the backfill operation.

    Args:
        result: BackfillResult object

    Returns:
        Markdown report content
    """
    oldest = "N/A"
    newest = "N/A"
    if result.oldest_timestamp_ms:
        oldest = datetime.fromtimestamp(result.oldest_timestamp_ms / 1000, tz=timezone.utc).isoformat()
    if result.newest_timestamp_ms:
        newest = datetime.fromtimestamp(result.newest_timestamp_ms / 1000, tz=timezone.utc).isoformat()

    report = f"""# Hyperliquid Address Backfill Report

## Summary
- **Address:** `{result.address}`
- **Coin:** `{result.coin}`
- **Duration:** {result.duration_seconds:.2f} seconds

## Results
| Metric | Count |
|--------|-------|
| Total Fetched | {result.total_fetched} |
| Saved | {result.saved} |
| Duplicates | {result.duplicates} |
| Errors | {result.errors} |

## Time Range
- **Oldest:** {oldest}
- **Newest:** {newest}

## Success Rate
- {result.saved / result.total_fetched * 100:.1f}% new trades
- {result.duplicates / result.total_fetched * 100:.1f}% duplicates

---
*Generated at {datetime.now(timezone.utc).isoformat()}*
"""

    create_markdown_artifact(
        markdown=report,
        key=f"backfill-report-{result.address[:8]}-{result.coin}"
    )

    return report


@flow(
    name="backfill_address_history",
    description="Fetch historical Hyperliquid fills for an address and store in database",
)
def backfill_address_flow(
    address: str,
    coin: Optional[str] = None,
    limit: int = 2000,
    db_url: str = DB_URL,
    db_schema: str = TRADES_SCHEMA,
) -> BackfillResult:
    """
    Main flow for backfilling address trade history.

    This flow:
    1. Fetches historical fills from Hyperliquid REST API
    2. Batch inserts to database with duplicate handling
    3. Updates fetch metadata
    4. Logs statistics and creates report artifact

    Args:
        address: Ethereum address (0x...)
        coin: Optional coin filter (e.g., "ETH", "SOL")
        limit: Maximum number of fills to process per batch (default: 2000)
        db_url: Database connection URL
        db_schema: Database schema name

    Returns:
        BackfillResult with operation statistics

    Raises:
        ValueError: On invalid address format
        HyperliquidAPIError: On API errors

    Example:
        >>> result = backfill_address_flow("0x1234...", "ETH")
        >>> print(f"Saved {result.saved} trades")
    """
    flow_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)

    flow_logger.info("=" * 60)
    flow_logger.info("Starting Hyperliquid Address History Backfill")
    flow_logger.info("=" * 60)
    flow_logger.info(f"Address: {address}")
    flow_logger.info(f"Coin: {coin or 'All coins'}")
    flow_logger.info(f"Limit: {limit}")
    flow_logger.info(f"Schema: {db_schema}")
    flow_logger.info("=" * 60)

    # Validate address
    if not address or not address.startswith("0x"):
        raise ValueError(f"Invalid address format: {address}")

    if coin:
        coin = coin.upper()

    # Step 1: Fetch fills from API
    fills = fetch_user_fills_task(address, coin)

    if not fills:
        flow_logger.warning(f"No fills found for {address}/{coin or 'all'}")
        return BackfillResult(
            address=address,
            coin=coin or "ALL",
            total_fetched=0,
            saved=0,
            duplicates=0,
            errors=0,
            oldest_timestamp_ms=None,
            newest_timestamp_ms=None,
            duration_seconds=0,
        )

    # Calculate time range
    timestamps = [f.timestamp_ms for f in fills if f.timestamp_ms]
    oldest_timestamp_ms = min(timestamps) if timestamps else None
    newest_timestamp_ms = max(timestamps) if timestamps else None

    # Step 2: Save in batches
    all_stats = {"saved": 0, "duplicates": 0, "errors": 0}

    for i in range(0, len(fills), limit):
        batch = fills[i:i + limit]
        flow_logger.info(f"Processing batch {i // limit + 1}: {len(batch)} fills")

        stats = save_trades_batch_task(batch, db_url, db_schema)
        all_stats["saved"] += stats["saved"]
        all_stats["duplicates"] += stats["duplicates"]
        all_stats["errors"] += stats["errors"]

    # Step 3: Update metadata
    if all_stats["saved"] > 0:
        update_fetch_metadata_task(
            address=address,
            coin=coin or "ALL",
            total_saved=all_stats["saved"],
            oldest_timestamp_ms=oldest_timestamp_ms,
            db_url=db_url,
        )

    # Calculate duration
    duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()

    # Create result
    result = BackfillResult(
        address=address,
        coin=coin or "ALL",
        total_fetched=len(fills),
        saved=all_stats["saved"],
        duplicates=all_stats["duplicates"],
        errors=all_stats["errors"],
        oldest_timestamp_ms=oldest_timestamp_ms,
        newest_timestamp_ms=newest_timestamp_ms,
        duration_seconds=duration_seconds,
    )

    # Step 4: Create report
    report = create_backfill_report_task(result)

    # Log final summary
    flow_logger.info("=" * 60)
    flow_logger.info("Backfill Complete")
    flow_logger.info("=" * 60)
    flow_logger.info(f"Total fetched: {result.total_fetched}")
    flow_logger.info(f"Saved: {result.saved}")
    flow_logger.info(f"Duplicates: {result.duplicates}")
    flow_logger.info(f"Errors: {result.errors}")
    flow_logger.info(f"Duration: {duration_seconds:.2f}s")
    flow_logger.info("=" * 60)

    return result


@dataclass
class PopulateResult:
    """Result of a populate operation."""
    processed: int
    inserted: int
    skipped: int
    errors: int
    duration_seconds: float


@task(name="populate_address_trades")
def populate_address_trades_task(
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    db_url: str = DB_URL,
    batch_size: int = 2000,
) -> dict:
    """
    Populate address_trades from existing trades.

    Args:
        start_time: Start time filter (inclusive)
        end_time: End time filter (inclusive)
        db_url: Database connection URL
        batch_size: Batch size for insert optimization

    Returns:
        Dictionary with statistics
    """
    from db_continuous import init_db, populate_address_trades_from_existing

    task_logger = get_run_logger()
    task_logger.info(f"Populating address_trades: start={start_time}, end={end_time}, batch_size={batch_size}")

    # Ensure tables and triggers exist
    task_logger.info("Initializing database schema...")
    init_db(db_url=db_url)

    return populate_address_trades_from_existing(
        start_time=start_time,
        end_time=end_time,
        db_url=db_url,
        batch_size=batch_size,
    )


@task(name="create_populate_report")
def create_populate_report_task(
    result: PopulateResult,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
) -> str:
    """
    Create a markdown report artifact for the populate operation.

    Args:
        result: PopulateResult object
        start_time: Start time used
        end_time: End time used

    Returns:
        Markdown report content
    """
    start_str = start_time.isoformat() if start_time else "N/A"
    end_str = end_time.isoformat() if end_time else "N/A"

    report = f"""# Address Trades Populate Report

## Summary
- **Time Range:** {start_str} to {end_str}
- **Duration:** {result.duration_seconds:.2f} seconds

## Results
| Metric | Count |
|--------|-------|
| Trades Processed | {result.processed} |
| Address Trades Inserted | {result.inserted} |
| Duplicates Skipped | {result.skipped} |
| Errors | {result.errors} |

## Efficiency
- {result.processed / result.duration_seconds:.1f} trades/second
- {result.inserted / result.processed * 100:.1f}% new address trades

---
*Generated at {datetime.now(timezone.utc).isoformat()}*
"""

    create_markdown_artifact(
        markdown=report,
        key=f"populate-report-{start_time.isoformat() if start_time else 'all'}"
    )

    return report


@flow(
    name="populate_address_trades",
    description="Populate address_trades table from existing trades within a time range",
)
def populate_address_trades_flow(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    start_days_ago: Optional[int] = None,
    end_days_ago: Optional[int] = None,
    db_url: str = DB_URL,
    batch_size: int = 2000,
) -> PopulateResult:
    """
    Flow to populate address_trades from existing trades.

    This flow processes trades within the specified time range and creates
    corresponding entries in the address_trades table by extracting addresses
    from the users JSON array.

    Args:
        start_time: Start time filter (inclusive)
        end_time: End time filter (inclusive)
        start_days_ago: Alternative to start_time - days ago from now
        end_days_ago: Alternative to end_time - days ago from now
        db_url: Database connection URL

    Returns:
        PopulateResult with statistics

    Example:
        >>> # Populate last 7 days
        >>> result = populate_address_trades_flow(start_days_ago=7)

        >>> # Populate specific range
        >>> from datetime import datetime, timezone
        >>> result = populate_address_trades_flow(
        ...     start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ...     end_time=datetime(2024, 1, 31, tzinfo=timezone.utc)
        ... )
    """
    flow_logger = get_run_logger()
    start_time_flow = datetime.now(timezone.utc)

    # Convert days_ago to datetime
    if start_days_ago is not None and start_time is None:
        start_time = datetime.now(timezone.utc) - __import__('datetime').timedelta(days=start_days_ago)
    if end_days_ago is not None and end_time is None:
        end_time = datetime.now(timezone.utc) - __import__('datetime').timedelta(days=end_days_ago)

    flow_logger.info("=" * 60)
    flow_logger.info("Populating Address Trades from Existing Trades")
    flow_logger.info("=" * 60)
    flow_logger.info(f"Start time: {start_time or 'unlimited'}")
    flow_logger.info(f"End time: {end_time or 'unlimited'}")
    flow_logger.info("=" * 60)

    # Run populate task
    stats = populate_address_trades_task(start_time, end_time, db_url, batch_size)

    # Calculate duration
    duration_seconds = (datetime.now(timezone.utc) - start_time_flow).total_seconds()

    # Create result
    result = PopulateResult(
        processed=stats["processed"],
        inserted=stats["inserted"],
        skipped=stats["skipped"],
        errors=stats["errors"],
        duration_seconds=duration_seconds,
    )

    # Create report
    create_populate_report_task(result, start_time, end_time)

    # Log final summary
    flow_logger.info("=" * 60)
    flow_logger.info("Population Complete")
    flow_logger.info("=" * 60)
    flow_logger.info(f"Trades processed: {result.processed}")
    flow_logger.info(f"Address trades inserted: {result.inserted}")
    flow_logger.info(f"Duplicates skipped: {result.skipped}")
    flow_logger.info(f"Errors: {result.errors}")
    flow_logger.info(f"Duration: {duration_seconds:.2f}s")
    flow_logger.info("=" * 60)

    return result


def main():
    """CLI entry point for running backfill flows locally."""
    parser = argparse.ArgumentParser(
        description="Backfill Hyperliquid address trade history"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Fetch command - fetch from Hyperliquid API
    fetch_parser = subparsers.add_parser("fetch", help="Fetch fills from Hyperliquid REST API")
    fetch_parser.add_argument(
        "--address",
        type=str,
        required=True,
        help="Ethereum address (0x...)"
    )
    fetch_parser.add_argument(
        "--coin",
        type=str,
        default=None,
        help="Coin filter (e.g., ETH, SOL). If not specified, fetches all coins."
    )
    fetch_parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Batch size limit (default: 2000)"
    )

    # Populate command - populate from existing trades
    populate_parser = subparsers.add_parser("populate", help="Populate address_trades from existing trades")
    populate_parser.add_argument(
        "--start-days",
        type=int,
        default=None,
        help="Start time as days ago from now (e.g., 7 for last 7 days)"
    )
    populate_parser.add_argument(
        "--end-days",
        type=int,
        default=None,
        help="End time as days ago from now"
    )

    # Common arguments
    for subparser in [fetch_parser, populate_parser]:
        subparser.add_argument(
            "--db-url",
            type=str,
            default=os.environ.get("TRADES_DB_URL", DB_URL),
            help="Database connection URL"
        )
        subparser.add_argument(
            "--db-schema",
            type=str,
            default=os.environ.get("TRADES_SCHEMA", TRADES_SCHEMA),
            help="Database schema name"
        )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        exit(1)

    # Configure logging for local runs
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if args.command == "fetch":
        # Run the fetch flow
        result = backfill_address_flow(
            address=args.address,
            coin=args.coin,
            limit=args.limit,
            db_url=args.db_url,
            db_schema=args.db_schema,
        )
        # Exit with error code if any errors occurred
        if result.errors > 0:
            exit(1)

    elif args.command == "populate":
        # Run the populate flow
        populate_address_trades_flow(
            start_days_ago=args.start_days,
            end_days_ago=args.end_days,
            db_url=args.db_url,
        )


if __name__ == "__main__":
    main()
