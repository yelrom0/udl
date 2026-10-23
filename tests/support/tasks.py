"""
------------
- tasks.py -
------------

held(): an async context, for an async fixture to yield what it gives
"""

# System Imports
import asyncio
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager


@asynccontextmanager
async def held[T](context: AbstractAsyncContextManager[T]) -> AsyncGenerator[T]:
    """`context`, entered and left in a task of its own.

    A fixture's setup and teardown run in different tasks, and anyio's task groups (which the
    MCP SDK's client and session manager run) must be left in the task they were entered in.
    """
    entered: asyncio.Future[T] = asyncio.get_running_loop().create_future()
    done = asyncio.Event()

    async def hold() -> None:
        async with context as value:
            entered.set_result(value)
            await done.wait()

    holder = asyncio.create_task(hold())
    await asyncio.wait([entered, holder], return_when=asyncio.FIRST_COMPLETED)
    if not entered.done():
        holder.result()  # (raises why the context could not be entered)
    try:
        yield entered.result()
    finally:
        done.set()
        await holder
