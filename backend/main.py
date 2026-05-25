"""
AOR AR5700D WebSDR — FastAPI application.

Start the server:
    python run.py
  or directly:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import load_config
from backend.device.ar5700d import AR5700D
from backend.device.mock_serial import MockAR5700D
from backend.device.poller import SpectrumPoller
from backend.api.control import router as control_router
from backend.api.stream import spectrum_ws_handler

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
cfg = load_config()


# ── Application lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect the device and start the poller on startup; clean up on shutdown."""

    # ── Startup ───────────────────────────────────────────────────────────────
    if cfg.device.mock:
        log.info("Mock mode enabled — using software-simulated AR5700D")
        device: AR5700D = MockAR5700D()
    else:
        device = AR5700D(
            port=cfg.device.port,
            baudrate=cfg.device.baudrate,
            timeout=cfg.device.timeout,
        )

    connected = False
    try:
        device.connect()
        device.set_frequency(cfg.receiver.default_frequency)
        device.set_mode(cfg.receiver.default_mode)
        device.set_attenuator(cfg.receiver.default_attenuator)
        device.set_spectrum_center(cfg.spectrum.center_hz)
        device.set_spectrum_span(cfg.spectrum.span_hz)
        connected = True
        log.info("Device ready — firmware: %s", device.get_firmware_version())
    except Exception as exc:
        log.error("Device startup failed: %s", exc)
        log.warning(
            "Server will start in disconnected state. "
            "Connect the device and restart, or enable mock mode."
        )

    poller = SpectrumPoller(device=device, fps=cfg.spectrum.fps)
    if connected:
        poller.start()

    # Expose shared state to request handlers
    app.state.device = device
    app.state.poller = poller
    app.state.cfg = cfg
    app.state.db_min = cfg.spectrum.db_min
    app.state.db_max = cfg.spectrum.db_max

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    poller.stop()
    device.disconnect()
    log.info("Shutdown complete")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AOR AR5700D WebSDR",
    description="Web-based spectrum display and receiver control for the AOR AR5700D.",
    version="0.1.0",
    lifespan=lifespan,
)

# REST control routes
app.include_router(control_router)

# WebSocket spectrum stream
@app.websocket("/ws/spectrum")
async def ws_spectrum(websocket: WebSocket):
    await spectrum_ws_handler(
        websocket=websocket,
        poller=websocket.app.state.poller,
        db_min=websocket.app.state.db_min,
        db_max=websocket.app.state.db_max,
    )

# Static files — serve the frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")
