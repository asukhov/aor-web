"""
Spectrum DSP utilities for Phase 1 (AR5700D FD-command 160-bin data).

Responsibilities
----------------
- Normalize raw dBm arrays to the [0.0, 1.0] display range
- Compute the frequency axis from center/span
- Optional exponential moving average (EMA) smoothing
- Pack frames into the binary WebSocket wire format

Wire format (binary, little-endian)
------------------------------------
Offset  Size      Type       Field
------  --------  ---------  ------------------------------------------
0       2         uint8[2]   magic:     0xAA 0x55
2       1         uint8      type:      0x01 = spectrum frame
3       1         uint8      flags:     reserved, 0x00
4       4         uint32     n_bins:    number of magnitude values
8       8         int64      center_hz: centre frequency in Hz
16      4         uint32     span_hz:   span in Hz
20      4         float32    db_min:    display floor (dBm)
24      4         float32    db_max:    display ceiling (dBm)
28      n_bins×4  float32[]  values:    normalised magnitudes [0.0, 1.0]

Total at n_bins=160:  28 + 640 = 668 bytes per frame.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

from backend.device.ar5700d import FD_BINS

# ── Wire format ───────────────────────────────────────────────────────────────

FRAME_MAGIC = bytes([0xAA, 0x55])
FRAME_TYPE_SPECTRUM = 0x01

# struct format: < = LE, 2s magic, B type, B flags, I n_bins, q center, I span, f db_min, f db_max
_HEADER = struct.Struct("<2sBBIqIff")
assert _HEADER.size == 28


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class NormalizedFrame:
    """Spectrum frame ready for the browser — values scaled to [0.0, 1.0]."""
    values: np.ndarray     # shape (n_bins,), dtype float32
    freqs_hz: np.ndarray   # shape (n_bins,), dtype float64 — bin centre frequencies
    center_hz: int
    span_hz: int
    db_min: float
    db_max: float


# ── Processor ─────────────────────────────────────────────────────────────────

class SpectrumProcessor:
    """
    Converts raw dBm bin arrays into display-ready normalised frames.

    Parameters
    ----------
    db_min:
        Lower display floor in dBm.  Signals at or below this level map to 0.0.
    db_max:
        Upper display ceiling in dBm.  Signals at or above this level map to 1.0.
    smoothing:
        Exponential moving average coefficient.
        0.0 = no smoothing (raw), 0.9 = heavy smoothing.
    """

    def __init__(
        self,
        db_min: float = -120.0,
        db_max: float = -20.0,
        smoothing: float = 0.0,
    ) -> None:
        self.db_min = db_min
        self.db_max = db_max
        self.smoothing = max(0.0, min(0.99, smoothing))
        self._ema: Optional[np.ndarray] = None
        self._db_range = db_max - db_min

    def process(
        self,
        bins: list[float],
        center_hz: int,
        span_hz: int,
    ) -> NormalizedFrame:
        """Normalise *bins* and return a :class:`NormalizedFrame`."""
        arr = np.asarray(bins, dtype=np.float32)

        # Exponential moving average smoothing
        if self.smoothing > 0.0:
            if self._ema is None or self._ema.shape != arr.shape:
                self._ema = arr.copy()
            else:
                self._ema = self.smoothing * self._ema + (1.0 - self.smoothing) * arr
            arr = self._ema

        # Normalise to [0.0, 1.0] and clamp
        norm = (arr - self.db_min) / self._db_range
        norm = np.clip(norm, 0.0, 1.0).astype(np.float32)

        # Frequency axis — evenly spaced bin centres
        freqs = np.linspace(
            center_hz - span_hz / 2,
            center_hz + span_hz / 2,
            len(arr),
            dtype=np.float64,
        )

        return NormalizedFrame(
            values=norm,
            freqs_hz=freqs,
            center_hz=center_hz,
            span_hz=span_hz,
            db_min=self.db_min,
            db_max=self.db_max,
        )

    def update_range(self, db_min: float, db_max: float) -> None:
        """Update display range and reset the EMA state."""
        self.db_min = db_min
        self.db_max = max(db_max, db_min + 1.0)
        self._db_range = self.db_max - self.db_min
        self._ema = None


# ── Wire packing ──────────────────────────────────────────────────────────────

def pack_spectrum_frame(frame: NormalizedFrame) -> bytes:
    """
    Serialise a :class:`NormalizedFrame` to the binary WebSocket wire format.

    The returned bytes can be sent directly via ``websocket.send_bytes()``.
    """
    n_bins = len(frame.values)
    header = _HEADER.pack(
        FRAME_MAGIC,
        FRAME_TYPE_SPECTRUM,
        0,                  # flags (reserved)
        n_bins,
        frame.center_hz,
        frame.span_hz,
        frame.db_min,
        frame.db_max,
    )
    payload = frame.values.astype(np.float32).tobytes()
    return header + payload


def unpack_spectrum_frame(data: bytes) -> NormalizedFrame:
    """
    Deserialise a wire-format buffer back into a :class:`NormalizedFrame`.

    Primarily useful for testing the round-trip.
    """
    if len(data) < _HEADER.size:
        raise ValueError(f"Frame too short: {len(data)} bytes")
    magic, ftype, _flags, n_bins, center_hz, span_hz, db_min, db_max = (
        _HEADER.unpack(data[: _HEADER.size])
    )
    if magic != FRAME_MAGIC:
        raise ValueError(f"Bad magic: {magic!r}")
    values = np.frombuffer(data[_HEADER.size:], dtype=np.float32)[:n_bins]
    freqs = np.linspace(
        center_hz - span_hz / 2,
        center_hz + span_hz / 2,
        n_bins,
        dtype=np.float64,
    )
    return NormalizedFrame(
        values=values,
        freqs_hz=freqs,
        center_hz=center_hz,
        span_hz=span_hz,
        db_min=db_min,
        db_max=db_max,
    )
