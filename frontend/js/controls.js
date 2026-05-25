/**
 * controls.js — UI control panel logic.
 *
 * Handles all user interactions (frequency entry, mode/span/attenuator selects,
 * dB range sliders) and calls the REST API to apply changes to the receiver.
 */

/** Format a frequency in Hz as a human-readable MHz string (e.g. "145.8000"). */
export function hzToMhz(hz) {
  return (hz / 1e6).toFixed(4);
}

/** Parse a MHz string to integer Hz; returns null on failure. */
export function mhzToHz(str) {
  const v = parseFloat(str.replace(',', '.'));
  if (!isFinite(v) || v <= 0) return null;
  return Math.round(v * 1e6);
}

/** Format Hz for display in the frequency axis (short labels). */
export function formatHz(hz) {
  if (hz >= 1e9)  return `${(hz / 1e9).toFixed(3)} GHz`;
  if (hz >= 1e6)  return `${(hz / 1e6).toFixed(3)} MHz`;
  if (hz >= 1e3)  return `${(hz / 1e3).toFixed(1)} kHz`;
  return `${hz} Hz`;
}


export class ControlPanel {
  /**
   * @param {Object} opts
   * @param {string}   opts.apiBase         REST API base URL, e.g. 'http://host:8080'
   * @param {Function} opts.onFrequencyChange   Called with Hz when user tunes
   * @param {Function} opts.onSpanChange         Called with spanHz
   * @param {Function} opts.onRangeChange        Called with {dbMin, dbMax}
   */
  constructor({ apiBase, onFrequencyChange, onSpanChange, onRangeChange }) {
    this._api    = apiBase;
    this._onFreq = onFrequencyChange;
    this._onSpan = onSpanChange;
    this._onRange = onRangeChange;

    this._currentHz = 145_800_000;
    this._currentStep = 10_000;

    this._bindElements();
  }

  // ── Public API ────────────────────────────────────────────────────────────

  /** Update the frequency display without sending to device. */
  setFrequencyDisplay(hz) {
    this._currentHz = hz;
    document.getElementById('freq-input').value = hzToMhz(hz);
  }

  /** Update signal level badge. */
  setSignalLevel(dbm) {
    const el = document.getElementById('signal-value');
    if (el) el.textContent = dbm === null ? '--' : dbm.toFixed(1);
  }

  /** Update the connection status indicator. */
  setStatus(state) {
    // state: 'connecting' | 'connected' | 'disconnected'
    const ind  = document.getElementById('status-indicator');
    const text = document.getElementById('status-text');
    if (!ind || !text) return;
    ind.className = `status-${state}`;
    text.textContent = { connecting: 'Connecting…', connected: 'Connected', disconnected: 'Disconnected' }[state] || state;
  }

  /** Update FPS counter. */
  setFps(fps) {
    const el = document.getElementById('fps-counter');
    if (el) el.textContent = `${fps.toFixed(1)}`;
  }

  // ── Binding ───────────────────────────────────────────────────────────────

  _bindElements() {
    // Frequency input — commit on Enter or blur
    const freqEl = document.getElementById('freq-input');
    freqEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { freqEl.blur(); this._commitFrequency(); }
      if (e.key === 'Escape') { freqEl.value = hzToMhz(this._currentHz); freqEl.blur(); }
    });
    freqEl.addEventListener('blur', () => this._commitFrequency());

    // Arrow buttons
    document.getElementById('freq-up')
      .addEventListener('click', () => this._nudgeFrequency(+1));
    document.getElementById('freq-down')
      .addEventListener('click', () => this._nudgeFrequency(-1));

    // Step selector
    document.getElementById('freq-step')
      .addEventListener('change', (e) => {
        this._currentStep = parseInt(e.target.value, 10);
      });

    // Mode selector
    document.getElementById('mode-select')
      .addEventListener('change', (e) => {
        this._post('/api/mode', { mode: parseInt(e.target.value, 10) });
      });

    // Span selector
    document.getElementById('span-select')
      .addEventListener('change', (e) => {
        const spanHz    = parseInt(e.target.value, 10);
        const centerHz  = this._currentHz;
        this._post('/api/spectrum', { center_hz: centerHz, span_hz: spanHz })
          .then(() => this._onSpan && this._onSpan(spanHz));
      });

    // Attenuator
    document.getElementById('att-select')
      .addEventListener('change', (e) => {
        this._post('/api/attenuator', { level: parseInt(e.target.value, 10) });
      });

    // dB range sliders
    const minSlider  = document.getElementById('db-min-slider');
    const maxSlider  = document.getElementById('db-max-slider');
    const minLabel   = document.getElementById('db-min-label');
    const maxLabel   = document.getElementById('db-max-label');

    const updateRange = () => {
      let dbMin = parseInt(minSlider.value, 10);
      let dbMax = parseInt(maxSlider.value, 10);
      if (dbMax <= dbMin + 10) dbMax = dbMin + 10;
      maxSlider.value = dbMax;
      minLabel.textContent = dbMin;
      maxLabel.textContent = dbMax;
      this._onRange && this._onRange({ dbMin, dbMax });
    };

    minSlider.addEventListener('input', updateRange);
    maxSlider.addEventListener('input', updateRange);

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (document.activeElement === freqEl) return;
      if (e.key === 'ArrowUp')   { e.preventDefault(); this._nudgeFrequency(+1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); this._nudgeFrequency(-1); }
    });
  }

  _commitFrequency() {
    const el = document.getElementById('freq-input');
    const hz = mhzToHz(el.value);
    if (hz === null) {
      el.value = hzToMhz(this._currentHz);   // restore
      return;
    }
    el.value = hzToMhz(hz);
    if (hz === this._currentHz) return;
    this._currentHz = hz;

    this._post('/api/frequency', { hz })
      .then(() => {
        // Also update spectrum center to track the tuned frequency
        const spanEl = document.getElementById('span-select');
        const spanHz = parseInt(spanEl.value, 10);
        return this._post('/api/spectrum', { center_hz: hz, span_hz: spanHz });
      })
      .then(() => this._onFreq && this._onFreq(hz))
      .catch((e) => console.error('Frequency set failed:', e));
  }

  _nudgeFrequency(direction) {
    const hz = this._currentHz + direction * this._currentStep;
    document.getElementById('freq-input').value = hzToMhz(hz);
    this._currentHz = hz;

    this._post('/api/frequency', { hz })
      .then(() => {
        const spanEl = document.getElementById('span-select');
        const spanHz = parseInt(spanEl.value, 10);
        return this._post('/api/spectrum', { center_hz: hz, span_hz: spanHz });
      })
      .then(() => this._onFreq && this._onFreq(hz))
      .catch((e) => console.error('Nudge failed:', e));
  }

  async _post(path, body) {
    const res = await fetch(`${this._api}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${path} → ${res.status}: ${text}`);
    }
    return res.json();
  }
}
