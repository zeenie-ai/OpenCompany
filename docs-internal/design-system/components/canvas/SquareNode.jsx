import * as React from 'react';

/**
 * SquareNode — OpenCompany canvas node. 64px icon square with accent-tinted
 * 2px border + 135° gradient fill, status pip (top-left), gear button
 * (top-right), in/out connection handles, label below. Executing nodes
 * pulse a three-layer glow (requires tokens/animations.css).
 *
 * Trigger variant (`trigger`): no input handle, a lightning ⚡ badge, and a
 * continuous "listening/armed" breathing pulse while waiting for events
 * (status="listening"). The glow uses `pulseColor` — a theme-chosen high
 * contrast color, independent of the node's role fill — so it stays visible
 * on any theme background.
 */
const PIP_COLORS = {
  idle: 'var(--fg-faint)',
  executing: null, // glow color
  listening: null, // glow color
  waiting: 'var(--warning)',
  success: 'var(--success)',
  error: 'var(--destructive)',
};

export function SquareNode({
  icon,
  label,
  color = 'var(--dracula-cyan)',
  status = 'idle',
  selected = false,
  executing = false,
  trigger = false,
  pulseColor,
  showGear = true,
  showInput = true,
  showOutput = true,
  showToolOutput = false,
  size = 64,
  onClick,
  onGearClick,
  style,
}) {
  const [hover, setHover] = React.useState(false);
  // Glow/pulse color is theme-contrast (pulseColor), falling back to the
  // node's own role color. The fill/border always use `color`.
  const glow = pulseColor || color;
  const armed = status === 'listening';
  const isPulsing = executing || armed;
  const pipColor = (status === 'executing' || status === 'listening') ? glow : (PIP_COLORS[status] || PIP_COLORS.idle);
  const hasInput = showInput && !trigger; // triggers never take input
  const handleStyle = (pos) => ({
    position: 'absolute',
    width: 12,
    height: 12,
    borderRadius: '50%',
    zIndex: 20,
    ...pos,
  });
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        cursor: 'pointer',
        userSelect: 'none',
        width: size + 24,
        ...style,
      }}
    >
      <div
        className={executing ? 'opencompany-pulse' : armed ? 'opencompany-trigger-armed' : undefined}
        style={{
          '--node-pulse-color': glow,
          position: 'relative',
          width: size,
          height: size,
          borderRadius: 'var(--radius-node, 10px)',
          border: `2px solid ${selected ? color : `color-mix(in srgb, ${color} 60%, transparent)`}`,
          background: `linear-gradient(135deg, color-mix(in srgb, ${color} 18%, var(--surface-card)) 0%, var(--surface-card) 100%)`,
          boxShadow: selected
            ? `0 0 0 1px ${color}, 0 4px 14px color-mix(in srgb, ${color} 32%, transparent)`
            : hover
              ? `0 4px 14px color-mix(in srgb, ${color} 28%, transparent)`
              : `0 2px 8px color-mix(in srgb, ${color} 18%, transparent)`,
          transform: hover && !selected ? 'translateY(-1px)' : 'none',
          transition: 'transform 150ms ease, border-color 150ms ease, box-shadow 150ms ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: Math.round(size * 0.44),
          color: 'var(--fg-default)',
        }}
      >
        {icon}

        {/* status pip */}
        <span
          className={status === 'listening' ? 'opencompany-pip-pulse' : undefined}
          style={{
            position: 'absolute',
            top: -4,
            left: -4,
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: pipColor,
            boxShadow:
              status === 'executing' || status === 'listening' || status === 'success' || status === 'error'
                ? `0 0 6px color-mix(in srgb, ${pipColor} 70%, transparent)`
                : 'none',
            zIndex: 30,
          }}
        />

        {/* trigger lightning badge (bottom-left) */}
        {trigger ? (
          <span
            title="Trigger — starts the workflow"
            className={armed ? 'opencompany-bolt' : undefined}
            style={{
              position: 'absolute',
              bottom: -4,
              left: -4,
              width: 16,
              height: 16,
              borderRadius: 'var(--radius-sm, 4px)',
              background: 'var(--dracula-yellow, #f1fa8c)',
              border: '1px solid var(--surface-card)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 9,
              lineHeight: 1,
              color: '#1a1d21',
              zIndex: 30,
            }}
          >
            ⚡
          </span>
        ) : null}

        {/* gear button */}
        {showGear ? (
          <button
            type="button"
            title="Edit parameters"
            onClick={(e) => { e.stopPropagation(); onGearClick && onGearClick(e); }}
            style={{
              position: 'absolute',
              top: -8,
              right: -8,
              width: 20,
              height: 20,
              borderRadius: '50%',
              background: 'var(--surface-card)',
              border: '1px solid var(--border-default)',
              color: 'var(--fg-muted)',
              fontSize: 10,
              lineHeight: 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              zIndex: 30,
              padding: 0,
            }}
          >
            ⚙️
          </button>
        ) : null}

        {/* handles */}
        {hasInput ? (
          <span
            title="Input"
            style={{
              ...handleStyle({ left: -6, top: '50%', transform: 'translateY(-50%)' }),
              background: 'var(--bg-app)',
              border: '2px solid var(--fg-faint)',
            }}
          />
        ) : null}
        {showOutput ? (
          <span
            title="Output"
            style={{
              ...handleStyle({ right: -6, top: '50%', transform: 'translateY(-50%)' }),
              background: color,
              border: `2px solid ${color}`,
            }}
          />
        ) : null}
        {showToolOutput ? (
          <span
            title="Tool output"
            style={{
              ...handleStyle({ top: -6, left: '50%', transform: 'translateX(-50%)' }),
              background: color,
              border: `2px solid ${color}`,
            }}
          />
        ) : null}
      </div>

      {label ? (
        <div
          style={{
            marginTop: 6,
            fontFamily: 'var(--font-sans)',
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--fg-default)',
            textAlign: 'center',
            maxWidth: size + 40,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
}
