"""
Background spectrum poller.

Runs in a daemon thread, calls AR5700D.get_spectrum() at a configurable
frame rate, and distributes SpectrumFrame objects to subscribed async queues.

Architecture
------------
                 ┌──────────────────────┐
                 │  SpectrumPoller      │
                 │  (daemon thread)     │
                 │                      │
  AR5700D ──────►│  _poll_loop()        │
  get_spectrum() │      │               │
                 │      ▼               │
                 │  _callbacks list     │
                 │    cb_1  cb_2  ...   │
                 └──┬───┴───┴───────────┘
                    │   │   └── asyncio.Queue  ←── WS client 2
                    │   └────── asyncio.Queue  ←── WS client 1
                    └────────── asyncio.Queue  ←── WS client N

Each WebSocket client subscribes to its own asyncio.Queue via subscribe().
The polling thread pushes frames into queues using loop.call_soon_threadsafe()
so the queues are safely consumed by asyncio coroutines.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, Optional

from .ar5700d import AR5700D, SpectrumFrame

log = logging.getLogger(__name__)

_FrameCallback = Callable[[SpectrumFrame], None]


class SpectrumPoller:
    """
    Polls the receiver for spectrum data and fans out to async subscribers.

    Parameters
    ----------
    device:
        Connected :class:`AR5700D` (or :class:`MockAR5700D`) instance.
    fps:
        Target frames per second.  Actual rate may be lower if the device
        cannot keep up (FD command takes ~50 ms over USB serial).
    """

    def __init__(self, device: AR5700D, fps: float = 10.0) -> None:
        self._device = device
        self._fps = max(0.5, fps)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: list[_FrameCallback] = []
        self._cb_lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling thread.  No-op if already running."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="spectrum-poller",
        )
        self._thread.start()
        log.info("Spectrum poller started (target %.1f fps)", self._fps)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the polling thread and wait for it to exit."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        log.info("Spectrum poller stopped")

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread) and self._thread.is_alive()

    # ── Subscription ─────────────────────────────────────────────────────────

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        maxsize: int = 4,
    ) -> asyncio.Queue:
        """
        Create an :class:`asyncio.Queue` that receives :class:`SpectrumFrame` objects.

        The queue is safe to ``await queue.get()`` inside coroutines running
        on *loop*.  When the queue is full (client is slow) the oldest frame is
        silently dropped so the waterfall always shows fresh data.

        Call :meth:`unsubscribe` with the returned queue when the client disconnects.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

        def _callback(frame: SpectrumFrame) -> None:
            # Evict stale frame if queue is saturated
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                loop.call_soon_threadsafe(q.put_nowait, frame)
            except Exception as exc:
                log.debug("Queue put_nowait failed: %s", exc)

        # Attach the callback reference to the queue so we can remove it later
        q._poller_cb = _callback  # type: ignore[attr-defined]
        with self._cb_lock:
            self._callbacks.append(_callback)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove the subscription associated with *queue*."""
        cb = getattr(queue, "_poller_cb", None)
        if cb is None:
            return
        with self._cb_lock:
            try:
                self._callbacks.remove(cb)
            except ValueError:
                pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Main loop running in the daemon thread."""
        interval = 1.0 / self._fps
        consecutive_errors = 0

        while self._running:
            t0 = time.monotonic()

            try:
                frame = self._device.get_spectrum()
                consecutive_errors = 0

                with self._cb_lock:
                    callbacks = list(self._callbacks)

                for cb in callbacks:
                    try:
                        cb(frame)
                    except Exception as exc:
                        log.debug("Subscriber callback raised: %s", exc)

            except Exception as exc:
                consecutive_errors += 1
                lvl = logging.WARNING if consecutive_errors < 5 else logging.ERROR
                log.log(lvl, "Poll error #%d: %s", consecutive_errors, exc)

                if consecutive_errors >= 30:
                    log.critical("Too many consecutive poll errors — stopping poller")
                    self._running = False
                    break

                # Back off exponentially, up to 5 s
                time.sleep(min(consecutive_errors * 0.5, 5.0))
                continue

            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -0.05:
                log.debug("Poll loop running %.0f ms behind schedule", -sleep_time * 1000)
