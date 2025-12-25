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

- **Price Chart with Prediction Bands**: Visualize actual prices with P10/P50/P90 predictions
- **Return Distribution**: Scatter plot of actual vs predicted returns
- **Pinball Loss**: Loss metrics by quantile across horizons
- **Direction Accuracy & Coverage**: Model performance metrics
- **Recent Samples Table**: Individual prediction samples with outcomes

### Controls

- **Coin Selector**: Switch between BTC, ETH, SOL
- **Horizon Selector**: Choose prediction horizon (5m, 15m, 30m, 1h)
- **Time Range**: Filter data by time window (1h, 6h, 24h, 48h, all)
- **Show Bands**: Toggle prediction band visualization
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
    "actual_return": 0.005,
    "p10": -0.002,
    "p50": 0.003,
    "p90": 0.008
  }
]
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
