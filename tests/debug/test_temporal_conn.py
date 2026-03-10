
import asyncio
from temporalio.client import Client

async def main():
    try:
        print("Connecting to Temporal at localhost:7233...")
        client = await Client.connect("localhost:7233")
        print("Connected successfully!")
        
        # Try to check health/namespace
        desc = await client.describe_namespace("default")
        print(f"Namespace 'default' description: {desc}")
        
    except Exception as e:
        print(f"Failed to connect: {e}")

if __name__ == "__main__":
    asyncio.run(main())
