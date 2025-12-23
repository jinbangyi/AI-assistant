"""
Temporal workflow for subscribing to Hyperliquid exchange trade data
and persisting trades to PostgreSQL.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Dict, Optional
import asyncio
import json
from datetime import datetime

from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.activity import heartbeat
import asyncpg
import psycopg
import websockets


# Hyperliquid WebSocket URL
WS_URL = "wss://api.hyperliquid.xyz/ws"


@activity.defn
def setup_database_table(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    symbol: str,
) -> bool:
    """
    Create the trades table if it doesn't exist.
    Uses synchronous psycopg to avoid event loop conflicts with Temporal.
    
    Args:
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        symbol: Trading symbol (used for table naming)
    
    Returns:
        True if successful
    """
    print(f"[ACTIVITY] setup_database_table called with symbol: {symbol}")
    print(f"[ACTIVITY] Database connection: {user}@{host}:{port}/{database}")
    activity.logger.info(f"[ACTIVITY] Setting up database table for symbol: {symbol}")
    activity.logger.info(f"[ACTIVITY] Database connection: {user}@{host}:{port}/{database}")
    
    try:
        # Create connection using synchronous psycopg
        print(f"[ACTIVITY] Connecting to database using psycopg...")
        activity.logger.info("[ACTIVITY] Connecting to database using psycopg...")
        
        conn_string = f"host={host} port={port} dbname={database} user={user} password={password} connect_timeout=30"
        conn = psycopg.connect(conn_string)
        
        print(f"[ACTIVITY] Database connection successful")
        activity.logger.info("[ACTIVITY] Database connection successful")
        
        try:
            # Create table if it doesn't exist
            # Using symbol in table name to allow multiple symbols
            table_name = f"hyperliquid_trades_{symbol.lower()}"
            
            print(f"[ACTIVITY] Creating table {table_name}...")
            activity.logger.info(f"[ACTIVITY] Creating table {table_name}...")
            
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id SERIAL PRIMARY KEY,
                        trade_id BIGINT UNIQUE NOT NULL,
                        coin VARCHAR(50) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        price NUMERIC(20, 8) NOT NULL,
                        size NUMERIC(20, 8) NOT NULL,
                        timestamp TIMESTAMP NOT NULL,
                        tx_hash VARCHAR(100),
                        users TEXT[],
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                
            print(f"[ACTIVITY] Table {table_name} created successfully")
            activity.logger.info(f"[ACTIVITY] Table {table_name} created successfully")
            
            # Create index on trade_id for faster lookups
            print(f"[ACTIVITY] Creating indexes on {table_name}...")
            activity.logger.info(f"[ACTIVITY] Creating indexes on {table_name}...")
            
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_trade_id 
                    ON {table_name}(trade_id)
                """)
                
                # Create index on timestamp for time-based queries
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_timestamp 
                    ON {table_name}(timestamp)
                """)
                conn.commit()
            
            print(f"[ACTIVITY] Database table {table_name} ready")
            activity.logger.info(f"[ACTIVITY] Database table {table_name} ready")
            return True
            
        finally:
            conn.close()
            print(f"[ACTIVITY] Database connection closed")
            activity.logger.info("[ACTIVITY] Database connection closed")
            
    except Exception as e:
        print(f"[ACTIVITY ERROR] Error setting up database table: {e}")
        print(f"[ACTIVITY ERROR] Error type: {type(e).__name__}")
        activity.logger.error(f"[ACTIVITY ERROR] Error setting up database table: {e}")
        activity.logger.error(f"[ACTIVITY ERROR] Error type: {type(e).__name__}")
        import traceback
        traceback_str = traceback.format_exc()
        print(f"[ACTIVITY ERROR] Traceback: {traceback_str}")
        activity.logger.error(f"[ACTIVITY ERROR] Traceback: {traceback_str}")
        raise


@activity.defn
def persist_trade(
    trade: Dict,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    symbol: str,
) -> bool:
    """
    Persist a single trade to PostgreSQL.
    Uses synchronous psycopg to avoid event loop conflicts.
    
    Args:
        trade: Trade data dictionary
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        symbol: Trading symbol
    
    Returns:
        True if successful
    """
    table_name = f"hyperliquid_trades_{symbol.lower()}"
    
    conn_string = f"host={host} port={port} dbname={database} user={user} password={password} connect_timeout=30"
    conn = psycopg.connect(conn_string)
    
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {table_name} 
                (trade_id, coin, side, price, size, timestamp, tx_hash, users)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trade_id) DO NOTHING
            """,
                (
                    trade["trade_id"],
                    trade["coin"],
                    trade["side"],
                    trade["price"],
                    trade["size"],
                    trade["timestamp"],
                    trade.get("tx_hash"),
                    trade.get("users", []),
                )
            )
        conn.commit()
        return True
        
    except Exception as e:
        activity.logger.error(f"Error persisting trade: {e}")
        raise
        
    finally:
        conn.close()


@activity.defn
async def subscribe_and_persist_trades(
    symbol: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    duration_seconds: Optional[int] = None,
) -> Dict:
    """
    Subscribe to Hyperliquid trades for a symbol and persist them to PostgreSQL.
    This is a long-running activity that continues until cancelled or duration expires.
    
    Args:
        symbol: Trading symbol (e.g., "ETH", "BTC")
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        duration_seconds: Optional duration in seconds (None = run until cancelled)
    
    Returns:
        Dictionary with statistics about persisted trades
    """
    print(f"[ACTIVITY] Starting trade subscription for symbol: {symbol}")
    activity.logger.info(f"[ACTIVITY] Starting trade subscription for symbol: {symbol}")
    
    table_name = f"hyperliquid_trades_{symbol.lower()}"
    trades_count = 0
    start_time = datetime.now()
    
    # Create database connection that will be reused
    # Use synchronous psycopg connection to avoid event loop conflicts
    print(f"[ACTIVITY] Creating database connection...")
    conn_string = f"host={host} port={port} dbname={database} user={user} password={password} connect_timeout=30"
    db_conn = psycopg.connect(conn_string)
    print(f"[ACTIVITY] Database connection established")
    
    try:
        # Connect to Hyperliquid WebSocket
        async with websockets.connect(WS_URL) as ws:
            # Subscribe to trades
            sub_msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "trades",
                    "coin": symbol
                }
            }
            await ws.send(json.dumps(sub_msg))
            activity.logger.info(f"Subscribed to trades for {symbol}")
            
            # Set timeout if duration is specified
            end_time = None
            if duration_seconds:
                end_time = start_time + timedelta(seconds=duration_seconds)
            
            # Listen for trades
            while True:
                # Check if we should stop
                if end_time and datetime.now() >= end_time:
                    activity.logger.info(f"Duration limit reached for {symbol}")
                    break
                
                try:
                    # Heartbeat to let Temporal know we're alive and check for cancellation
                    # Wrap in try-except in case server doesn't support heartbeating
                    try:
                        heartbeat(trades_count)
                    except Exception:
                        # Server may not support heartbeating, continue anyway
                        pass
                    
                    # Receive message with timeout
                    timeout = 1.0  # 1 second timeout for checking cancellation
                    message = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    data = json.loads(message)
                    
                    # Process trade messages
                    if data.get("channel") == "trades":
                        trades = data.get("data", [])
                        for trade_data in trades:
                            # Parse trade
                            ts_ms = trade_data["time"]
                            dt = datetime.fromtimestamp(ts_ms / 1000)
                            
                            trade = {
                                "trade_id": trade_data["tid"],
                                "coin": trade_data["coin"],
                                "side": "buy" if trade_data["side"] == "B" else "sell",
                                "price": Decimal(str(trade_data["px"])),
                                "size": Decimal(str(trade_data["sz"])),
                                "timestamp": dt,
                                "tx_hash": trade_data.get("hash"),
                                "users": trade_data.get("users", []),
                            }
                            
                            # Persist trade using the shared connection
                            try:
                                with db_conn.cursor() as cur:
                                    cur.execute(f"""
                                        INSERT INTO {table_name} 
                                        (trade_id, coin, side, price, size, timestamp, tx_hash, users)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                        ON CONFLICT (trade_id) DO NOTHING
                                    """,
                                        (
                                            trade["trade_id"],
                                            trade["coin"],
                                            trade["side"],
                                            trade["price"],
                                            trade["size"],
                                            trade["timestamp"],
                                            trade.get("tx_hash"),
                                            trade.get("users", []),
                                        )
                                    )
                                db_conn.commit()
                                trades_count += 1
                                
                                if trades_count % 100 == 0:
                                    activity.logger.info(
                                        f"Persisted {trades_count} trades for {symbol}"
                                    )
                                    
                            except Exception as e:
                                activity.logger.error(f"Error persisting trade: {e}")
                                
                except asyncio.TimeoutError:
                    # Timeout is expected, continue loop to check cancellation
                    continue
                except websockets.exceptions.ConnectionClosed:
                    activity.logger.warning(f"WebSocket connection closed for {symbol}")
                    break
                except Exception as e:
                    activity.logger.error(f"Error processing message: {e}")
                    # Continue processing other messages
                    continue
        
    except Exception as e:
        activity.logger.error(f"Error in subscribe_and_persist_trades: {e}")
        raise
    finally:
        # Close database connection
        db_conn.close()
        print(f"[ACTIVITY] Database connection closed")
    
    duration = (datetime.now() - start_time).total_seconds()
    activity.logger.info(
        f"Completed subscription for {symbol}: {trades_count} trades in {duration:.2f} seconds"
    )
    
    return {
        "symbol": symbol,
        "trades_count": trades_count,
        "duration_seconds": duration,
        "start_time": start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
    }


@workflow.defn
class HyperliquidTradeSubscriptionWorkflow:
    """
    Workflow that subscribes to real-time trade data from Hyperliquid exchange
    and persists each trade to PostgreSQL.
    """
    
    @workflow.run
    async def run(
        self,
        symbol: str,
        db_host: str,
        db_port: int = 5432,
        db_database: str = "temporal",
        db_user: str = "temporal",
        db_password: str = "temporal",
        duration_seconds: Optional[int] = None,
    ) -> Dict:
        """
        Run the trade subscription workflow.
        
        Args:
            symbol: Trading symbol (e.g., "ETH", "BTC", "SOL")
            db_host: PostgreSQL host
            db_port: PostgreSQL port (default: 5432)
            db_database: Database name (default: "temporal")
            db_user: Database user (default: "temporal")
            db_password: Database password (default: "temporal")
            duration_seconds: Optional duration in seconds (None = run until cancelled)
        
        Returns:
            Dictionary with workflow execution results
        """
        workflow.logger.info(
            f"Starting Hyperliquid trade subscription workflow for {symbol}"
        )
        
        try:
            # Step 1: Setup database table
            await workflow.execute_activity(
                setup_database_table,
                args=[
                    db_host,
                    db_port,
                    db_database,
                    db_user,
                    db_password,
                    symbol,
                ],
                start_to_close_timeout=timedelta(minutes=2),  # Increased to 2 minutes
            )
            
            workflow.logger.info(f"Database table setup complete for {symbol}")
            
            # Step 2: Subscribe to trades and persist them
            # This is a long-running activity that can be cancelled
            result = await workflow.execute_activity(
                subscribe_and_persist_trades,
                args=[
                    symbol,
                    db_host,
                    db_port,
                    db_database,
                    db_user,
                    db_password,
                    duration_seconds,
                ],
                start_to_close_timeout=timedelta(hours=24),  # Long timeout for long-running activity
                # Note: heartbeat_timeout removed as server version doesn't support it
            )
            
            workflow.logger.info(
                f"Trade subscription completed for {symbol}: {result['trades_count']} trades"
            )
            
            return {
                "status": "success",
                "symbol": symbol,
                "trades_count": result["trades_count"],
                "duration_seconds": result["duration_seconds"],
                "start_time": result["start_time"],
                "end_time": result["end_time"],
            }
            
        except Exception as e:
            error_msg = f"Workflow failed for {symbol}: {str(e)}"
            workflow.logger.error(error_msg)
            return {
                "status": "error",
                "symbol": symbol,
                "error": str(e),
            }


async def main():
    """Example usage of the workflow."""
    import os
    
    # Connect to Temporal (use env var or default to localhost)
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(temporal_address, namespace="default")
    
    # Run the workflow with long execution timeout
    # Note: execute_workflow blocks until completion, so use start_workflow for long-running workflows
    # For testing, use a short duration (e.g., 10 seconds) instead of None
    result = await client.execute_workflow(
        "HyperliquidTradeSubscriptionWorkflow",  # Use workflow name string
        args=[
            "ETH",  # symbol
            "biz-db",  # db_host
            5432,  # db_port
            "biz",  # db_database (using the biz database on biz-db server)
            "biz",  # db_user
            "biz_password",  # db_password
            10,  # duration_seconds (10 seconds for testing, None would run forever)
        ],
        id=f"test-hyperliquid-workflow-{int(datetime.now().timestamp())}",
        task_queue="hyperliquid-trades",
        execution_timeout=timedelta(days=7),  # Workflow can run for up to 7 days
        run_timeout=timedelta(days=7),  # Single run can last up to 7 days
    )
    
    print(f"Workflow result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
