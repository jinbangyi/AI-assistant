#!/usr/bin/env python3
"""
Launcher script for the Continuous Hyperliquid Trade Subscriber.

This script provides a convenient way to start the continuous trade subscriber
with custom configuration options.

Usage:
    python run_continuous_subscriber.py [options]

    Options:
        --coins STR          Comma-separated list of coins (default: ETH,SOL,BTC)
        --interval INT       Batch flush interval in seconds (default: 10)
        --batch-size INT     Maximum trades per batch (default: 10000)
        --health-port INT    Health check HTTP port (default: 8080)

Examples:
    # Start with default settings
    python run_continuous_subscriber.py

    # Start for specific coins
    python run_continuous_subscriber.py --coins ETH,BTC,AVAX

    # Start with faster flush interval
    python run_continuous_subscriber.py --interval 5

    # Start with custom batch size and health port
    python run_continuous_subscriber.py --batch-size 5000 --health-port 9090
"""

import sys
import os

# Add flows directory to path so we can import the subscriber
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "flows"))

from continuous_trade_subscriber import main

if __name__ == "__main__":
    main()
