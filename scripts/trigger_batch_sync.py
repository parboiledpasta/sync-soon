import asyncio
import os
import uuid
from dotenv import load_dotenv

from temporalio.client import Client
from semabridge.orchestration.temporal.workflows import SyncWorkflowInput
from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor

load_dotenv()
settings = get_settings()

async def main():
    print("Extracting list of semantic models from Fabric...")
    extractor = FabricExtractor(settings.fabric)
    models = extractor.list_semantic_models()
    
    # Get all unique display names, ignore none
    unique_models = list(set([m.get("displayName") for m in models if m.get("displayName")]))
    print(f"Found {len(unique_models)} unique models to sync.")

    print("Connecting to Temporal (Docker) via localhost:7233...")
    client = await Client.connect("localhost:7233", namespace="default")
    
    inputs = []
    for model_name in unique_models:
        inp = SyncWorkflowInput(
            tenant_id=os.getenv("FABRIC_TENANT_ID", "test_tenant"),
            model_id=model_name,
            database=os.getenv("SNOWFLAKE_DATABASE", "ANALYTICS_DB"),
            schema=os.getenv("SNOWFLAKE_SCHEMA", "SEMANTIC_LAYER"),
            snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
            fabric_workspace_id=os.getenv("FABRIC_WORKSPACE_ID", ""),
            warehouse_size="SMALL",
            version_tag="v1.0.batch-test",
            force_full_sync=False  # incremental updates
        )
        inputs.append(inp)
    
    batch_run_id = f"batch-sync-{uuid.uuid4().hex[:8]}"
    print(f"Triggering BatchSyncWorkflow for {len(inputs)} models (ID: {batch_run_id})...")
    
    # We will start the workflow but not await it immediately to allow printing progress
    try:
        handle = await client.start_workflow(
            "BatchSyncWorkflow",
            inputs,
            id=batch_run_id,
            task_queue="semabridge-sync",
        )
        
        print(f"BatchSyncWorkflow dispatched successfully! Waiting for completion...")
        print("This may take several minutes as it schedules all tasks dynamically over the Docker Temporal worker.")
        print()
        
        results = await handle.result()
        
        print("\nBatch Sync Complete! Results Breakdown:")
        succeeded = 0
        failed = 0
        for r in results:
            # Handle both dataclass and dict responses gracefully
            if isinstance(r, dict):
                model_id = r.get("model_id")
                status = r.get("phase")
                err = r.get("error")
                changes = r.get("changes_detected")
            else:
                model_id = getattr(r, "model_id", "Unknown")
                status = getattr(r, "phase", "Unknown")
                err = getattr(r, "error", "")
                changes = getattr(r, "changes_detected", 0)
                
            if status == "completed":
                succeeded += 1
                print(f" [\u2713] Model: {model_id} | Discovered Changes: {changes}")
            else:
                failed += 1
                if "Extraction failed" in str(err) or "No tables match" in str(err):
                    print(f" [-] Model: {model_id} | Skipped (Not found in Snowflake)")
                else:
                    print(f" [\u2717] Model: {model_id} | Failed: {err}")
                    
        print(f"\nFinal Summary: {succeeded} Succeeded to Deploy, {failed} Skipped/Failed (out of {len(unique_models)}).")
        
    except Exception as e:
        print(f"Batch sync execution completely failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
