import asyncio
import os
from dotenv import load_dotenv
from semabridge.orchestration.orchestrator_adapter import run_sync

# Load environment variables from .env
load_dotenv()

async def main():
    print("Triggering sync via Temporal with real credentials...")
    try:
        result = await run_sync(
            tenant_id=os.getenv("FABRIC_TENANT_ID", "test_tenant"),
            model_id="SalesModel",  # A likely model name
            database=os.getenv("SNOWFLAKE_DATABASE", "ANALYTICS_DB"),
            schema=os.getenv("SNOWFLAKE_SCHEMA", "SEMANTIC_LAYER"),
            snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
            fabric_workspace_id=os.getenv("FABRIC_WORKSPACE_ID", ""),
            warehouse_size="SMALL",
            version_tag="v1.0.distributed-test",
            force_full_sync=True,
            mode="temporal"
        )
        print(f"Sync Status: {result.status}")
        print(f"Changes Detected: {result.changes_detected}")
        print(f"Deployed: {result.deployed}")
        if result.error:
            print(f"Error: {result.error}")
    except Exception as e:
        print(f"Sync failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
