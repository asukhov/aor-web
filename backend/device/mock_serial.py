"""
Software simulation of the AR5700D for development and testing.

Use this when no hardware is connected by setting ``device.mock: true``
in config.yaml.  Generates a synthetic spectrum with:
  - Gaussian noise floor at ~ -100 dBm
  - A carrier at the tuned frequency (slow AM modulation for realism)
  - A handful of weak spurious signals
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from .ar5700d import (
    AR5700D,
    FD_BINS,
    FD_DBM_BASE,
    FD_LEVEL_OFFSET,
    SpectrumFrame,
    DeviceStatus,
    Mode,
    _decode_fd_bytes,
)


class MockAR5700D(AR5700D):
    """Drop-in replacement for :class:`AR5700D` that never touches a serial port."""

    def __init__(self) -> None:
        super().__init__(port="MOCK")
        self._connected = False
        self._frequency = 145_800_000
        self._mode = int(Mode.NFM)
        self._attenuator = 0
        self._firmware = "MOCK-1.00"
        # A few synthetic spurious signals scattered across the band
        self._spurs = [
            (0.25, -88.0),   # 25 % offset from left edge, -88 dBm
            (0.60, -91.0),   # 60 % offset
            (0.80, -85.0),   # 80 % offset — slightly stronger
        ]

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        self._connected = True
        self._center_hz = self._frequency
        self._span_hz = 500_000

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Control ───────────────────────────────────────────────────────────────

    def get_firmware_version(self) -> str:
        return self._firmware

    def set_frequency(self, freq_hz: int) -> None:
        self._frequency = freq_hz
        self._center_hz = freq_hz

    def get_frequency(self) -> int:
        return self._frequency

    def set_mode(self, mode: int) -> None:
        self._mode = mode

    def get_mode(self) -> int:
        return self._mode

    def set_attenuator(self, level: int) -> None:
        self._attenuator = level

    def get_attenuator(self) -> int:
        return self._attenuator

    def get_signal_level(self) -> float:
        return -73.0 + random.gauss(0.0, 2.0)

    def set_spectrum_center(self, freq_hz: int) -> None:
        self._center_hz = freq_hz

    def set_spectrum_span(self, span_hz: int) -> None:
        self._span_hz = span_hz

    # ── Spectrum ──────────────────────────────────────────────────────────────

    def get_spectrum(self) -> SpectrumFrame:
        raw = self._generate_fd_bytes()
        return SpectrumFrame(
            bins=_decode_fd_bytes(raw),
            center_hz=self._center_hz,
            span_hz=self._span_hz,
            timestamp=time.monotonic(),
        )

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=self._connected,
            frequency_hz=self._frequency,
            mode=self._mode,
            mode_label=Mode.label(self._mode),
            signal_dbm=self.get_signal_level(),
            attenuator=self._attenuator,
            firmware=self._firmware,
        )

    # ── Synthetic spectrum generation ─────────────────────────────────────────

    def _generate_fd_bytes(self) -> bytes:
        """
        Build a 160-byte FD-format buffer with a synthetic spectrum.

        The spectrum has a main carrier at the center bin (the tuned frequency)
        with slow amplitude modulation, plus a few fixed spurious signals and
        a Gaussian noise floor.
        """
        t = time.monotonic()
        center_bin = FD_BINS // 2
        result = bytearray(FD_BINS)

        for i in range(FD_BINS):
            # Noise floor: -100 dBm ± 3 dBm
            dbm = -100.0 + random.gauss(0.0, 3.0)

            # Main carrier at center bin — 6 dB bandwidth, slow AM
            dist_c = abs(i - center_bin)
            if dist_c < 8:
                carrier_peak = -55.0 + 8.0 * math.sin(2 * math.pi * t * 0.25)
                dbm = max(dbm, carrier_peak - dist_c * 5.0)

            # Spurious signals at fixed fractional positions
            for frac, spur_dbm in self._spurs:
                spur_bin = int(frac * FD_BINS)
                dist_s = abs(i - spur_bin)
                if dist_s < 4:
                    # Add slow drift so they look alive
                    level = spur_dbm + 3.0 * math.sin(2 * math.pi * t * 0.1 + frac * 6)
                    dbm = max(dbm, level - dist_s * 6.0)

            # Encode to raw byte:  raw = dbm - FD_DBM_BASE + FD_LEVEL_OFFSET
            raw_val = int(dbm - FD_DBM_BASE + FD_LEVEL_OFFSET)
            result[i] = max(0, min(255, raw_val))

        return bytes(result)
