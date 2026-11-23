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
        await service.db.claim_lease("orch-gate:local:0", "someone", timedelta(minutes=1))
    ) is not None


async def test_a_gate_longer_than_its_lease_keeps_the_lane(service: UDLService) -> None:
    """(A `make check` of 8½ minutes outlasts a 5-minute lease: the lane renews it, so a second
    agent's gate cannot start on the same host, 27.5.)"""
    slow = GateLane(
        service.db,
        host="local",
        holder="deepseek-a:1",
        clock=SystemClock(),
        ttl=timedelta(milliseconds=150),
    )
    other = GateLane(
        service.db,
        host="local",
        holder="deepseek-b:1",
        clock=SystemClock(),
        ttl=timedelta(minutes=1),
    )
    order: list[str] = []

    async def one(lane: GateLane, name: str, how_long: float) -> None:
        async with lane.held():
            order.append(f"{name} in")
            await asyncio.sleep(how_long)
            order.append(f"{name} out")

    async with asyncio.timeout(5):
        await asyncio.gather(one(slow, "slow", 0.9), one(other, "other", 0.05))

    assert order == ["slow in", "slow out", "other in", "other out"]


async def test_two_lanes_of_one_holder_name_are_two_lanes(service: UDLService) -> None:
    """(A push is its own run: two of them, even by one agent or by udl for the owner, do not
    take each other's lease for granted.)"""
    first = GateLane(service.db, host="local", holder="udl:1", clock=SystemClock())
    second = GateLane(service.db, host="local", holder="udl:2", clock=SystemClock())
    order: list[str] = []

    async def one(lane: GateLane, name: str) -> None:
        async with lane.held():
            order.append(f"{name} in")
            await asyncio.sleep(0.05)
            order.append(f"{name} out")

    async with asyncio.timeout(5):
        await asyncio.gather(one(first, "a"), one(second, "b"))

    assert order in (
        ["a in", "a out", "b in", "b out"],
        ["b in", "b out", "a in", "a out"],
    )


async def test_the_lane_is_released_when_the_gate_fails(service: UDLService) -> None:
    lane = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock())

    with pytest.raises(RuntimeError, match="the gate failed"):
        async with lane.held():
            raise RuntimeError("the gate failed")

    held = await service.db.claim_lease("orch-gate:local:0", "someone", timedelta(minutes=1))
    assert held is not None
    assert held.expires_at > datetime.now(UTC)


async def test_two_slots_let_two_gates_run_at_once_and_a_third_waits(
    service: UDLService,
) -> None:
    """(The card's `gates_per_host = 2`: a bigger host raises the setting, and the third push
    waits for one of the first two.)"""
    lanes = [
        GateLane(service.db, host="local", holder=f"deepseek-{name}", clock=SystemClock(), slots=2)
        for name in ("a", "b", "c")
    ]
    order: list[str] = []

    async def one(lane: GateLane, name: str) -> None:
        async with lane.held():
            order.append(f"{name} in")
            await asyncio.sleep(0.2)
            order.append(f"{name} out")

    async with asyncio.timeout(10):
        await asyncio.gather(*(one(lane, name) for lane, name in zip(lanes, "abc", strict=True)))

    # (The first two start before either ends; the third starts only after one of them.)
    assert order[0] in ("a in", "b in")
    assert order[1] in ("a in", "b in")
    assert order[2] in ("a out", "b out")


async def test_a_third_push_waits_for_a_slot(service: UDLService) -> None:
    """(One slot: the second push waits, as 27.5's single lane always did.)"""
    first = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock())
    second = GateLane(service.db, host="local", holder="deepseek-b", clock=SystemClock())
    order: list[str] = []

    async def one(lane: GateLane, name: str) -> None:
        async with lane.held():
            order.append(f"{name} in")
            await asyncio.sleep(0.05)
            order.append(f"{name} out")

    async with asyncio.timeout(5):
        await asyncio.gather(one(first, "a"), one(second, "b"))

    assert order in (
        ["a in", "a out", "b in", "b out"],
        ["b in", "b out", "a in", "a out"],
    )


async def test_a_slot_of_a_lane_is_one_of_its_own_names(service: UDLService) -> None:
    lane = GateLane(service.db, host="local", holder="deepseek-a", clock=SystemClock(), slots=3)

    assert lane.names == ["orch-gate:local:0", "orch-gate:local:1", "orch-gate:local:2"]
    async with lane.held():
        assert lane.name in lane.names


def test_a_lane_needs_a_slot() -> None:
    with pytest.raises(ValueError, match="one slot or more"):
        GateLane(None, host="local", holder="a", clock=SystemClock(), slots=0)  # type: ignore[arg-type]
