/**
 * spectrum.js — Canvas spectrum curve renderer (power vs frequency).
 *
 * Draws a filled area chart on a dark background showing the current
 * power spectrum.  Overlays a dB grid and the cursor frequency marker.
 */

export class SpectrumRenderer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {number} dbMin   Display floor in dBm
   * @param {number} dbMax   Display ceiling in dBm
   */
  constructor(canvas, dbMin = -120, dbMax = -20) {
    this._canvas  = canvas;
    this._ctx     = canvas.getContext('2d', { alpha: false });
    this.dbMin    = dbMin;
    this.dbMax    = dbMax;
    this._lastValues    = null;
    this._lastCenterHz  = 0;
    this._lastSpanHz    = 0;
  }

  /** Update display range and redraw. */
  setRange(dbMin, dbMax) {
    this.dbMin = dbMin;
    this.dbMax = Math.max(dbMax, dbMin + 1);
    if (this._lastValues) {
      this.draw(this._lastValues, this._lastCenterHz, this._lastSpanHz);
    }
  }

  /** Resize canvas to new pixel dimensions. */
  resize(width, height) {
    this._canvas.width  = width;
    this._canvas.height = height;
    if (this._lastValues) {
      this.draw(this._lastValues, this._lastCenterHz, this._lastSpanHz);
    }
  }

  /**
   * Render one frame.
   *
   * @param {Float32Array} values    Normalised [0, 1] magnitudes from the server.
   * @param {number}       centerHz  Center frequency in Hz.
   * @param {number}       spanHz    Span in Hz.
   */
  draw(values, centerHz, spanHz) {
    this._lastValues   = values;
    this._lastCenterHz = centerHz;
    this._lastSpanHz   = spanHz;

    const ctx    = this._ctx;
    const W      = this._canvas.width;
    const H      = this._canvas.height;
    const nBins  = values.length;

    // Clear
    ctx.fillStyle = '#0b0d10';
    ctx.fillRect(0, 0, W, H);

    // ── dB grid lines ─────────────────────────────────────────────────────
    const dbRange  = this.dbMax - this.dbMin;
    const gridStep = this._niceStep(dbRange, 6);
    ctx.strokeStyle = '#1e2330';
    ctx.lineWidth   = 1;
    ctx.fillStyle   = '#4b5563';
    ctx.font        = '10px monospace';
    ctx.textAlign   = 'left';

    for (let db = Math.ceil(this.dbMin / gridStep) * gridStep; db <= this.dbMax; db += gridStep) {
      const y = H - ((db - this.dbMin) / dbRange) * H;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
      if (db !== this.dbMin) {
        ctx.fillText(`${db} dBm`, 3, y - 2);
      }
    }

    // ── Spectrum fill ──────────────────────────────────────────────────────
    ctx.beginPath();
    ctx.moveTo(0, H);

    for (let x = 0; x < W; x++) {
      const binF  = x * (nBins - 1) / (W - 1);
      const bin0  = Math.floor(binF);
      const bin1  = Math.min(bin0 + 1, nBins - 1);
      const frac  = binF - bin0;
      const v     = values[bin0] * (1 - frac) + values[bin1] * frac;
      const y     = H - v * H;
      if (x === 0) ctx.lineTo(0, y);
      else         ctx.lineTo(x, y);
    }

    ctx.lineTo(W, H);
    ctx.closePath();

    // Gradient fill
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0,   'rgba(59,130,246,0.85)');
    grad.addColorStop(0.6, 'rgba(59,130,246,0.35)');
    grad.addColorStop(1,   'rgba(59,130,246,0.05)');
    ctx.fillStyle = grad;
    ctx.fill();

    // Bright top line
    ctx.beginPath();
    ctx.strokeStyle = '#60a5fa';
    ctx.lineWidth   = 1.5;

    for (let x = 0; x < W; x++) {
      const binF = x * (nBins - 1) / (W - 1);
      const bin0 = Math.floor(binF);
      const bin1 = Math.min(bin0 + 1, nBins - 1);
      const frac = binF - bin0;
      const v    = values[bin0] * (1 - frac) + values[bin1] * frac;
      const y    = H - v * H;
      if (x === 0) ctx.moveTo(x, y);
      else         ctx.lineTo(x, y);
    }
    ctx.stroke();

    // ── Center frequency marker ────────────────────────────────────────────
    const cx = W / 2;
    ctx.strokeStyle = 'rgba(251,191,36,0.7)';
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, H);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /** Choose a "nice" grid step for a given range and target number of lines. */
  _niceStep(range, targetLines) {
    const raw  = range / targetLines;
    const mag  = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const nice = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10;
    return nice * mag;
  }
}
