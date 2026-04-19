"""Per-process concurrency cap for active voice calls.

We cap simultaneous WebSocket sessions via a semaphore the routers acquire
at handshake time. If exhausted, we respond with TwiML that politely ends
the call — this protects runaway Anthropic spend on retry storms upstream
(e.g. a misconfigured Twilio auto-retry) and bounds memory per instance.

`CallGate` is held on the FastAPI app state and cloned per-process; scale
out across processes rather than raising the per-instance cap beyond the
LLM provider's per-account rate limit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class GateFull(RuntimeError):
    """Raised when all call slots are in use."""


class CallGate:
    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def max(self) -> int:
        return self._max

    @property
    def active(self) -> int:
        return self._active

    def try_acquire_nowait(self) -> bool:
        """Non-blocking acquire. Returns False when the gate is full."""
        if self._sem.locked():
            return False
        acquired = self._sem._value > 0 and self._try_take()
        return acquired

    def _try_take(self) -> bool:
        # asyncio.Semaphore has no public non-blocking acquire; emulate one.
        if self._sem._value <= 0:
            return False
        self._sem._value -= 1
        return True

    def release(self) -> None:
        self._sem.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Reservation-style acquire for code paths that can wait briefly."""
        await self._sem.acquire()
        async with self._lock:
            self._active += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active -= 1
            self._sem.release()
