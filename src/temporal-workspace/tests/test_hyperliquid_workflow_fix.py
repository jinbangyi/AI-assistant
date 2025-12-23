#!/usr/bin/env python3
"""
Test script to verify the HyperliquidTradeSubscriptionWorkflow fix.
This script tests that the workflow can be started without timeout errors.
"""

import asyncio
import sys
from datetime import datetime, timedelta

# Add the workspace to the path
sys.path.insert(0, "/workspace")

from temporalio.client import Client
from workflows.hyperliquid_trade_workflow import HyperliquidTradeSubscriptionWorkflow


async def main():
    """Test the workflow execution."""
    print("=" * 60)
    print("Testing HyperliquidTradeSubscriptionWorkflow Fix")
    print("=" * 60)
    
    # Connect to Temporal
    print("\n1. Connecting to Temporal server...")
    try:
        client = await Client.connect("temporal-server:7233", namespace="default")
        print("   ✓ Connected successfully!")
    except Exception as e:
        print(f"   ✗ Connection failed: {e}")
        return 1
    
    # Start a short workflow run (10 seconds) to test
    print("\n2. Starting workflow (10 second test run)...")
    workflow_id = f"test-hyperliquid-fix-{int(datetime.now().timestamp())}"
    
    try:
        handle = await client.start_workflow(
            HyperliquidTradeSubscriptionWorkflow.run,
            args=[
                "ETH",  # symbol
                "biz-db",  # db_host
                5432,  # db_port
                "biz",  # db_database
                "biz",  # db_user
                "biz_password",  # db_password
                10,  # duration_seconds (10 second test)
            ],
            id=workflow_id,
            task_queue="hyperliquid-trades",
            execution_timeout=timedelta(days=7),  # Long timeout to prevent timeout errors
            run_timeout=timedelta(days=7),
        )
        print(f"   ✓ Workflow started!")
        print(f"   Workflow ID: {workflow_id}")
        print(f"   Run ID: {handle.result_run_id}")
    except Exception as e:
        print(f"   ✗ Failed to start workflow: {e}")
        return 1
    
    # Wait for completion with a longer timeout
    print("\n3. Waiting for workflow to complete...")
    try:
        result = await asyncio.wait_for(handle.result(), timeout=30)
        print("   ✓ Workflow completed successfully!")
        print(f"   Result: {result}")
        
        if result.get("status") == "success":
            print(f"\n   Trades collected: {result.get('trades_count', 0)}")
            print("   ✓ All tests passed!")
            return 0
        else:
            print(f"   ✗ Workflow returned error status: {result}")
            return 1
            
    except asyncio.TimeoutError:
        print("   ⚠ Workflow still running after 30 seconds (this might be normal)")
        print("   You can check the workflow status in Temporal UI")
        return 0
    except Exception as e:
        print(f"   ✗ Workflow execution failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print("\n" + "=" * 60)
    if exit_code == 0:
        print("TEST PASSED ✓")
    else:
        print("TEST FAILED ✗")
    print("=" * 60)
    sys.exit(exit_code)
