// OpenCompany UI kit — node canvas: dot grid, dashed edges, square nodes,
// rectangular AI Agent node. Click nodes to select; Start runs the flow.
const DS_CV = window.OpenCompanyDesignSystem_2559cf;

// Rectangular agent node (the larger card-style node from the product).
function AgentNode({ x, y, status, selected, executing, onClick, onGearClick }) {
  const color = 'var(--node-agent)';
  return (
    <div
      onClick={onClick}
      className={executing ? 'opencompany-pulse' : undefined}
      style={{
        '--node-pulse-color': color,
        position: 'absolute', left: x, top: y, width: 190,
        borderRadius: 12,
        border: `2px solid ${selected ? color : `color-mix(in srgb, ${color} 60%, transparent)`}`,
        background: `linear-gradient(135deg, color-mix(in srgb, ${color} 14%, var(--surface-card)) 0%, var(--surface-card) 100%)`,
        boxShadow: selected
          ? `0 0 0 1px ${color}, 0 4px 14px color-mix(in srgb, ${color} 32%, transparent)`
          : `0 2px 8px color-mix(in srgb, ${color} 18%, transparent)`,
        cursor: 'pointer', userSelect: 'none', zIndex: 10,
        padding: '14px 12px 10px', textAlign: 'center',
        transition: 'border-color 150ms ease, box-shadow 150ms ease',
      }}
    >
      <span style={{ position: 'absolute', top: -4, left: -4, width: 10, height: 10, borderRadius: '50%', zIndex: 30, background: status === 'success' ? 'var(--success)' : status === 'executing' ? color : 'var(--fg-faint)', boxShadow: status !== 'idle' ? `0 0 6px color-mix(in srgb, ${status === 'executing' ? color : 'var(--success)'} 60%, transparent)` : 'none' }}></span>
      <button type="button" title="Edit parameters" onClick={(e) => { e.stopPropagation(); onGearClick && onGearClick(); }} style={{ position: 'absolute', top: -8, right: -8, width: 20, height: 20, borderRadius: '50%', background: 'var(--surface-card)', border: '1px solid var(--border-default)', fontSize: 10, cursor: 'pointer', zIndex: 30, padding: 0 }}>⚙️</button>
      <div style={{ fontSize: 26, lineHeight: 1 }}>🤖</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-default)', marginTop: 6 }}>AI Agent</div>
      <div style={{ fontSize: 11, color: color, marginTop: 2 }}>LangGraph Agent</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 10.5, color: 'var(--fg-muted)' }}>
        <span>Memory</span><span>Tool</span>
      </div>
      {/* handles: in (left), out (right), memory + tool (bottom) */}
      <span style={{ position: 'absolute', left: -6, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, borderRadius: '50%', background: 'var(--bg-app)', border: '2px solid var(--fg-faint)', zIndex: 20 }}></span>
      <span style={{ position: 'absolute', right: -6, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, borderRadius: '50%', background: color, border: `2px solid ${color}`, zIndex: 20 }}></span>
      <span style={{ position: 'absolute', left: 28, bottom: -6, width: 11, height: 11, borderRadius: 3, transform: 'rotate(45deg)', background: 'var(--bg-app)', border: '2px solid var(--fg-faint)', zIndex: 20 }}></span>
      <span style={{ position: 'absolute', right: 28, bottom: -6, width: 11, height: 11, borderRadius: 3, transform: 'rotate(45deg)', background: 'var(--bg-app)', border: '2px solid var(--fg-faint)', zIndex: 20 }}></span>
    </div>
  );
}

const BASE_NODES = [
  { id: 'receive', type: 'square', icon: '💬', label: 'WhatsApp Receive', color: 'var(--node-trigger)', x: 70, y: 210, showInput: false, trigger: true },
  { id: 'agent', type: 'agent', x: 330, y: 150 },
  { id: 'send', type: 'square', icon: '📤', label: 'WhatsApp Send', color: 'var(--node-trigger)', x: 670, y: 210, showOutput: false },
  { id: 'memory', type: 'square', icon: '🧠', label: 'Simple Memory', color: 'var(--node-agent)', x: 290, y: 400, showInput: false, showToolOutput: true },
  { id: 'toolkit', type: 'square', icon: '📱', label: 'Android Toolkit', color: 'var(--node-tool)', x: 480, y: 400, showInput: false, showToolOutput: true },
  { id: 'search', type: 'square', icon: '🔍', label: 'Web Search Tool', color: 'var(--node-model)', x: 130, y: 420, showInput: false, showToolOutput: true },
];

// Edge endpoints (hand-tuned against the node geometry above).
const EDGES = [
  { id: 'e1', from: [134, 242], to: [330, 215], color: 'var(--dracula-pink)' },
  { id: 'e2', from: [520, 215], to: [670, 242], color: 'var(--dracula-purple)' },
  { id: 'e3', from: [358, 286], to: [322, 400], color: 'var(--dracula-purple)' },
  { id: 'e4', from: [492, 286], to: [512, 400], color: 'var(--dracula-green)' },
  { id: 'e5', from: [358, 286], to: [162, 420], color: 'var(--dracula-cyan)' },
];

const RUN_ORDER = ['receive', 'agent', 'memory', 'search', 'toolkit', 'send'];

function CanvasView({ running, extraNodes, selectedId, onSelect, onConfigure }) {
  const { SquareNode } = DS_CV;
  const [step, setStep] = React.useState(-1);

  React.useEffect(() => {
    if (!running) { setStep(-1); return; }
    setStep(0);
    const id = window.setInterval(() => {
      setStep((s) => (s + 1) % (RUN_ORDER.length + 2));
    }, 900);
    return () => window.clearInterval(id);
  }, [running]);

  const statusOf = (nodeId) => {
    if (!running || step < 0) return 'idle';
    const idx = RUN_ORDER.indexOf(nodeId);
    if (idx === -1) return 'idle';
    if (idx === step) return 'executing';
    if (idx < step) return 'success';
    return 'idle';
  };

  return (
    <div style={{
      position: 'relative', flex: 1, overflow: 'hidden', background: 'var(--bg-canvas)',
      backgroundImage: 'radial-gradient(color-mix(in srgb, var(--fg-muted) 30%, transparent) 1px, transparent 1px)',
      backgroundSize: '20px 20px',
    }}>
      <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
        {EDGES.map((e) => {
          const [x1, y1] = e.from;
          const [x2, y2] = e.to;
          const mx = (x1 + x2) / 2;
          return (
            <path
              key={e.id}
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke={e.color}
              strokeWidth="2"
              strokeDasharray="6 6"
              opacity="0.8"
            />
          );
        })}
      </svg>

      {BASE_NODES.map((n) =>
        n.type === 'agent' ? (
          <AgentNode
            key={n.id} x={n.x} y={n.y}
            status={statusOf(n.id)}
            executing={statusOf(n.id) === 'executing'}
            selected={selectedId === n.id}
            onClick={() => onSelect(n.id)}
            onGearClick={() => onConfigure && onConfigure({ id: n.id, icon: '🤖', label: 'AI Agent' })}
          />
        ) : (
          <div key={n.id} style={{ position: 'absolute', left: n.x, top: n.y, zIndex: 10 }}>
            <SquareNode
              icon={n.icon} label={n.label} color={n.color}
              status={n.trigger && statusOf(n.id) === 'idle' ? 'listening' : statusOf(n.id)}
              executing={statusOf(n.id) === 'executing'}
              trigger={!!n.trigger}
              pulseColor={n.trigger ? 'var(--node-trigger)' : undefined}
              selected={selectedId === n.id}
              showInput={n.showInput !== false}
              showOutput={n.showOutput !== false}
              showToolOutput={!!n.showToolOutput}
              onClick={() => onSelect(n.id)}
              onGearClick={() => onConfigure && onConfigure({ id: n.id, icon: n.icon, label: n.label })}
            />
          </div>
        )
      )}

      {extraNodes.map((n) => (
        <div key={n.id} style={{ position: 'absolute', left: n.x, top: n.y, zIndex: 10 }}>
          <SquareNode
            icon={n.icon} label={n.label} color={n.color}
            selected={selectedId === n.id}
            onClick={() => onSelect(n.id)}
            onGearClick={() => onConfigure && onConfigure({ id: n.id, icon: n.icon, label: n.label })}
          />
        </div>
      ))}
    </div>
  );
}

window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, { CanvasView });
