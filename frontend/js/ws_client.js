/**
 * ws_client.js — WebSocket client for the /ws/spectrum endpoint.
 *
 * Parses incoming binary frames (see backend/dsp/spectrum.py for wire format)
 * and calls registered frame callbacks with typed data.
 *
 * Wire format (little-endian):
 *   Offset  Size      Type       Field
 *   0       2         uint8[2]   magic: 0xAA 0x55
 *   2       1         uint8      type: 0x01
 *   3       1         uint8      flags (reserved)
 *   4       4         uint32     n_bins
 *   8       8         int64      center_hz
 *   16      4         uint32     span_hz
 *   20      4         float32    db_min
 *   24      4         float32    db_max
 *   28      n_bins×4  float32[]  normalised magnitudes [0.0, 1.0]
 */

const MAGIC_0 = 0xAA;
const MAGIC_1 = 0x55;
const TYPE_SPECTRUM = 0x01;
const HEADER_SIZE   = 28;

export class SpectrumWSClient {
  /**
   * @param {string}   url        WebSocket URL, e.g. 'ws://host:8080/ws/spectrum'
   * @param {Function} onFrame    Called with a parsed SpectrumFrame object
   * @param {Function} onStatus   Called with 'connecting' | 'connected' | 'disconnected'
   */
  constructor(url, onFrame, onStatus) {
    this._url      = url;
    this._onFrame  = onFrame;
    this._onStatus = onStatus;
    this._ws       = null;
    this._reconnectDelay = 2000;
    this._stopped  = false;
  }

  connect() {
    if (this._stopped) return;
    this._onStatus('connecting');
    try {
      this._ws = new WebSocket(this._url);
      this._ws.binaryType = 'arraybuffer';

      this._ws.onopen    = () => {
        this._reconnectDelay = 2000;
        this._onStatus('connected');
      };
      this._ws.onmessage = (ev) => this._onMessage(ev);
      this._ws.onclose   = () => {
        this._onStatus('disconnected');
        if (!this._stopped) {
          setTimeout(() => this.connect(), this._reconnectDelay);
          this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 15_000);
        }
      };
      this._ws.onerror   = () => {
        // onclose fires after onerror; no extra action needed
      };
    } catch (e) {
      this._onStatus('disconnected');
      if (!this._stopped) {
        setTimeout(() => this.connect(), this._reconnectDelay);
      }
    }
  }

  disconnect() {
    this._stopped = true;
    if (this._ws) {
      this._ws.onclose = null;
      this._ws.close();
    }
  }

  /** Send a text JSON command to the server. */
  send(cmd) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(cmd));
    }
  }

  /** Update dB display range (tells the server-side processor). */
  setRange(dbMin, dbMax) {
    this.send({ cmd: 'set_range', db_min: dbMin, db_max: dbMax });
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  _onMessage(ev) {
    if (!(ev.data instanceof ArrayBuffer)) return;
    const buf  = ev.data;
    const view = new DataView(buf);

    if (buf.byteLength < HEADER_SIZE) return;

    // Validate magic
    if (view.getUint8(0) !== MAGIC_0 || view.getUint8(1) !== MAGIC_1) return;
    if (view.getUint8(2) !== TYPE_SPECTRUM) return;

    const nBins    = view.getUint32(4, true);
    // center_hz is int64 — use BigInt then convert to Number (safe up to 9007 THz)
    const centerHz = Number(view.getBigInt64(8, true));
    const spanHz   = view.getUint32(16, true);
    const dbMin    = view.getFloat32(20, true);
    const dbMax    = view.getFloat32(24, true);

    if (buf.byteLength < HEADER_SIZE + nBins * 4) return;

    const values = new Float32Array(buf, HEADER_SIZE, nBins);

    this._onFrame({
      values,
      centerHz,
      spanHz,
      dbMin,
      dbMax,
      nBins,
    });
  }
}
