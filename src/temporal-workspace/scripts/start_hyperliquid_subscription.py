"""
Client script to start a Hyperliquid trade subscription workflow.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from temporalio.client import Client

from workflows.hyperliquid_trade_workflow import HyperliquidTradeSubscriptionWorkflow


async def main():
    """Start a Hyperliquid trade subscription workflow."""
    if len(sys.argv) < 2:
        print("Usage: python start_hyperliquid_subscription.py <SYMBOL> [DB_HOST] [DB_PORT] [DB_NAME] [DB_USER] [DB_PASSWORD]")
        print("Example: python start_hyperliquid_subscription.py ETH localhost 5433 temporal temporal temporal")
        sys.exit(1)
    
    symbol = sys.argv[1]
    db_host = sys.argv[2] if len(sys.argv) > 2 else "localhost"
    db_port = int(sys.argv[3]) if len(sys.argv) > 3 else 5433
    db_database = sys.argv[4] if len(sys.argv) > 4 else "temporal"
    db_user = sys.argv[5] if len(sys.argv) > 5 else "temporal"
    db_password = sys.argv[6] if len(sys.argv) > 6 else "temporal"
    
    # Connect to Temporal
    temporal_address = "localhost:7233"
    temporal_namespace = "default"
    
    print(f"Connecting to Temporal at {temporal_address} (namespace: {temporal_namespace})")
    client = await Client.connect(temporal_address, namespace=temporal_namespace)
    
    # Generate unique workflow ID
    workflow_id = f"hyperliquid-trades-{symbol}-{int(datetime.now().timestamp())}"
    
    print(f"Starting workflow for symbol: {symbol}")
    print(f"Workflow ID: {workflow_id}")
    print(f"Database: {db_user}@{db_host}:{db_port}/{db_database}")
    
    # Start workflow with long execution timeout for long-running workflows
    handle = await client.start_workflow(
        "HyperliquidTradeSubscriptionWorkflow",  # Use workflow name string
        args=[symbol, db_host, db_port, db_database, db_user, db_password, None],
        id=workflow_id,
        task_queue="hyperliquid-trades",
        execution_timeout=timedelta(days=7),  # Workflow can run for up to 7 days
        run_timeout=timedelta(days=7),  # Single run can last up to 7 days
    )
    
    print(f"Workflow started! Workflow ID: {handle.id}")
    print(f"Run ID: {handle.result_run_id}")
    print("\nTo view the workflow in Temporal UI, visit: http://localhost:8088")
    print(f"To cancel the workflow, run:")
    print(f"  python -c \"import asyncio; from temporalio.client import Client; asyncio.run(Client.connect('localhost:7233').then(lambda c: c.get_workflow_handle('{workflow_id}').cancel()))\"")
    
    # Optionally wait for result (this will block until workflow completes)
    # Uncomment the following lines if you want to wait for completion:
    # try:
    #     result = await handle.result()
    #     print(f"\nWorkflow completed: {result}")
    # except Exception as e:
    #     print(f"\nWorkflow failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
