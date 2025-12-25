"""
Multi-Task Quantile Regression for Price Prediction from Address-Level Trading Features

This module implements a training pipeline that:
1. Extracts address-level trading features (activity, directionality, position changes)
2. Extracts market features from K-lines (OHLCV, technical indicators)
3. Builds samples using sliding window approach
4. Trains a multi-task MLP to predict price distributions at multiple horizons

Plan Reference: plan.md
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from loguru import logger
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
import joblib

MINUTE = 60
MINUTE_MS = 60000
HOUR = 3600
HOUR_MS = 3600000
DAY = 86400
DAY_MS = 86400000

# =============================================================================
# Configuration
# =============================================================================

CONFIG = {
    # Data paths
    "data_dir": Path("./temp-data"),
    "trades_dir": Path("./temp-data/trades"),
    "candles_dir": Path("./temp-data/candles"),
    "verify_candles_dir": Path("./temp-data/verify-candles"),
    "model_path": Path("./best_model.pt"),

    # Coins to process
    "coins": [
        "BTC",
        # "ETH",
        # "SOL",
    ],

    # Time windows (in seconds)
    "feature_window": 60 * MINUTE,  # 60 minutes of past data for features
    "sample_step": 5 * MINUTE,  # 5 minutes = step size for sliding window

    # Prediction horizons (in seconds)
    "horizons": [
        MINUTE,
        5 * MINUTE,    # 5 minutes
        10 * MINUTE,   # 10 minutes
        15 * MINUTE,    # 15 minutes
        # 30 * MINUTE,   # 30 minutes
        # HOUR,   # 1 hour
        # 4 * HOUR,  # 4 hours
        # 12 * HOUR,  # 12 hours
        # DAY,  # 1 day
        # 7 * DAY, # 7 days
        # 14 * DAY # 14 days
    ],

    "min_trades_per_address": 1,  # Minimum trades in window to consider address

    # Quantiles to predict
    "quantiles": [0.1, 0.5, 0.9],

    # K-line intervals for market features
    "kline_intervals": ["1m", "5m", "15m", "1h"],

    # Training params
    "train_split": 0.7,
    "val_split": 0.15,
    "test_split": 0.15,

    "batch_size": 256,
    "learning_rate": 1e-3,
    "num_epochs": 50,
    "early_stopping_patience": 5,

    # Model architecture
    "input_dim": 80,  # Will be computed dynamically
    "hidden_dims": [128, 64],
    "dropout": 0.1,

    # Device - MPS for Apple Silicon, CUDA for NVIDIA, CPU fallback
    "device": "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu"),

    # DataLoader optimizations for MPS
    "num_workers": 4,  # Number of worker processes for data loading
    "pin_memory": True,  # Pin memory for faster GPU transfer
    "persistent_workers": True,  # Keep workers alive between epochs
    "enable_amp": True,  # Enable automatic mixed precision for MPS/CUDA
    "enable_compile": False,  # Set True to use torch.compile (PyTorch 2.0+)

    # Sample building optimizations
    "profile_sample_building": False,  # Enable profiling to identify hotspots
    "enable_parallel_sample_building": False,  # Enable per-coin parallelism (experimental)
}


# 1) 设置进程亲和（Linux）
# try:
#     os.sched_setaffinity(0, {0, 1})  # 使用 CPU 0 和 1，改为你需要的集合
# except AttributeError:
#     pass  # Windows / 不支持时忽略

# 2) 限制数值/并行库线程
# os.environ.setdefault("OMP_NUM_THREADS", "4")
# os.environ.setdefault("MKL_NUM_THREADS", "4")
# os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

# 3) PyTorch 线程控制
# torch.set_num_threads(4)
# torch.set_num_interop_threads(2)
print(torch.get_num_threads(), torch.get_num_interop_threads())
# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class Candle:
    """K-line candle data."""
    t: int  # start timestamp in ms
    T: int  # end timestamp in ms
    s: str  # symbol
    i: str  # interval
    o: float  # open
    c: float  # close
    h: float  # high
    l: float  # low
    v: float  # volume
    n: int  # number of trades

    @classmethod
    def from_dict(cls, d: dict) -> "Candle":
        return cls(
            t=d["t"], T=d["T"], s=d["s"], i=d["i"],
            o=float(d["o"]), c=float(d["c"]), h=float(d["h"]),
            l=float(d["l"]), v=float(d["v"]), n=d.get("n", 0)
        )


@dataclass
class Trade:
    """Trade data."""
    id: int
    coin: str
    side: str  # "buy" or "sell"
    price: float
    size: float
    timestamp: float  # unix timestamp in seconds
    trade_id: int
    tx_hash: str
    taker: str
    maker: str

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            id=d["id"], coin=d["coin"], side=d["side"],
            price=float(d["price"]), size=float(d["size"]),
            timestamp=float(d["timestamp"]), trade_id=d["trade_id"],
            tx_hash=d["tx_hash"], taker=d["taker"], maker=d["maker"]
        )


@dataclass
class TrainingSample:
    """A single training sample."""
    timestamp: int  # Sample timestamp in seconds
    address: str  # Address being analyzed
    features: np.ndarray  # Combined feature vector
    targets: dict[str, float]  # Returns for each horizon key
    coin: str


# =============================================================================
# Feature Extractors
# =============================================================================

class AddressFeatureExtractor:
    """Extract features from an address's trading history."""

    def __init__(self, window_sec: int = 3600):
        self.window_sec = window_sec

    def extract(self, trades: list[Trade], current_price: float) -> dict[str, float]:
        """
        Extract features from trades within the window.

        Features:
        - Trade activity: count, total volume, avg/max size
        - Directionality: net volume, buy/sell ratio
        - Position changes: delta position
        - Behavior patterns: side switches, first/last side
        - Price interaction: % above VWAP, near high/low
        """
        if not trades:
            return self._zero_features()

        trade_count = len(trades)

        # Single pass to collect all data
        buy_volume = 0.0
        sell_volume = 0.0
        sizes = []
        prices = []
        first_side = 1 if trades[0].side == "buy" else -1
        last_side = 1 if trades[-1].side == "buy" else -1
        side_switches = 0
        prev_side = trades[0].side

        for t in trades:
            sizes.append(t.size)
            prices.append(t.price)

            if t.side == "buy":
                buy_volume += t.size * t.price
            else:
                sell_volume += t.size * t.price

            if t.side != prev_side:
                side_switches += 1
                prev_side = t.side

        # Volume metrics
        gross_volume = buy_volume + sell_volume
        net_volume = buy_volume - sell_volume

        # Size metrics - use numpy for efficiency
        sizes_arr = np.array(sizes, dtype=np.float32)
        avg_size = float(sizes_arr.mean())
        max_size = float(sizes_arr.max())
        std_size = float(sizes_arr.std()) if len(sizes) > 1 else 0.0

        # Directionality
        net_vol_ratio = net_volume / gross_volume if gross_volume > 0 else 0.0
        buy_count = sum(1 for t in trades if t.side == "buy")
        buy_ratio = buy_count / trade_count if trade_count > 0 else 0.0

        # Position changes
        delta_position = net_volume / current_price if current_price > 0 else 0.0

        # Price interaction - compute vwap in single pass
        prices_arr = np.array(prices, dtype=np.float32)
        total_size = float(sizes_arr.sum())
        if total_size > 0:
            vwap = float(np.sum(prices_arr * sizes_arr) / total_size)
        else:
            vwap = current_price

        above_vwap_count = np.sum(prices_arr > vwap)
        above_vwap_ratio = above_vwap_count / trade_count if trade_count > 0 else 0.0

        # Price momentum
        if len(prices) >= 2:
            price_change = float((prices_arr[-1] - prices_arr[0]) / prices_arr[0])
            price_volatility = float(np.std(prices_arr / prices_arr[0]))
        else:
            price_change = 0.0
            price_volatility = 0.0

        # Time distribution
        if len(trades) >= 2:
            time_span = trades[-1].timestamp - trades[0].timestamp
            trade_frequency = trade_count / time_span if time_span > 0 else 0.0
        else:
            trade_frequency = 0.0

        # Whale indicator: 1 if max_size is significantly larger than average (2x threshold)
        is_whale = 1 if (avg_size > 0 and max_size > 2 * avg_size) else 0

        return {
            # Activity (6 features)
            "trade_count": trade_count,
            "gross_volume": gross_volume,
            "avg_size": avg_size,
            "max_size": max_size,
            "std_size": std_size,
            "trade_frequency": trade_frequency,

            # Directionality (5 features)
            "net_volume": net_volume,
            "net_vol_ratio": net_vol_ratio,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_ratio": buy_ratio,

            # Position (1 feature)
            "delta_position": delta_position,

            # Behavior (4 features)
            "first_side": first_side,
            "last_side": last_side,
            "side_switches": side_switches,
            "side_switch_ratio": side_switches / trade_count if trade_count > 1 else 0.0,

            # Price interaction (3 features)
            "above_vwap_ratio": above_vwap_ratio,
            "price_change": price_change,
            "price_volatility": price_volatility,

            # Whale indicator (1 feature)
            "is_whale": is_whale,
        }

    def _zero_features(self) -> dict[str, float]:
        """Return zero features for addresses with no trades."""
        return {
            "trade_count": 0, "gross_volume": 0, "avg_size": 0, "max_size": 0,
            "std_size": 0, "trade_frequency": 0, "net_volume": 0, "net_vol_ratio": 0,
            "buy_volume": 0, "sell_volume": 0, "buy_ratio": 0, "delta_position": 0,
            "first_side": 0, "last_side": 0, "side_switches": 0, "side_switch_ratio": 0,
            "above_vwap_ratio": 0, "price_change": 0, "price_volatility": 0, "is_whale": 0,
        }


class MarketFeatureExtractor:
    """Extract market features from K-lines."""

    def __init__(self, intervals: Optional[list[str]] = None):
        self.intervals = intervals or ["1m", "5m", "15m", "1h"]
        self._cached_df: Optional[pd.DataFrame] = None
        self._cached_candles: Optional[list[Candle]] = None

    def extract(self, candles_1m: list[Candle], timestamp_ms: int) -> dict[str, float]:
        """
        Extract market features at a given timestamp.

        Features:
        - OHLCV from multiple timeframes
        - Technical indicators: RSI, MACD, Bollinger Bands
        - Volatility, momentum
        """
        if not candles_1m:
            return self._zero_features()

        # Cache DataFrame if candles haven't changed
        if self._cached_candles is not candles_1m:
            self._cached_df = self._candles_to_df(candles_1m)
            self._cached_candles = candles_1m

        df = self._cached_df
        if df is None or df.empty or len(df) < 14:
            return self._zero_features()

        # Current candle
        current_idx = df.index.get_loc(timestamp_ms) if timestamp_ms in df.index else len(df) - 1
        current = df.iloc[current_idx]

        features = {}

        # Current price features (5 features)
        current_close = current["close"]
        features["current_price"] = current_close
        features["open"] = current["open"]
        features["high"] = current["high"]
        features["low"] = current["low"]
        features["volume"] = current["volume"]

        # Returns (4 features) - use vectorized operations
        close_series = df["close"]
        if current_idx >= 1:
            features["return_1m"] = (current_close - close_series.iloc[current_idx - 1]) / close_series.iloc[current_idx - 1]
        else:
            features["return_1m"] = 0.0

        if current_idx >= 5:
            features["return_5m"] = (current_close - close_series.iloc[current_idx - 5]) / close_series.iloc[current_idx - 5]
        else:
            features["return_5m"] = 0.0

        if current_idx >= 15:
            features["return_15m"] = (current_close - close_series.iloc[current_idx - 15]) / close_series.iloc[current_idx - 15]
        else:
            features["return_15m"] = 0.0

        if current_idx >= 60:
            features["return_1h"] = (current_close - close_series.iloc[current_idx - 60]) / close_series.iloc[current_idx - 60]
        else:
            features["return_1h"] = 0.0

        # Moving averages (3 features) - reuse slices
        if current_idx >= 20:
            close_window_20 = close_series.iloc[current_idx - 20:current_idx + 1]
            ma_20 = float(close_window_20.mean())
            features["ma_20"] = ma_20
            features["price_vs_ma20"] = (current_close - ma_20) / ma_20
        else:
            features["ma_20"] = current_close
            features["price_vs_ma20"] = 0.0

        if current_idx >= 50:
            close_window_50 = close_series.iloc[current_idx - 50:current_idx + 1]
            ma_50 = float(close_window_50.mean())
            features["ma_50"] = ma_50
            features["price_vs_ma50"] = (current_close - ma_50) / ma_50
        else:
            features["ma_50"] = current_close
            features["price_vs_ma50"] = 0.0

        # Volatility (2 features) - reuse computed windows
        if current_idx >= 20:
            returns = close_series.iloc[current_idx - 20:current_idx + 1].pct_change().dropna()
            features["volatility_20"] = float(returns.std()) if len(returns) > 0 else 0.0
        else:
            features["volatility_20"] = 0.0

        if current_idx >= 60:
            returns = close_series.iloc[current_idx - 60:current_idx + 1].pct_change().dropna()
            features["volatility_60"] = float(returns.std()) if len(returns) > 0 else 0.0
        else:
            features["volatility_60"] = 0.0

        # RSI (1 feature)
        features["rsi_14"] = self._calculate_rsi(close_series.values, current_idx)

        # Bollinger Bands (2 features) - reuse window
        if current_idx >= 20:
            bb_period = 20
            bb_std = 2
            close_window_bb = close_series.iloc[current_idx - bb_period:current_idx + 1]
            bb_middle = float(close_window_bb.mean())
            bb_std_val = float(close_window_bb.std())
            bb_upper = bb_middle + bb_std * bb_std_val
            bb_lower = bb_middle - bb_std * bb_std_val
            bb_range = bb_upper - bb_lower
            features["bb_percent"] = (current_close - bb_lower) / bb_range if bb_range > 0 else 0.5
            features["bb_width"] = bb_range / bb_middle if bb_middle > 0 else 0.0
        else:
            features["bb_percent"] = 0.5
            features["bb_width"] = 0.0

        # Volume features (3 features) - reuse window
        if current_idx >= 20:
            volume_window_20 = df["volume"].iloc[current_idx - 20:current_idx + 1]
            volume_ma_20 = float(volume_window_20.mean())
            features["volume_ma_20"] = volume_ma_20
            features["volume_ratio"] = current["volume"] / volume_ma_20 if volume_ma_20 > 0 else 1.0
        else:
            features["volume_ma_20"] = current["volume"]
            features["volume_ratio"] = 1.0

        if current_idx >= 5:
            vol_5_ago = df["volume"].iloc[current_idx - 5]
            features["volume_change_5"] = (current["volume"] - vol_5_ago) / vol_5_ago if vol_5_ago > 0 else 0.0
        else:
            features["volume_change_5"] = 0.0

        return features

    def _candles_to_df(self, candles: list[Candle]) -> pd.DataFrame:
        """Convert candles to pandas DataFrame."""
        data = [{
            "timestamp": c.t,
            "open": c.o,
            "high": c.h,
            "low": c.l,
            "close": c.c,
            "volume": c.v,
        } for c in candles]
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.set_index("timestamp").sort_index()
        return df

    def _calculate_rsi(self, prices: np.ndarray, idx: int, period: int = 14) -> float:
        """Calculate RSI at a given index."""
        if idx < period:
            logger.warning(f"Insufficient data for RSI calculation, idx: {idx}")
            return 50.0

        recent_prices = prices[idx - period:idx + 1]
        deltas = np.diff(recent_prices)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi)

    def _zero_features(self) -> dict[str, float]:
        """Return zero features when insufficient data."""
        keys = [
            "current_price", "open", "high", "low", "volume",
            "return_1m", "return_5m", "return_15m", "return_1h",
            "ma_20", "price_vs_ma20", "ma_50", "price_vs_ma50",
            "volatility_20", "volatility_60", "rsi_14",
            "bb_percent", "bb_width", "volume_ma_20", "volume_ratio", "volume_change_5"
        ]
        return {k: 0.0 for k in keys}


# =============================================================================
# Data Loader
# =============================================================================

class TrainingDataBuilder:
    """Build training samples from raw trades and candle data."""

    def __init__(
        self,
        feature_window: int,
        sample_step: int,
        horizons: list[int],
        min_trades: int,
        enable_parallel: bool = False,
        profile: bool = False,
    ):
        self.feature_window = feature_window
        self.sample_step = sample_step
        self.horizons = horizons
        self.min_trades = min_trades
        self.enable_parallel = enable_parallel
        self.profile = profile
        self.addr_extractor = AddressFeatureExtractor(feature_window)
        self.market_extractor = MarketFeatureExtractor()

        # Prebuild feature order for vectorized assembly
        dummy_addr = self.addr_extractor._zero_features()
        dummy_market = self.market_extractor._zero_features()
        self.feature_order = list(dummy_addr.keys()) + list(dummy_market.keys())
        self.addr_feature_count = len(dummy_addr)

    def build_samples(
        self,
        trades: list[Trade],
        candles_1m: list[Candle],
        coin: str,
    ) -> list[TrainingSample]:
        """
        Build training samples using sliding window.

        For each time step:
        1. Get trades in [t - window, t) for each address
        2. Extract address features
        3. Extract market features
        4. Calculate future returns for each horizon

        Optimizations:
        - Precompute market features per timestamp (avoid re-extraction)
        - Use sliding window over sorted trades instead of per-iter filtering
        - Vectorize feature assembly using prebuilt feature order
        """
        samples = []

        # Timing accumulators for profiling
        timings = {
            "prep": 0.0,
            "market_cache": 0.0,
            "trades_group": 0.0,
            "loop": 0.0,
            "window_trades": 0.0,
            "addr_extract": 0.0,
            "feature_assembly": 0.0,
            "targets": 0.0,
        }
        t0 = time.time()

        # Build timestamp -> price mapping
        candles_by_ts = {c.t: c for c in candles_1m}
        all_timestamps = sorted(candles_by_ts.keys())

        # Determine start time (need window + max horizon data)
        max_horizon = max(self.horizons)
        start_idx = int(self.feature_window / MINUTE) + 1  # Buffer for feature window

        logger.info(f"Building samples for {coin}: {len(all_timestamps)} candles, start_idx={start_idx}, max_horizon={max_horizon // (MINUTE)}m")

        # OPTIMIZATION 1: Precompute market features per timestamp
        t_cache = time.time()
        market_features_cache: dict[int, dict[str, float]] = {}
        for ts in all_timestamps:
            market_features_cache[ts] = self.market_extractor.extract(candles_1m, ts)
        timings["market_cache"] = time.time() - t_cache

        # OPTIMIZATION 2: Sort trades by timestamp and group by address upfront
        t_group = time.time()
        trades_sorted = sorted(trades, key=lambda t: t.timestamp)

        # Group trades by address, maintaining sorted order
        addr_trades_sorted: dict[str, list[Trade]] = {}
        for tr in trades_sorted:
            if tr.taker not in addr_trades_sorted:
                addr_trades_sorted[tr.taker] = []
            addr_trades_sorted[tr.taker].append(tr)
        timings["trades_group"] = time.time() - t_group

        timings["prep"] = time.time() - t0
        t_loop = time.time()

        # Pre-allocate numpy arrays for feature vectors (optional optimization)
        # We'll still use lists for samples as we don't know count upfront

        # Calculate step in minutes for the range
        step_minutes = self.sample_step // MINUTE

        for i in tqdm(range(start_idx, len(all_timestamps) - max_horizon // MINUTE - 1, step_minutes), desc=f"Processing {coin}"):
            iter_t0 = time.time()

            t_ms = all_timestamps[i]
            t_sec = t_ms // 1000
            window_start = t_sec - self.feature_window

            # Current price
            current_candle = candles_by_ts[t_ms]
            current_price = current_candle.c

            # OPTIMIZATION 3: Use sliding window instead of full filter
            # Build per-address trade windows using binary search + slice
            t_window = time.time()

            active_addresses: list[str] = []
            addr_windows: dict[str, list[Trade]] = {}

            for addr, addr_trade_list in addr_trades_sorted.items():
                # Binary search for window boundaries (trades are sorted)
                left = 0
                right = len(addr_trade_list)

                # Find left bound (first trade >= window_start)
                while left < right:
                    mid = (left + right) // 2
                    if addr_trade_list[mid].timestamp < window_start:
                        left = mid + 1
                    else:
                        right = mid

                start_idx_trades = left

                # Find right bound (first trade >= t_sec)
                left = start_idx_trades
                right = len(addr_trade_list)
                while left < right:
                    mid = (left + right) // 2
                    if addr_trade_list[mid].timestamp < t_sec:
                        left = mid + 1
                    else:
                        right = mid

                end_idx_trades = left

                window_trades = addr_trade_list[start_idx_trades:end_idx_trades]

                if len(window_trades) >= self.min_trades:
                    active_addresses.append(addr)
                    addr_windows[addr] = window_trades

            timings["window_trades"] += time.time() - t_window

            # Extract features for active addresses
            t_extract = time.time()

            # Get cached market features for this timestamp
            market_features = market_features_cache[t_ms]

            for addr in active_addresses:
                addr_trade_list = addr_windows[addr]

                # Extract address features
                addr_features = self.addr_extractor.extract(addr_trade_list, current_price)

                # OPTIMIZATION 4: Vectorized feature assembly
                t_assemble = time.time()
                feature_vector = np.empty(len(self.feature_order), dtype=np.float32)
                for idx, key in enumerate(self.feature_order):
                    if idx < self.addr_feature_count:
                        feature_vector[idx] = addr_features[key]
                    else:
                        feature_vector[idx] = market_features[key]
                timings["feature_assembly"] += time.time() - t_assemble

                # Calculate targets (future returns)
                t_targets = time.time()
                targets = {}
                valid_sample = True

                for horizon_sec in self.horizons:
                    future_t_ms = t_ms + horizon_sec * 1000

                    # Find the candle at or after future_t_ms
                    future_candle = self._find_future_candle(all_timestamps, candles_by_ts, future_t_ms)

                    if future_candle is None:
                        valid_sample = False
                        break

                    future_price = future_candle.c
                    ret = (future_price - current_price) / current_price if current_price > 0 else 0
                    targets[f"return_{horizon_sec}s"] = ret

                timings["targets"] += time.time() - t_targets

                if valid_sample:
                    samples.append(TrainingSample(
                        timestamp=t_sec,
                        address=addr,
                        features=feature_vector,
                        targets=targets,
                        coin=coin,
                    ))

            timings["loop"] += time.time() - iter_t0

        total_time = time.time() - t0
        logger.info(f"Generated {len(samples)} samples for {coin} in {total_time:.2f}s")

        if self.profile:
            logger.info("=== Profile timings ===")
            for name, val in timings.items():
                pct = val / total_time * 100 if total_time > 0 else 0
                logger.info(f"  {name}: {val:.3f}s ({pct:.1f}%)")

        return samples

    def _find_future_candle(
        self,
        timestamps: list[int],
        candles_by_ts: dict[int, Candle],
        target_ms: int,
    ) -> Optional[Candle]:
        """Find the first candle at or after target_ms."""
        # Binary search for efficiency
        left, right = 0, len(timestamps)
        while left < right:
            mid = (left + right) // 2
            if timestamps[mid] < target_ms:
                left = mid + 1
            else:
                right = mid

        if left < len(timestamps):
            return candles_by_ts[timestamps[left]]
        return None


class PricePredictionDataset(Dataset):
    """PyTorch dataset for price prediction."""

    def __init__(
        self,
        samples: list[TrainingSample],
        feature_scaler: Optional[StandardScaler] = None,
        horizons: Optional[list[int]] = None,
        quantiles: Optional[list[float]] = None,
    ):
        self.samples = samples
        self.horizons = horizons or [300, 3600, 14400, 43200, 86400, 604800, 1209600]
        self.quantiles = quantiles or [0.1, 0.5, 0.9]

        # Extract features and targets
        features = np.stack([s.features for s in samples])

        # Initialize or fit feature scaler
        if feature_scaler is None:
            self.feature_scaler = StandardScaler()
            self.feature_scaler.fit(features)
        else:
            self.feature_scaler = feature_scaler

        self.features = torch.FloatTensor(self.feature_scaler.transform(features))

        # Extract targets for each horizon
        self.targets = {}
        for h in self.horizons:
            key = f"return_{h}s"
            targets_h = np.array([s.targets.get(key, 0) for s in samples])
            self.targets[h] = torch.FloatTensor(targets_h).unsqueeze(1)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, dict[int, torch.Tensor]]:
        return self.features[idx], {h: self.targets[h][idx] for h in self.horizons}


# =============================================================================
# Model Definition
# =============================================================================

class QuantileLoss(nn.Module):
    """Pinball loss for quantile regression."""

    def __init__(self, quantiles: Optional[list[float]] = None):
        super().__init__()
        # Register quantiles as buffer - they'll move with the model and won't be treated as parameters
        quantile_list = quantiles or [0.1, 0.5, 0.9]
        self.register_buffer('quantiles', torch.tensor(quantile_list, dtype=torch.float32))

    def forward(
        self,
        predictions: torch.Tensor,  # [batch, num_quantiles]
        targets: torch.Tensor,      # [batch, 1]
    ) -> torch.Tensor:
        """
        Calculate pinball loss for each quantile.

        L_q(y, y_hat) = max(q * (y - y_hat), (q - 1) * (y - y_hat))
        """
        batch_size = predictions.shape[0]
        targets_expanded = targets.expand(-1, len(self.quantiles))

        errors = targets_expanded - predictions
        # Quantiles are already on the correct device via register_buffer
        quantiles = self.quantiles.to(predictions.device)

        losses = torch.max(
            quantiles * errors,
            (quantiles - 1) * errors
        )

        return losses.mean()


class MultiTaskQuantileModel(nn.Module):
    """
    Multi-task MLP with separate quantile heads for each horizon.

    Architecture:
        Input -> Shared MLP -> Horizon-specific Heads -> Quantile Outputs
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dims: list[int] = [128, 64],
        horizons: Optional[list[int]] = None,
        quantiles: Optional[list[float]] = None,
        dropout: float = 0.1,
    ):
        super().__init__()

        self.horizons = horizons or [300, 3600, 14400, 43200, 86400, 604800, 1209600]
        self.quantiles = quantiles or [0.1, 0.5, 0.9]

        # Shared backbone
        layers = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.append(nn.Linear(prev_dim, hidden_dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            prev_dim = hidden_dim
        self.backbone = nn.Sequential(*layers)

        # Horizon-specific heads
        self.heads = nn.ModuleDict({
            f"h_{h}": nn.Linear(prev_dim, len(self.quantiles))
            for h in self.horizons
        })

    def forward(self, x: torch.Tensor) -> dict[int, torch.Tensor]:
        """
        Forward pass.

        Args:
            x: [batch, input_dim]

        Returns:
            Dict mapping horizon to predictions [batch, num_quantiles]
        """
        shared = self.backbone(x)

        outputs = {}
        for h in self.horizons:
            outputs[h] = self.heads[f"h_{h}"](shared)

        return outputs


# =============================================================================
# Training Pipeline
# =============================================================================

class Trainer:
    """Training pipeline for multi-task quantile regression."""

    def __init__(
        self,
        model: MultiTaskQuantileModel,
        train_loader: DataLoader,
        val_loader: DataLoader,
        horizons: list[int],
        quantiles: list[float],
        learning_rate: float = 1e-3,
        device: str = "cpu",
        enable_amp: bool = False,
        enable_compile: bool = False,
    ):
        self.model = model.to(device)
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.horizons = horizons
        self.quantiles = quantiles
        self.device = device
        self.enable_amp = enable_amp and device in ("mps", "cuda")

        # Log device configuration
        logger.info(f"Using device: {device}")
        if self.enable_amp:
            logger.info(f"AMP (Automatic Mixed Precision) enabled for {device}")

        # Optional torch.compile for PyTorch 2.0+
        if enable_compile:
            try:
                self.model = torch.compile(self.model)
                logger.info("Model compiled with torch.compile")
            except Exception as e:
                logger.warning(f"torch.compile not available: {e}")

        self.criterion = QuantileLoss(quantiles)
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer, mode='min', factor=0.5, patience=3
        )

        self.best_val_loss = float('inf')
        self.patience_counter = 0

        # AMP GradScaler for mixed precision training
        self.scaler = torch.GradScaler() if self.enable_amp else None

    def train_epoch(self) -> float:
        """Train for one epoch."""
        self.model.train()
        total_loss = 0
        num_batches = 0

        for features, targets_dict in self.train_loader:
            features = features.to(self.device, non_blocking=True)
            targets_dict = {k: v.to(self.device, non_blocking=True) for k, v in targets_dict.items()}

            self.optimizer.zero_grad()

            if self.enable_amp and self.scaler is not None:
                # AMP forward pass
                with torch.autocast(device_type=self.device):
                    predictions = self.model(features)
                    loss = 0
                    for h in self.horizons:
                        pred_h = predictions[h]
                        target_h = targets_dict[h]
                        loss += self.criterion(pred_h, target_h)
                    loss = loss / len(self.horizons)

                # AMP backward pass
                self.scaler.scale(loss).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                # Standard forward pass
                predictions = self.model(features)
                loss = 0
                for h in self.horizons:
                    pred_h = predictions[h]
                    target_h = targets_dict[h]
                    loss += self.criterion(pred_h, target_h)
                loss = loss / len(self.horizons)

                # Standard backward pass
                loss.backward()
                self.optimizer.step()

            total_loss += loss.item()
            num_batches += 1

        return total_loss / num_batches

    def validate(self) -> float:
        """Validate the model."""
        self.model.eval()
        total_loss = 0
        num_batches = 0

        with torch.no_grad():
            for features, targets_dict in self.val_loader:
                features = features.to(self.device, non_blocking=True)
                targets_dict = {k: v.to(self.device, non_blocking=True) for k, v in targets_dict.items()}

                if self.enable_amp:
                    with torch.autocast(device_type=self.device):
                        predictions = self.model(features)
                        loss = 0
                        for h in self.horizons:
                            pred_h = predictions[h]
                            target_h = targets_dict[h]
                            loss += self.criterion(pred_h, target_h)
                        loss = loss / len(self.horizons)
                else:
                    predictions = self.model(features)
                    loss = 0
                    for h in self.horizons:
                        pred_h = predictions[h]
                        target_h = targets_dict[h]
                        loss += self.criterion(pred_h, target_h)
                    loss = loss / len(self.horizons)

                total_loss += loss.item()
                num_batches += 1

        return total_loss / num_batches

    def train(self, num_epochs: int, patience: int = 5) -> dict:
        """
        Full training loop with early stopping.

        Returns:
            Training history dict
        """
        history = {"train_loss": [], "val_loss": []}

        logger.info(f"Starting training for {num_epochs} epochs...")
        logger.info(f"Device: {self.device}")
        logger.info(f"Model parameters: {sum(p.numel() for p in self.model.parameters()):,}")

        for epoch in range(num_epochs):
            train_loss = self.train_epoch()
            val_loss = self.validate()

            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)

            self.scheduler.step(val_loss)

            logger.info(
                f"Epoch {epoch + 1}/{num_epochs} - "
                f"Train Loss: {train_loss:.6f}, Val Loss: {val_loss:.6f}"
            )

            # Early stopping
            if val_loss < self.best_val_loss:
                self.best_val_loss = val_loss
                self.patience_counter = 0
                self._save_checkpoint()
            else:
                self.patience_counter += 1
                if self.patience_counter >= patience:
                    logger.info(f"Early stopping at epoch {epoch + 1}")
                    break

        self._load_checkpoint()
        return history

    def _save_checkpoint(self):
        """Save best model checkpoint."""
        torch.save(self.model.state_dict(), "best_model.pt")

    def _load_checkpoint(self):
        """Load best model checkpoint."""
        self.model.load_state_dict(torch.load("best_model.pt"))


# =============================================================================
# Evaluation Metrics
# =============================================================================

class Evaluator:
    """Evaluate model predictions."""

    def __init__(
        self,
        model: MultiTaskQuantileModel,
        test_loader: DataLoader,
        horizons: list[int],
        quantiles: list[float],
        device: str = "cpu",
    ):
        self.model = model.to(device)
        self.model.eval()
        self.test_loader = test_loader
        self.horizons = horizons
        self.quantiles = quantiles
        self.device = device

        # Find median index (usually 0.5 is index 1 if quantiles=[0.1, 0.5, 0.9])
        self.median_idx = quantiles.index(0.5) if 0.5 in quantiles else len(quantiles) // 2

    def evaluate(self) -> dict:
        """
        Compute evaluation metrics.

        Metrics:
        - Pinball loss per quantile and horizon
        - Direction accuracy (using median prediction)
        - Coverage (% of true values in [P10, P90] interval)
        """
        self.model.eval()

        # Accumulate predictions and targets
        all_predictions: dict[int, list[torch.Tensor]] = {h: [] for h in self.horizons}
        all_targets: dict[int, list[torch.Tensor]] = {h: [] for h in self.horizons}

        with torch.no_grad():
            for features, targets_dict in self.test_loader:
                features = features.to(self.device)
                predictions = self.model(features)

                for h in self.horizons:
                    all_predictions[h].append(predictions[h].cpu())
                    all_targets[h].append(targets_dict[h].cpu())

        # Concatenate all batches
        pred_concat = {}
        target_concat = {}
        for h in self.horizons:
            pred_concat[h] = torch.cat(all_predictions[h], dim=0)  # [N, num_quantiles]
            target_concat[h] = torch.cat(all_targets[h], dim=0)    # [N, 1]

        # Calculate metrics
        metrics = {}

        for h in self.horizons:
            pred_h = pred_concat[h]
            target_h = target_concat[h]

            # Pinball loss per quantile
            for i, q in enumerate(self.quantiles):
                errors = target_h - pred_h[:, i:i+1]
                loss = torch.max(q * errors, (q - 1) * errors).mean()
                metrics[f"pinball_q{q}_h{h}"] = loss.item()

            # Direction accuracy (using median)
            pred_median = pred_h[:, self.median_idx]
            actual_direction = (target_h.squeeze() > 0).float()
            pred_direction = (pred_median > 0).float()
            accuracy = (actual_direction == pred_direction).float().mean()
            metrics[f"direction_accuracy_h{h}"] = accuracy.item()

            # Coverage (P10 to P90)
            if 0.1 in self.quantiles and 0.9 in self.quantiles:
                p10_idx = self.quantiles.index(0.1)
                p90_idx = self.quantiles.index(0.9)
                in_interval = (
                    (target_h.squeeze() >= pred_h[:, p10_idx]) &
                    (target_h.squeeze() <= pred_h[:, p90_idx])
                ).float()
                coverage = in_interval.mean()
                metrics[f"coverage_p10_p90_h{h}"] = coverage.item()

        return metrics


# =============================================================================
# Main Pipeline
# =============================================================================

def load_data(
    data_dir: Path,
    coins: list[str],
) -> tuple[dict[str, list[Trade]], dict[str, list[Candle]]]:
    """Load trades and candles from JSON files."""

    all_trades: dict[str, list[Trade]] = {}
    all_candles: dict[str, list[Candle]] = {}

    for coin in coins:
        # Load trades
        trade_files = list((data_dir / "trades").glob(f"{coin}_trades_*.json"))
        if trade_files:
            with trade_files[0].open("r") as f:
                trade_data = json.load(f)
            all_trades[coin] = [Trade.from_dict(t) for t in trade_data]
            logger.info(f"Loaded {len(all_trades[coin])} trades for {coin}")

        # Load 1m candles
        candle_files = list((data_dir / "candles").glob(f"{coin}_1m_candles_*.json"))
        if candle_files:
            with candle_files[0].open("r") as f:
                candle_data = json.load(f)
            all_candles[coin] = [Candle.from_dict(c) for c in candle_data]
            logger.info(f"Loaded {len(all_candles[coin])} 1m candles for {coin}")

    return all_trades, all_candles


# =============================================================================
# Verification Pipeline
# =============================================================================

def verify_model() -> None:
    """
    Run verification on hold-out data and save results for dashboard.

    Generates verify_results.json with:
    - Per-sample predictions and actuals
    - Aggregated metrics by horizon
    """
    import uuid
    from datetime import datetime, timezone

    logger.info("=" * 60)
    logger.info("Verification Pipeline")
    logger.info("=" * 60)

    # Load verification candles
    verify_dir = Path("./temp-data/verify-candles")
    results = {}
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    for coin in CONFIG["coins"]:
        logger.info(f"Verifying {coin}...")

        # Load verification candles
        verify_files = list(verify_dir.glob(f"{coin}_1m_candles_*.json"))
        if not verify_files:
            logger.warning(f"No verify candles found for {coin}")
            continue

        with verify_files[0].open("r") as f:
            candle_data = json.load(f)
        verify_candles = [Candle.from_dict(c) for c in candle_data]
        logger.info(f"Loaded {len(verify_candles)} verify candles for {coin}")

        # Load trained model
        model_path = CONFIG["model_path"]
        if not model_path.exists():
            logger.error(f"Model not found at {model_path}. Please train first.")
            return

        # Create a dummy sample to get the correct feature dimension
        market_extractor = MarketFeatureExtractor()
        addr_extractor = AddressFeatureExtractor(CONFIG["feature_window"])
        dummy_addr_features = addr_extractor._zero_features()

        # Use the first candle to extract market features and get feature dim
        if verify_candles:
            first_ts = verify_candles[0].t
            dummy_market_features = market_extractor.extract(verify_candles, first_ts)
            all_features = {**dummy_addr_features, **dummy_market_features}
            feature_dim = len(all_features)  # Total number of features
        else:
            feature_dim = 80  # Fallback

        model = MultiTaskQuantileModel(
            input_dim=feature_dim,
            hidden_dims=CONFIG["hidden_dims"],
            horizons=CONFIG["horizons"],
            quantiles=CONFIG["quantiles"],
            dropout=CONFIG["dropout"],
        )

        model.load_state_dict(torch.load(model_path, map_location=CONFIG["device"]))
        model.to(CONFIG["device"])
        model.eval()

        # Load feature scaler from training
        scaler_path = Path("./temp-data/feature_scaler.pkl")
        if scaler_path.exists():
            feature_scaler = joblib.load(scaler_path)
            logger.info(f"Loaded feature scaler from {scaler_path}")
            logger.info(f"  Scaler feature dim: {len(feature_scaler.mean_)}")
            logger.info(f"  Current feature dim: {feature_dim}")

            # Check if dimensions match
            if len(feature_scaler.mean_) != feature_dim:
                logger.warning(f"Feature dimension mismatch! Scaler: {len(feature_scaler.mean_)}, Model: {feature_dim}")
                logger.warning(f"This will cause prediction errors. Need to retrain or adjust features.")
        else:
            logger.warning(f"Feature scaler not found at {scaler_path}")
            logger.warning("Using dummy scaler (zero mean, unit variance) - predictions will be inaccurate!")
            feature_scaler = StandardScaler()
            feature_scaler.mean_ = np.zeros(feature_dim)
            feature_scaler.scale_ = np.ones(feature_dim)

        # Build verify samples
        builder = TrainingDataBuilder(
            feature_window=CONFIG["feature_window"],
            sample_step=CONFIG["sample_step"],
            horizons=CONFIG["horizons"],
            min_trades=CONFIG["min_trades_per_address"],
            enable_parallel=CONFIG["enable_parallel_sample_building"],
            profile=CONFIG["profile_sample_building"],
        )

        # Note: We need trades for verification too, but since we're evaluating
        # on market data, we'll use a simplified approach
        samples = []
        candles_by_ts = {c.t: c for c in verify_candles}
        all_timestamps = sorted(candles_by_ts.keys())

        max_horizon = max(CONFIG["horizons"])
        start_idx = int(CONFIG["feature_window"] / MINUTE) + 1

        # Precompute market features cache for efficiency
        market_features_cache: dict[int, dict[str, float]] = {}
        for ts in all_timestamps:
            market_features_cache[ts] = market_extractor.extract(verify_candles, ts)

        # Calculate step in minutes for the range
        step_minutes = builder.sample_step // MINUTE

        logger.info(f"Generating verification samples for {coin}...")
        for i in tqdm(range(start_idx, len(all_timestamps) - max_horizon // MINUTE - 1, step_minutes), desc=f"Verify {coin}"):
            t_ms = all_timestamps[i]
            t_sec = t_ms // 1000

            current_candle = candles_by_ts[t_ms]
            current_price = current_candle.c

            # Get cached market features for this timestamp
            market_features = market_features_cache[t_ms]

            # Combine features using the same order as TrainingDataBuilder
            feature_vector = np.empty(len(builder.feature_order), dtype=np.float32)
            for idx, key in enumerate(builder.feature_order):
                if idx < builder.addr_feature_count:
                    feature_vector[idx] = dummy_addr_features[key]
                else:
                    feature_vector[idx] = market_features[key]

            # Calculate targets
            targets = {}
            valid_sample = True

            for horizon_sec in CONFIG["horizons"]:
                future_t_ms = t_ms + horizon_sec * 1000
                future_candle = builder._find_future_candle(all_timestamps, candles_by_ts, future_t_ms)

                if future_candle is None:
                    valid_sample = False
                    break

                future_price = future_candle.c
                ret = (future_price - current_price) / current_price if current_price > 0 else 0
                targets[f"return_{horizon_sec}s"] = ret

            if valid_sample:
                samples.append(TrainingSample(
                    timestamp=t_sec,
                    address="market_only",
                    features=feature_vector,
                    targets=targets,
                    coin=coin,
                ))

        logger.info(f"Generated {len(samples)} verification samples for {coin}")

        if len(samples) == 0:
            logger.warning(f"No samples generated for {coin}")
            continue

        # Get quantile indices dynamically for robustness
        quantiles = CONFIG["quantiles"]
        p10_idx = quantiles.index(0.1) if 0.1 in quantiles else None
        p50_idx = quantiles.index(0.5) if 0.5 in quantiles else None
        p90_idx = quantiles.index(0.9) if 0.9 in quantiles else None

        # Get predictions
        all_predictions = {h: [] for h in CONFIG["horizons"]}
        all_targets = {h: [] for h in CONFIG["horizons"]}

        sample_list = []
        model.eval()

        with torch.no_grad():
            for sample in samples:
                features = torch.FloatTensor(feature_scaler.transform(sample.features.reshape(1, -1))).to(CONFIG["device"])
                pred_dict = model(features)

                sample_data = {
                    "timestamp": sample.timestamp,
                    "open": None,  # Will fill from candle
                    "high": None,
                    "low": None,
                    "close": None,
                    "volume": None,
                    "targets": {},
                    "predictions": {},
                }

                # Get candle data
                if sample.timestamp * 1000 in candles_by_ts:
                    c = candles_by_ts[sample.timestamp * 1000]
                    sample_data["open"] = c.o
                    sample_data["high"] = c.h
                    sample_data["low"] = c.l
                    sample_data["close"] = c.c
                    sample_data["volume"] = c.v

                for h in CONFIG["horizons"]:
                    h_key = f"return_{h}s"
                    pred = pred_dict[h][0].cpu().numpy()  # [num_quantiles]

                    sample_data["targets"][h_key] = sample.targets[h_key]
                    sample_data["predictions"][h_key] = {
                        "p10": float(pred[p10_idx]) if p10_idx is not None else None,
                        "p50": float(pred[p50_idx]) if p50_idx is not None else None,
                        "p90": float(pred[p90_idx]) if p90_idx is not None else None,
                    }

                    all_predictions[h].append(pred)
                    all_targets[h].append(sample.targets[h_key])

                sample_list.append(sample_data)

        # Calculate metrics
        metrics = {}
        metrics["run_id"] = run_id
        metrics["generated_at"] = int(time.time())
        metrics["coin"] = coin
        metrics["metrics"] = {}

        for h in CONFIG["horizons"]:
            pred_h = np.array(all_predictions[h])  # [N, num_quantiles]
            target_h = np.array(all_targets[h])    # [N]

            h_metrics = {}

            # Pinball loss per quantile
            for i, q in enumerate(CONFIG["quantiles"]):
                errors = target_h - pred_h[:, i]
                loss = np.mean(np.maximum(q * errors, (q - 1) * errors))
                h_metrics[f"pinball_p{int(q*100)}"] = float(loss)

            # Direction accuracy
            if p50_idx is not None:
                pred_median = pred_h[:, p50_idx]
                actual_direction = target_h > 0
                pred_direction = pred_median > 0
                accuracy = np.mean(actual_direction == pred_direction)
                h_metrics["direction_accuracy"] = float(accuracy)
            else:
                h_metrics["direction_accuracy"] = None

            h_metrics["sample_count"] = len(target_h)

            # Coverage
            if p10_idx is not None and p90_idx is not None:
                in_interval = (target_h >= pred_h[:, p10_idx]) & (target_h <= pred_h[:, p90_idx])
                coverage = np.mean(in_interval)
                h_metrics["coverage_p10_p90"] = float(coverage)
            else:
                h_metrics["coverage_p10_p90"] = None

            metrics["metrics"][f"horizon_{h}"] = h_metrics

        results[coin] = {
            "run_id": run_id,
            "generated_at": int(time.time()),
            "metrics": metrics["metrics"],
            "samples": sample_list,
        }

        logger.info(f"Verification complete for {coin}")
        for h in CONFIG["horizons"]:
            h_m = metrics["metrics"].get(f"horizon_{h}", {})
            pinball = h_m.get('pinball_p50', None)
            dir_acc = h_m.get('direction_accuracy', None)
            coverage = h_m.get('coverage_p10_p90', None)

            log_parts = [f"Horizon {h}s:"]
            if pinball is not None:
                log_parts.append(f"Pinball={pinball:.6f}")
            if dir_acc is not None:
                log_parts.append(f"DirAcc={dir_acc:.4f}")
            if coverage is not None:
                log_parts.append(f"Coverage={coverage:.4f}")

            logger.info(f"  {' '.join(log_parts)}")

    # Save results
    verify_results_path = Path("./temp-data/verify_results.json")
    with verify_results_path.open("w") as f:
        json.dump(results, f, indent=2)

    logger.info("=" * 60)
    logger.info(f"Verification results saved to {verify_results_path}")
    logger.info("=" * 60)


def main(verify_only: bool = False):
    """Main training pipeline."""

    if verify_only:
        verify_model()
        return

    logger.info("=" * 60)
    logger.info("Multi-Task Quantile Regression Training Pipeline")
    logger.info("=" * 60)

    # Load data
    logger.info("Loading data...")
    all_trades, all_candles = load_data(
        Path(CONFIG["data_dir"]),
        CONFIG["coins"],
    )

    if not all_trades or not all_candles:
        logger.error("No data loaded. Please run init_training_and_verify_data.py first.")
        return

    # Build samples for each coin
    builder = TrainingDataBuilder(
        feature_window=CONFIG["feature_window"],
        sample_step=CONFIG["sample_step"],
        horizons=CONFIG["horizons"],
        min_trades=CONFIG["min_trades_per_address"],
        enable_parallel=CONFIG["enable_parallel_sample_building"],
        profile=CONFIG["profile_sample_building"],
    )

    all_samples = []
    for coin in CONFIG["coins"]:
        if coin in all_trades and coin in all_candles:
            samples = builder.build_samples(
                trades=all_trades[coin],
                candles_1m=all_candles[coin],
                coin=coin,
            )
            all_samples.extend(samples)

    logger.info(f"Total samples across all coins: {len(all_samples)}")

    if len(all_samples) == 0:
        logger.error("No samples generated. Check your data configuration.")
        return

    # Time-series split
    n_samples = len(all_samples)
    train_end = int(n_samples * CONFIG["train_split"])
    val_end = int(n_samples * (CONFIG["train_split"] + CONFIG["val_split"]))

    train_samples = all_samples[:train_end]
    val_samples = all_samples[train_end:val_end]
    test_samples = all_samples[val_end:]

    logger.info(f"Train samples: {len(train_samples)}, Val: {len(val_samples)}, Test: {len(test_samples)}")

    # Get feature dimensionality
    feature_dim = len(train_samples[0].features)
    logger.info(f"Feature dimension: {feature_dim}")

    # Create datasets
    feature_scaler = StandardScaler()
    feature_scaler.fit(np.stack([s.features for s in train_samples]))

    train_dataset = PricePredictionDataset(
        train_samples,
        feature_scaler=feature_scaler,
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
    )

    val_dataset = PricePredictionDataset(
        val_samples,
        feature_scaler=feature_scaler,
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
    )

    test_dataset = PricePredictionDataset(
        test_samples,
        feature_scaler=feature_scaler,
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
    )

    # Create data loaders with MPS-optimized settings
    pin_memory = CONFIG["pin_memory"] and CONFIG["device"] in ("mps", "cuda")
    persistent_workers = CONFIG["persistent_workers"] and CONFIG["num_workers"] > 0

    train_loader = DataLoader(
        train_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=True,
        num_workers=CONFIG["num_workers"],
        pin_memory=pin_memory,
        persistent_workers=persistent_workers,
    )

    val_loader = DataLoader(
        val_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
        num_workers=CONFIG["num_workers"],
        pin_memory=pin_memory,
        persistent_workers=persistent_workers,
    )

    test_loader = DataLoader(
        test_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
        num_workers=CONFIG["num_workers"],
        pin_memory=pin_memory,
        persistent_workers=persistent_workers,
    )

    # Create model
    model = MultiTaskQuantileModel(
        input_dim=feature_dim,
        hidden_dims=CONFIG["hidden_dims"],
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
        dropout=CONFIG["dropout"],
    )

    logger.info(f"Model architecture:")
    logger.info(f"  Input dim: {feature_dim}")
    logger.info(f"  Hidden dims: {CONFIG['hidden_dims']}")
    logger.info(f"  Horizons: {CONFIG['horizons']}")
    logger.info(f"  Quantiles: {CONFIG['quantiles']}")

    # Train
    trainer = Trainer(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
        learning_rate=CONFIG["learning_rate"],
        device=CONFIG["device"],
        enable_amp=CONFIG["enable_amp"],
        enable_compile=CONFIG["enable_compile"],
    )

    history = trainer.train(
        num_epochs=CONFIG["num_epochs"],
        patience=CONFIG["early_stopping_patience"],
    )

    # Evaluate
    logger.info("\nEvaluating on test set...")
    evaluator = Evaluator(
        model=model,
        test_loader=test_loader,
        horizons=CONFIG["horizons"],
        quantiles=CONFIG["quantiles"],
        device=CONFIG["device"],
    )

    metrics = evaluator.evaluate()

    logger.info("\n" + "=" * 60)
    logger.info("Test Set Evaluation Results")
    logger.info("=" * 60)

    # Group metrics by horizon
    for h in CONFIG["horizons"]:
        logger.info(f"\nHorizon: {h}s ({h // 60}m for short horizons)")
        for q in CONFIG["quantiles"]:
            key = f"pinball_q{q}_h{h}"
            if key in metrics:
                logger.info(f"  Pinball Loss (q={q}): {metrics[key]:.6f}")

        dir_key = f"direction_accuracy_h{h}"
        if dir_key in metrics:
            logger.info(f"  Direction Accuracy: {metrics[dir_key]:.4f}")

        cov_key = f"coverage_p10_p90_h{h}"
        if cov_key in metrics:
            logger.info(f"  Coverage [P10, P90]: {metrics[cov_key]:.4f} (target: 0.80)")

    logger.info("\n" + "=" * 60)
    logger.info("Training complete! Best model saved to 'best_model.pt'")
    logger.info("=" * 60)

    # Save training history
    history_path = Path("./temp-data/training_history.json")
    with history_path.open("w") as f:
        json.dump(history, f, indent=2)
    logger.info(f"Training history saved to {history_path}")

    # Save feature scaler for verification
    scaler_path = Path("./temp-data/feature_scaler.pkl")
    joblib.dump(feature_scaler, scaler_path)
    logger.info(f"Feature scaler saved to {scaler_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true", help="Only run verification, skip training")
    args = parser.parse_args()

    main(verify_only=args.verify_only)
