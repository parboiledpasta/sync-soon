import asyncio
from temporalio.client import Client

async def main():
    try:
        client = await Client.connect("localhost:7233")
        async for execution in client.list_workflows('ExecutionStatus="Running"'):
            print(f"Workflow: {execution.id}")
            handle = client.get_workflow_handle(execution.id, run_id=execution.run_id)
            desc = await handle.describe()
            print("Status:", desc.status)
            for act in desc.raw_info.pending_activities:
                print("Activity:", act.activity_type.name)
                print("Attempt:", act.attempt)
                if act.last_failure:
                    print("Failure:", act.last_failure.message)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
