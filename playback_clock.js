'use strict';
// Presentation time is logarithmic in the original stroke timeline. Rendering
// always receives original time, preserving the sequential stroke geometry.
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PaintingClock = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  function createClock(rawDuration, overviewMs = 240000, curve = 99) {
    const raw = Number(rawDuration), overview = Number(overviewMs), a = Number(curve);
    if (!Number.isFinite(raw) || raw <= 0) throw new RangeError('rawDuration must be positive');
    if (!Number.isFinite(overview) || overview <= 0) throw new RangeError('overviewMs must be positive');
    if (!Number.isFinite(a) || a < 0) throw new RangeError('curve must be non-negative');
    const clamp = (value, upper) => Math.max(0, Math.min(upper, Number(value) || 0));
    const logarithm = Math.log1p(a);
    function duration(mode = 'log') { return mode === 'real' ? raw : overview; }
    function rawToPresentation(value, mode = 'log') {
      const s = clamp(value, raw);
      if (mode === 'real') return s;
      if (s === 0) return 0;
      if (s === raw) return overview;
      return overview * (a === 0 ? s / raw : Math.log1p(a * s / raw) / logarithm);
    }
    function presentationToRaw(value, mode = 'log') {
      const t = clamp(value, duration(mode));
      if (mode === 'real') return t;
      if (t === 0) return 0;
      if (t === overview) return raw;
      return raw * (a === 0 ? t / overview : Math.expm1(logarithm * t / overview) / a);
    }
    function advance(value, delta, mode = 'log', speed = 1) {
      return presentationToRaw(rawToPresentation(value, mode) + Math.max(0, Number(delta) || 0) * Math.max(0, Number(speed) || 0), mode);
    }
    return { rawDuration: raw, overviewMs: overview, curve: a, duration, rawToPresentation, presentationToRaw, advance };
  }
  return { createClock };
});
