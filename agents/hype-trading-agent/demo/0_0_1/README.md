# Hype Trading Agent - Demo 0.0.1

Multi-Task Quantile Regression for Price Prediction from Address-Level Trading Features.

## Overview

This module implements a training pipeline that:
1. Extracts address-level trading features (activity, directionality, position changes)
2. Extracts market features from K-lines (OHLCV, technical indicators)
3. Builds samples using sliding window approach
4. Trains a multi-task MLP to predict price distributions at multiple horizons
5. Provides a verification dashboard for model analytics

## Project Structure

```
demo/0_0_1/
├── main.py                          # Training pipeline
├── init_training_and_verify_data.py # Data initialization script
├── verify_api.py                    # FastAPI server for dashboard
├── admin.html                       # Verification analytics dashboard
├── best_model.pt                    # Trained model checkpoint
├── temp-data/                       # Data directory
│   ├── candles/                     # Training k-line data
│   ├── verify-candles/              # Verification k-line data
│   ├── trades/                      # Trade data
│   ├── training_history.json        # Training loss history
│   └── verify_results.json          # Verification predictions & metrics
└── README.md                        # This file
```

## Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL database with trades data
- Hyperliquid API access

### Installation

```bash
# Install dependencies
pip install torch loguru scikit-learn pandas numpy fastapi uvicorn

# Or use the project's pyproject.toml
pip install -e /path/to/AI-assistant
```

### Step 1: Initialize Data

Fetch training and verification data from Hyperliquid API and database:

```bash
python init_training_and_verify_data.py
```

This creates:
- `temp-data/candles/` - K-line data for training
- `temp-data/verify-candles/` - K-line data for verification
- `temp-data/trades/` - Trade data from database

### Step 2: Train Model

Run the training pipeline:

```bash
python main.py
```

This will:
- Build training samples from trades and candles
- Train the multi-task quantile regression model
- Save the best model to `best_model.pt`
- Save training history to `temp-data/training_history.json`

**Note:** The model currently supports BTC. To add ETH/SOL, modify the `coins` list in `main.py`.

### Step 3: Run Verification Dashboard

Start the FastAPI server:

```bash
python verify_api.py
```

The dashboard will be available at:
- Dashboard: http://localhost:8000
- API Docs: http://localhost:8000/docs

## Dashboard Features

### Analytics Display

- **Price with Prediction Bands**: Shows verification data with actual price and P10/P50/P90 predicted return bands. The predictions are for the future time window (timestamp + horizon), calculated as: `predicted_price = current_price * (1 + predicted_return)`.
- **Price Prediction (Actual vs Predicted)**: Compares chain data (full actual prices) with verify data (predicted prices from model). Uses two data sources:
  - Actual Price (Chain): Full price history from verify candles
  - P10/P50/P90: Predicted future prices from verification samples only
- **Pinball Loss**: Loss metrics by quantile across horizons - measures quantile regression accuracy
- **Direction Accuracy & Coverage**: Model performance metrics
- **Trading Strategy PnL**: Band-based trading strategy profit visualization with:
  - Cumulative PnL line chart showing equity curve over time
  - Per-trade return bars (green for wins, red for losses)
  - Summary cards showing total PnL and win rate
- **Recent Samples Table**: Individual prediction samples with outcomes

### Understanding Prediction Bands

The P10/P50/P90 values represent **predicted returns** for a future time window:

- **P10 (10th percentile)**: 10% probability that actual return will be below this value
- **P50 (Median)**: 50% probability that actual return will be below this value
- **P90 (90th percentile)**: 90% probability that actual return will be below this value

**Example with 5-minute horizon:**
- At 12:00:00, current price is $88,000
- Model predicts: P10=-0.2%, P50=+0.1%, P90=+0.4%
- Predicted prices for 12:05:00:
  - P10: $88,000 × (1 - 0.002) = $87,824
  - P50: $88,000 × (1 + 0.001) = $88,088
  - P90: $88,000 × (1 + 0.004) = $88,352

### Controls

- **Coin Selector**: Dynamically loaded from main.py CONFIG (BTC, ETH, SOL, etc.)
- **Horizon Selector**: Dynamically loaded from main.py CONFIG (1m, 5m, 10m, 15m, etc.)
- **Time Range**: Filter data by time window (1h, 6h, 24h, 48h, all)
- **Show Bands**: Toggle prediction band visualization (affects "Price with Prediction Bands" chart)
- **Refresh**: Reload data from API
- **Recompute**: Trigger verification pipeline
- **Export**: Download predictions as CSV

## API Endpoints

### `GET /api/health`
Health check endpoint.

### `GET /api/kline`
Get k-line data with predicted quantile bands.

**Query Parameters:**
- `coin` (required): Coin symbol (BTC, ETH, SOL)
- `horizon` (optional): Horizon in seconds (default: 300)
- `time_range` (optional): Time range filter (default: 24h)
- `run_id` (optional): Specific run ID

**Response:**
```json
[
  {
    "timestamp": 1766473200,
    "open": 98000.0,
    "high": 98500.0,
    "low": 97800.0,
    "close": 98200.0,
    "volume": 1234.56,
    "p10": -0.002,
    "p50": 0.003,
    "p90": 0.008
  }
]
```

**Note:** The `p10`, `p50`, `p90` values are predicted **returns** for the future time window (timestamp + horizon), not the current price. To get predicted future prices, multiply: `predicted_price = close * (1 + pX)`.

### `GET /api/chain-data`
Get chain data (actual prices from verify candles) for comparison.

**Query Parameters:**
- `coin` (required): Coin symbol
- `horizon` (optional): Horizon in seconds (default: 300)
- `time_range` (optional): Time range filter (default: 24h)

**Response:**
```json
[
  {
    "timestamp": 1766473200,
    "open": 98000.0,
    "high": 98500.0,
    "low": 97800.0,
    "close": 98200.0,
    "volume": 1234.56
  }
]
```

### `GET /api/profit`
Get band-based trading strategy profit analysis.

**Trading Strategy:**
- **LONG**: Enter when `p10 > 0` AND `p90 > 0` (both bands predict positive return)
- **SHORT**: Enter when `p10 < 0` AND `p90 < 0` (both bands predict negative return)
- **FLAT**: No position when bands disagree (mixed signals)

**Query Parameters:**
- `coin` (required): Coin symbol
- `horizon` (optional): Horizon in seconds (default: 300)
- `time_range` (optional): Time range filter (default: 24h)

**Response:**
```json
{
  "coin": "BTC",
  "horizon_sec": 300,
  "summary": {
    "total_pnl_pct": 2.34,
    "total_trades": 42,
    "win_rate": 58.5,
    "max_drawdown_pct": -1.23,
    "sharpe_ratio": 1.85
  },
  "trades": [
    {
      "entry_time": 1766473200,
      "exit_time": 1766473500,
      "position": "LONG",
      "entry_price": 98000.0,
      "exit_price": 98500.0,
      "return_pct": 0.51
    }
  ],
  "equity_curve": [
    {
      "timestamp": 1766473200,
      "equity_pct": 0.0
    },
    {
      "timestamp": 1766473500,
      "equity_pct": 0.51
    }
  ]
}
```

### `GET /api/config`
Get model configuration from main.py.

**Response:**
```json
{
  "coins": ["BTC"],
  "horizons": [
    {"value": 60, "label": "1m"},
    {"value": 300, "label": "5m"},
    {"value": 600, "label": "10m"},
    {"value": 900, "label": "15m"}
  ]
}
```

### `GET /api/metrics`
Get model metrics for a coin.

**Query Parameters:**
- `coin` (required): Coin symbol

**Response:**
```json
{
  "coin": "BTC",
  "run_id": "run_20241226_123456",
  "generated_at": 1766473200,
  "horizons": [
    {
      "horizon_sec": 300,
      "horizon_label": "5m",
      "pinball_p10": 0.0012,
      "pinball_p50": 0.0015,
      "pinball_p90": 0.0011,
      "direction_accuracy": 0.62,
      "coverage_p10_p90": 0.78,
      "sample_count": 1500
    }
  ],
  "recent_samples": [...]
}
```

### `POST /api/recompute`
Trigger recomputation of verification results.

**Response:**
```json
{
  "status": "success",
  "message": "Verification results recomputed successfully",
  "timestamp": "1766473200"
}
```

### `GET /api/run_ids`
List all stored verification runs.

**Response:**
```json
[
  {
    "run_id": "run_20241226_123456",
    "coin": "BTC",
    "timestamp": 1766473200,
    "sample_count": 1500,
    "datetime": "2024-12-26T12:34:56Z"
  }
]
```

### `GET /api/export`
Export verification data for a coin.

**Query Parameters:**
- `coin` (required): Coin symbol
- `format` (optional): Export format - csv or json (default: csv)
- `time_range` (optional): Time range filter (default: 24h)

**Response:** CSV or JSON file download

### `GET /api/training_history`
Get training history for loss curve visualization.

**Response:**
```json
{
  "train_loss": [0.0234, 0.0198, ...],
  "val_loss": [0.0256, 0.0212, ...]
}
```

## Model Architecture

### Features

**Address-Level Features (20):**
- Trade activity: count, total volume, avg/max size, std size, frequency
- Directionality: net volume, buy/sell ratio, buy/sell volume
- Position changes: delta position
- Behavior patterns: first/last side, side switches
- Price interaction: above VWAP ratio, price change, volatility
- Whale indicator

**Market Features (23):**
- OHLCV: open, high, low, close, volume
- Returns: 1m, 5m, 15m, 1h
- Moving averages: MA20, MA50, price vs MA
- Volatility: 20-period, 60-period
- Technical: RSI-14, Bollinger Bands
- Volume: MA20, ratio, 5-period change

### Model

Multi-task MLP with:
- Shared backbone: [128, 64] hidden layers with ReLU and dropout
- Horizon-specific heads: Separate output for each prediction horizon
- Quantile outputs: P10, P50, P90 for each horizon

### Training

- **Loss:** Pinball loss for quantile regression
- **Optimizer:** AdamW with ReduceLROnPlateau scheduler
- **Validation:** Time-series split (70/15/15)
- **Early Stopping:** 5 epochs patience
- **Mixed Precision:** AMP enabled for MPS/CUDA

## Configuration

Edit `main.py` to modify:

```python
CONFIG = {
    "coins": ["BTC"],  # Add "ETH", "SOL" as needed
    "feature_window": 60 * MINUTE,  # Feature extraction window
    "sample_step": 5 * MINUTE,  # Sliding window step
    "horizons": [5*MINUTE, 15*MINUTE, 30*MINUTE],  # Prediction horizons
    "quantiles": [0.1, 0.5, 0.9],  # Quantiles to predict
    "batch_size": 256,
    "learning_rate": 1e-3,
    "num_epochs": 50,
}
```

## Environment Variables

```bash
# Database connection
export TRADES_DB_URL="postgresql+psycopg2://prefect:prefect@localhost:5435/prefect"
export TRADES_SCHEMA="hyperliquid_continuous"
```

## Troubleshooting

### No data loaded error
Make sure to run `init_training_and_verify_data.py` first to fetch the required data.

### MPS/CUDA errors
The code automatically detects and uses MPS (Apple Silicon) or CUDA (NVIDIA). If you encounter issues, set:
```python
CONFIG["device"] = "cpu"
CONFIG["enable_amp"] = False
```

### Dashboard shows no data
Ensure `verify_results.json` exists in `temp-data/`. Run verification to generate it.

## Development

### Adding new coins

1. Add coin symbol to `coins` list in both `main.py` and `init_training_and_verify_data.py`
2. Run data initialization
3. Retrain the model

### Adding new horizons

1. Add horizon to `horizons` list in `CONFIG`
2. Retrain the model
3. Update dashboard horizon selector in `admin.html`

## License

Part of the AI Assistant project.
