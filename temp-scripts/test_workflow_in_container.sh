#!/bin/bash
# Test script to start a Hyperliquid workflow from within the worker container

docker exec temporal-worker-hyperliquid python3 << 'EOF'
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, '/workspace')
from temporalio.client import Client
from hyperliquid_trade_workflow import HyperliquidTradeSubscriptionWorkflow

async def test():
    try:
        print("Connecting to Temporal at temporal-server:7233...")
        client = await Client.connect("temporal-server:7233", namespace="default")
        print("✓ Connected successfully!")
        
        symbol = "ETH"
        workflow_id = f"test-hyperliquid-{symbol}-{int(datetime.now().timestamp())}"
        
        print(f"\nStarting workflow:")
        print(f"  Symbol: {symbol}")
        print(f"  Workflow ID: {workflow_id}")
        print(f"  Database: biz@biz-db:5432/biz")
        print(f"  Duration: 30 seconds (test)")
        
        handle = await client.start_workflow(
            HyperliquidTradeSubscriptionWorkflow.run,
            symbol,
            "biz-db",  # db_host - use container name
            5432,  # db_port - internal port
            "biz",  # db_database
            "biz",  # db_user
            "biz_password",  # db_password
            30,  # duration_seconds - test for 30 seconds
            id=workflow_id,
            task_queue="hyperliquid-trades",
        )
        
        print(f"\n✓ Workflow started!")
        print(f"  Workflow ID: {handle.id}")
        print(f"  Run ID: {handle.result_run_id}")
        print(f"\nWaiting for workflow to complete (30 seconds)...")
        
        result = await handle.result()
        print(f"\n✓ Workflow completed!")
        print(f"  Status: {result.get('status')}")
        print(f"  Trades persisted: {result.get('trades_count', 0)}")
        print(f"  Duration: {result.get('duration_seconds', 0):.2f} seconds")
        
        await client.close()
        print("\n✓ Test completed successfully!")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

asyncio.run(test())
EOF
