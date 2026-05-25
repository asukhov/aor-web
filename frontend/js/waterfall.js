/**
 * waterfall.js — Canvas-based scrolling waterfall renderer.
 *
 * Algorithm
 * ---------
 * An off-screen ImageData buffer holds the full pixel array for the waterfall
 * canvas.  Each new spectrum frame:
 *   1. Shifts the entire buffer down by one row using copyWithin() — a single
 *      bulk memory operation, no per-row loop needed.
 *   2. Writes the new row's RGB values into offset 0 of the buffer using a
 *      pre-computed 256-entry color LUT.
 *   3. Copies the buffer to the visible canvas with putImageData().
 *
 * Color map: black → blue → cyan → green → yellow → red (classic SDR scheme).
 */

// ── Colormap LUT ─────────────────────────────────────────────────────────────

/** Build a 256-entry RGB lookup table for normalised signal strength [0, 1]. */
function buildColormap() {
  // Control points: [normalised_value, [R, G, B]]
  const stops = [
    [0.00, [  0,   0,   0]],   // black    — noise floor
    [0.20, [  0,   0, 180]],   // blue
    [0.40, [  0, 200, 220]],   // cyan
    [0.55, [  0, 200,  50]],   // green
    [0.70, [220, 220,   0]],   // yellow
    [0.85, [255, 100,   0]],   // orange
    [1.00, [255,   0,   0]],   // red      — strongest signal
  ];

  const lut = new Uint8Array(256 * 3);

  for (let i = 0; i < 256; i++) {
    const v = i / 255;

    // Find surrounding stops
    let lower = stops[0];
    let upper = stops[stops.length - 1];
    for (let s = 0; s < stops.length - 1; s++) {
      if (v >= stops[s][0] && v <= stops[s + 1][0]) {
        lower = stops[s];
        upper = stops[s + 1];
        break;
      }
    }

    const range = upper[0] - lower[0];
    const t = range > 0 ? (v - lower[0]) / range : 0;

    lut[i * 3]     = Math.round(lower[1][0] + t * (upper[1][0] - lower[1][0]));
    lut[i * 3 + 1] = Math.round(lower[1][1] + t * (upper[1][1] - lower[1][1]));
    lut[i * 3 + 2] = Math.round(lower[1][2] + t * (upper[1][2] - lower[1][2]));
  }

  return lut;
}

const COLORMAP = buildColormap();


// ── WaterfallRenderer ────────────────────────────────────────────────────────

export class WaterfallRenderer {
  /**
   * @param {HTMLCanvasElement} canvas  The visible waterfall canvas element.
   */
  constructor(canvas) {
    this._canvas = canvas;
    this._ctx    = canvas.getContext('2d', { alpha: false });
    this._buf    = null;   // ImageData for the full canvas
    this._width  = 0;
    this._height = 0;
  }

  /**
   * Call whenever the canvas is resized.  Clears the waterfall.
   */
  resize(width, height) {
    this._canvas.width  = width;
    this._canvas.height = height;
    this._width  = width;
    this._height = height;
    this._buf = this._ctx.createImageData(width, height);
    // Pre-fill alpha channel to 255 (fully opaque)
    for (let i = 3; i < this._buf.data.length; i += 4) {
      this._buf.data[i] = 255;
    }
  }

  /**
   * Add one row to the waterfall.
   *
   * @param {Float32Array} values  Normalised magnitudes [0.0, 1.0], length = nBins.
   */
  addRow(values) {
    if (!this._buf) return;

    const data    = this._buf.data;
    const width   = this._width;
    const height  = this._height;
    const nBins   = values.length;
    const rowBytes = width * 4;

    // ── 1. Shift entire buffer DOWN by one row ────────────────────────────
    // copyWithin(target, start, end) — no explicit loop needed
    data.copyWithin(rowBytes, 0, (height - 1) * rowBytes);

    // ── 2. Write new row at y = 0 ─────────────────────────────────────────
    for (let x = 0; x < width; x++) {
      // Map pixel column to spectrum bin (linear interpolation)
      const binF   = x * (nBins - 1) / (width - 1);
      const bin0   = Math.floor(binF);
      const bin1   = Math.min(bin0 + 1, nBins - 1);
      const frac   = binF - bin0;
      const v      = values[bin0] * (1 - frac) + values[bin1] * frac;

      const lutIdx = Math.min(255, Math.floor(v * 255)) * 3;
      const px     = x * 4;
      data[px]     = COLORMAP[lutIdx];
      data[px + 1] = COLORMAP[lutIdx + 1];
      data[px + 2] = COLORMAP[lutIdx + 2];
      // data[px + 3] = 255 — already set in resize()
    }

    // ── 3. Flush to visible canvas ────────────────────────────────────────
    this._ctx.putImageData(this._buf, 0, 0);
  }

  /** Clear the waterfall to black. */
  clear() {
    if (!this._buf) return;
    this._buf.data.fill(0);
    for (let i = 3; i < this._buf.data.length; i += 4) {
      this._buf.data[i] = 255;
    }
    this._ctx.putImageData(this._buf, 0, 0);
  }
}
