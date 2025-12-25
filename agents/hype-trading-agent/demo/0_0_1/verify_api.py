"""
FastAPI server for verification analytics dashboard.

Provides endpoints for:
- K-line data with predicted quantile bands
- Model metrics (pinball loss, direction accuracy, coverage)
- Run management (list runs, recompute results)
- CSV export
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import numpy as np
import pandas as pd
import torch

# Configuration
VERIFY_RESULTS_PATH = Path("./temp-data/verify_results.json")
TRAINING_HISTORY_PATH = Path("./temp-data/training_history.json")
MODEL_PATH = Path("./best_model.pt")
ADMIN_HTML_PATH = Path("./admin.html")

# Horizons in seconds
HORIZONS = {
    "300": "5m",
    "900": "15m",
    "1800": "30m",
    "3600": "1h",
    "14400": "4h",
    "43200": "12h",
    "86400": "1d",
}


class RangeFilter(str, Enum):
    """Time range filters."""
    hour_1 = "1h"
    hour_6 = "6h"
    hour_24 = "24h"
    hour_48 = "48h"
    all = "all"


app = FastAPI(title="Verification Analytics API", version="0.0.1")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class KlinePoint:
    """Single k-line data point with predictions."""
    timestamp: int  # Unix timestamp in seconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    actual_return: float  # Actual return for the horizon
    p10: float  # 10th percentile prediction
    p50: float  # 50th percentile (median) prediction
    p90: float  # 90th percentile prediction


@dataclass
class HorizonMetrics:
    """Metrics for a specific horizon."""
    horizon_sec: int
    horizon_label: str
    pinball_p10: float
    pinball_p50: float
    pinball_p90: float
    direction_accuracy: float
    coverage_p10_p90: float
    sample_count: int


@dataclass
class MetricsResponse:
    """Metrics response for a coin."""
    coin: str
    run_id: str
    generated_at: int
    horizons: list[HorizonMetrics]
    recent_samples: list[dict[str, Any]]


@dataclass
class RunInfo:
    """Information about a stored run."""
    run_id: str
    coin: str
    timestamp: int
    sample_count: int


# =============================================================================
# Data Loading
# =============================================================================

def load_verify_results() -> dict[str, Any]:
    """Load verification results from JSON file."""
    if not VERIFY_RESULTS_PATH.exists():
        return {}

    with VERIFY_RESULTS_PATH.open("r") as f:
        return json.load(f)


def load_training_history() -> dict[str, Any]:
    """Load training history for loss curve."""
    if not TRAINING_HISTORY_PATH.exists():
        return {}

    with TRAINING_HISTORY_PATH.open("r") as f:
        return json.load(f)


def get_runs() -> list[RunInfo]:
    """Get list of available runs from verify_results.json."""
    data = load_verify_results()
    runs = []

    for coin, coin_data in data.items():
        if isinstance(coin_data, dict) and "run_id" in coin_data:
            runs.append(RunInfo(
                run_id=coin_data["run_id"],
                coin=coin,
                timestamp=coin_data.get("generated_at", int(time.time())),
                sample_count=len(coin_data.get("samples", [])),
            ))

    # Sort by timestamp descending
    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs


def filter_by_range(
    samples: list[dict[str, Any]],
    time_range: RangeFilter,
    reference_time: int,
) -> list[dict[str, Any]]:
    """Filter samples by time range."""
    if time_range == RangeFilter.all:
        return samples

    range_seconds = {
        RangeFilter.hour_1: 3600,
        RangeFilter.hour_6: 6 * 3600,
        RangeFilter.hour_24: 24 * 3600,
        RangeFilter.hour_48: 48 * 3600,
    }

    cutoff = reference_time - range_seconds.get(time_range, 24 * 3600)
    return [s for s in samples if s.get("timestamp", 0) >= cutoff]


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "timestamp": str(int(time.time()))}


@app.get("/api/kline")
async def get_kline(
    coin: str = Query(..., description="Coin symbol (e.g., BTC, ETH, SOL)"),
    horizon: str = Query("300", description="Horizon in seconds (e.g., 300, 900, 1800, 3600)"),
    time_range: RangeFilter = Query(RangeFilter.hour_24, description="Time range filter"),
    run_id: Optional[str] = Query(None, description="Specific run ID to use"),
) -> list[dict[str, Any]]:
    """
    Get k-line data with predicted quantile bands.

    Returns OHLCV data with actual returns and p10/p50/p90 predictions.
    """
    data = load_verify_results()

    if not data:
        raise HTTPException(
            status_code=404,
            detail="No verification data found. Please run verification first: python main.py --verify-only"
        )

    if coin not in data:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for coin: {coin}. Available coins: {list(data.keys())}"
        )

    coin_data = data[coin]
    samples = coin_data.get("samples", [])

    if not samples:
        raise HTTPException(status_code=404, detail=f"No samples found for coin: {coin}")

    # Filter by time range
    now = int(time.time())
    samples = filter_by_range(samples, time_range, now)

    # Filter by horizon
    horizon_key = f"return_{horizon}s"
    result = []

    for s in samples:
        if horizon_key not in s.get("targets", {}):
            continue

        result.append({
            "timestamp": s["timestamp"],
            "open": s.get("open", 0),
            "high": s.get("high", 0),
            "low": s.get("low", 0),
            "close": s.get("close", 0),
            "volume": s.get("volume", 0),
            "actual_return": s["targets"][horizon_key],
            "p10": s["predictions"][horizon_key].get("p10", 0),
            "p50": s["predictions"][horizon_key].get("p50", 0),
            "p90": s["predictions"][horizon_key].get("p90", 0),
        })

    # Sort by timestamp
    result.sort(key=lambda x: x["timestamp"])
    return result


@app.get("/api/metrics")
async def get_metrics(
    coin: str = Query(..., description="Coin symbol (e.g., BTC, ETH, SOL)"),
) -> MetricsResponse:
    """
    Get metrics for a coin.

    Returns pinball loss, direction accuracy, coverage, and recent samples.
    """
    data = load_verify_results()

    if not data:
        raise HTTPException(
            status_code=404,
            detail="No verification data found. Please run verification first: python main.py --verify-only"
        )

    if coin not in data:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for coin: {coin}. Available coins: {list(data.keys())}"
        )

    coin_data = data[coin]
    metrics_data = coin_data.get("metrics", {})

    horizons_list = []
    for h_sec, h_label in HORIZONS.items():
        h_metrics = metrics_data.get(f"horizon_{h_sec}", {})
        if h_metrics:
            horizons_list.append(HorizonMetrics(
                horizon_sec=int(h_sec),
                horizon_label=h_label,
                pinball_p10=h_metrics.get("pinball_p10", 0),
                pinball_p50=h_metrics.get("pinball_p50", 0),
                pinball_p90=h_metrics.get("pinball_p90", 0),
                direction_accuracy=h_metrics.get("direction_accuracy", 0),
                coverage_p10_p90=h_metrics.get("coverage_p10_p90", 0),
                sample_count=h_metrics.get("sample_count", 0),
            ))

    # Get recent samples (last 20)
    samples = coin_data.get("samples", [])
    recent_samples = samples[-20:] if len(samples) > 20 else samples

    return MetricsResponse(
        coin=coin,
        run_id=coin_data.get("run_id", "unknown"),
        generated_at=coin_data.get("generated_at", int(time.time())),
        horizons=horizons_list,
        recent_samples=recent_samples,
    )


@app.post("/api/recompute")
async def recompute() -> dict[str, str]:
    """
    Trigger recomputation of verification results.

    This will run the verification pipeline to generate new predictions.
    """
    try:
        # Run the verification script
        result = subprocess.run(
            ["uv", "run", "python", "main.py", "--verify-only"],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Recomputation failed: {result.stderr}",
            )

        return {
            "status": "success",
            "message": "Verification results recomputed successfully",
            "timestamp": str(int(time.time())),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Recomputation timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recomputation error: {str(e)}")


@app.get("/api/run_ids")
async def get_run_ids() -> list[dict[str, Any]]:
    """List all stored runs."""
    runs = get_runs()
    return [
        {
            "run_id": r.run_id,
            "coin": r.coin,
            "timestamp": r.timestamp,
            "sample_count": r.sample_count,
            "datetime": datetime.fromtimestamp(r.timestamp, tz=timezone.utc).isoformat(),
        }
        for r in runs
    ]


@app.get("/api/export")
async def export_data(
    coin: str = Query(..., description="Coin symbol (e.g., BTC, ETH, SOL)"),
    format: str = Query("csv", description="Export format: csv or json"),
    time_range: RangeFilter = Query(RangeFilter.hour_24, description="Time range filter"),
) -> Response:
    """
    Export verification data for a coin.

    Supports CSV and JSON formats.
    """
    data = load_verify_results()

    if coin not in data:
        raise HTTPException(status_code=404, detail=f"No data found for coin: {coin}")

    coin_data = data[coin]
    samples = coin_data.get("samples", [])

    # Filter by time range
    now = int(time.time())
    samples = filter_by_range(samples, time_range, now)

    if format.lower() == "json":
        json_data = json.dumps(samples, indent=2)
        return Response(
            content=json_data,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={coin}_verify_{now}.json"
            },
        )

    # CSV export
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "timestamp", "open", "high", "low", "close", "volume",
        "return_300s_p10", "return_300s_p50", "return_300s_p90", "return_300s_actual",
        "return_900s_p10", "return_900s_p50", "return_900s_p90", "return_900s_actual",
        "return_1800s_p10", "return_1800s_p50", "return_1800s_p90", "return_1800s_actual",
    ])

    for s in samples:
        row = [
            s["timestamp"],
            s.get("open", 0),
            s.get("high", 0),
            s.get("low", 0),
            s.get("close", 0),
            s.get("volume", 0),
        ]

        for h in ["300", "900", "1800"]:
            h_key = f"return_{h}s"
            preds = s.get("predictions", {}).get(h_key, {})
            actual = s.get("targets", {}).get(h_key, 0)
            row.extend([
                preds.get("p10", 0),
                preds.get("p50", 0),
                preds.get("p90", 0),
                actual,
            ])

        writer.writerow(row)

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={coin}_verify_{now}.csv"
        },
    )


@app.get("/api/training_history")
async def get_training_history() -> dict[str, Any]:
    """Get training history for loss curve visualization."""
    return load_training_history()


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    """
    Get model configuration (coins, horizons, etc).

    Returns the CONFIG from main.py including available coins and prediction horizons.
    """
    # Read main.py to extract CONFIG
    main_py_path = Path("./main.py")
    if not main_py_path.exists():
        # Return default config
        return {
            "coins": ["BTC"],
            "horizons": [
                {"value": 60, "label": "1m"},
                {"value": 300, "label": "5m"},
                {"value": 600, "label": "10m"},
                {"value": 900, "label": "15m"},
            ]
        }

    with main_py_path.open("r") as f:
        content = f.read()

    # Extract coins list - handles comments and quoted strings
    coins = []
    coins_match = re.search(r'"coins":\s*\[(.*?)\]', content, re.DOTALL)
    if coins_match:
        coins_str = coins_match.group(1)
        for line in coins_str.split('\n'):
            line = line.strip()
            # Match quoted strings, skip comments
            if line and not line.startswith('#'):
                m = re.search(r'"([^"]+)"', line)
                if m:
                    coins.append(m.group(1))

    if not coins:
        coins = ["BTC"]

    # Extract horizons list - handles MINUTE constants and expressions
    horizons = []
    horizons_match = re.search(r'"horizons":\s*\[(.*?)\]', content, re.DOTALL)
    if horizons_match:
        horizons_str = horizons_match.group(1)
        for line in horizons_str.split('\n'):
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Remove trailing comma
            line = line.rstrip(',')

            # Parse the horizon value (e.g., "5 * MINUTE" or "300" or just "MINUTE")
            try:
                # Replace constants with values
                h_clean = line.replace('MINUTE', '60').replace('HOUR', '3600').replace('DAY', '86400')
                result = eval(h_clean)
                # Handle both single values and tuples from eval
                if isinstance(result, tuple):
                    h_value = result[0]
                elif isinstance(result, (int, float)):
                    h_value = int(result)
                else:
                    continue
            except:
                continue

            # Create label
            h_min = h_value // 60
            if h_min < 60:
                label = f"{h_min}m"
            elif h_min < 1440:
                label = f"{h_min // 60}h"
            else:
                label = f"{h_min // 1440}d"

            horizons.append({"value": h_value, "label": label})

    if not horizons:
        horizons = [
            {"value": 60, "label": "1m"},
            {"value": 300, "label": "5m"},
        ]

    return {
        "coins": coins,
        "horizons": horizons,
    }


@app.get("/api/chain-data")
async def get_chain_data(
    coin: str = Query(..., description="Coin symbol (e.g., BTC, ETH, SOL)"),
    horizon: int = Query(300, description="Horizon in seconds"),
    time_range: str = Query("24h", description="Time range filter"),
) -> list[dict[str, Any]]:
    """
    Get chain data (actual prices from verify candles) for comparison.

    Returns the actual price data for the specified coin and time range.
    This returns ALL candles from the verify candles file, not just verification samples.
    """
    import re

    verify_dir = Path("./temp-data/verify-candles")

    # Find the verify candle file
    verify_files = list(verify_dir.glob(f"{coin}_1m_candles_*.json"))
    if not verify_files:
        raise HTTPException(status_code=404, detail=f"No verify candles found for {coin}")

    with verify_files[0].open("r") as f:
        candle_data = json.load(f)

    # Parse time range - use the actual data range instead of time-based cutoff
    # because the verify candles have a fixed range
    range_seconds = {
        "1h": 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "48h": 48 * 3600,
        "all": float('inf'),
    }
    range_limit = range_seconds.get(time_range, 24 * 3600)

    # Get the timestamp range from the actual data
    if candle_data:
        first_ts = candle_data[0].get("t", 0) // 1000
        last_ts = candle_data[-1].get("t", 0) // 1000
        data_span = last_ts - first_ts

        # Use the smaller of requested range or actual data span
        effective_range = min(range_limit, data_span) if range_limit != float('inf') else data_span
        cutoff = last_ts - effective_range
    else:
        cutoff = 0

    # Filter and format data
    result = []
    for c in candle_data:
        ts = c.get("t", 0) // 1000  # Convert to seconds
        if ts < cutoff:
            continue

        current_price = float(c.get("c", 0))

        result.append({
            "timestamp": ts,
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": current_price,
            "volume": float(c.get("v", 0)),
        })

    # Sort by timestamp
    result.sort(key=lambda x: x["timestamp"])
    return result


@app.get("/api/profit")
async def get_profit(
    coin: str = Query(..., description="Coin symbol (e.g., BTC, ETH, SOL)"),
    horizon: int = Query(300, description="Horizon in seconds"),
    time_range: str = Query("24h", description="Time range filter"),
) -> dict[str, Any]:
    """
    Compute band-based trading PnL from verification results.

    Uses a simple strategy:
    - LONG if both p10 > 0 and p90 > 0 (strong bullish signal)
    - SHORT if both p10 < 0 and p90 < 0 (strong bearish signal)
    - FLAT otherwise (uncertain signal)

    Returns time series of cumulative PnL and summary statistics.
    """
    # Load verification results
    if not VERIFY_RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No verification results found. Run verification first.")

    with VERIFY_RESULTS_PATH.open("r") as f:
        verify_data = json.load(f)

    # Handle nested structure: { "BTC": { "samples": [...] } }
    if coin in verify_data:
        coin_data = verify_data[coin]
        samples = coin_data.get("samples", [])
    else:
        # If coin not found, try to use first available coin
        first_coin = next(iter(verify_data.keys()), None)
        if first_coin and "samples" in verify_data[first_coin]:
            samples = verify_data[first_coin].get("samples", [])
        else:
            # Fallback: assume verify_data is the samples array directly
            samples = verify_data if isinstance(verify_data, list) else []

    if not samples:
        raise HTTPException(status_code=404, detail=f"No verification samples found for {coin}")

    # Parse time range
    range_seconds = {
        "1h": 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "48h": 48 * 3600,
        "all": float('inf'),
    }
    cutoff = time.time() - range_seconds.get(time_range, 24 * 3600)

    # Get the horizon key
    h_key = f"return_{horizon}s"

    # Build PnL time series
    trades = []
    cumulative_pnl = 0.0
    equity_curve = []

    for sample in samples:
        ts = sample.get("timestamp", 0)
        if ts < cutoff:
            continue

        close_price = sample.get("close", 0)
        if close_price <= 0:
            continue

        # Get predictions for this horizon
        preds = sample.get("predictions", {}).get(h_key, {})
        if not preds:
            continue

        p10 = preds.get("p10", 0)
        p50 = preds.get("p50", 0)
        p90 = preds.get("p90", 0)

        # Get actual return
        targets = sample.get("targets", {})
        actual_return = targets.get(h_key, 0)

        # Generate signal
        signal = 0  # flat
        if p10 > 0 and p90 > 0:
            signal = 1  # long
        elif p10 < 0 and p90 < 0:
            signal = -1  # short

        # Only track actual trades (non-flat signals)
        if signal != 0:
            # Calculate PnL
            trade_return = signal * actual_return
            cumulative_pnl += trade_return

            trades.append({
                "entry_time": ts,
                "exit_time": ts + horizon,  # Approximate exit time
                "position": "LONG" if signal == 1 else "SHORT",
                "entry_price": close_price,
                "exit_price": close_price * (1 + actual_return),  # Approximate exit price
                "return_pct": trade_return,
            })

            equity_curve.append({
                "timestamp": ts,
                "equity_pct": cumulative_pnl,  # Frontend expects equity_pct
            })

    # Calculate summary statistics
    if not trades:
        return {
            "coin": coin,
            "horizon_sec": horizon,
            "time_range": time_range,
            "trades": [],
            "equity_curve": [],
            "summary": {
                "total_pnl_pct": 0.0,
                "total_trades": 0,
                "win_rate": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
            }
        }

    # Add starting point to equity curve
    if equity_curve:
        first_timestamp = equity_curve[0]["timestamp"]
        equity_curve.insert(0, {
            "timestamp": first_timestamp - horizon,
            "equity_pct": 0.0,
        })

    # Count winning/losing trades
    winning_trades = sum(1 for t in trades if t["return_pct"] > 0)
    total_trades = len(trades)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    # Calculate max drawdown
    peak = 0.0  # Start at 0 (initial equity)
    max_drawdown = 0.0
    for point in equity_curve:
        if point["equity_pct"] > peak:
            peak = point["equity_pct"]
        drawdown = peak - point["equity_pct"]  # Absolute drawdown
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Calculate Sharpe ratio (simplified, assuming 252 trading days per year)
    # For simplicity, we'll use the standard deviation of returns
    returns = [t["return_pct"] for t in trades]
    if len(returns) > 1:
        returns_std = np.std(returns)
        returns_mean = np.mean(returns)
        sharpe_ratio = (returns_mean / returns_std) * np.sqrt(252) if returns_std > 0 else 0
    else:
        sharpe_ratio = 0

    summary = {
        "total_pnl_pct": cumulative_pnl * 100,
        "total_trades": total_trades,
        "win_rate": win_rate * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "sharpe_ratio": sharpe_ratio,
    }

    return {
        "coin": coin,
        "horizon_sec": horizon,
        "time_range": time_range,
        "trades": trades,
        "equity_curve": equity_curve,
        "summary": summary,
    }


# =============================================================================
# Serve Dashboard
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Serve the admin dashboard HTML."""
    if ADMIN_HTML_PATH.exists():
        with ADMIN_HTML_PATH.open("r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard not found. Please create admin.html</h1>")


# =============================================================================
# Main
# =============================================================================

def main():
    """Run the API server."""
    logger.info("Starting Verification Analytics API...")
    logger.info(f"Dashboard available at: http://localhost:8000")
    logger.info(f"API docs available at: http://localhost:8000/docs")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
