"""Injectable clock and sleeper — the only two side-effecting time
primitives used anywhere in the dispatch path. Both `InstagramPublisher`
and `ExecutionDispatchService` take these as constructor dependencies
rather than calling `datetime.now()`/`asyncio.sleep()` directly, so tests
can be fully deterministic (fake time, zero real sleeping) per this
milestone's own explicit requirement.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_iso(self) -> str: ...


class Sleeper(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class AsyncioSleeper:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
