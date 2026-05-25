# AOR AR5700D WebSDR

A self-hosted web-based spectrum display and receiver control panel for the
**AOR AR5700D** wideband digital receiver.  Open a browser from any device on
your network and get a live scrolling waterfall, spectrum curve, and full
receiver control — no plugins, no Flash, no Java.

```
┌──────────────────────────────────────────────────────────────────┐
│  AOR AR5700D WebSDR           Signal: -73.4 dBm  ● Connected    │
├──────────────┬──────────┬────────┬─────┬──────────────┬─────────┤
│ 145.8000 MHz │ NFM  ▲▼ │ 500kHz │ Off │ -120 ──── -20│ 10.0fps │
├──────────────┴──────────┴────────┴─────┴──────────────┴─────────┤
│                     Spectrum curve (power vs freq)               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                     Waterfall (scrolling)                        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Table of contents

1. [How it works](#how-it-works)
2. [Hardware requirements](#hardware-requirements)
3. [Architecture](#architecture)
4. [Project structure](#project-structure)
5. [Installation — Windows (primary target)](#installation--windows-primary-target)
6. [Installation — Linux](#installation--linux)
7. [Configuration reference](#configuration-reference)
8. [Running the server](#running-the-server)
9. [Using the web UI](#using-the-web-ui)
10. [REST API reference](#rest-api-reference)
11. [WebSocket wire format](#websocket-wire-format)
12. [Mock mode (no hardware)](#mock-mode-no-hardware)
13. [Running the tests](#running-the-tests)
14. [AR5700D serial protocol notes](#ar5700d-serial-protocol-notes)
15. [Troubleshooting](#troubleshooting)
16. [Roadmap — Phase 2 (I/Q streaming)](#roadmap--phase-2-iq-streaming)

---

## How it works

**Phase 1 (this release)** uses the AR5700D's built-in serial control interface
(USB CDC ACM — the same COM port SDR# uses) rather than raw I/Q streaming.
The receiver has a built-in spectrum analyser accessible via the `FD` command
which returns 160 spectral bins at whatever center frequency and span you
configure.  The server polls this at up to 10 fps, processes the data, and
streams it to the browser over a binary WebSocket.

```
AR5700D
  │  USB CDC ACM (virtual COM port)
  │  115 200 bps, ASCII commands + binary FD response
  ▼
Python backend  (FastAPI + asyncio)
  ├── Serial thread  — sends FD command, reads 160-byte response
  ├── Poller thread  — drives serial thread at target fps
  ├── REST API       — frequency / mode / span / attenuator control
  └── WebSocket      — binary spectrum frames to N simultaneous browsers
            │
            │  ws://host:8080/ws/spectrum
            │  668 bytes / frame  ×  10 fps  =  ~53 kbit/s per client
            ▼
Browser  (vanilla JS, zero dependencies)
  ├── Waterfall canvas  — scrolling colour display
  ├── Spectrum canvas   — live power-vs-frequency curve + dB grid
  └── Control panel     — frequency, mode, span, attenuation, dB range
```

**Why not raw I/Q?**  Raw I/Q streaming from the AR5700D requires loading
firmware onto the Cypress FX2LP USB chip at each power-on using Linux tools
(`cycfx2prog`), and live GNU Radio source support was not yet available at
the time of writing.  Phase 2 (see [Roadmap](#roadmap--phase-2-iq-streaming))
will add this for higher-resolution displays.

---

## Hardware requirements

| Item | Notes |
|---|---|
| AOR AR5700D | Wideband digital receiver |
| USB cable | Standard USB-A to USB-B or USB-C (depends on your unit) |
| Windows PC | Windows 10/11, connected to the receiver via USB |
| Python 3.11+ | Available from [python.org](https://python.org) — **no build tools needed** |
| Network | The PC running the server must be reachable from your browser |

The AR5700D exposes two USB interfaces.  This project uses **only the control
interface** (virtual COM port / CDC ACM) — the same one SDR# uses for tuning.
No special driver is required on Windows 10/11; the OS CDC ACM driver handles
it automatically.

The I/Q streaming interface (second USB port, Cypress FX2LP) is not used in
Phase 1.

---

## Architecture

### Data flow

```
┌─────────────┐   FD\r (ASCII)    ┌──────────────────┐
│  AR5700D    │ ──────────────────►│  ar5700d.py      │
│  (receiver) │◄─────────────────  │  serial layer    │
└─────────────┘   160 bytes (bin) └──────┬───────────┘
                                         │ SpectrumFrame
                                         ▼
                                  ┌──────────────────┐
                                  │  poller.py       │
                                  │  daemon thread   │
                                  │  10 fps target   │
                                  └──────┬───────────┘
                                         │ asyncio.Queue (per WS client)
                                         ▼
                                  ┌──────────────────┐
                                  │  stream.py       │
                                  │  WebSocket       │◄── Browser client 1
                                  │  handler         │◄── Browser client 2
                                  └──────┬───────────┘◄── Browser client N
                                         │ 668 bytes binary
                                         ▼
                                  ┌──────────────────┐
                                  │  ws_client.js    │
                                  │  DataView parse  │
                                  └──────┬───────────┘
                                    ┌────┴────┐
                              waterfall.js  spectrum.js
                              (canvas)      (canvas)
```

### Thread model

| Thread | Role |
|---|---|
| asyncio event loop (main) | FastAPI HTTP + WebSocket handlers |
| `spectrum-poller` (daemon) | Calls `AR5700D.get_spectrum()` at target fps; pushes frames to subscribed `asyncio.Queue` objects via `loop.call_soon_threadsafe()` |
| Serial I/O (synchronous, inside poller) | All serial reads/writes serialised by `threading.Lock` — safe to call from HTTP handlers concurrently |

---

## Project structure

```
aor-web/
│
├── run.py                     # Entry point — python run.py
├── config.yaml                # All configuration (edit before first run)
├── requirements.txt           # Python dependencies (Windows-compatible wheels)
│
├── backend/
│   ├── config.py              # Config dataclasses + YAML loader
│   ├── main.py                # FastAPI app, lifespan, route registration
│   │
│   ├── device/
│   │   ├── ar5700d.py         # AR5700D serial protocol layer (thread-safe)
│   │   ├── mock_serial.py     # Software simulation — no hardware needed
│   │   └── poller.py          # Background polling thread + async fan-out
│   │
│   ├── dsp/
│   │   └── spectrum.py        # Normalisation, freq axis, binary wire packing
│   │
│   └── api/
│       ├── control.py         # REST endpoints (freq / mode / span / att / range)
│       └── stream.py          # WebSocket /ws/spectrum handler
│
├── frontend/
│   ├── index.html             # Single-page app shell
│   ├── css/
│   │   └── style.css          # Dark theme, zero external CSS deps
│   └── js/
│       ├── app.js             # Bootstrap, resize, FPS counter, status polling
│       ├── waterfall.js       # Canvas waterfall renderer (copyWithin scroll)
│       ├── spectrum.js        # Canvas spectrum curve + dB grid
│       ├── controls.js        # REST calls, keyboard shortcuts, UI bindings
│       └── ws_client.js       # WebSocket client, binary frame parser, reconnect
│
└── tests/
    └── test_ar5700d.py        # 35 unit tests (no hardware required)
```

---

## Installation — Windows (primary target)

These steps assume a clean Windows 10 or 11 machine.  No compiler, no Visual
Studio, no WSL — just Python and pip.

### Step 1 — Install Python

Download **Python 3.11** or newer from https://www.python.org/downloads/windows/

During installation, tick **"Add python.exe to PATH"**.

Verify:

```cmd
python --version
```
```
Python 3.11.9
```

### Step 2 — Get the project

Either clone the repository (if you have Git installed):

```cmd
git clone https://github.com/YOUR_USERNAME/aor-web.git
cd aor-web
```

Or download and extract the ZIP, then open a Command Prompt in the extracted
folder.

### Step 3 — Install dependencies

All packages ship as pre-built Windows wheels.  No C compiler is needed.

```cmd
pip install -r requirements.txt
```

Expected output (abbreviated):

```
Collecting fastapi>=0.111.0
Collecting uvicorn>=0.29.0
Collecting pyserial>=3.5
Collecting numpy>=1.26.0
Collecting pyyaml>=6.0.1
...
Successfully installed fastapi-0.111.x numpy-1.26.x pyserial-3.5 ...
```

### Step 4 — Find the COM port

Plug in the AR5700D via USB.  Open **Device Manager** → **Ports (COM & LPT)**.
You will see an entry such as:

```
USB Serial Device (COM3)
```
or
```
AOR AR5700D (COM4)
```

Note the `COMx` number.

### Step 5 — Configure

Open `config.yaml` in any text editor (Notepad works fine) and set the port:

```yaml
device:
  port: "COM3"      # ← change to your COM port number
  mock: false
```

Adjust the default frequency if needed:

```yaml
receiver:
  default_frequency: 145800000   # Hz — 145.800 MHz
  default_mode: 1                # 1 = NFM
```

### Step 6 — Run

```cmd
python run.py
```

Expected output:

```
Starting AOR AR5700D WebSDR on http://0.0.0.0:8080
Press Ctrl+C to stop.

INFO     Connecting to AR5700D on COM3 @ 115200 bps
INFO     Connected. Firmware: 1.05
INFO     Device ready — firmware: 1.05
INFO     Spectrum poller started (target 10.0 fps)
INFO     Uvicorn running on http://0.0.0.0:8080
```

### Step 7 — Open in browser

Navigate to `http://localhost:8080` on the same machine, or
`http://192.168.x.x:8080` from any other device on the same network
(replace with the actual IP of the Windows machine).

---

## Installation — Linux

Linux installation follows the same steps but uses a virtual environment and
a different port path.

```bash
git clone https://github.com/YOUR_USERNAME/aor-web.git
cd aor-web

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Set the port in `config.yaml`:

```yaml
device:
  port: "/dev/ttyACM0"    # or /dev/ttyUSB0 — check dmesg after plugging in
```

Add your user to the `dialout` group so Python can open the serial port
without `sudo`:

```bash
sudo usermod -a -G dialout $USER
# Log out and back in for the group change to take effect
```

Run:

```bash
python run.py
```

For a production-style deployment with automatic restart, create a systemd
service:

```ini
# /etc/systemd/system/aor-web.service
[Unit]
Description=AOR AR5700D WebSDR
After=network.target

[Service]
User=yourusername
WorkingDirectory=/home/yourusername/aor-web
ExecStart=/home/yourusername/aor-web/.venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now aor-web
sudo systemctl status aor-web
```

---

## Configuration reference

All settings live in `config.yaml` in the project root.  The server reads
this file at startup; restart required for changes to take effect.

```yaml
device:
  port: null          # COM port. null = auto-detect by AOR device name.
                      # Windows: "COM3"  Linux: "/dev/ttyACM0"
  baudrate: 115200    # Default baud rate. Do not change unless you used the
                      # UB command to change the receiver's baud rate.
  timeout: 2.0        # Seconds to wait for a serial response before raising
                      # AR5700DTimeout. Increase if you see timeout errors.
  mock: false         # true = run without hardware (synthetic spectrum).
                      # Useful for development and UI testing.

receiver:
  default_frequency: 145800000  # Hz. Receiver is tuned here on startup.
  default_mode: 1               # Receive mode set on startup.
                                # 0=WFM 1=NFM 2=AM 3=LSB 4=USB 5=CW-U
                                # 6=CW-L 7=RAW 8=SFM 9=WAM
                                # 40=DALL 41=DCR 42=dPMR 43=DMR 44=P25
                                # 45=D-STAR 46=YSF 47=EJ-47
  default_attenuator: 0         # 0=off, 1=10dB, 2=20dB, 3=30dB

spectrum:
  center_hz: 145800000  # Hz. Initial spectrum analyser center frequency.
  span_hz: 500000       # Hz. Initial span. Smaller spans = finer resolution
                        # across the 160 bins (e.g. 25kHz → 156 Hz/bin).
  fps: 10               # Target waterfall frames per second.
                        # Practical maximum is ~20 fps over USB serial.
  db_min: -120.0        # dBm. Signals at or below this map to the coldest
                        # colour (black). Adjust for your noise floor.
  db_max: -20.0         # dBm. Signals at or above this map to the hottest
                        # colour (red). Adjust for your strongest signal.

server:
  host: "0.0.0.0"  # Bind address. 0.0.0.0 = all interfaces (accessible
                   # from the network). Use "127.0.0.1" for localhost only.
  port: 8080       # HTTP and WebSocket port.
```

### COM port auto-detection

If `port` is set to `null`, the server scans all COM ports and picks the first
one whose description contains any of these strings:
`AOR`, `AR5700`, `AR5001`, `CP210`, `CH340`, `CDC`.

If auto-detection fails, set the port explicitly.  You can list all available
ports without starting the server:

```python
from backend.device.ar5700d import AR5700D
print(AR5700D.list_ports())
```

Or via the REST API once the server is running:

```
GET http://localhost:8080/api/ports
```

---

## Running the server

### Normal mode (hardware connected)

```cmd
python run.py
```

### Mock mode (no hardware — synthetic spectrum)

Either edit `config.yaml`:

```yaml
device:
  mock: true
```

Or pass it as a one-liner without editing the file:

```cmd
python -c "
import backend.config as c; _orig = c.load_config
def _p(*a,**k):
  cfg = _orig(*a,**k); cfg.device.mock = True; return cfg
c.load_config = _p
import uvicorn; uvicorn.run('backend.main:app', host='0.0.0.0', port=8080)
"
```

### Custom port / host

```cmd
python run.py
```

Server host and port are read from `config.yaml` → `server.host` /
`server.port`.  To override without editing the file, set environment
variables — or simply edit the YAML.

### Interactive API docs

FastAPI generates live API documentation automatically.  While the server is
running, open:

- `http://localhost:8080/docs` — Swagger UI (try endpoints interactively)
- `http://localhost:8080/redoc` — ReDoc (cleaner reference view)

---

## Using the web UI

### Frequency entry

Type a frequency in **MHz** in the large input at the top left and press
**Enter** or click elsewhere to tune.  Accepted formats:

| Input | Tunes to |
|---|---|
| `145.8` | 145.800 MHz |
| `145.8000` | 145.800 MHz |
| `433.5` | 433.500 MHz |
| `1296.1` | 1296.100 MHz |

Use the **▲ / ▼ arrow buttons** next to the frequency input to step up/down
by the selected step size.  The step size selector supports 1 kHz, 5 kHz,
10 kHz, 25 kHz, 100 kHz, and 1 MHz.

**Keyboard shortcuts** (when the frequency box is not focused):
- `↑` — step frequency up
- `↓` — step frequency down

### Mode selector

Selects the demodulation mode used by the receiver.  Available modes:

| Value | Name | Use case |
|---|---|---|
| 0 | WFM | Broadcast FM (87.5 – 108 MHz) |
| 1 | NFM | Amateur / PMR / public safety |
| 2 | AM | Shortwave, aviation (VHF) |
| 3 | LSB | HF amateur below 10 MHz |
| 4 | USB | HF amateur above 10 MHz |
| 5 | CW-U | CW (upper pitch) |
| 6 | CW-L | CW (lower pitch) |
| 40 | DALL | Auto digital mode detection |
| 43 | DMR | DMR / MotoTRBO |
| 44 | P25 | APCO Project 25 |
| 45 | D-STAR | D-STAR |
| 46 | YSF | Yaesu System Fusion |

### Span selector

Controls the frequency range shown in the waterfall and spectrum.  A narrower
span gives finer bin resolution across the 160 bins:

| Span | Resolution per bin |
|---|---|
| 10 kHz | ~62 Hz |
| 25 kHz | ~156 Hz |
| 50 kHz | ~313 Hz |
| 100 kHz | ~625 Hz |
| 250 kHz | ~1.6 kHz |
| **500 kHz** (default) | **~3.1 kHz** |
| 1 MHz | ~6.25 kHz |
| 2 MHz | ~12.5 kHz |

### Attenuator

Applies RF attenuation before the first stage.  Use when strong nearby signals
are causing intermodulation:

- `Off` — maximum sensitivity
- `10 dB` — moderate attenuation
- `20 dB` — strong attenuation
- `30 dB` — maximum attenuation

### dB range sliders

The two range sliders control the **display floor** (left) and **display
ceiling** (right) in dBm.  Signals below the floor appear as cold/black;
signals above the ceiling are clipped to hot/red.

Adjust these to match your RF environment:

- **Quiet rural site**: floor −120, ceiling −40
- **Suburban with some QRM**: floor −110, ceiling −30
- **Urban / strong local signals**: floor −100, ceiling −20

Changes are applied immediately and also sent to the server so newly
connecting clients inherit the same range.

### Waterfall colours

| Colour | Signal strength |
|---|---|
| Black | At or below display floor |
| Blue | Weak signal |
| Cyan | Low-medium signal |
| Green | Medium signal |
| Yellow | Strong signal |
| Orange/Red | At or above display ceiling |

---

## REST API reference

Base URL: `http://host:8080`

All `POST` endpoints accept and return `application/json`.

---

### `GET /api/status`

Returns the current receiver state.  Requires device to be connected.

**Response `200 OK`:**

```json
{
  "connected": true,
  "frequency_hz": 145800000,
  "mode": 1,
  "mode_label": "NFM",
  "signal_dbm": -73.4,
  "attenuator": 0,
  "firmware": "1.05"
}
```

**Response `503`** when device is not connected:

```json
{"detail": "Device not connected"}
```

---

### `GET /api/ports`

Lists available COM ports on the server machine.  Useful for finding the right
port if auto-detection fails.

**Response `200 OK`:**

```json
{
  "ports": ["COM3", "COM5", "COM1"]
}
```

AOR-branded ports (matched by description keywords) are listed first.

---

### `GET /api/modes`

Returns all supported mode values and their labels.

**Response `200 OK`:**

```json
{
  "modes": [
    {"value": 0, "label": "WFM"},
    {"value": 1, "label": "NFM"},
    {"value": 2, "label": "AM"},
    ...
  ]
}
```

---

### `POST /api/frequency`

Tune the receiver.

**Request:**

```json
{"hz": 145800000}
```

Valid range: `100000` (100 kHz) to `3700000000` (3.7 GHz).

**Response `200 OK`:**

```json
{"ok": true, "frequency_hz": 145800000}
```

**Response `400`** for out-of-range frequency:

```json
{"detail": "Frequency 0 Hz is out of the AR5700D range (100 kHz – 3.7 GHz)"}
```

---

### `POST /api/mode`

Set receive mode.

**Request:**

```json
{"mode": 1}
```

**Response `200 OK`:**

```json
{"ok": true, "mode": 1, "label": "NFM"}
```

---

### `POST /api/attenuator`

Set attenuator level.

**Request:**

```json
{"level": 0}
```

**Response `200 OK`:**

```json
{"ok": true, "level": 0}
```

---

### `POST /api/spectrum`

Set the spectrum analyser center frequency and span.  This updates both the
receiver's internal spectrum parameters and the server state so new WebSocket
frames reflect the new range.

**Request:**

```json
{"center_hz": 433500000, "span_hz": 100000}
```

**Response `200 OK`:**

```json
{"ok": true, "center_hz": 433500000, "span_hz": 100000}
```

---

### `POST /api/range`

Update the dBm display range.  Affects all currently connected WebSocket
clients on their next frame.

**Request:**

```json
{"db_min": -110.0, "db_max": -30.0}
```

**Response `200 OK`:**

```json
{"ok": true, "db_min": -110.0, "db_max": -30.0}
```

---

### `GET /docs`

Interactive Swagger UI — try all endpoints in the browser.

---

### Example: curl

```bash
# Tune to 433.5 MHz
curl -X POST http://localhost:8080/api/frequency \
     -H "Content-Type: application/json" \
     -d '{"hz": 433500000}'

# Set mode to AM
curl -X POST http://localhost:8080/api/mode \
     -H "Content-Type: application/json" \
     -d '{"mode": 2}'

# Get status
curl http://localhost:8080/api/status

# List ports
curl http://localhost:8080/api/ports
```

---

### Example: Python requests

```python
import requests

BASE = "http://localhost:8080"

# Tune to 2m calling
requests.post(f"{BASE}/api/frequency", json={"hz": 145_800_000})

# 500 kHz span centered on tuned frequency
requests.post(f"{BASE}/api/spectrum", json={"center_hz": 145_800_000, "span_hz": 500_000})

# Read status
status = requests.get(f"{BASE}/api/status").json()
print(f"Frequency: {status['frequency_hz'] / 1e6:.4f} MHz  Signal: {status['signal_dbm']} dBm")
```

---

## WebSocket wire format

Connect to `ws://host:8080/ws/spectrum`.

The server sends **binary messages** at the configured `fps`.  Each message is
**668 bytes** (for the standard 160-bin configuration):

```
Offset  Size      Type       Field
------  --------  ---------  -----------------------------------------------
0       2         uint8[2]   magic:     0xAA 0x55 (frame validation)
2       1         uint8      type:      0x01 = spectrum frame
3       1         uint8      flags:     reserved, currently 0x00
4       4         uint32 LE  n_bins:    number of magnitude values (160)
8       8         int64  LE  center_hz: centre frequency in Hz
16      4         uint32 LE  span_hz:   span in Hz
20      4         float32 LE db_min:    display floor in dBm
24      4         float32 LE db_max:    display ceiling in dBm
28      640       float32 LE values:    normalised magnitudes [0.0 – 1.0]
                             (160 bins × 4 bytes each)
```

### Parsing example — JavaScript

```javascript
ws.binaryType = 'arraybuffer';
ws.onmessage = (ev) => {
  const view = new DataView(ev.data);

  // Validate magic
  if (view.getUint8(0) !== 0xAA || view.getUint8(1) !== 0x55) return;
  if (view.getUint8(2) !== 0x01) return; // not a spectrum frame

  const nBins    = view.getUint32(4, true);
  const centerHz = Number(view.getBigInt64(8, true));  // BigInt → Number
  const spanHz   = view.getUint32(16, true);
  const dbMin    = view.getFloat32(20, true);
  const dbMax    = view.getFloat32(24, true);

  // Zero-copy typed array view into the payload
  const values = new Float32Array(ev.data, 28, nBins);  // [0.0 – 1.0]

  // values[0] is the leftmost bin at (centerHz - spanHz/2)
  // values[nBins-1] is the rightmost bin at (centerHz + spanHz/2)
};
```

### Sending control commands — JavaScript

The browser can send **text JSON messages** over the same WebSocket to update
per-client settings without a full HTTP round-trip:

```javascript
// Update dB display range for this client only
ws.send(JSON.stringify({ cmd: 'set_range', db_min: -110, db_max: -30 }));

// Ping / keep-alive check
ws.send(JSON.stringify({ cmd: 'ping' }));
// Server replies: {"status": "ok", "cmd": "pong"}
```

### Bandwidth

| Configuration | Bytes/frame | At 10 fps |
|---|---|---|
| 160 bins (Phase 1) | 668 bytes | ~53 kbit/s per client |
| 1024 bins (Phase 2, planned) | 4124 bytes | ~330 kbit/s per client |

---

## Mock mode (no hardware)

Enable mock mode to run the server with a fully synthetic spectrum — useful
for development, UI work, and testing on machines that do not have the receiver
connected.

In `config.yaml`:

```yaml
device:
  mock: true
```

The mock device (`backend/device/mock_serial.py`) generates:

- A Gaussian noise floor at ~−100 dBm ± 3 dBm
- A main carrier at the tuned frequency with slow sinusoidal AM modulation
- Three weak spurious signals at fixed fractional positions across the span
- All signals drift slowly to look realistic

All REST endpoints and the WebSocket stream behave identically to the real
hardware mode.  Mode changes, frequency tuning, and span changes all take
effect in the synthetic spectrum.

---

## Running the tests

The test suite requires no hardware.  All 35 tests use either the mock device
or the serial layer with a `unittest.mock` patched port.

```bash
# Install pytest if not already installed
pip install pytest

# Run all tests with verbose output
python -m pytest tests/ -v
```

Expected output:

```
tests/test_ar5700d.py::TestDecodeFdBytes::test_noise_floor_byte PASSED
tests/test_ar5700d.py::TestDecodeFdBytes::test_strong_signal_byte PASSED
tests/test_ar5700d.py::TestDecodeFdBytes::test_monotonic_mapping PASSED
...
tests/test_ar5700d.py::TestMockAR5700D::test_get_spectrum_returns_frame PASSED
tests/test_ar5700d.py::TestMockAR5700D::test_spectrum_bins_in_range PASSED
tests/test_ar5700d.py::TestSpectrumProcessor::test_midpoint_maps_to_half PASSED
tests/test_ar5700d.py::TestWireFormat::test_round_trip PASSED
tests/test_ar5700d.py::TestWireFormat::test_packed_size PASSED
35 passed in 0.05s
```

### What is tested

| Test class | Covers |
|---|---|
| `TestDecodeFdBytes` | Raw FD byte → dBm conversion, edge cases |
| `TestModeEnum` | Mode labels, fallback for unknown values, options list |
| `TestAR5700DSerial` | Serial command formatting, response parsing, FD binary parsing (with/without echo), timeout handling — all with mocked serial port |
| `TestMockAR5700D` | Full mock device lifecycle, spectrum generation |
| `TestSpectrumProcessor` | Normalisation floor/ceiling/midpoint, clamping, frequency axis |
| `TestWireFormat` | Binary pack/unpack round-trip, size, magic validation |

---

## AR5700D serial protocol notes

The AR5700D uses the **AR5001D command set** with additional digital mode
commands.  All communication is ASCII over USB CDC ACM (virtual COM port) at
115 200 bps by default.

### Command format

```
TX:  <CMD><VALUE>\r
RX:  <CMD> <VALUE> \r\n
```

Query (no value):

```
TX:  RF\r
RX:  RF 0145800000 \r\n
```

Set:

```
TX:  RF0145800000\r
RX:  RF 0145800000 \r\n
```

### Key commands used by this project

| Command | Direction | Format | Description |
|---|---|---|---|
| `VR` | Query | `VR 1.05 \r\n` | Firmware version |
| `RF` | Set/Query | 10-digit Hz | Receive frequency |
| `MD` | Set/Query | 2-digit mode | Receive mode (00–54) |
| `AT` | Set/Query | 2-digit int | Attenuator/pre-amp level |
| `LM` | Query | hex `00`–`FF` | S-meter (relative) |
| `LMX` | Query | `-073 0` | S-meter in dBm + squelch flag |
| `CF` | Set | 10-digit Hz | Spectrum analyser center frequency |
| `FF` | Set | 10-digit Hz | Spectrum analyser span |
| `FD` | Binary response | See below | Fast spectrum data (160 bytes) |

### FD command (spectrum data)

```
TX:  FD\r
RX:  FD <160 binary bytes> \r\n
```

Each byte encodes one spectral bin:

```
dBm = (byte − 0x20) − 100

byte 0x20 (32)  →  −100 dBm  (noise floor)
byte 0x84 (132) →     0 dBm
byte 0xFF (255) →  +123 dBm  (theoretical maximum, well above saturation)
```

The 160 bins are evenly spaced across the span set by `CF`/`FF`, centred at
the frequency set by `CF`.

### Baud rate

The default baud rate is 115 200 bps.  It can be changed with the `UB`
command.  This project always opens the port at 115 200 unless you change
`device.baudrate` in `config.yaml`.

---

## Troubleshooting

### "No COM port specified and auto-detection found no AOR device"

The server cannot find the receiver's COM port automatically.

1. Check it appears in Device Manager → Ports (COM & LPT).
2. Set the port explicitly in `config.yaml`:
   ```yaml
   device:
     port: "COM3"
   ```
3. Verify no other application (SDR#, AOR software) has the port open — only
   one process can own a COM port at a time.

---

### "Cannot open COM3: [Errno 13] Permission denied" (Linux)

Your user is not in the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

Or use the explicit device path and check ownership:

```bash
ls -l /dev/ttyACM0
# Should show: crw-rw---- 1 root dialout ...
```

---

### Timeout errors / garbled responses

- Check no other app has the port open (SDR# must be closed).
- Try increasing `device.timeout` to `5.0` in `config.yaml`.
- Try `device.baudrate: 9600` if the receiver's baud rate was changed with `UB`.
- Unplug and re-plug the USB cable; the receiver sometimes needs a reset.

---

### Waterfall is frozen / no WebSocket data

1. Open browser DevTools → Console — check for WebSocket error messages.
2. Confirm the server is running: `curl http://localhost:8080/api/status`
3. Check that the firewall on the Windows machine allows inbound connections
   on port 8080.

To open port 8080 in Windows Firewall:

```cmd
netsh advfirewall firewall add rule name="AOR WebSDR" protocol=TCP dir=in localport=8080 action=allow
```

---

### The waterfall updates but frequency control does nothing

The REST call may be failing silently.  Open DevTools → Network tab, click a
control in the UI, and inspect the request/response.  Common causes:

- Browser is connecting to a different host than the server.
- The device serial connection was lost — check the server log.

---

### FPS is much lower than configured

The AR5700D's `FD` command takes approximately 20–50 ms per call over USB
serial (includes USB latency, serial baud overhead for the 160-byte binary
response, and device processing time).  This puts a practical ceiling of
roughly **15–20 fps** even with `fps: 20`.  The default of 10 fps leaves
comfortable headroom.

If you see consistently low FPS (< 5):
- Check the server log for poll error messages.
- Ensure the USB cable is in good condition.
- Reduce `fps` to 5 to ease the load on the serial interface.

---

## Roadmap — Phase 2 (I/Q streaming)

Phase 2 will add raw I/Q streaming via the AR5700D's second USB port (Cypress
FX2LP chip) for much higher resolution waterfall display and audio demodulation.

### What changes

| Feature | Phase 1 | Phase 2 |
|---|---|---|
| Spectrum source | Device built-in FFT (160 bins via `FD` command) | Raw I/Q @ 1.125 MSps → numpy FFT (1024–16384 bins) |
| Frequency resolution | 3.1 kHz/bin @ 500 kHz span | 1.1 Hz/bin @ 1.125 MHz bandwidth |
| Audio demodulation | No | AM, FM, SSB in numpy → Opus WebSocket |
| Linux requirement | No (serial only) | Yes (firmware upload via `cycfx2prog`) |

### I/Q technical details

- **USB device ID**: `08d0:a001` (AOR, LTD. DIGI-RECEIVER)
- **Chip**: Cypress EZ-USB FX2LP
- **Transfer type**: Isochronous USB
- **Sample rate**: 1.125 MSps
- **Sample format**: `float32`, I and Q interleaved
- **Python library**: `usb1` (Python ctypes wrapper for `libusb-1.0`)
- **Firmware**: `fx2fw.hex` — must be loaded at each power-on via `cycfx2prog`
- **GNU Radio**: AOR provide a `.grc` file (`iq5001_file_in.grc`) for
  offline playback of recorded I/Q files

### Planned Phase 2 additions

```
backend/device/iq_source.py    — libusb isochronous transfer capture
backend/dsp/fft_engine.py      — rolling numpy FFT, configurable window
backend/dsp/demod.py           — AM / FM / SSB demodulation
backend/api/audio.py           — WebSocket audio streaming (Opus)
frontend/js/audio.js           — Web Audio API playback
```

---

## Licence

MIT Licence — see `LICENSE` file (to be added).
