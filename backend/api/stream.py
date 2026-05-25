"""
WebSocket endpoint: /ws/spectrum

Streams binary-packed normalised spectrum frames to browser clients at the
configured frame rate.  Each frame is 668 bytes (28-byte header + 160 × float32).

See backend/dsp/spectrum.py for the full wire format description.

Incoming text messages from the browser are handled for live control:

    {"cmd": "set_range",  "db_min": -110, "db_max": -30}
    {"cmd": "ping"}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from backend.dsp.spectrum import SpectrumProcessor, pack_spectrum_frame

if TYPE_CHECKING:
    from backend.device.poller import SpectrumPoller

log = logging.getLogger(__name__)


async def spectrum_ws_handler(
    websocket: WebSocket,
    poller: "SpectrumPoller",
    db_min: float = -120.0,
    db_max: float = -20.0,
) -> None:
    """
    Handle one WebSocket client for the lifetime of the connection.

    The coroutine runs two concurrent tasks:
    - Waiting for the next spectrum frame from the poller queue
    - Waiting for an incoming text message from the browser

    Whichever completes first is processed; the loop then restarts.
    This means control messages (e.g. set_range) are handled promptly
    without blocking the frame stream.
    """
    await websocket.accept()
    client_addr = f"{websocket.client.host}:{websocket.client.port}"
    log.info("Spectrum WS client connected: %s", client_addr)

    loop = asyncio.get_event_loop()
    queue = poller.subscribe(loop)
    processor = SpectrumProcessor(db_min=db_min, db_max=db_max)

    try:
        while True:
            # Two concurrent awaitables — whichever fires first wins
            recv_task  = asyncio.ensure_future(websocket.receive_text())
            frame_task = asyncio.ensure_future(queue.get())

            done, pending = await asyncio.wait(
                {recv_task, frame_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the loser to avoid leaked coroutines
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # ── Incoming text message from browser ────────────────────────
            if recv_task in done:
                try:
                    raw = recv_task.result()
                    await _handle_text(raw, websocket, processor)
                except WebSocketDisconnect:
                    break
                except Exception as exc:
                    log.debug("recv_task error: %s", exc)
                    break

            # ── New spectrum frame ready ───────────────────────────────────
            if frame_task in done:
                try:
                    spec_frame = frame_task.result()
                except Exception as exc:
                    log.debug("frame_task error: %s", exc)
                    continue

                norm = processor.process(
                    spec_frame.bins,
                    spec_frame.center_hz,
                    spec_frame.span_hz,
                )
                wire = pack_spectrum_frame(norm)
                try:
                    await websocket.send_bytes(wire)
                except WebSocketDisconnect:
                    break
                except Exception as exc:
                    log.warning("WS send error (%s): %s", client_addr, exc)
                    break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.error("WS handler error (%s): %s", client_addr, exc, exc_info=True)
    finally:
        poller.unsubscribe(queue)
        log.info("Spectrum WS client disconnected: %s", client_addr)


async def _handle_text(
    raw: str,
    ws: WebSocket,
    processor: SpectrumProcessor,
) -> None:
    """Process a text message sent by the browser over the WebSocket."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    cmd = msg.get("cmd")

    if cmd == "set_range":
        db_min = float(msg["db_min"])
        db_max = float(msg["db_max"])
        processor.update_range(db_min, db_max)
        await ws.send_text(json.dumps({"status": "ok", "cmd": "set_range"}))

    elif cmd == "ping":
        await ws.send_text(json.dumps({"status": "ok", "cmd": "pong"}))
