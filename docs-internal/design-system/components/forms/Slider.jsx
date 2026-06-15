import * as React from 'react';

/**
 * Slider — settings-panel range control: 6px track, accent fill,
 * 16px draggable thumb. Used with a % readout in the row label.
 */
export function Slider({ value, defaultValue = 50, min = 0, max = 100, step = 1, onChange, color = 'var(--primary)', disabled = false, style }) {
  const [internal, setInternal] = React.useState(defaultValue);
  const cur = value !== undefined ? value : internal;
  const trackRef = React.useRef(null);
  const draggingRef = React.useRef(false);
  const pct = ((cur - min) / (max - min)) * 100;

  const setFromClientX = React.useCallback((clientX) => {
    const el = trackRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    let p = (clientX - r.left) / r.width;
    p = Math.max(0, Math.min(1, p));
    let v = min + p * (max - min);
    v = Math.round(v / step) * step;
    v = Math.max(min, Math.min(max, v));
    if (value === undefined) setInternal(v);
    onChange && onChange(v);
  }, [min, max, step, value, onChange]);

  React.useEffect(() => {
    const move = (e) => { if (draggingRef.current) setFromClientX(e.clientX); };
    const up = () => { draggingRef.current = false; };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  }, [setFromClientX]);

  return (
    <div
      ref={trackRef}
      role="slider"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={cur}
      onPointerDown={(e) => {
        if (disabled) return;
        draggingRef.current = true;
        setFromClientX(e.clientX);
      }}
      style={{
        position: 'relative', height: 20, display: 'flex', alignItems: 'center',
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
        touchAction: 'none', userSelect: 'none', width: '100%',
        ...style,
      }}
    >
      <div style={{ position: 'absolute', left: 0, right: 0, height: 6, borderRadius: 'var(--radius-pill, 999px)', background: 'var(--bg-hover)', border: '1px solid var(--border-default)' }} />
      <div style={{ position: 'absolute', left: 0, width: pct + '%', height: 6, borderRadius: 'var(--radius-pill, 999px)', background: color }} />
      <div style={{
        position: 'absolute', left: `calc(${pct}% - 8px)`,
        width: 16, height: 16, borderRadius: '50%',
        background: 'var(--bg-elevated)',
        border: `2px solid ${color}`,
        boxShadow: 'var(--shadow-card)',
      }} />
    </div>
  );
}
