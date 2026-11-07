import asyncio

from app.services.agent_runtime import worker
from app.utils.mongodb import close_async_connection, get_async_database


async def main():
    try:
        await worker(await get_async_database())
    finally:
        await close_async_connection()


if __name__ == "__main__":
    asyncio.run(main())
