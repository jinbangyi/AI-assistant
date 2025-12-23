import asyncio
from temporalio.client import Client

async def test():
    try:
        c = await Client.connect('localhost:7233')
        print('Connected to Temporal successfully')
        await c.close()
    except Exception as e:
        print(f'Failed to connect: {e}')

asyncio.run(test())
