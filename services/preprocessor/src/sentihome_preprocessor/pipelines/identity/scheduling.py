"""Resource-aware scheduling for the identity DAG (Epic 10.11.3).

The router dispatches every (frame × branch) concurrently via
``asyncio.gather``. With a handful of models on one box that's fine,
but as the pipeline set grows (CC-ReID, gait, 3D-shape, …) and as work
is placed on shared or multi-GPU resources, unbounded concurrency
thrashes the device. :class:`ResourcePool` puts a per-resource
concurrency cap in front of each ``pipeline.run`` so inference is
bounded by the hardware, not by how many frames happen to be in the
window.

* A logical resource (``"gpu"`` / ``"cpu"`` / ``"npu"``, or a named
  multi-GPU pool) maps to one :class:`asyncio.Semaphore`. A 2-GPU node
  → pool size 2; a single iGPU → 1; CPU → a few.
* A pipeline acquires its resource slot for the duration of ``run``;
  excess work queues (backpressure) instead of piling onto the device.
* Semaphores are created lazily on first use so they bind to the
  running event loop (the router is constructed before the loop exists).

Pairs with the window *budget* enforced in the router: a deadline so a
slow/contended window degrades gracefully (drop not-yet-started work,
log it) rather than blowing past the alert-latency target. The
``ResourcePool`` + budget are also exactly what the DAG simulator
(10.11.3b) models, so the sim and prod share this scheduling layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Sane single-box defaults. One iGPU → gpu=1; CPU pipelines can
# overlap a little; unknown classes fall back to ``default_size``.
_DEFAULT_SIZES: dict[str, int] = {"gpu": 1, "cpu": 4, "npu": 1}


class ResourcePool:
    """Per-resource concurrency limiter for pipeline inference.

    Construct once (e.g. at router init) with the per-class pool sizes
    for the deployment; acquire a slot around each inference call via
    :meth:`slot`. Thread/async note: all use is from the single event
    loop, so the lazy get-or-create of a semaphore (no ``await``
    between read and write) is race-free.
    """

    __slots__ = ("_default", "_sems", "_sizes")

    def __init__(self, sizes: dict[str, int] | None = None, *, default_size: int = 4) -> None:
        self._sizes: dict[str, int] = {**_DEFAULT_SIZES, **(sizes or {})}
        self._default = default_size
        self._sems: dict[str, asyncio.Semaphore] = {}

    def _semaphore(self, resource_class: str) -> asyncio.Semaphore:
        sem = self._sems.get(resource_class)
        if sem is None:
            size = self._sizes.get(resource_class, self._default)
            sem = asyncio.Semaphore(max(1, size))
            self._sems[resource_class] = sem
        return sem

    @asynccontextmanager
    async def slot(self, resource_class: str) -> AsyncIterator[None]:
        """Acquire (and on exit release) one concurrency slot for the
        given resource class. Blocks (queues) when the class is at
        capacity — the backpressure that keeps a shared/single device
        from being oversubscribed."""
        sem = self._semaphore(resource_class)
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def size(self, resource_class: str) -> int:
        """Configured capacity for a class (for telemetry / the sim)."""
        return self._sizes.get(resource_class, self._default)
