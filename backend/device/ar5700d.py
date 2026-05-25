"""
AOR AR5700D serial control layer.

Protocol summary
----------------
Transport : USB CDC ACM — appears as a virtual COM port (COMx on Windows,
            /dev/ttyACM0 on Linux) with no special drivers beyond the OS
            built-in CDC ACM driver.
Baud rate : 115 200 bps (default; changeable with the UB command)
Commands  : ASCII text terminated with CR  (b'\\r')
Responses : ASCII text terminated with SP+CR+LF  (b' \\r\\n')

Special binary response — FD command
-------------------------------------
The FD (fast-display) command returns 160 raw bytes of spectrum magnitude:

    TX:  b'FD\\r'
    RX:  b'FD ' + <160 bytes> + b' \\r\\n'

Each byte encodes one spectral bin:
    dBm = (raw_byte - FD_LEVEL_OFFSET) + FD_DBM_BASE
        = raw_byte - 0x20 - 100

    raw_byte = 0x20 (32)  →  -100 dBm  (noise floor)
    raw_byte = 0x84 (132) →    0 dBm
    raw_byte = 0x00       →  -132 dBm  (below floor)

Spectrum span/center are set with CF and FF before issuing FD.

Reference: AR5001D command list (AR5700D uses a compatible superset with
additional digital mode commands MD 40-54).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

# ── Protocol constants ────────────────────────────────────────────────────────

RESP_TERM = b" \r\n"       # every ASCII response ends with SP CR LF
FD_BINS = 160              # number of spectral bins returned by FD command
FD_LEVEL_OFFSET = 0x20    # subtract from raw byte before applying dBm base
FD_DBM_BASE = -100.0      # dBm value when (raw_byte - FD_LEVEL_OFFSET) == 0

# Keywords that help auto-detect the control COM port
_AOR_KEYWORDS = ("AOR", "AR5700", "AR5001", "CP210", "CH340", "CDC")


# ── Mode enumeration ──────────────────────────────────────────────────────────

class Mode(IntEnum):
    """Receive mode identifiers (AR5001D / AR5700D command set)."""
    WFM   = 0    # Wide FM           (broadcast)
    NFM   = 1    # Narrow FM         (amateur, PMR, public safety)
    AM    = 2    # Amplitude modulation
    LSB   = 3    # Lower sideband
    USB   = 4    # Upper sideband
    CW_U  = 5    # CW (upper pitch)
    CW_L  = 6    # CW (lower pitch)
    RAW   = 7    # Raw / discriminator output
    SFM   = 8    # Super narrow FM
    WAM   = 9    # Wide AM
    # AR5700D digital modes
    DALL  = 40   # Auto digital (any supported digital mode)
    DCR   = 41   # D-CR / IDAS / NXDN
    DPMR  = 42   # dPMR
    DMR   = 43   # DMR / MotoTRBO
    P25   = 44   # APCO Project 25
    DSTAR = 45   # D-STAR
    YSF   = 46   # Yaesu System Fusion
    EJ47  = 47   # EJ-47

    @classmethod
    def label(cls, value: int) -> str:
        """Return mode name string; falls back to 'MODExx' for unknown values."""
        try:
            return cls(value).name
        except ValueError:
            return f"MODE{value:02d}"

    @classmethod
    def options(cls) -> list[dict]:
        """Return list of {value, label} dicts for UI dropdowns."""
        return [{"value": int(m), "label": m.name} for m in cls]


# ── Exceptions ────────────────────────────────────────────────────────────────

class AR5700DError(Exception):
    """Base exception for all AR5700D errors."""


class AR5700DTimeout(AR5700DError):
    """Serial read timed out while waiting for a response."""


class AR5700DNotConnected(AR5700DError):
    """A command was issued while the device is not connected."""


class AR5700DProtocolError(AR5700DError):
    """Received an unexpected or malformed response from the device."""


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SpectrumFrame:
    """One spectrum sweep returned by the FD command."""
    bins: list[float]   # dBm values, length == FD_BINS (160)
    center_hz: int      # center frequency at time of capture
    span_hz: int        # span in Hz at time of capture
    timestamp: float    # time.monotonic() at capture


@dataclass
class DeviceStatus:
    """Aggregated device state snapshot (multiple serial queries)."""
    connected: bool
    frequency_hz: int
    mode: int
    mode_label: str
    signal_dbm: Optional[float]
    attenuator: int
    firmware: str


# ── AR5700D class ─────────────────────────────────────────────────────────────

class AR5700D:
    """
    Thread-safe interface to the AOR AR5700D receiver via USB serial.

    All public methods acquire ``_lock`` so they can be called safely from
    multiple threads simultaneously (e.g. HTTP handler + spectrum poller).

    Usage::

        dev = AR5700D(port="COM3")
        dev.connect()
        dev.set_frequency(145_800_000)
        frame = dev.get_spectrum()
        dev.disconnect()
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115_200,
        timeout: float = 2.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._firmware: str = "unknown"
        # Spectrum state — mirrored here so SpectrumFrame can be populated
        self._center_hz: int = 145_800_000
        self._span_hz: int = 500_000

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the serial port and verify the device responds.

        Raises :class:`AR5700DError` if the port cannot be opened or the
        device does not respond to a firmware version query.
        """
        port = self._port or self._auto_detect_port()
        if port is None:
            raise AR5700DError(
                "No COM port specified and auto-detection found no AOR device. "
                "Set 'device.port' in config.yaml  (e.g. COM3 or /dev/ttyACM0)."
            )
        log.info("Connecting to AR5700D on %s @ %d bps", port, self._baudrate)
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
                write_timeout=self._timeout,
            )
            # Allow USB CDC ACM to settle, then discard any buffered data that
            # the device may have sent (e.g. unsolicited spectrum frames left
            # over from a previous SDR# session).
            time.sleep(0.2)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            # Send a bare CR to flush any partial command stuck in the device's
            # receive buffer; discard whatever it echoes back.
            self._serial.write(b"\r")
            time.sleep(0.1)
            self._serial.reset_input_buffer()
            self._firmware = self._query_firmware_unlocked()
            log.info("Connected. Firmware: %s", self._firmware)
        except serial.SerialException as exc:
            self._serial = None
            raise AR5700DError(f"Cannot open {port}: {exc}") from exc

    def disconnect(self) -> None:
        """Close the serial port."""
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
                log.info("Disconnected from AR5700D")
            self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Receiver control ──────────────────────────────────────────────────────

    def get_firmware_version(self) -> str:
        with self._lock:
            return self._query_firmware_unlocked()

    def set_frequency(self, freq_hz: int) -> None:
        """Tune receiver to *freq_hz* Hz (100 kHz – 3.7 GHz)."""
        with self._lock:
            self._send_command("RF", f"{freq_hz:010d}")

    def get_frequency(self) -> int:
        with self._lock:
            return int(self._query_unlocked("RF"))

    def set_mode(self, mode: int) -> None:
        """Set receive mode; use :class:`Mode` enum values or raw int."""
        with self._lock:
            self._send_command("MD", f"{int(mode):02d}")

    def get_mode(self) -> int:
        with self._lock:
            return int(self._query_unlocked("MD"))

    def set_attenuator(self, level: int) -> None:
        """Set attenuator level (0 = off; positive = attenuation in device steps)."""
        with self._lock:
            self._send_command("AT", f"{level:02d}")

    def get_attenuator(self) -> int:
        with self._lock:
            return int(self._query_unlocked("AT"))

    def get_signal_level(self) -> float:
        """Return S-meter reading in dBm via the LMX command."""
        with self._lock:
            try:
                resp = self._query_unlocked("LMX")
                # Response: "-073 0"  (dBm value + squelch status flag)
                return float(resp.strip().split()[0])
            except (ValueError, IndexError) as exc:
                log.warning("Cannot parse LMX response: %r (%s)", resp, exc)
                return -999.0

    # ── Spectrum control ──────────────────────────────────────────────────────

    def set_spectrum_center(self, freq_hz: int) -> None:
        """Set the spectrum analyser center frequency (CF command)."""
        with self._lock:
            self._send_command("CF", f"{freq_hz:010d}")
            self._center_hz = freq_hz

    def set_spectrum_span(self, span_hz: int) -> None:
        """Set the spectrum analyser span in Hz (FF command)."""
        with self._lock:
            self._send_command("FF", f"{span_hz:010d}")
            self._span_hz = span_hz

    def get_spectrum(self) -> SpectrumFrame:
        """
        Request one spectrum frame via the FD (fast-display) command.

        Returns a :class:`SpectrumFrame` with :data:`FD_BINS` (160) dBm values.
        Acquires the serial lock; safe to call from any thread.
        """
        with self._lock:
            raw = self._request_fd_unlocked()
        bins = _decode_fd_bytes(raw)
        return SpectrumFrame(
            bins=bins,
            center_hz=self._center_hz,
            span_hz=self._span_hz,
            timestamp=time.monotonic(),
        )

    def get_status(self) -> DeviceStatus:
        """Return a snapshot of the full device state (several serial queries)."""
        with self._lock:
            freq = int(self._query_unlocked("RF"))
            mode = int(self._query_unlocked("MD"))
            att  = int(self._query_unlocked("AT"))
            try:
                sig = float(self._query_unlocked("LMX").strip().split()[0])
            except Exception:
                sig = None
        return DeviceStatus(
            connected=self.is_connected,
            frequency_hz=freq,
            mode=mode,
            mode_label=Mode.label(mode),
            signal_dbm=sig,
            attenuator=att,
            firmware=self._firmware,
        )

    # ── Class-level helpers ───────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[str]:
        """Return available COM port names; AOR-branded ports are listed first."""
        priority: list[str] = []
        others: list[str] = []
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if any(kw in desc for kw in _AOR_KEYWORDS):
                priority.append(p.device)
            else:
                others.append(p.device)
        return priority + others

    # ── Internal helpers (must be called with _lock held) ─────────────────────

    def _assert_connected(self) -> None:
        if not self.is_connected:
            raise AR5700DNotConnected("Device is not connected.")

    def _send_command(self, cmd: str, value: str = "") -> None:
        """Write an ASCII command.  Does NOT read any response."""
        self._assert_connected()
        packet = f"{cmd}{value}\r".encode("ascii")
        log.debug("TX: %r", packet)
        self._serial.write(packet)  # type: ignore[union-attr]

    def _read_ascii_response(self, expected_cmd: Optional[str] = None) -> str:
        """
        Read one ASCII response terminated by b' \\r\\n'.

        Returns the value portion of the response (after the command echo).
        Raises :class:`AR5700DTimeout` if no data arrives within the timeout.

        If *expected_cmd* is given and the echo does not match, a warning is
        logged so protocol desyncs are visible in the log.
        """
        self._assert_connected()
        buf = bytearray()
        while True:
            ch = self._serial.read(1)  # type: ignore[union-attr]
            if not ch:
                raise AR5700DTimeout(
                    f"Timeout waiting for response (buffer so far: {bytes(buf)!r})"
                )
            buf += ch
            if buf.endswith(RESP_TERM):
                break
        text = buf.decode("ascii", errors="replace").strip()
        log.debug("RX: %r", text)
        # Response echoes the command name: "RF 0145800000" → return "0145800000"
        parts = text.split(None, 1)
        if expected_cmd and (not parts or parts[0] != expected_cmd):
            log.warning(
                "Protocol desync: sent %r but response echo was %r (full: %r)",
                expected_cmd, parts[0] if parts else "", text[:60],
            )
        return parts[1] if len(parts) > 1 else parts[0]

    def _query_unlocked(self, cmd: str) -> str:
        """Send a query command and return the value string.  Lock must be held.

        Flushes the input buffer before sending so that any stale bytes left by
        a previous FD frame (or unsolicited device output) cannot pollute this
        response.
        """
        self._serial.reset_input_buffer()  # type: ignore[union-attr]
        self._send_command(cmd)
        return self._read_ascii_response(expected_cmd=cmd)

    def _query_firmware_unlocked(self) -> str:
        try:
            return self._query_unlocked("VR")
        except AR5700DError:
            return "unknown"

    def _request_fd_unlocked(self) -> bytes:
        """
        Send FD command and read the 160-byte binary spectrum response.

        Expected response format::

            b'FD ' + <160 binary bytes> + b' \\r\\n'

        Some firmware variants may omit the 'FD ' echo — both cases are handled.
        The suffix is consumed by reading until RESP_TERM (rather than a fixed
        3-byte read) so firmware variants that use a shorter \\r\\n suffix are
        tolerated without leaving bytes in the buffer.
        """
        self._assert_connected()
        self._serial.reset_input_buffer()  # type: ignore[union-attr]
        self._serial.write(b"FD\r")  # type: ignore[union-attr]

        # Read the first 3 bytes to detect echo presence
        prefix = self._serial.read(3)  # type: ignore[union-attr]
        if len(prefix) < 3:
            raise AR5700DTimeout("Timeout reading FD response prefix")

        if prefix == b"FD ":
            # Normal case: echo present — read 160 data bytes
            data = self._serial.read(FD_BINS)  # type: ignore[union-attr]
        else:
            # No echo: the 3 bytes we read are already spectrum data
            data = prefix + self._serial.read(FD_BINS - 3)  # type: ignore[union-attr]

        if len(data) < FD_BINS:
            raise AR5700DTimeout(
                f"FD response truncated: expected {FD_BINS} bytes, got {len(data)}"
            )

        # Consume suffix by reading until RESP_TERM; handles both ' \r\n' (3 B)
        # and '\r\n' (2 B) firmware variants without leaving bytes in the buffer.
        sfx_buf = bytearray()
        while len(sfx_buf) < 10:
            ch = self._serial.read(1)  # type: ignore[union-attr]
            if not ch:
                log.debug("FD suffix read timeout after %d bytes: %r", len(sfx_buf), bytes(sfx_buf))
                break
            sfx_buf += ch
            if sfx_buf.endswith(RESP_TERM):
                break

        log.debug("RX FD: %d bytes, suffix: %r", len(data), bytes(sfx_buf))
        return bytes(data)

    @staticmethod
    def _auto_detect_port() -> Optional[str]:
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if any(kw in desc for kw in _AOR_KEYWORDS):
                log.info("Auto-detected AOR device on %s (%s)", p.device, p.description)
                return p.device
        log.warning("Auto-detection: no AOR-branded COM port found")
        return None


# ── Pure DSP helper (module-level, easily unit-tested) ────────────────────────

def _decode_fd_bytes(raw: bytes) -> list[float]:
    """
    Convert raw FD response bytes to a list of dBm values.

    Formula:  dBm = (byte - FD_LEVEL_OFFSET) + FD_DBM_BASE
                  = byte - 0x20 - 100
    """
    return [float((b - FD_LEVEL_OFFSET) + FD_DBM_BASE) for b in raw[:FD_BINS]]
