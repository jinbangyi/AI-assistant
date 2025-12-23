"""
Temporal worker for Hyperliquid trade subscription workflow.
Run this worker to process workflow and activity tasks.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from temporalio.client import Client
from temporalio.worker import Worker

from workflows.hyperliquid_trade_workflow import (
    HyperliquidTradeSubscriptionWorkflow,
    setup_database_table,
    persist_trade,
    subscribe_and_persist_trades,
)


async def main():
    """Run the Temporal worker."""
    # Get connection parameters from environment or use defaults
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "hyperliquid-trades")
    
    print(f"[WORKER] Connecting to Temporal at {temporal_address} (namespace: {temporal_namespace})")
    print(f"[WORKER] Task queue: {task_queue}")
    print(f"[WORKER] Registering activities: setup_database_table, persist_trade, subscribe_and_persist_trades")
    print(f"[WORKER] Registering workflow: HyperliquidTradeSubscriptionWorkflow")
    
    # Connect to Temporal
    client = await Client.connect(
        temporal_address,
        namespace=temporal_namespace,
    )
    
    print(f"[WORKER] Connected to Temporal successfully")
    
    # Create a thread pool executor for synchronous activities
    activity_executor = ThreadPoolExecutor(max_workers=10)
    print(f"[WORKER] Created thread pool executor for synchronous activities")
    
    # Create worker
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HyperliquidTradeSubscriptionWorkflow],
        activities=[
            setup_database_table,
            persist_trade,
            subscribe_and_persist_trades,
        ],
        activity_executor=activity_executor,
    )
    
    print(f"[WORKER] Worker created successfully")
    print(f"[WORKER] Starting worker on task queue: {task_queue}")
    print(f"[WORKER] Waiting for workflow and activity tasks...")
    
    # Run worker
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
