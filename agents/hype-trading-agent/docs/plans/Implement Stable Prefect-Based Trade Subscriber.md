## Plan: Implement Stable Prefect-Based Trade Subscriber

Build a never-stop multi-coin trade subscriber with time-based batch writes (10s/10,000 trades), trade_id uniqueness enforcement with partial batch retry, graceful shutdown, automatic reconnection, and health monitoring endpoint.

### Steps

1. **Add unique constraint and individual trade insertion** in db.py - set `unique=True` on db.py, modify db.py to first attempt bulk insert, on `IntegrityError` fall back to individual inserts with per-trade try-except blocks that log duplicates (coin, trade_id, tx_hash, timestamp) and continue inserting remaining trades

2. **Implement dual-trigger batch write system** in hyperliquid_trades_flow.py - create thread-safe `queue.Queue`, add background daemon thread that flushes when 10 seconds elapsed (`BATCH_FLUSH_INTERVAL_SECONDS` env var) OR 10,000 trades reached, track last trade timestamp and error counts in shared state for health checks

3. **Convert to infinite never-stop subscriber** - remove `duration_minutes` from hyperliquid_trades_flow.py, wrap WebSocket in `while not shutdown_flag` loop with exponential backoff reconnection (1s→2s→5s→10s→30s max), log all reconnection attempts with retry count, and maintain global statistics (total_trades, error_count, last_trade_time, start_time)

4. **Add graceful shutdown and health check endpoint** - implement signal handlers (SIGTERM/SIGINT) for clean shutdown, create lightweight HTTP server using `http.server` on port 8080 that returns JSON health status (`{"status": "healthy", "uptime_seconds": X, "total_trades": Y, "queue_depth": Z, "last_trade_timestamp": "...", "error_count": N}`), and ensure final batch flush before exit

5. **Create deployment infrastructure** - add `run_continuous_subscriber.py` launcher in prefect-workspace, update docker-compose.yaml with `hyperliquid-subscriber` service exposing port 8080, environment variables (`HYPERLIQUID_COINS="ETH,SOL,BTC"`, `BATCH_FLUSH_INTERVAL_SECONDS=10`, `HEALTH_CHECK_PORT=8080`), and `restart: unless-stopped` policy
