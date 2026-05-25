/**
 * app.js — Application bootstrap and main render loop.
 *
 * Wires together:
 *   SpectrumWSClient → receives binary frames from the server
 *   WaterfallRenderer → scrolling colour waterfall
 *   SpectrumRenderer  → power-vs-frequency curve
 *   ControlPanel      → frequency / mode / span UI
 *
 * Also manages canvas resizing and the frequency axis tick labels.
 */

import { SpectrumWSClient } from './ws_client.js';
import { WaterfallRenderer }  from './waterfall.js';
import { SpectrumRenderer }   from './spectrum.js';
import { ControlPanel, formatHz } from './controls.js';
import { CanvasInteraction }  from './interaction.js';

// ── Configuration ─────────────────────────────────────────────────────────────

const WS_URL  = `ws://${location.host}/ws/spectrum`;
const API_URL = `${location.protocol}//${location.host}`;

// ── Elements ──────────────────────────────────────────────────────────────────

const waterfallCanvas  = document.getElementById('waterfall-canvas');
const spectrumCanvas   = document.getElementById('spectrum-canvas');
const freqAxisTop      = document.getElementById('freq-axis-top');
const freqAxisBottom   = document.getElementById('freq-axis-bottom');
const spectrumWrap     = document.getElementById('spectrum-wrap');
const waterfallWrap    = document.getElementById('waterfall-wrap');

// ── Renderers ─────────────────────────────────────────────────────────────────

const waterfall = new WaterfallRenderer(waterfallCanvas);
const spectrum  = new SpectrumRenderer(spectrumCanvas, -120, -20);

// ── State ─────────────────────────────────────────────────────────────────────

let centerHz = 145_800_000;
let spanHz   = 500_000;
let dbMin    = -120;
let dbMax    = -20;

// FPS tracking
let frameCount = 0;
let lastFpsTime = performance.now();

// ── Control panel ─────────────────────────────────────────────────────────────

const controls = new ControlPanel({
  apiBase: API_URL,
  onFrequencyChange(hz) { centerHz = hz; updateFreqAxis(); },
  onSpanChange(hz)      { spanHz   = hz; updateFreqAxis(); },
  onRangeChange({ dbMin: mn, dbMax: mx }) {
    dbMin = mn;
    dbMax = mx;
    spectrum.setRange(mn, mx);
    ws.setRange(mn, mx);
  },
});

// ── Canvas mouse interaction ──────────────────────────────────────────────────

// eslint-disable-next-line no-unused-vars
const interaction = new CanvasInteraction({
  spectrumWrap,
  waterfallWrap,
  getCenter: () => centerHz,
  getSpan:   () => spanHz,

  onTune(hz) {
    controls.tuneToHz(hz);
  },

  onRangeSelect(lo, hi) {
    const newCenter = Math.round((lo + hi) / 2);
    const newSpan   = Math.round(hi - lo);
    controls.tuneToHz(newCenter);
    controls.setSpanHz(newSpan);
  },
});

// ── WebSocket client ──────────────────────────────────────────────────────────

const ws = new SpectrumWSClient(
  WS_URL,
  onFrame,
  (state) => controls.setStatus(state),
);

function onFrame(frame) {
  // Update shared state from the server's authoritative values
  centerHz = frame.centerHz;
  spanHz   = frame.spanHz;

  // Render
  waterfall.addRow(frame.values);
  spectrum.draw(frame.values, frame.centerHz, frame.spanHz);

  updateFreqAxis();

  // FPS counter
  frameCount++;
  const now = performance.now();
  if (now - lastFpsTime >= 1000) {
    controls.setFps(frameCount * 1000 / (now - lastFpsTime));
    frameCount = 0;
    lastFpsTime = now;
  }
}

// ── Frequency axis labels ──────────────────────────────────────────────────────

function updateFreqAxis() {
  _renderAxis(freqAxisTop);
  _renderAxis(freqAxisBottom);
}

function _renderAxis(container) {
  container.innerHTML = '';
  const W      = container.offsetWidth || 800;
  const nTicks = Math.floor(W / 80);   // ~80 px per label
  for (let i = 0; i <= nTicks; i++) {
    const frac = i / nTicks;
    const hz   = (centerHz - spanHz / 2) + frac * spanHz;
    const tick = document.createElement('span');
    tick.className = 'freq-tick';
    tick.style.left = `${frac * 100}%`;
    tick.textContent = formatHz(hz);
    container.appendChild(tick);
  }
}

// ── Canvas resize ─────────────────────────────────────────────────────────────

function resize() {
  const wW = waterfallCanvas.parentElement.clientWidth;
  const wH = waterfallCanvas.parentElement.clientHeight;
  waterfall.resize(wW, wH);

  const sW = spectrumCanvas.parentElement.clientWidth;
  const sH = spectrumCanvas.parentElement.clientHeight;
  spectrum.resize(sW, sH);

  updateFreqAxis();
}

window.addEventListener('resize', resize);

// ── Status polling (signal level + connection check) ──────────────────────────

async function pollStatus() {
  try {
    const res = await fetch(`${API_URL}/api/status`);
    if (!res.ok) return;
    const data = await res.json();
    controls.setFrequencyDisplay(data.frequency_hz);
    controls.setSignalLevel(data.signal_dbm);

    // Sync mode selector
    const modeEl = document.getElementById('mode-select');
    if (modeEl) modeEl.value = String(data.mode);
  } catch (_) {
    // Not connected — status will show via WS status callback
  }
}

setInterval(pollStatus, 5000);

// ── Boot ──────────────────────────────────────────────────────────────────────

resize();
pollStatus();
ws.connect();
