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
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from itertools import groupby
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from loguru import logger
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

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
        5 * MINUTE,    # 5 minutes
        15 * MINUTE,    # 15 minutes
        30 * MINUTE,   # 30 minutes
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

    # Device
    "device": "cuda" if torch.cuda.is_available() else "cpu",
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

        # Calculate basic metrics
        trade_count = len(trades)
        buy_trades = [t for t in trades if t.side == "buy"]
        sell_trades = [t for t in trades if t.side == "sell"]

        # Volume metrics
        buy_volume = sum(t.size * t.price for t in buy_trades)
        sell_volume = sum(t.size * t.price for t in sell_trades)
        gross_volume = buy_volume + sell_volume
        net_volume = buy_volume - sell_volume

        # Size metrics
        sizes = [t.size for t in trades]
        avg_size = np.mean(sizes)
        max_size = np.max(sizes)
        std_size = np.std(sizes) if len(sizes) > 1 else 0

        # Directionality
        net_vol_ratio = net_volume / gross_volume if gross_volume > 0 else 0
        buy_count = len(buy_trades)
        sell_count = len(sell_trades)
        buy_ratio = buy_count / trade_count if trade_count > 0 else 0

        # Position changes (simplified - using trade direction)
        # In a real system, you'd track actual positions
        delta_position = net_volume / current_price if current_price > 0 else 0

        # Behavior patterns
        first_side = 1 if trades[0].side == "buy" else -1
        last_side = 1 if trades[-1].side == "buy" else -1
        side_switches = sum(1 for i in range(1, len(trades)) if trades[i].side != trades[i-1].side)

        # Price interaction
        prices = [t.price for t in trades]
        vwap = sum(p * s for p, s in zip(prices, sizes)) / sum(sizes) if sum(sizes) > 0 else current_price
        above_vwap_count = sum(1 for t in trades if t.price > vwap)
        above_vwap_ratio = above_vwap_count / trade_count if trade_count > 0 else 0

        # Price momentum within window
        if len(prices) >= 2:
            price_change = (prices[-1] - prices[0]) / prices[0]
            price_volatility = np.std([p / prices[0] for p in prices])
        else:
            price_change = 0
            price_volatility = 0

        # Time distribution
        if len(trades) >= 2:
            time_span = trades[-1].timestamp - trades[0].timestamp
            trade_frequency = trade_count / time_span if time_span > 0 else 0
        else:
            trade_frequency = 0

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
            "side_switch_ratio": side_switches / trade_count if trade_count > 1 else 0,

            # Price interaction (3 features)
            "above_vwap_ratio": above_vwap_ratio,
            "price_change": price_change,
            "price_volatility": price_volatility,

            # Whale indicator (1 feature - will be computed statistically)
            "is_whale": 1 if max_size > 0 else 0,  # Simplified - actual whale status computed across all addresses
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

        # Convert to DataFrame for easier processing
        df = self._candles_to_df(candles_1m)

        if df.empty or len(df) < 14:  # Need at least 14 periods for RSI
            return self._zero_features()

        # Current candle
        current_idx = df.index.get_loc(timestamp_ms) if timestamp_ms in df.index else len(df) - 1
        current = df.iloc[current_idx]

        features = {}

        # Current price features (5 features)
        features["current_price"] = current["close"]
        features["open"] = current["open"]
        features["high"] = current["high"]
        features["low"] = current["low"]
        features["volume"] = current["volume"]

        # Returns (4 features)
        if current_idx >= 1:
            features["return_1m"] = (current["close"] - df.iloc[current_idx - 1]["close"]) / df.iloc[current_idx - 1]["close"]
        else:
            features["return_1m"] = 0

        if current_idx >= 5:
            features["return_5m"] = (current["close"] - df.iloc[current_idx - 5]["close"]) / df.iloc[current_idx - 5]["close"]
        else:
            features["return_5m"] = 0

        if current_idx >= 15:
            features["return_15m"] = (current["close"] - df.iloc[current_idx - 15]["close"]) / df.iloc[current_idx - 15]["close"]
        else:
            features["return_15m"] = 0

        if current_idx >= 60:
            features["return_1h"] = (current["close"] - df.iloc[current_idx - 60]["close"]) / df.iloc[current_idx - 60]["close"]
        else:
            features["return_1h"] = 0

        # Moving averages (3 features)
        if current_idx >= 20:
            features["ma_20"] = df.iloc[current_idx - 20:current_idx + 1]["close"].mean()
            features["price_vs_ma20"] = (current["close"] - features["ma_20"]) / features["ma_20"]
        else:
            features["ma_20"] = current["close"]
            features["price_vs_ma20"] = 0

        if current_idx >= 50:
            features["ma_50"] = df.iloc[current_idx - 50:current_idx + 1]["close"].mean()
            features["price_vs_ma50"] = (current["close"] - features["ma_50"]) / features["ma_50"]
        else:
            features["ma_50"] = current["close"]
            features["price_vs_ma50"] = 0

        # Volatility (2 features)
        if current_idx >= 20:
            returns = df.iloc[current_idx - 20:current_idx + 1]["close"].pct_change().dropna()
            features["volatility_20"] = returns.std() if len(returns) > 0 else 0
        else:
            features["volatility_20"] = 0

        if current_idx >= 60:
            returns = df.iloc[current_idx - 60:current_idx + 1]["close"].pct_change().dropna()
            features["volatility_60"] = returns.std() if len(returns) > 0 else 0
        else:
            features["volatility_60"] = 0

        # RSI (1 feature)
        features["rsi_14"] = self._calculate_rsi(df["close"].values, current_idx)

        # Bollinger Bands (2 features)
        if current_idx >= 20:
            bb_period = 20
            bb_std = 2
            bb_middle = df.iloc[current_idx - bb_period:current_idx + 1]["close"].mean()
            bb_std_val = df.iloc[current_idx - bb_period:current_idx + 1]["close"].std()
            bb_upper = bb_middle + bb_std * bb_std_val
            bb_lower = bb_middle - bb_std * bb_std_val
            features["bb_percent"] = (current["close"] - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5
            features["bb_width"] = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        else:
            features["bb_percent"] = 0.5
            features["bb_width"] = 0

        # Volume features (3 features)
        if current_idx >= 20:
            features["volume_ma_20"] = df.iloc[current_idx - 20:current_idx + 1]["volume"].mean()
            features["volume_ratio"] = current["volume"] / features["volume_ma_20"] if features["volume_ma_20"] > 0 else 1
        else:
            features["volume_ma_20"] = current["volume"]
            features["volume_ratio"] = 1

        if current_idx >= 5:
            features["volume_change_5"] = (current["volume"] - df.iloc[current_idx - 5]["volume"]) / df.iloc[current_idx - 5]["volume"] if df.iloc[current_idx - 5]["volume"] > 0 else 0
        else:
            features["volume_change_5"] = 0

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
        feature_window,
        sample_step,
        horizons,
        min_trades,
    ):
        self.feature_window = feature_window
        self.sample_step = sample_step
        self.horizons = horizons
        self.min_trades = min_trades
        self.addr_extractor = AddressFeatureExtractor(feature_window)
        self.market_extractor = MarketFeatureExtractor()

    def build_samples(
        self,
        trades: list[Trade],
        candles_1m: list[Candle],
        coin: str,
        end_time: int,
    ) -> list[TrainingSample]:
        """
        Build training samples using sliding window.

        For each time step:
        1. Get trades in [t - window, t) for each address
        2. Extract address features
        3. Extract market features
        4. Calculate future returns for each horizon
        """
        samples = []

        # Build timestamp -> price mapping
        candles_by_ts = {c.t: c for c in candles_1m}
        all_timestamps = sorted(candles_by_ts.keys())

        # Determine start time (need window + max horizon data)
        max_horizon = max(self.horizons)
        start_idx = int(self.feature_window / MINUTE) + 1  # Buffer for feature window

        logger.info(f"Building samples for {coin}: {len(all_timestamps)} candles, start_idx={start_idx}, max_horizon={max_horizon // (HOUR)}h")

        for i in tqdm(range(start_idx, len(all_timestamps) - max_horizon / MINUTE - 1), desc=f"Processing {coin}"):
            t_ms = all_timestamps[i]
            t_sec = t_ms // 1000

            # Current price
            current_candle = candles_by_ts[t_ms]
            current_price = current_candle.c

            # Get trades in feature window
            window_start = t_sec - self.feature_window
            window_trades = [
                tr for tr in trades
                if window_start <= tr.timestamp < t_sec
            ]

            # Group by address
            addr_trades: dict[str, list[Trade]] = {}
            for tr in window_trades:
                if tr.taker not in addr_trades:
                    addr_trades[tr.taker] = []
                addr_trades[tr.taker].append(tr)

            # Only process addresses with enough trades
            for addr, addr_trade_list in addr_trades.items():
                if len(addr_trade_list) < self.min_trades:
                    continue

                # Extract features
                addr_features = self.addr_extractor.extract(addr_trade_list, current_price)
                market_features = self.market_extractor.extract(candles_1m, t_ms)

                # Combine features
                all_features = {**addr_features, **market_features}
                feature_vector = np.array(list(all_features.values()), dtype=np.float32)

                # Calculate targets (future returns)
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

                if valid_sample:
                    samples.append(TrainingSample(
                        timestamp=t_sec,
                        address=addr,
                        features=feature_vector,
                        targets=targets,
                        coin=coin,
                    ))

        logger.info(f"Generated {len(samples)} samples for {coin}")
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
        target_scaler: Optional[StandardScaler] = None,
        horizons: Optional[list[int]] = None,
        quantiles: Optional[list[float]] = None,
        fit_scalers: bool = False,
    ):
        self.samples = samples
        self.horizons = horizons or [300, 3600, 14400, 43200, 86400, 604800, 1209600]
        self.quantiles = quantiles or [0.1, 0.5, 0.9]

        # Extract features and targets
        features = np.stack([s.features for s in samples])

        # Initialize or fit feature scaler
        if feature_scaler is None:
            self.feature_scaler = StandardScaler()
            if fit_scalers:
                self.feature_scaler.fit(features)
            else:
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
        self.quantiles = torch.tensor(quantiles or [0.1, 0.5, 0.9])

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
        quantiles_device = self.quantiles.to(predictions.device)

        losses = torch.max(
            quantiles_device * errors,
            (quantiles_device - 1) * errors
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
    ):
        self.model = model.to(device)
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.horizons = horizons
        self.quantiles = quantiles
        self.device = device

        self.criterion = QuantileLoss(quantiles)
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer, mode='min', factor=0.5, patience=3
        )

        self.best_val_loss = float('inf')
        self.patience_counter = 0

    def train_epoch(self) -> float:
        """Train for one epoch."""
        self.model.train()
        total_loss = 0
        num_batches = 0

        for features, targets_dict in self.train_loader:
            features = features.to(self.device)
            targets_dict = {k: v.to(self.device) for k, v in targets_dict.items()}

            self.optimizer.zero_grad()

            # Forward pass
            predictions = self.model(features)

            # Calculate loss for each horizon
            loss = 0
            for h in self.horizons:
                pred_h = predictions[h]  # [batch, num_quantiles]
                target_h = targets_dict[h]  # [batch, 1]
                loss += self.criterion(pred_h, target_h)

            loss = loss / len(self.horizons)

            # Backward pass
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
                features = features.to(self.device)
                targets_dict = {k: v.to(self.device) for k, v in targets_dict.items()}

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


def main():
    """Main training pipeline."""

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
    )

    all_samples = []
    for coin in CONFIG["coins"]:
        if coin in all_trades and coin in all_candles:
            # Get end time from candles
            end_time = all_candles[coin][-1].T // 1000

            samples = builder.build_samples(
                trades=all_trades[coin],
                candles_1m=all_candles[coin],
                coin=coin,
                end_time=end_time,
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

    # Create data loaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=True,
        num_workers=0,
    )

    val_loader = DataLoader(
        val_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
    )

    test_loader = DataLoader(
        test_dataset,
        batch_size=CONFIG["batch_size"],
        shuffle=False,
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


if __name__ == "__main__":
    main()
