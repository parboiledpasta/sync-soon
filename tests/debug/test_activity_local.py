
import asyncio
import os
import time
from dataclasses import dataclass
from dotenv import load_dotenv

# Mock temporal context or just run the function
load_dotenv()

from semabridge.orchestration.temporal.activities import extract_metadata, ExtractInput

async def test_activity():
    inp = ExtractInput(
        tenant_id=os.getenv("FABRIC_TENANT_ID", "test_tenant"),
        model_id="SalesModel",
        database=os.getenv("SNOWFLAKE_DATABASE", "ANALYTICS_DB"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "SEMANTIC_LAYER"),
        snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
        warehouse_size="SMALL"
    )
    
    print(f"Testing extract_metadata with account: {inp.snowflake_account}")
    result = await extract_metadata(inp)
    
    if result.error:
        print(f"Activity Error: {result.error}")
    else:
        print(f"Activity Success!")
        print(f"Table Count: {result.table_count}")
        print(f"SML Length: {len(result.sml_json)}")

if __name__ == "__main__":
    asyncio.run(test_activity())
