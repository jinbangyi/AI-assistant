"""
Example data pipeline workflow for multi-user Temporal setup.

This demonstrates a more complex workflow with multiple steps,
error handling, and user-specific configurations.
"""

from datetime import timedelta
from temporalio import workflow
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
import asyncio
from typing import List, Dict, Optional
import random


@activity.defn
async def extract_data(source: str, user: Optional[str] = None) -> List[Dict]:
    """Extract data from a source."""
    user_id = user or "anonymous"
    print(f"[User: {user_id}] Extracting data from: {source}")
    await asyncio.sleep(1)
    
    # Simulate data extraction
    data = [
        {"id": i, "value": random.randint(1, 100), "source": source}
        for i in range(5)
    ]
    
    print(f"[User: {user_id}] Extracted {len(data)} records")
    return data


@activity.defn
async def transform_data(data: List[Dict], user: Optional[str] = None) -> List[Dict]:
    """Transform the extracted data."""
    user_id = user or "anonymous"
    print(f"[User: {user_id}] Transforming {len(data)} records")
    await asyncio.sleep(2)
    
    # Simulate transformation
    transformed = [
        {**record, "transformed_value": record["value"] * 2}
        for record in data
    ]
    
    print(f"[User: {user_id}] Transformation complete")
    return transformed


@activity.defn
async def load_data(data: List[Dict], destination: str, user: Optional[str] = None) -> bool:
    """Load data to a destination."""
    user_id = user or "anonymous"
    print(f"[User: {user_id}] Loading {len(data)} records to: {destination}")
    await asyncio.sleep(1)
    
    # Simulate loading
    print(f"[User: {user_id}] Successfully loaded data")
    return True


@activity.defn
async def send_notification(message: str, user: Optional[str] = None) -> None:
    """Send a notification (e.g., email, Slack)."""
    user_id = user or "anonymous"
    print(f"[User: {user_id}] Notification: {message}")


@workflow.defn
class DataPipelineWorkflow:
    """A complete ETL pipeline workflow."""
    
    @workflow.run
    async def run(
        self,
        source: str = "database",
        destination: str = "warehouse",
        user: Optional[str] = None,
        send_notifications: bool = True,
    ) -> Dict:
        """
        A complete ETL pipeline workflow.
        
        Args:
            source: Data source identifier
            destination: Data destination identifier
            user: Optional user identifier for multi-user tracking
            send_notifications: Whether to send notifications
        """
        user_id = user or "anonymous"
        workflow.logger.info(f"Starting data pipeline for user: {user_id}")
        
        try:
            # Extract
            if send_notifications:
                await workflow.execute_activity(
                    send_notification,
                    f"Starting extraction from {source}",
                    user_id,
                    start_to_close_timeout=timedelta(seconds=10),
                )
            
            raw_data = await workflow.execute_activity(
                extract_data,
                source,
                user_id,
                start_to_close_timeout=timedelta(seconds=10),
            )
            
            # Transform
            if send_notifications:
                await workflow.execute_activity(
                    send_notification,
                    f"Transforming {len(raw_data)} records",
                    user_id,
                    start_to_close_timeout=timedelta(seconds=10),
                )
            
            transformed_data = await workflow.execute_activity(
                transform_data,
                raw_data,
                user_id,
                start_to_close_timeout=timedelta(seconds=10),
            )
            
            # Load
            if send_notifications:
                await workflow.execute_activity(
                    send_notification,
                    f"Loading to {destination}",
                    user_id,
                    start_to_close_timeout=timedelta(seconds=10),
                )
            
            success = await workflow.execute_activity(
                load_data,
                args=[transformed_data, destination, user_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            
            if send_notifications:
                await workflow.execute_activity(
                    send_notification,
                    f"Pipeline completed successfully for user: {user_id}",
                    user_id,
                    start_to_close_timeout=timedelta(seconds=10),
                )
            
            workflow.logger.info(f"Data pipeline completed successfully for user: {user_id}")
            return {
                "status": "success",
                "records_processed": len(transformed_data),
                "user": user_id,
            }
            
        except Exception as e:
            error_msg = f"Pipeline failed for user {user_id}: {str(e)}"
            workflow.logger.error(error_msg)
            
            if send_notifications:
                await workflow.execute_activity(
                    send_notification,
                    args=[error_msg, user_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
            
            raise


async def main():
    """Example usage."""
    # Connect to Temporal
    client = await Client.connect("localhost:7233", namespace="default")
    
    # Run the workflow
    result = await client.execute_workflow(
        DataPipelineWorkflow.run,
        "production-db",
        "analytics-warehouse",
        "data-engineer-1",
        True,
        id="data-pipeline-example",
        task_queue="example-tasks",
    )
    
    print(f"Pipeline result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
