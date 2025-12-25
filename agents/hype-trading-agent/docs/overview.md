# Hype Trading Agent Overview

## Background Info

[btc, eth, sol] one day:
- 15361 active addresses
- 554698 trades, 6.5 trades/second

## Requirements

background task: 
- read trades: 
  1. listen hypeliquid ws
  2. transform & save to pg
  -> Pipelines

    - Address Taging
    - Sentiment Analysis

    -> Metrics

Metrics -> Strategies -> Actions -> Execution

State Management -> Logging & Monitoring -> Configuration -> Deployment -> Testing & Validation -> Maintenance & Updates

## DB Schema

all the table start with prefix `hyperliquid_` mean the raw data from Hyperliquid, other tables are derived or processed data.

tables

| Table Name             | Description                                      |
| ---------------------- | ------------------------------------------------ |
| trades                 | Raw trade data from Hyperliquid                  |
| address_trades         | Denormalized trade data indexed by address       |
| address_fetch_metadata | Metadata for tracking address trade fetch status |

### hyperliquid ws trade

Raw trade data from Hyperliquid

field explain: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/notation
subscribe typing define: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions#data-type-definitions

example:

```yaml
{
  "coin": "SOL",
  # B = Bid = Buy, A = Ask = Short
  "side": "A",
  # price
  "px": "121.36",
  # size
  "sz": "0.94",
  "time": 1766558767005,
  "hash": "0x087ee4cccf0467d109f80431fdd86f0207dd00b26a0786a3ac47901f8e0841bb",
  "tid": 1003335566908927,
  "users": [
    # taker
    "0xc5c4515e7e95dabbabf4fc26e9f1f6558d0d1006",
    # maker
    "0xd080b654474d679c03df4fd676aa818c1adfa608"
  ]
}
```

tables:

```sql
-- 1. Addresses table (entity)
CREATE TABLE hyperliquid_addresses (
    address      TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT NOW()   -- For ingestion time (optional)
    updated_at   TIMESTAMPTZ DEFAULT NOW()   -- For last update time
    updated_reason TEXT                     -- e.g. 'ingestion', 'correction'
);

-- 2. Trades table (entity)
CREATE TABLE hyperliquid_trades (
    tid          NUMERIC PRIMARY KEY,        -- Use global trade ID as PK
    coin         TEXT      NOT NULL,         -- Or use a coin_id + lookup table
    side         CHAR(1)   NOT NULL CHECK (side IN ('B', 'A')), -- 'B'=buy, 'A'=ask/short
    px           NUMERIC(20,10) NOT NULL,    -- Always use numeric for price
    sz           NUMERIC(20,10) NOT NULL,    -- Size in base currency
    time_ms      BIGINT    NOT NULL,         -- Milliseconds since Unix epoch (preserves precision)
    hash         TEXT      NOT NULL,         -- 66-char hex string
    created_at   TIMESTAMPTZ DEFAULT NOW()   -- For ingestion time (optional)
    updated_at   TIMESTAMPTZ DEFAULT NOW()   -- For last update time
    updated_reason TEXT                     -- e.g. 'ingestion', 'correction'
);

-- 3. Junction table: many-to-many relationship
CREATE TABLE hyperliquid_trade_participants (
    tid          NUMERIC REFERENCES hyperliquid_trades(tid) ON DELETE CASCADE,
    address      TEXT NOT NULL,
    PRIMARY KEY (tid, address),
    created_at   TIMESTAMPTZ DEFAULT NOW()   -- For ingestion time (optional)
    updated_at   TIMESTAMPTZ DEFAULT NOW()   -- For last update time
    updated_reason TEXT                     -- e.g. 'ingestion', 'correction'
);
```
