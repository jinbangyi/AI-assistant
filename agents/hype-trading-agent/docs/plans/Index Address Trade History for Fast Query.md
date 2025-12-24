## Plan: Index Address Trade History for Fast Query

Build an address-centric indexing system with denormalized `address_trades` table for fast queries, implement Hyperliquid REST API client for lazy backfill of historical user fills (with 10,000 fill hard limit documented in code), supporting single-coin queries with manual workflow trigger.

### Steps

1. **Create denormalized `address_trades` table** — Add schema in db_continuous.py with columns `(id, address, coin, side, price, size, timestamp, trade_id, tx_hash, created_at)`, composite index on `(address, coin, timestamp DESC)`, and unique constraint on `(address, trade_id)` for deduplication; prioritize query speed over storage efficiency

2. **Build auto-population mechanism** — Create PostgreSQL trigger or SQLAlchemy event listener in db_continuous.py that fires on `trades` table INSERT to extract each address from `users` JSON array (typically 2 addresses per trade) and insert corresponding rows into `address_trades`, creating denormalized copies for O(1) address lookups

3. **Implement Hyperliquid REST API client** — Create `src/prefect-workspace/lib/hyperliquid_api.py` with `fetch_user_fills(address, coin=None)` calling `POST https://api.hyperliquid.xyz/info` with body `{"type": "userFills", "user": "0x..."}` or `{"type": "userFillsByTime", "user": "0x...", "startTime": <ms>, "endTime": <ms>}`; add `httpx` to pyproject.toml; include code comment: `# Hyperliquid API hard limit: only 10,000 most recent fills available, older data is permanently inaccessible`; handle 2000 fills/response pagination; add retry with exponential backoff; no authentication required

4. **Add fetch metadata tracking table** — Create `address_fetch_metadata` in db_continuous.py with schema `(address, coin, last_fetch_timestamp, oldest_trade_timestamp, total_fills_count, last_updated)` and unique constraint on `(address, coin)` to prevent duplicate API calls and track backfill status

5. **Build lazy query with auto-backfill** — Implement `query_address_trades(address, coin, min_trades=100)` in db_continuous.py that: (a) queries `address_trades` by address+coin, (b) if count < min_trades, triggers `fetch_user_fills()`, (c) inserts to `trades` (auto-populates `address_trades` via trigger), (d) deduplicates using `trade_id` constraint, (e) updates metadata, (f) returns complete results DESC by timestamp

6. **Create manual backfill Prefect flow** — Add `flows/backfill_address_history.py` with parameters `(address, coin, limit=2000)` that fetches from REST API, batch inserts to database with duplicate handling, logs statistics (saved/duplicates/errors), and provides user-triggered workflow via Prefect UI for one-time historical data population
