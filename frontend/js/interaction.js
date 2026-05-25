/**
 * interaction.js — Mouse interaction on the spectrum and waterfall canvases.
 *
 * Features
 * --------
 *   Click          → tune receiver to that frequency
 *   Click + drag   → zoom into selected frequency range
 *                    (sets center = midpoint, span = selected width)
 *
 * A crosshair cursor line and frequency tooltip track the mouse on both
 * panels simultaneously.  A shaded selection band appears on both panels
 * while dragging.
 *
 * Usage
 * -----
 *   const ix = new CanvasInteraction({
 *     spectrumWrap,
 *     waterfallWrap,
 *     onTune(hz)               { ... },
 *     onRangeSelect(lo, hi)    { ... },
 *     getCenter()              { return centerHz; },
 *     getSpan()                { return spanHz;   },
 *   });
 */

import { formatHz } from './controls.js';

export class CanvasInteraction {
  /**
   * @param {Object}   opts
   * @param {Element}  opts.spectrumWrap   - #spectrum-wrap element
   * @param {Element}  opts.waterfallWrap  - #waterfall-wrap element
   * @param {Function} opts.onTune         - called with (hz: number)
   * @param {Function} opts.onRangeSelect  - called with (loHz: number, hiHz: number)
   * @param {Function} opts.getCenter      - returns current center Hz
   * @param {Function} opts.getSpan        - returns current span Hz
   */
  constructor({ spectrumWrap, waterfallWrap, onTune, onRangeSelect, getCenter, getSpan }) {
    this._wraps         = [spectrumWrap, waterfallWrap];
    this._onTune        = onTune;
    this._onRangeSelect = onRangeSelect;
    this._getCenter     = getCenter;
    this._getSpan       = getSpan;

    // Active drag state
    this._drag = null;   // { startHz: number, activeWrap: Element } | null

    // Per-wrap overlay element references
    this._ov = new Map();
    for (const wrap of this._wraps) {
      this._ov.set(wrap, {
        cursor:    wrap.querySelector('.freq-cursor'),
        selection: wrap.querySelector('.freq-selection'),
        tooltip:   wrap.querySelector('.freq-tooltip'),
      });
    }

    this._bind();
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  /** Convert a clientX pixel coordinate to Hz using the given wrap's bounding rect. */
  _pxToHz(clientX, wrap) {
    const { left, width } = wrap.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - left) / width));
    return this._getCenter() - this._getSpan() / 2 + frac * this._getSpan();
  }

  /** Convert Hz to a CSS percentage within the current view. */
  _hzToPct(hz) {
    return ((hz - (this._getCenter() - this._getSpan() / 2)) / this._getSpan()) * 100;
  }

  /** Show/update the cursor line and tooltip on every panel. */
  _showCursor(hz) {
    const pct = this._hzToPct(hz);
    for (const wrap of this._wraps) {
      const { cursor, tooltip } = this._ov.get(wrap);
      if (cursor) {
        cursor.style.left    = `${pct}%`;
        cursor.style.display = 'block';
      }
      if (tooltip) {
        // Keep tooltip inside view: clamp between 5 % and 85 % so text
        // doesn't overflow the left or right edge.
        const clampedPct = Math.max(5, Math.min(85, pct));
        tooltip.style.left    = `${clampedPct}%`;
        tooltip.textContent   = formatHz(hz);
        tooltip.style.display = 'block';
      }
    }
  }

  /** Hide cursor lines and tooltips on every panel. */
  _hideCursor() {
    for (const wrap of this._wraps) {
      const { cursor, tooltip } = this._ov.get(wrap);
      if (cursor)  cursor.style.display  = 'none';
      if (tooltip) tooltip.style.display = 'none';
    }
  }

  /** Update (or create) the selection band on every panel. */
  _showSelection(startHz, endHz) {
    const startPct = this._hzToPct(startHz);
    const endPct   = this._hzToPct(endHz);
    const left  = Math.min(startPct, endPct);
    const width = Math.abs(endPct - startPct);
    for (const wrap of this._wraps) {
      const { selection } = this._ov.get(wrap);
      if (selection) {
        selection.style.left    = `${left}%`;
        selection.style.width   = `${width}%`;
        selection.style.display = 'block';
      }
    }
  }

  /** Hide selection bands on every panel. */
  _hideSelection() {
    for (const wrap of this._wraps) {
      const { selection } = this._ov.get(wrap);
      if (selection) selection.style.display = 'none';
    }
  }

  // ── Event binding ─────────────────────────────────────────────────────────

  _bind() {
    for (const wrap of this._wraps) {
      wrap.addEventListener('mousemove',  e => this._onMove(e, wrap));
      wrap.addEventListener('mouseleave', ()  => this._onLeave());
      wrap.addEventListener('mousedown',  e => this._onDown(e, wrap));
    }
    // Track drag globally so the mouse can leave the canvas while dragging.
    document.addEventListener('mousemove', e => this._onDocMove(e));
    document.addEventListener('mouseup',   e => this._onUp(e));
  }

  // ── Handlers ──────────────────────────────────────────────────────────────

  _onMove(e, wrap) {
    if (this._drag) return;   // handled by _onDocMove during drag
    this._showCursor(this._pxToHz(e.clientX, wrap));
  }

  _onLeave() {
    if (!this._drag) {
      this._hideCursor();
      this._hideSelection();
    }
  }

  _onDown(e, wrap) {
    if (e.button !== 0) return;
    e.preventDefault();
    this._drag = {
      startHz:    this._pxToHz(e.clientX, wrap),
      activeWrap: wrap,
    };
  }

  _onDocMove(e) {
    if (!this._drag) return;
    const hz = this._pxToHz(e.clientX, this._drag.activeWrap);
    this._showCursor(hz);
    // Only show selection band once the drag exceeds 1 % of span
    if (Math.abs(hz - this._drag.startHz) > this._getSpan() * 0.01) {
      this._showSelection(this._drag.startHz, hz);
    }
  }

  _onUp(e) {
    if (!this._drag) return;

    const endHz   = this._pxToHz(e.clientX, this._drag.activeWrap);
    const deltaHz = Math.abs(endHz - this._drag.startHz);

    if (deltaHz > this._getSpan() * 0.01) {
      // Meaningful drag → zoom into selected range
      const lo = Math.min(this._drag.startHz, endHz);
      const hi = Math.max(this._drag.startHz, endHz);
      if (this._onRangeSelect) this._onRangeSelect(lo, hi);
    } else {
      // Tiny movement → treat as click-to-tune
      if (this._onTune) this._onTune(Math.round(endHz));
    }

    this._hideSelection();
    this._drag = null;
  }
}
