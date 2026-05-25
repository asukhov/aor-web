"""
REST API: device control endpoints.

Endpoints
---------
GET  /api/status                 → current device state snapshot
GET  /api/ports                  → list available COM ports
GET  /api/modes                  → list valid mode values + labels
POST /api/frequency    {hz}      → tune to frequency
POST /api/mode         {mode}    → set receive mode
POST /api/attenuator   {level}   → set attenuator
POST /api/spectrum     {center_hz, span_hz}  → set spectrum parameters
POST /api/range        {db_min, db_max}      → update display dB range
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.device.ar5700d import AR5700D, Mode

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# ── Request models ────────────────────────────────────────────────────────────

class FrequencyRequest(BaseModel):
    hz: int

class ModeRequest(BaseModel):
    mode: int

class AttenuatorRequest(BaseModel):
    level: int

class SpectrumParamsRequest(BaseModel):
    center_hz: int
    span_hz: int

class RangeRequest(BaseModel):
    db_min: float
    db_max: float

# ── Helper ────────────────────────────────────────────────────────────────────

def _dev(request: Request) -> AR5700D:
    return request.app.state.device  # type: ignore[no-any-return]

def _require_connected(request: Request) -> AR5700D:
    dev = _dev(request)
    if not dev.is_connected:
        raise HTTPException(status_code=503, detail="Device not connected")
    return dev

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(request: Request):
    """Return the current receiver state as JSON."""
    dev = _require_connected(request)
    try:
        s = dev.get_status()
        return {
            "connected":    s.connected,
            "frequency_hz": s.frequency_hz,
            "mode":         s.mode,
            "mode_label":   s.mode_label,
            "signal_dbm":   s.signal_dbm,
            "attenuator":   s.attenuator,
            "firmware":     s.firmware,
        }
    except Exception as exc:
        log.error("get_status failed: %s", exc)
        raise HTTPException(500, str(exc))


@router.get("/ports")
async def list_ports():
    """Return a list of available COM ports for manual configuration."""
    return {"ports": AR5700D.list_ports()}


@router.get("/modes")
async def list_modes():
    """Return all supported receive mode values with their labels."""
    return {"modes": Mode.options()}


@router.post("/frequency")
async def set_frequency(body: FrequencyRequest, request: Request):
    """Tune the receiver.  Frequency in Hz (100 000 – 3 700 000 000)."""
    if not (100_000 <= body.hz <= 3_700_000_000):
        raise HTTPException(
            400,
            f"Frequency {body.hz} Hz is out of the AR5700D range "
            "(100 kHz – 3.7 GHz)"
        )
    dev = _require_connected(request)
    try:
        dev.set_frequency(body.hz)
        return {"ok": True, "frequency_hz": body.hz}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/mode")
async def set_mode(body: ModeRequest, request: Request):
    """Set receive mode.  See GET /api/modes for valid values."""
    dev = _require_connected(request)
    try:
        dev.set_mode(body.mode)
        return {"ok": True, "mode": body.mode, "label": Mode.label(body.mode)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/attenuator")
async def set_attenuator(body: AttenuatorRequest, request: Request):
    """Set attenuator level (0 = off)."""
    dev = _require_connected(request)
    try:
        dev.set_attenuator(body.level)
        return {"ok": True, "level": body.level}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/spectrum")
async def set_spectrum_params(body: SpectrumParamsRequest, request: Request):
    """Update the spectrum analyser center frequency and span."""
    dev = _require_connected(request)
    try:
        dev.set_spectrum_center(body.center_hz)
        dev.set_spectrum_span(body.span_hz)
        # Mirror the new values into the poller's processor via app state
        request.app.state.spectrum_center_hz = body.center_hz
        request.app.state.spectrum_span_hz = body.span_hz
        return {"ok": True, "center_hz": body.center_hz, "span_hz": body.span_hz}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/range")
async def set_display_range(body: RangeRequest, request: Request):
    """Update the dBm display floor/ceiling for all connected WebSocket clients."""
    if body.db_max <= body.db_min:
        raise HTTPException(400, "db_max must be greater than db_min")
    # Stored on app state; WebSocket handler reads it per-message too
    request.app.state.db_min = body.db_min
    request.app.state.db_max = body.db_max
    return {"ok": True, "db_min": body.db_min, "db_max": body.db_max}
