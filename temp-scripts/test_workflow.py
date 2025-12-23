#!/usr/bin/env python3
"""Test script to start a Hyperliquid trade subscription workflow."""
import asyncio
import sys
from datetime import datetime

# Add the workspace to path
sys.path.insert(0, '/home/coder/benny/AI-assistant/src/temporal-workspace')

from temporalio.client import Client
from hyperliquid_trade_workflow import HyperliquidTradeSubscriptionWorkflow


async def main():
    """Start a test workflow."""
    try:
        # Connect to Temporal
        print("Connecting to Temporal at localhost:7233...")
        client = await Client.connect("localhost:7233", namespace="default")
        print("Connected successfully!")
        
        # Start workflow with a short duration for testing (30 seconds)
        symbol = "ETH"
        workflow_id = f"test-hyperliquid-{symbol}-{int(datetime.now().timestamp())}"
        
        print(f"\nStarting workflow for symbol: {symbol}")
        print(f"Workflow ID: {workflow_id}")
        print("Using biz-db database (localhost:5434)")
        
        handle = await client.start_workflow(
            HyperliquidTradeSubscriptionWorkflow.run,
            symbol,
            "host.docker.internal",  # db_host - use host.docker.internal from container
            5434,  # db_port - biz-db port
            "biz",  # db_database
            "biz",  # db_user
            "biz_password",  # db_password
            30,  # duration_seconds - test for 30 seconds
            id=workflow_id,
            task_queue="hyperliquid-trades",
        )
        
        print(f"\n✓ Workflow started successfully!")
        print(f"  Workflow ID: {handle.id}")
        print(f"  Run ID: {handle.result_run_id}")
        print(f"\nWaiting for workflow to complete (30 seconds)...")
        
        # Wait for result
        result = await handle.result()
        print(f"\n✓ Workflow completed successfully!")
        print(f"  Result: {result}")
        
        await client.close()
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
