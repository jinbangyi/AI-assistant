"""
Example Temporal worker for multi-user support.

Workers connect to Temporal and execute workflows/activities.
Each user/team can run their own worker connected to their namespace.
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
# Also add workspace root if running in container
if Path('/workspace').exists():
    sys.path.insert(0, '/workspace')

from workflows.example_data_pipeline import (
    DataPipelineWorkflow,
    extract_data,
    transform_data,
    load_data,
    send_notification,
)


async def run_worker(
    temporal_address: str = "localhost:7233",
    namespace: str = "default",
    task_queue: str = "example-tasks",
):
    """
    Run a Temporal worker.
    
    Args:
        temporal_address: Temporal server address
        namespace: Namespace to connect to (for multi-user isolation)
        task_queue: Task queue name
    """
    print(f"Connecting to Temporal at {temporal_address}")
    print(f"Namespace: {namespace}")
    print(f"Task Queue: {task_queue}")
    
    # Connect to Temporal
    client = await Client.connect(temporal_address, namespace=namespace)
    
    # Create and run worker
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[DataPipelineWorkflow],
        activities=[
            extract_data,
            transform_data,
            load_data,
            send_notification,
        ],
    )
    
    print(f"Worker started! Listening for tasks on queue: {task_queue}")
    print("Press Ctrl+C to stop...")
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        print("\nWorker stopped")


async def main():
    """Main entry point."""
    import os
    
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "example-tasks")
    
    await run_worker(temporal_address, namespace, task_queue)


if __name__ == "__main__":
    asyncio.run(main())
