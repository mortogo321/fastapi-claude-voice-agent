"""Regression tests for CallGate accounting.

Covers the webrtc path (`try_acquire_nowait` + `release`) staying in sync
with the Twilio path (`slot()` + `active >= max` pre-check) and `/health`.
"""

from __future__ import annotations

import pytest

from app.concurrency import CallGate


async def test_nowait_acquire_tracks_active() -> None:
    gate = CallGate(2)
    assert gate.active == 0

    assert gate.try_acquire_nowait() is True
    assert gate.active == 1
    assert gate.try_acquire_nowait() is True
    assert gate.active == 2

    # Full: further acquires fail and leave the counter untouched.
    assert gate.try_acquire_nowait() is False
    assert gate.active == 2

    gate.release()
    assert gate.active == 1
    assert gate.try_acquire_nowait() is True
    assert gate.active == 2

    gate.release()
    gate.release()
    assert gate.active == 0


async def test_nowait_and_slot_share_capacity() -> None:
    gate = CallGate(1)
    assert gate.try_acquire_nowait() is True

    entered = False

    async def try_slot() -> None:
        nonlocal entered
        async with gate.slot():
            entered = True

    # slot() must not slip past a nowait-held permit; cancel the waiter.
    import asyncio

    task = asyncio.create_task(try_slot())
    await asyncio.sleep(0)
    assert entered is False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    gate.release()
    async with gate.slot():
        assert gate.active == 1
    assert gate.active == 0


async def test_release_without_acquire_raises() -> None:
    gate = CallGate(1)
    with pytest.raises(RuntimeError):
        gate.release()


def test_invalid_max_rejected() -> None:
    with pytest.raises(ValueError):
        CallGate(0)
