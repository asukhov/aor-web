"""
Unit tests for the AR5700D device layer.

These tests run without any hardware — the serial port is mocked using
unittest.mock, and the MockAR5700D is tested end-to-end.

Run:
    python -m pytest tests/ -v
  or:
    python -m unittest discover -s tests
"""
import time
import unittest
from unittest.mock import MagicMock, patch, call

from backend.device.ar5700d import (
    AR5700D,
    AR5700DNotConnected,
    AR5700DTimeout,
    FD_BINS,
    FD_DBM_BASE,
    FD_LEVEL_OFFSET,
    Mode,
    _decode_fd_bytes,
)
from backend.device.mock_serial import MockAR5700D
from backend.dsp.spectrum import (
    SpectrumProcessor,
    pack_spectrum_frame,
    unpack_spectrum_frame,
)


# ── _decode_fd_bytes ──────────────────────────────────────────────────────────

class TestDecodeFdBytes(unittest.TestCase):

    def test_noise_floor_byte(self):
        """Byte value 0x20 (FD_LEVEL_OFFSET) should map to FD_DBM_BASE (-100 dBm)."""
        raw = bytes([FD_LEVEL_OFFSET] * FD_BINS)
        result = _decode_fd_bytes(raw)
        self.assertEqual(len(result), FD_BINS)
        self.assertAlmostEqual(result[0], FD_DBM_BASE)

    def test_strong_signal_byte(self):
        """Byte 0x84 (132) → (132 - 32) + (-100) = 0 dBm."""
        raw = bytes([0x84] * FD_BINS)
        result = _decode_fd_bytes(raw)
        self.assertAlmostEqual(result[0], 0.0)

    def test_below_floor_byte(self):
        """Byte 0 → (0 - 32) + (-100) = -132 dBm (below display floor, still valid)."""
        raw = bytes([0x00] * FD_BINS)
        result = _decode_fd_bytes(raw)
        self.assertAlmostEqual(result[0], -132.0)

    def test_length_truncates_long_input(self):
        """Output is capped at FD_BINS even when input is longer."""
        long_ = bytes([0x20] * 300)
        self.assertEqual(len(_decode_fd_bytes(long_)), FD_BINS)

    def test_length_short_input_returns_actual_length(self):
        """Short inputs return whatever bytes were provided (error recovery upstream)."""
        short = bytes([0x20] * 80)
        self.assertEqual(len(_decode_fd_bytes(short)), 80)

    def test_monotonic_mapping(self):
        """Higher byte values must produce higher dBm values."""
        raw = bytes(range(FD_BINS))
        result = _decode_fd_bytes(raw)
        for i in range(1, len(result)):
            self.assertGreater(result[i], result[i - 1])


# ── Mode enum ─────────────────────────────────────────────────────────────────

class TestModeEnum(unittest.TestCase):

    def test_known_labels(self):
        self.assertEqual(Mode.label(0),  "WFM")
        self.assertEqual(Mode.label(1),  "NFM")
        self.assertEqual(Mode.label(2),  "AM")
        self.assertEqual(Mode.label(3),  "LSB")
        self.assertEqual(Mode.label(4),  "USB")
        self.assertEqual(Mode.label(43), "DMR")
        self.assertEqual(Mode.label(44), "P25")

    def test_unknown_label_fallback(self):
        self.assertEqual(Mode.label(99), "MODE99")
        self.assertEqual(Mode.label(7),  "RAW")

    def test_options_returns_list(self):
        opts = Mode.options()
        self.assertIsInstance(opts, list)
        self.assertTrue(all("value" in o and "label" in o for o in opts))


# ── AR5700D serial protocol layer (with mocked serial port) ───────────────────

def _make_response(cmd: str, value: str) -> list[bytes]:
    """Produce a byte-by-byte sequence simulating 'CMD VALUE \r\n'."""
    full = f"{cmd} {value} \r\n".encode("ascii")
    return [bytes([b]) for b in full]


class TestAR5700DSerial(unittest.TestCase):

    def _build_device_with_mock_serial(self, serial_mock):
        """Return an AR5700D whose _serial attribute is pre-set to the mock."""
        dev = AR5700D.__new__(AR5700D)
        AR5700D.__init__(dev, port="COM99")
        import threading
        dev._lock     = threading.Lock()
        dev._serial   = serial_mock
        dev._firmware = "TEST-1.00"
        dev._center_hz = 145_800_000
        dev._span_hz   = 500_000
        return dev

    def test_not_connected_raises(self):
        dev = AR5700D(port="COM99")
        with self.assertRaises(AR5700DNotConnected):
            dev.get_spectrum()

    def test_set_frequency_sends_correct_command(self):
        mock_s = MagicMock()
        mock_s.is_open = True
        # Stub read() so _read_ascii_response doesn't hang (not called by set_frequency)
        mock_s.read.side_effect = _make_response("RF", "0145.800000")
        dev = self._build_device_with_mock_serial(mock_s)
        dev.set_frequency(145_800_000)
        mock_s.write.assert_called_once_with(b"RF0145.800000\r")

    def test_set_mode_pads_to_two_digits(self):
        mock_s = MagicMock()
        mock_s.is_open = True
        mock_s.read.side_effect = _make_response("MD", "01")
        dev = self._build_device_with_mock_serial(mock_s)
        dev.set_mode(1)
        mock_s.write.assert_called_once_with(b"MD01\r")

    def test_get_frequency_parses_response(self):
        mock_s = MagicMock()
        mock_s.is_open = True
        mock_s.read.side_effect = (
            _make_response("RF", "0433500000")   # get_frequency query
        )
        dev = self._build_device_with_mock_serial(mock_s)
        freq = dev.get_frequency()
        self.assertEqual(freq, 433_500_000)

    def test_get_frequency_parses_compact_mhz_response(self):
        """Compact 'RF0102.200000 \\r\\n' (no space) format → 102 200 000 Hz."""
        mock_s = MagicMock()
        mock_s.is_open = True
        # Simulate the real device response: no space between echo and value
        raw = b"RF0102.200000 \r\n"
        mock_s.read.side_effect = [bytes([b]) for b in raw]
        dev = self._build_device_with_mock_serial(mock_s)
        freq = dev.get_frequency()
        self.assertEqual(freq, 102_200_000)

    def test_parse_hz_integer_format(self):
        """Hz-integer string (legacy/mock) parses correctly."""
        self.assertEqual(AR5700D._parse_hz("0145800000"), 145_800_000)
        self.assertEqual(AR5700D._parse_hz("0433500000"), 433_500_000)

    def test_parse_hz_mhz_decimal_format(self):
        """MHz-decimal string (real hardware) parses correctly."""
        self.assertEqual(AR5700D._parse_hz("0145.800000"), 145_800_000)
        self.assertEqual(AR5700D._parse_hz("0102.200000"), 102_200_000)
        self.assertEqual(AR5700D._parse_hz("3700.000000"), 3_700_000_000)
        # Rounding: sub-Hz remainder must round correctly
        self.assertEqual(AR5700D._parse_hz("0145.8000004"), 145_800_000)

    def test_fd_response_with_echo(self):
        """FD response with 'FD ' echo prefix parsed correctly."""
        mock_s = MagicMock()
        mock_s.is_open = True

        # Simulate: b'FD ' + 160 bytes + b' \r\n'
        fd_data = bytes(range(FD_BINS))  # 160 bytes with known values
        responses = [
            b"FD ",          # 3-byte prefix read
            fd_data,         # 160-byte spectrum read
            b" \r\n",        # 3-byte suffix read
        ]
        mock_s.read.side_effect = responses
        dev = self._build_device_with_mock_serial(mock_s)

        frame = dev.get_spectrum()
        self.assertEqual(len(frame.bins), FD_BINS)
        # First byte is 0 → -132 dBm
        self.assertAlmostEqual(frame.bins[0], (0 - FD_LEVEL_OFFSET) + FD_DBM_BASE)

    def test_fd_response_without_echo(self):
        """FD response without echo prefix also parsed correctly."""
        mock_s = MagicMock()
        mock_s.is_open = True

        fd_data = bytes([FD_LEVEL_OFFSET] * FD_BINS)   # all at -100 dBm
        # First 3 bytes are data (no echo)
        responses = [
            fd_data[:3],              # prefix read (not 'FD ')
            fd_data[3:],              # remaining data
            b" \r\n",                 # suffix
        ]
        mock_s.read.side_effect = responses
        dev = self._build_device_with_mock_serial(mock_s)

        frame = dev.get_spectrum()
        self.assertEqual(len(frame.bins), FD_BINS)
        for b_val in frame.bins:
            self.assertAlmostEqual(b_val, FD_DBM_BASE)

    def test_timeout_on_fd_short_response(self):
        """Truncated FD response raises AR5700DTimeout."""
        mock_s = MagicMock()
        mock_s.is_open = True
        mock_s.read.side_effect = [
            b"FD ",
            bytes(40),   # only 40 bytes instead of 160
            b"",          # EOF / timeout
        ]
        dev = self._build_device_with_mock_serial(mock_s)
        with self.assertRaises(AR5700DTimeout):
            dev.get_spectrum()


# ── MockAR5700D ───────────────────────────────────────────────────────────────

class TestMockAR5700D(unittest.TestCase):

    def setUp(self):
        self.dev = MockAR5700D()
        self.dev.connect()

    def test_is_connected_after_connect(self):
        self.assertTrue(self.dev.is_connected)

    def test_is_disconnected_after_disconnect(self):
        self.dev.disconnect()
        self.assertFalse(self.dev.is_connected)

    def test_set_get_frequency(self):
        self.dev.set_frequency(433_500_000)
        self.assertEqual(self.dev.get_frequency(), 433_500_000)

    def test_set_get_mode(self):
        self.dev.set_mode(int(Mode.AM))
        self.assertEqual(self.dev.get_mode(), int(Mode.AM))

    def test_get_spectrum_returns_frame(self):
        frame = self.dev.get_spectrum()
        self.assertEqual(len(frame.bins), FD_BINS)
        self.assertIsInstance(frame.center_hz, int)
        self.assertIsInstance(frame.span_hz, int)
        self.assertIsInstance(frame.timestamp, float)

    def test_spectrum_bins_in_range(self):
        frame = self.dev.get_spectrum()
        for b in frame.bins:
            self.assertGreaterEqual(b, -132.0)   # min possible dBm
            self.assertLessEqual(b, 27.0)         # max reasonable dBm

    def test_spectrum_center_tracks_frequency(self):
        self.dev.set_frequency(462_000_000)
        frame = self.dev.get_spectrum()
        self.assertEqual(frame.center_hz, 462_000_000)

    def test_firmware_version(self):
        self.assertIn("MOCK", self.dev.get_firmware_version())


# ── SpectrumProcessor ──────────────────────────────────────────────────────────

class TestSpectrumProcessor(unittest.TestCase):

    def _make_bins(self, dbm: float, n: int = FD_BINS) -> list[float]:
        return [dbm] * n

    def test_floor_maps_to_zero(self):
        proc = SpectrumProcessor(db_min=-120, db_max=-20)
        norm = proc.process(self._make_bins(-120), 145_800_000, 500_000)
        for v in norm.values:
            self.assertAlmostEqual(float(v), 0.0, places=5)

    def test_ceiling_maps_to_one(self):
        proc = SpectrumProcessor(db_min=-120, db_max=-20)
        norm = proc.process(self._make_bins(-20), 145_800_000, 500_000)
        for v in norm.values:
            self.assertAlmostEqual(float(v), 1.0, places=5)

    def test_midpoint_maps_to_half(self):
        proc = SpectrumProcessor(db_min=-120, db_max=-20)
        norm = proc.process(self._make_bins(-70), 145_800_000, 500_000)
        for v in norm.values:
            self.assertAlmostEqual(float(v), 0.5, places=5)

    def test_clamps_below_floor(self):
        proc = SpectrumProcessor(db_min=-120, db_max=-20)
        norm = proc.process(self._make_bins(-999), 145_800_000, 500_000)
        for v in norm.values:
            self.assertAlmostEqual(float(v), 0.0, places=5)

    def test_clamps_above_ceiling(self):
        proc = SpectrumProcessor(db_min=-120, db_max=-20)
        norm = proc.process(self._make_bins(0), 145_800_000, 500_000)
        for v in norm.values:
            self.assertAlmostEqual(float(v), 1.0, places=5)

    def test_freq_axis_length(self):
        proc = SpectrumProcessor()
        norm = proc.process(self._make_bins(-80), 145_800_000, 500_000)
        self.assertEqual(len(norm.freqs_hz), FD_BINS)

    def test_freq_axis_span(self):
        proc = SpectrumProcessor()
        norm = proc.process(self._make_bins(-80), 145_800_000, 500_000)
        self.assertAlmostEqual(norm.freqs_hz[0],  145_800_000 - 250_000, delta=1)
        self.assertAlmostEqual(norm.freqs_hz[-1], 145_800_000 + 250_000, delta=1)


# ── Wire format round-trip ────────────────────────────────────────────────────

class TestWireFormat(unittest.TestCase):

    def _make_norm_frame(self):
        import numpy as np
        proc  = SpectrumProcessor(db_min=-120, db_max=-20)
        bins  = [-80.0] * FD_BINS
        return proc.process(bins, 145_800_000, 500_000)

    def test_round_trip(self):
        frame    = self._make_norm_frame()
        packed   = pack_spectrum_frame(frame)
        unpacked = unpack_spectrum_frame(packed)

        self.assertEqual(unpacked.center_hz, frame.center_hz)
        self.assertEqual(unpacked.span_hz,   frame.span_hz)
        self.assertAlmostEqual(unpacked.db_min, frame.db_min, places=4)
        self.assertAlmostEqual(unpacked.db_max, frame.db_max, places=4)
        import numpy as np
        np.testing.assert_allclose(unpacked.values, frame.values, atol=1e-5)

    def test_packed_size(self):
        frame  = self._make_norm_frame()
        packed = pack_spectrum_frame(frame)
        # Header 28 bytes + 160 × 4 bytes = 668 bytes
        self.assertEqual(len(packed), 28 + FD_BINS * 4)

    def test_magic_bytes(self):
        frame  = self._make_norm_frame()
        packed = pack_spectrum_frame(frame)
        self.assertEqual(packed[0], 0xAA)
        self.assertEqual(packed[1], 0x55)

    def test_bad_magic_raises(self):
        frame  = self._make_norm_frame()
        packed = bytearray(pack_spectrum_frame(frame))
        packed[0] = 0x00   # corrupt magic
        with self.assertRaises(ValueError):
            unpack_spectrum_frame(bytes(packed))


if __name__ == "__main__":
    unittest.main()
