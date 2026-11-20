"""
-----------------------
- test_gate_lane.py -
-----------------------

One gate run at a time per host (27.5): two agents' test runs on one machine never fight for it.
The lane is a `Lease` (`orch-gate:<host>`), so a run that dies still lets the next one in when
the lease expires.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from udl.clock import SystemClock
from udl.core.service import UDLService
from udl.plugins.apps.orch.workspace import GateLane


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service whose database is the lane's (the memory one)."""
    async with UDLService(settings(tmp_path)) as running:
        yield running


async def test_two_gates_on_one_host_never_run_at_the_same_time(
    service: UDLService,
) -> None:
    """(The card's done-when: the lane serialises two pushes.)"""
    first = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock())
    second = GateLane(service.db, host="local", holder="deepseek-b", clock=SystemClock())
    order: list[str] = []

    async def one(lane: GateLane, name: str) -> None:
        async with lane.held():
            order.append(f"{name} in")
            await asyncio.sleep(0.05)
            order.append(f"{name} out")

    await asyncio.gather(one(first, "a"), one(second, "b"))

    assert order in (
        ["a in", "a out", "b in", "b out"],
        ["b in", "b out", "a in", "a out"],
    )


async def test_a_lane_for_another_host_does_not_wait(service: UDLService) -> None:
    here = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock())
    there = GateLane(service.db, host="thevault", holder="deepseek-b", clock=SystemClock())
    order: list[str] = []

    async def one(lane: GateLane, name: str) -> None:
        async with lane.held():
            order.append(name)
            await asyncio.sleep(0.05)

    async with asyncio.timeout(2):
        await asyncio.gather(one(here, "here"), one(there, "there"))

    assert sorted(order) == ["here", "there"]


async def test_a_lane_whose_holder_died_is_taken_after_its_lease_expires(
    service: UDLService,
) -> None:
    """(A gate that was killed leaves its lease behind: the next run waits for it to expire
    rather than for ever.)"""
    gone = GateLane(
        service.db,
        host="local",
        holder="deepseek-a",
        clock=SystemClock(),
        ttl=timedelta(milliseconds=150),
    )
    await gone.take()
    next_one = GateLane(
        service.db, host="local", holder="deepseek-b", clock=SystemClock(), ttl=timedelta(minutes=1)
    )

    async with asyncio.timeout(5):
        await next_one.take()
    await next_one.release()

    # (The lane is free: the holder that never released it is not holding it any more.)
    assert (
        await service.db.claim_lease("orch-gate:local", "someone", timedelta(minutes=1))
    ) is not None


async def test_the_lane_is_released_when_the_gate_fails(service: UDLService) -> None:
    lane = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock())

    with pytest.raises(RuntimeError, match="the gate failed"):
        async with lane.held():
            raise RuntimeError("the gate failed")

    held = await service.db.claim_lease("orch-gate:local", "someone", timedelta(minutes=1))
    assert held is not None
    assert held.expires_at > datetime.now(UTC)
