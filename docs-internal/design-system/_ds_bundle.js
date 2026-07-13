/* @ds-bundle: {"format":3,"namespace":"OpenCompanyDesignSystem_2559cf","components":[{"name":"ActionButton","sourcePath":"components/buttons/ActionButton.jsx"},{"name":"Button","sourcePath":"components/buttons/Button.jsx"},{"name":"ComponentItem","sourcePath":"components/canvas/ComponentItem.jsx"},{"name":"ModeToggle","sourcePath":"components/canvas/ModeToggle.jsx"},{"name":"SquareNode","sourcePath":"components/canvas/SquareNode.jsx"},{"name":"StatusBar","sourcePath":"components/canvas/StatusBar.jsx"},{"name":"WorkflowCard","sourcePath":"components/canvas/WorkflowCard.jsx"},{"name":"Avatar","sourcePath":"components/display/Avatar.jsx"},{"name":"Badge","sourcePath":"components/display/Badge.jsx"},{"name":"Card","sourcePath":"components/display/Card.jsx"},{"name":"ChatBubble","sourcePath":"components/display/ChatBubble.jsx"},{"name":"Kbd","sourcePath":"components/display/Kbd.jsx"},{"name":"LogLine","sourcePath":"components/display/LogLine.jsx"},{"name":"Tabs","sourcePath":"components/display/Tabs.jsx"},{"name":"EmptyState","sourcePath":"components/feedback/EmptyState.jsx"},{"name":"Modal","sourcePath":"components/feedback/Modal.jsx"},{"name":"Progress","sourcePath":"components/feedback/Progress.jsx"},{"name":"Spinner","sourcePath":"components/feedback/Spinner.jsx"},{"name":"Toast","sourcePath":"components/feedback/Toast.jsx"},{"name":"Tooltip","sourcePath":"components/feedback/Tooltip.jsx"},{"name":"ApiKeyInput","sourcePath":"components/forms/ApiKeyInput.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"RadioGroup","sourcePath":"components/forms/RadioGroup.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"Slider","sourcePath":"components/forms/Slider.jsx"},{"name":"Switch","sourcePath":"components/forms/Switch.jsx"},{"name":"Textarea","sourcePath":"components/forms/Textarea.jsx"},{"name":"Icon","sourcePath":"components/icons/Icon.jsx"},{"name":"CollapsibleSection","sourcePath":"components/panels/CollapsibleSection.jsx"},{"name":"DataCard","sourcePath":"components/panels/DataCard.jsx"},{"name":"PanelModal","sourcePath":"components/panels/PanelModal.jsx"},{"name":"SettingsSection","sourcePath":"components/panels/SettingsSection.jsx"},{"name":"SettingsRow","sourcePath":"components/panels/SettingsSection.jsx"}],"sourceHashes":{"components/buttons/ActionButton.jsx":"3de567509700","components/buttons/Button.jsx":"c81ef2c80131","components/canvas/ComponentItem.jsx":"fed2f287e532","components/canvas/ModeToggle.jsx":"2386b2142874","components/canvas/SquareNode.jsx":"5604401e105a","components/canvas/StatusBar.jsx":"f3ba959f8bac","components/canvas/WorkflowCard.jsx":"76dc0735c0e1","components/display/Avatar.jsx":"c636baa7014f","components/display/Badge.jsx":"1e9482639eb7","components/display/Card.jsx":"d60b5d2e81cb","components/display/ChatBubble.jsx":"97f3de1f9e95","components/display/Kbd.jsx":"0b1575a8f4ab","components/display/LogLine.jsx":"8b8dcbe178d2","components/display/Tabs.jsx":"29083e164683","components/feedback/EmptyState.jsx":"bcaf3094afb4","components/feedback/Modal.jsx":"b99b3f25e581","components/feedback/Progress.jsx":"1c91d593fe2b","components/feedback/Spinner.jsx":"47e22c9fed7a","components/feedback/Toast.jsx":"da490f96e375","components/feedback/Tooltip.jsx":"1181368448e9","components/forms/ApiKeyInput.jsx":"5ed6f076a952","components/forms/Checkbox.jsx":"a687ca85bef0","components/forms/Input.jsx":"955d8ddccdc7","components/forms/RadioGroup.jsx":"4ff6bd70406c","components/forms/Select.jsx":"79482ec6f9e9","components/forms/Slider.jsx":"4cd8ba4d0c99","components/forms/Switch.jsx":"8de73e29935c","components/forms/Textarea.jsx":"1d0b6f74e112","components/icons/Icon.jsx":"3fbda05c3dcd","components/panels/CollapsibleSection.jsx":"5ab48b069cd1","components/panels/DataCard.jsx":"644079601685","components/panels/PanelModal.jsx":"ca2f9656ca17","components/panels/SettingsSection.jsx":"568e16e1e05e","ui_kits/opencompany/App.jsx":"b5cca1c6f3c0","ui_kits/opencompany/CanvasView.jsx":"59f3b01513f3","ui_kits/opencompany/ConsoleDock.jsx":"0c284423b0b3","ui_kits/opencompany/Panels.jsx":"de31785649fa","ui_kits/opencompany/PanelsModals.jsx":"0d558a4e2995","ui_kits/opencompany/Toolbar.jsx":"6ba622604af7"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.OpenCompanyDesignSystem_2559cf = window.OpenCompanyDesignSystem_2559cf || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/buttons/ActionButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * ActionButton — the signature OpenCompany "soft tinted" toolbar button.
 * `intent` is a semantic role, not a color: run / stop / save / config /
 * secret / tools. Soft tint fill (15%), tinted border (60%), accent text;
 * hover deepens the fill to 25%; press nudges down 1px.
 */
function ActionButton({
  intent = 'save',
  children,
  disabled = false,
  iconOnly = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const base = `var(--action-${intent}`;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => {
      setHover(false);
      setActive(false);
    },
    onMouseDown: () => setActive(true),
    onMouseUp: () => setActive(false),
    style: {
      display: 'inline-flex',
      height: 'var(--h-control, 32px)',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      borderRadius: 'var(--radius-md, 6px)',
      padding: iconOnly ? 0 : '0 14px',
      width: iconOnly ? 'var(--h-control, 32px)' : undefined,
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 600,
      lineHeight: 1,
      border: `1px solid ${base}-border)`,
      background: hover && !disabled ? `${base}-hover)` : `${base}-soft)`,
      color: `${base}-ink)`,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      transform: active && !disabled ? 'translateY(1px)' : 'none',
      transition: 'background var(--dur-default, 180ms) var(--ease-default), transform var(--dur-fast, 90ms)',
      userSelect: 'none',
      whiteSpace: 'nowrap',
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { ActionButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/ActionButton.jsx", error: String((e && e.message) || e) }); }

// components/buttons/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Button — shadcn-derived general button. Solid primary, outline,
 * secondary, ghost, destructive (soft red tint), link.
 */
function Button({
  variant = 'default',
  size = 'default',
  children,
  disabled = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const sizes = {
    sm: {
      height: 28,
      padding: '0 10px',
      fontSize: 13
    },
    default: {
      height: 32,
      padding: '0 12px',
      fontSize: 14
    },
    lg: {
      height: 36,
      padding: '0 16px',
      fontSize: 14
    },
    icon: {
      height: 32,
      width: 32,
      padding: 0,
      fontSize: 14
    },
    'icon-sm': {
      height: 28,
      width: 28,
      padding: 0,
      fontSize: 13
    }
  };
  const variants = {
    default: {
      background: hover ? 'color-mix(in srgb, var(--primary) 85%, var(--bg-app))' : 'var(--primary)',
      color: 'var(--primary-foreground)',
      border: '1px solid transparent'
    },
    outline: {
      background: hover ? 'var(--bg-hover)' : 'var(--bg-elevated)',
      color: 'var(--fg-default)',
      border: '1px solid var(--border-default)'
    },
    secondary: {
      background: hover ? 'var(--bg-active)' : 'var(--bg-hover)',
      color: 'var(--fg-default)',
      border: '1px solid transparent'
    },
    ghost: {
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: 'var(--fg-default)',
      border: '1px solid transparent'
    },
    destructive: {
      background: hover ? 'color-mix(in srgb, var(--destructive) 20%, transparent)' : 'color-mix(in srgb, var(--destructive) 10%, transparent)',
      color: 'var(--destructive)',
      border: '1px solid transparent'
    },
    link: {
      background: 'transparent',
      color: 'var(--primary)',
      border: '1px solid transparent',
      textDecoration: hover ? 'underline' : 'none'
    }
  };
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => {
      setHover(false);
      setActive(false);
    },
    onMouseDown: () => setActive(true),
    onMouseUp: () => setActive(false),
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      borderRadius: 'var(--radius-lg, 8px)',
      fontFamily: 'var(--font-sans)',
      fontWeight: 500,
      lineHeight: 1,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      transform: active && !disabled ? 'translateY(1px)' : 'none',
      transition: 'background var(--dur-default, 180ms) var(--ease-default), transform var(--dur-fast, 90ms)',
      userSelect: 'none',
      whiteSpace: 'nowrap',
      ...sizes[size],
      ...variants[variant],
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/Button.jsx", error: String((e && e.message) || e) }); }

// components/canvas/ComponentItem.jsx
try { (() => {
/**
 * ComponentItem — draggable palette card: icon tile + name + description
 * + grip. Hover lifts -2px with a foreground ring.
 */
function ComponentItem({
  icon,
  name,
  description,
  onClick,
  style
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '8px 12px',
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      background: hover ? 'var(--bg-hover)' : 'var(--bg-app)',
      boxShadow: hover ? '0 0 0 2px color-mix(in srgb, var(--fg-default) 15%, transparent), var(--shadow-card-hover)' : 'none',
      transform: hover ? 'translateY(-2px)' : 'none',
      transition: 'all 150ms var(--ease-default)',
      cursor: 'grab',
      userSelect: 'none',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 36,
      height: 36,
      flexShrink: 0,
      borderRadius: 'var(--radius-md, 6px)',
      background: 'var(--bg-elevated)',
      boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--border-default) 40%, transparent)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: 18
    }
  }, icon || '📦'), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0,
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--fg-default)',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 11,
      lineHeight: 1.3,
      color: 'var(--fg-muted)',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, description)), /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--fg-faint)",
    strokeWidth: "2",
    style: {
      flexShrink: 0,
      opacity: hover ? 0.8 : 0.5,
      transition: 'opacity 150ms'
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "9",
    cy: "5",
    r: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "9",
    cy: "12",
    r: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "9",
    cy: "19",
    r: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "15",
    cy: "5",
    r: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "15",
    cy: "12",
    r: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "15",
    cy: "19",
    r: "1"
  })));
}
Object.assign(__ds_scope, { ComponentItem });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/canvas/ComponentItem.jsx", error: String((e && e.message) || e) }); }

// components/canvas/ModeToggle.jsx
try { (() => {
/**
 * ModeToggle — segmented Normal/Dev control from the top toolbar.
 * Active segment gets the role's soft tint (green=Normal, purple=Dev).
 */
function ModeToggle({
  mode = 'normal',
  onChange,
  style
}) {
  const seg = (id, label, color, dotPath) => {
    const isActive = mode === id;
    return /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => !isActive && onChange && onChange(id),
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        borderRadius: 'var(--radius-sm, 4px)',
        border: '1px solid ' + (isActive ? `color-mix(in srgb, ${color} 30%, transparent)` : 'transparent'),
        background: isActive ? `color-mix(in srgb, ${color} 8%, transparent)` : 'transparent',
        color: isActive ? color : 'var(--fg-muted)',
        fontFamily: 'var(--font-sans)',
        fontSize: 12,
        fontWeight: 600,
        padding: '4px 10px',
        cursor: isActive ? 'default' : 'pointer',
        transition: 'all var(--dur-default, 180ms) var(--ease-default)'
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "12",
      height: "12",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    }, dotPath), label);
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)',
      background: 'var(--surface-card)',
      padding: 2,
      gap: 2,
      ...style
    }
  }, seg('normal', 'Normal', 'var(--node-tool)', /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "10"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "12 6 12 12 16 14"
  }))), seg('dev', 'Dev', 'var(--node-agent)', /*#__PURE__*/React.createElement("polygon", {
    points: "13 2 3 14 12 14 11 22 21 10 12 10 13 2"
  })));
}
Object.assign(__ds_scope, { ModeToggle });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/canvas/ModeToggle.jsx", error: String((e && e.message) || e) }); }

// components/canvas/SquareNode.jsx
try { (() => {
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
  executing: null,
  // glow color
  listening: null,
  // glow color
  waiting: 'var(--warning)',
  success: 'var(--success)',
  error: 'var(--destructive)'
};
function SquareNode({
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
  style
}) {
  const [hover, setHover] = React.useState(false);
  // Glow/pulse color is theme-contrast (pulseColor), falling back to the
  // node's own role color. The fill/border always use `color`.
  const glow = pulseColor || color;
  const armed = status === 'listening';
  const isPulsing = executing || armed;
  const pipColor = status === 'executing' || status === 'listening' ? glow : PIP_COLORS[status] || PIP_COLORS.idle;
  const hasInput = showInput && !trigger; // triggers never take input
  const handleStyle = pos => ({
    position: 'absolute',
    width: 12,
    height: 12,
    borderRadius: '50%',
    zIndex: 20,
    ...pos
  });
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      position: 'relative',
      display: 'inline-flex',
      flexDirection: 'column',
      alignItems: 'center',
      cursor: 'pointer',
      userSelect: 'none',
      width: size + 24,
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: executing ? 'opencompany-pulse' : armed ? 'opencompany-trigger-armed' : undefined,
    style: {
      '--node-pulse-color': glow,
      position: 'relative',
      width: size,
      height: size,
      borderRadius: 'var(--radius-node, 10px)',
      border: `2px solid ${selected ? color : `color-mix(in srgb, ${color} 60%, transparent)`}`,
      background: `linear-gradient(135deg, color-mix(in srgb, ${color} 18%, var(--surface-card)) 0%, var(--surface-card) 100%)`,
      boxShadow: selected ? `0 0 0 1px ${color}, 0 4px 14px color-mix(in srgb, ${color} 32%, transparent)` : hover ? `0 4px 14px color-mix(in srgb, ${color} 28%, transparent)` : `0 2px 8px color-mix(in srgb, ${color} 18%, transparent)`,
      transform: hover && !selected ? 'translateY(-1px)' : 'none',
      transition: 'transform 150ms ease, border-color 150ms ease, box-shadow 150ms ease',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: Math.round(size * 0.44),
      color: 'var(--fg-default)'
    }
  }, icon, /*#__PURE__*/React.createElement("span", {
    className: status === 'listening' ? 'opencompany-pip-pulse' : undefined,
    style: {
      position: 'absolute',
      top: -4,
      left: -4,
      width: 10,
      height: 10,
      borderRadius: '50%',
      background: pipColor,
      boxShadow: status === 'executing' || status === 'listening' || status === 'success' || status === 'error' ? `0 0 6px color-mix(in srgb, ${pipColor} 70%, transparent)` : 'none',
      zIndex: 30
    }
  }), trigger ? /*#__PURE__*/React.createElement("span", {
    title: "Trigger \u2014 starts the workflow",
    className: armed ? 'opencompany-bolt' : undefined,
    style: {
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
      zIndex: 30
    }
  }, "\u26A1") : null, showGear ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    title: "Edit parameters",
    onClick: e => {
      e.stopPropagation();
      onGearClick && onGearClick(e);
    },
    style: {
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
      padding: 0
    }
  }, "\u2699\uFE0F") : null, hasInput ? /*#__PURE__*/React.createElement("span", {
    title: "Input",
    style: {
      ...handleStyle({
        left: -6,
        top: '50%',
        transform: 'translateY(-50%)'
      }),
      background: 'var(--bg-app)',
      border: '2px solid var(--fg-faint)'
    }
  }) : null, showOutput ? /*#__PURE__*/React.createElement("span", {
    title: "Output",
    style: {
      ...handleStyle({
        right: -6,
        top: '50%',
        transform: 'translateY(-50%)'
      }),
      background: color,
      border: `2px solid ${color}`
    }
  }) : null, showToolOutput ? /*#__PURE__*/React.createElement("span", {
    title: "Tool output",
    style: {
      ...handleStyle({
        top: -6,
        left: '50%',
        transform: 'translateX(-50%)'
      }),
      background: color,
      border: `2px solid ${color}`
    }
  }) : null), label ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      fontFamily: 'var(--font-sans)',
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--fg-default)',
      textAlign: 'center',
      maxWidth: size + 40,
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, label) : null);
}
Object.assign(__ds_scope, { SquareNode });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/canvas/SquareNode.jsx", error: String((e && e.message) || e) }); }

// components/canvas/StatusBar.jsx
try { (() => {
/**
 * StatusBar — 24px shell-prompt footer: connection pip, workflow name,
 * node count, theme, live clock. All mono, uppercase, 0.04em tracking.
 */
function StatusBar({
  connection = 'online',
  workflowName = '—',
  nodeCount = 0,
  themeName = 'DARK',
  clock,
  style
}) {
  const [time, setTime] = React.useState(() => new Date());
  React.useEffect(() => {
    if (clock !== undefined) return;
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, [clock]);
  const timeText = clock !== undefined ? clock : time.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
  const tones = {
    online: {
      color: 'var(--success)',
      label: 'ONLINE'
    },
    connecting: {
      color: 'var(--warning)',
      label: 'CONNECTING'
    },
    offline: {
      color: 'var(--destructive)',
      label: 'OFFLINE'
    }
  };
  const tone = tones[connection] || tones.online;
  const sep = /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.4
    }
  }, "|");
  return /*#__PURE__*/React.createElement("div", {
    role: "contentinfo",
    style: {
      display: 'flex',
      height: 'var(--h-statusbar, 24px)',
      alignItems: 'center',
      gap: 12,
      borderTop: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      padding: '0 14px',
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      letterSpacing: '0.04em',
      color: 'var(--fg-muted)',
      textTransform: 'uppercase',
      whiteSpace: 'nowrap',
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      fontWeight: 500,
      color: tone.color
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 6,
      height: 6,
      borderRadius: '50%',
      background: tone.color,
      display: 'inline-block',
      animation: connection === 'online' ? 'opencompany-pip-blink 2s ease-in-out infinite' : 'none'
    }
  }), tone.label), sep, /*#__PURE__*/React.createElement("span", null, "WF: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--fg-default)'
    }
  }, workflowName)), sep, /*#__PURE__*/React.createElement("span", null, "NODES: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--fg-default)'
    }
  }, nodeCount)), /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      alignItems: 'center',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("span", null, "THEME: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--fg-default)'
    }
  }, themeName)), sep, /*#__PURE__*/React.createElement("span", {
    style: {
      fontVariantNumeric: 'tabular-nums'
    }
  }, timeText)), /*#__PURE__*/React.createElement("style", null, '@keyframes opencompany-pip-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }'));
}
Object.assign(__ds_scope, { StatusBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/canvas/StatusBar.jsx", error: String((e && e.message) || e) }); }

// components/canvas/WorkflowCard.jsx
try { (() => {
/**
 * WorkflowCard — sidebar saved-workflow card. Selected gets a 3px accent
 * left edge; metadata row is mono.
 */
function WorkflowCard({
  name,
  nodeCount = 0,
  modified,
  selected = false,
  onClick,
  style
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      position: 'relative',
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid ' + (selected ? 'var(--accent)' : 'var(--border-default)'),
      borderLeftWidth: selected ? 3 : 1,
      background: selected ? 'var(--bg-active)' : hover ? 'var(--bg-hover)' : 'var(--bg-app)',
      padding: 12,
      cursor: 'pointer',
      transition: 'background var(--dur-default, 180ms) var(--ease-default)',
      userSelect: 'none',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginBottom: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 24,
      height: 24,
      flexShrink: 0,
      borderRadius: 'var(--radius-sm, 4px)',
      border: '1px solid ' + (selected ? 'var(--accent)' : 'var(--border-default)'),
      background: selected ? 'color-mix(in srgb, var(--accent) 20%, transparent)' : 'var(--bg-elevated)',
      color: selected ? 'var(--accent)' : 'var(--fg-muted)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "16 18 22 12 16 6"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "8 6 2 12 8 18"
  }))), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      fontWeight: 500,
      color: selected ? 'var(--accent)' : 'var(--fg-default)',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, name)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      color: selected ? 'var(--fg-default)' : 'var(--fg-muted)'
    }
  }, /*#__PURE__*/React.createElement("span", null, nodeCount, " nodes"), /*#__PURE__*/React.createElement("span", null, modified)));
}
Object.assign(__ds_scope, { WorkflowCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/canvas/WorkflowCard.jsx", error: String((e && e.message) || e) }); }

// components/display/Avatar.jsx
try { (() => {
/**
 * Avatar — initials in a soft accent tile. Used for providers/agents
 * in credential rows and chat.
 */
function Avatar({
  name = '',
  color = 'var(--accent)',
  size = 28,
  square = false,
  style
}) {
  const initials = String(name).split(/\s+/).map(w => w.charAt(0)).slice(0, 2).join('').toUpperCase();
  return /*#__PURE__*/React.createElement("span", {
    title: name,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      width: size,
      height: size,
      flexShrink: 0,
      borderRadius: square ? 'var(--radius-md, 6px)' : '50%',
      border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
      background: `color-mix(in srgb, ${color} 14%, transparent)`,
      color: color,
      fontFamily: 'var(--font-sans)',
      fontSize: Math.round(size * 0.38),
      fontWeight: 600,
      letterSpacing: '0.02em',
      userSelect: 'none',
      ...style
    }
  }, initials || '?');
}
Object.assign(__ds_scope, { Avatar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/Avatar.jsx", error: String((e && e.message) || e) }); }

// components/display/Badge.jsx
try { (() => {
/**
 * Badge — small count / label chip. Mono font for numeric counts.
 */
function Badge({
  variant = 'secondary',
  mono = false,
  color,
  children,
  style
}) {
  const variants = {
    default: {
      background: 'var(--primary)',
      color: 'var(--primary-foreground)',
      border: '1px solid transparent'
    },
    secondary: {
      background: 'var(--bg-hover)',
      color: 'var(--fg-muted)',
      border: '1px solid transparent'
    },
    outline: {
      background: 'transparent',
      color: 'var(--fg-muted)',
      border: '1px solid var(--border-default)'
    },
    accent: {
      background: `color-mix(in srgb, ${color || 'var(--accent)'} 12%, transparent)`,
      color: color || 'var(--accent)',
      border: `1px solid color-mix(in srgb, ${color || 'var(--accent)'} 30%, transparent)`
    }
  };
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      height: 18,
      borderRadius: 'var(--radius-md, 6px)',
      padding: '0 6px',
      fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
      fontSize: 11,
      fontWeight: 500,
      lineHeight: 1,
      whiteSpace: 'nowrap',
      ...variants[variant],
      ...style
    }
  }, children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/Badge.jsx", error: String((e && e.message) || e) }); }

// components/display/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Card — base surface. Optional hover lift (for clickable cards) and
 * selected state (3px accent left edge, like WorkflowSidebar cards).
 */
function Card({
  interactive = false,
  selected = false,
  children,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", _extends({
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      borderLeft: selected ? '3px solid var(--accent)' : '1px solid var(--border-default)',
      background: selected ? 'var(--bg-active)' : interactive && hover ? 'var(--bg-hover)' : 'var(--surface-card)',
      boxShadow: interactive && hover ? 'var(--shadow-card-hover)' : 'var(--shadow-card)',
      transform: interactive && hover ? 'translateY(-1px)' : 'none',
      transition: 'all var(--dur-default, 180ms) var(--ease-default)',
      cursor: interactive ? 'pointer' : 'default',
      padding: 12,
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/Card.jsx", error: String((e && e.message) || e) }); }

// components/display/ChatBubble.jsx
try { (() => {
/**
 * ChatBubble — dock Chat tab message. User = right, primary tint,
 * square corner bottom-right; agent = left, card surface.
 */
function ChatBubble({
  role = 'agent',
  time,
  children,
  style
}) {
  const user = role === 'user';
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: user ? 'flex-end' : 'flex-start',
      gap: 3,
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 420,
      borderRadius: user ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
      border: user ? '1px solid color-mix(in srgb, var(--primary) 35%, transparent)' : '1px solid var(--border-default)',
      background: user ? 'color-mix(in srgb, var(--primary) 18%, transparent)' : 'var(--surface-card)',
      color: 'var(--fg-default)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13.5,
      lineHeight: 1.45,
      padding: '8px 12px'
    }
  }, children), time ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 10,
      color: 'var(--fg-faint)',
      padding: '0 2px'
    }
  }, time) : null);
}
Object.assign(__ds_scope, { ChatBubble });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/ChatBubble.jsx", error: String((e && e.message) || e) }); }

// components/display/Kbd.jsx
try { (() => {
/**
 * Kbd — keyboard shortcut chip. Mono, bordered, bottom-weighted edge.
 */
function Kbd({
  children,
  style
}) {
  return /*#__PURE__*/React.createElement("kbd", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      minWidth: 20,
      height: 20,
      padding: '0 5px',
      borderRadius: 'var(--radius-sm, 4px)',
      border: '1px solid var(--border-default)',
      borderBottomWidth: 2,
      background: 'var(--bg-panel)',
      color: 'var(--fg-muted)',
      fontFamily: 'var(--font-mono)',
      fontSize: 10.5,
      lineHeight: 1,
      ...style
    }
  }, children);
}
Object.assign(__ds_scope, { Kbd });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/Kbd.jsx", error: String((e && e.message) || e) }); }

// components/display/LogLine.jsx
try { (() => {
/**
 * LogLine — console output row: faint [timestamp] + toned message,
 * all mono. Tones map to event kinds.
 */
const LOG_TONES = {
  muted: 'var(--fg-muted)',
  success: 'var(--success)',
  error: 'var(--destructive)',
  warning: 'var(--warning)',
  agent: 'var(--node-agent)',
  model: 'var(--node-model)',
  tool: 'var(--node-tool)',
  trigger: 'var(--node-trigger)'
};
function LogLine({
  time,
  tone = 'muted',
  children,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      lineHeight: 1.5,
      ...style
    }
  }, time ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--fg-faint)',
      flexShrink: 0
    }
  }, "[", time, "]") : null, /*#__PURE__*/React.createElement("span", {
    style: {
      color: LOG_TONES[tone] || tone,
      minWidth: 0,
      overflowWrap: 'break-word'
    }
  }, children));
}
Object.assign(__ds_scope, { LogLine });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/LogLine.jsx", error: String((e && e.message) || e) }); }

// components/display/Tabs.jsx
try { (() => {
/**
 * Tabs — console-dock style tab strip (Chat / Console / Terminal).
 * Active tab gets accent text + 2px underline.
 */
function Tabs({
  tabs,
  active,
  onChange,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 2,
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      padding: '0 8px',
      ...style
    }
  }, tabs.map(tab => {
    const id = typeof tab === 'string' ? tab : tab.id;
    const label = typeof tab === 'string' ? tab : tab.label;
    const isActive = active === id;
    return /*#__PURE__*/React.createElement(TabButton, {
      key: id,
      isActive: isActive,
      onClick: () => onChange && onChange(id)
    }, label);
  }));
}
function TabButton({
  isActive,
  onClick,
  children
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      appearance: 'none',
      background: hover && !isActive ? 'var(--bg-hover)' : 'transparent',
      border: 'none',
      borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
      color: isActive ? 'var(--accent)' : 'var(--fg-muted)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: isActive ? 600 : 500,
      padding: '8px 12px',
      cursor: 'pointer',
      transition: 'color var(--dur-fast, 90ms), background var(--dur-fast, 90ms)',
      marginBottom: -1
    }
  }, children);
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/feedback/EmptyState.jsx
try { (() => {
/**
 * EmptyState — dashed drop-zone style placeholder with icon, title,
 * hint and optional action. Matches "No workflows yet" pattern.
 */
function EmptyState({
  icon,
  title,
  hint,
  action,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      textAlign: 'center',
      border: '2px dashed var(--border-default)',
      borderRadius: 'var(--radius-xl, 12px)',
      background: 'transparent',
      padding: '32px 24px',
      ...style
    }
  }, icon ? /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--fg-faint)',
      marginBottom: 4
    }
  }, icon) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 15,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, title), hint ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      color: 'var(--fg-muted)',
      maxWidth: 320,
      lineHeight: 1.45
    }
  }, hint) : null, action ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, action) : null);
}
Object.assign(__ds_scope, { EmptyState });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/EmptyState.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Modal.jsx
try { (() => {
/**
 * Modal — overlay dialog. Flat rgba scrim (no blur), elevated card with
 * --shadow-modal, 18px title, ghost X. `inline` renders just the dialog
 * card (for specimens / embedding).
 */
function Modal({
  open = true,
  title,
  children,
  footer,
  onClose,
  width = 440,
  inline = false,
  style
}) {
  if (!open) return null;
  const card = /*#__PURE__*/React.createElement("div", {
    role: "dialog",
    "aria-modal": !inline,
    style: {
      width: '100%',
      maxWidth: width,
      borderRadius: 'var(--radius-xl, 12px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-elevated)',
      boxShadow: 'var(--shadow-modal)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      padding: '14px 16px',
      borderBottom: '1px solid var(--border-default)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 18,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, title), /*#__PURE__*/React.createElement(CloseButton, {
    onClick: onClose
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 16,
      color: 'var(--fg-default)',
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      lineHeight: 1.5
    }
  }, children), footer ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'flex-end',
      gap: 8,
      padding: '12px 16px',
      borderTop: '1px solid var(--border-default)',
      background: 'var(--bg-panel)'
    }
  }, footer) : null);
  if (inline) return card;
  return /*#__PURE__*/React.createElement("div", {
    onClick: e => {
      if (e.target === e.currentTarget && onClose) onClose();
    },
    style: {
      position: 'fixed',
      inset: 0,
      zIndex: 100,
      background: 'var(--bg-overlay)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24
    }
  }, card);
}
function CloseButton({
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    title: "Close",
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      width: 26,
      height: 26,
      padding: 0,
      flexShrink: 0,
      borderRadius: 'var(--radius-md, 6px)',
      border: 'none',
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: 'var(--fg-muted)',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'background var(--dur-fast, 90ms)'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M18 6 6 18"
  }), /*#__PURE__*/React.createElement("path", {
    d: "m6 6 12 12"
  })));
}
Object.assign(__ds_scope, { Modal });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Modal.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Progress.jsx
try { (() => {
/**
 * Progress — slim bar, accent fill, optional mono value label.
 */
function Progress({
  value = 0,
  color = 'var(--primary)',
  label,
  style
}) {
  const pct = Math.max(0, Math.min(100, value));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      width: '100%',
      ...style
    }
  }, label ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 5
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 12,
      fontWeight: 500,
      color: 'var(--fg-muted)'
    }
  }, label), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      color: 'var(--fg-muted)'
    }
  }, pct, "%")) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      height: 6,
      borderRadius: 'var(--radius-pill, 999px)',
      background: 'var(--bg-hover)',
      border: '1px solid var(--border-default)',
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      height: '100%',
      width: pct + '%',
      borderRadius: 'inherit',
      background: color,
      boxShadow: `0 0 8px color-mix(in srgb, ${color} 50%, transparent)`,
      transition: 'width var(--dur-slow, 320ms) var(--ease-default)'
    }
  })));
}
Object.assign(__ds_scope, { Progress });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Progress.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Spinner.jsx
try { (() => {
/**
 * Spinner — rotating arc in an accent color. Pair with mono label.
 */
function Spinner({
  size = 16,
  color = 'var(--primary)',
  style
}) {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'inline-flex',
      ...style
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    style: {
      animation: 'opencompany-spin 0.9s linear infinite'
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "9",
    stroke: `color-mix(in srgb, ${color} 20%, transparent)`,
    strokeWidth: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M21 12a9 9 0 0 0-9-9",
    stroke: color,
    strokeWidth: "3",
    strokeLinecap: "round"
  })), /*#__PURE__*/React.createElement("style", null, '@keyframes opencompany-spin { to { transform: rotate(360deg); } }'));
}
Object.assign(__ds_scope, { Spinner });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Spinner.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Toast.jsx
try { (() => {
/**
 * Toast — status notification: soft tint, status pip, mono timestamp.
 */
const TONES = {
  success: 'var(--success)',
  error: 'var(--destructive)',
  warning: 'var(--warning)',
  info: 'var(--info)'
};
function Toast({
  tone = 'info',
  title,
  message,
  time,
  onClose,
  style
}) {
  const c = TONES[tone] || TONES.info;
  return /*#__PURE__*/React.createElement("div", {
    role: "status",
    style: {
      display: 'flex',
      alignItems: 'flex-start',
      gap: 10,
      width: '100%',
      maxWidth: 360,
      borderRadius: 'var(--radius-lg, 8px)',
      border: `1px solid color-mix(in srgb, ${c} 35%, transparent)`,
      background: `color-mix(in srgb, ${c} 8%, var(--bg-elevated))`,
      boxShadow: 'var(--shadow-card)',
      padding: '10px 12px',
      fontFamily: 'var(--font-sans)',
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: '50%',
      background: c,
      boxShadow: `0 0 6px color-mix(in srgb, ${c} 60%, transparent)`,
      marginTop: 5,
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, title), time ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 10,
      color: 'var(--fg-faint)',
      flexShrink: 0
    }
  }, time) : null), message ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12.5,
      color: 'var(--fg-muted)',
      marginTop: 2,
      lineHeight: 1.4
    }
  }, message) : null), onClose ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClose,
    title: "Dismiss",
    style: {
      border: 'none',
      background: 'none',
      color: 'var(--fg-faint)',
      cursor: 'pointer',
      padding: 0,
      lineHeight: 1,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M18 6 6 18"
  }), /*#__PURE__*/React.createElement("path", {
    d: "m6 6 12 12"
  }))) : null);
}
Object.assign(__ds_scope, { Toast });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Toast.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Tooltip.jsx
try { (() => {
/**
 * Tooltip — hover label. Dark capsule, full-sentence copy allowed.
 */
function Tooltip({
  label,
  side = 'top',
  children,
  style
}) {
  const [show, setShow] = React.useState(false);
  const pos = side === 'bottom' ? {
    top: 'calc(100% + 6px)',
    left: '50%',
    transform: 'translateX(-50%)'
  } : {
    bottom: 'calc(100% + 6px)',
    left: '50%',
    transform: 'translateX(-50%)'
  };
  return /*#__PURE__*/React.createElement("span", {
    onMouseEnter: () => setShow(true),
    onMouseLeave: () => setShow(false),
    style: {
      position: 'relative',
      display: 'inline-flex',
      ...style
    }
  }, children, show ? /*#__PURE__*/React.createElement("span", {
    role: "tooltip",
    style: {
      position: 'absolute',
      zIndex: 60,
      ...pos,
      background: 'var(--fg-default)',
      color: 'var(--bg-app)',
      fontFamily: 'var(--font-sans)',
      fontSize: 11.5,
      fontWeight: 500,
      lineHeight: 1.35,
      borderRadius: 'var(--radius-md, 6px)',
      padding: '5px 9px',
      whiteSpace: 'nowrap',
      maxWidth: 260,
      boxShadow: 'var(--shadow-card-hover)',
      pointerEvents: 'none'
    }
  }, label) : null);
}
Object.assign(__ds_scope, { Tooltip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Tooltip.jsx", error: String((e && e.message) || e) }); }

// components/forms/ApiKeyInput.jsx
try { (() => {
/**
 * ApiKeyInput — credentials row: mono masked input with eye toggle,
 * Validate button (→ check + "Valid" when stored), optional delete.
 * Source: client/src/components/ui/ApiKeyInput.tsx.
 */
function ApiKeyInput({
  value,
  defaultValue = '',
  onChange,
  onSave,
  onDelete,
  placeholder = 'Enter API key...',
  loading = false,
  isStored = false,
  disabled = false,
  saveLabel = 'Validate',
  savedLabel = 'Valid',
  style
}) {
  const [internal, setInternal] = React.useState(defaultValue);
  const [visible, setVisible] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const cur = value !== undefined ? value : internal;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'stretch',
      gap: 4,
      width: '100%',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: visible ? 'text' : 'password',
    value: cur,
    placeholder: placeholder,
    disabled: disabled,
    onChange: e => {
      if (value === undefined) setInternal(e.target.value);
      onChange && onChange(e.target.value);
    },
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      height: 'var(--h-control, 32px)',
      width: '100%',
      borderRadius: 'var(--radius-lg, 8px)',
      border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
      boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
      background: 'var(--bg-input)',
      color: 'var(--fg-default)',
      fontFamily: 'var(--font-mono)',
      fontSize: 13,
      padding: '0 34px 0 12px',
      outline: 'none',
      transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)'
    }
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": visible ? 'Hide key' : 'Show key',
    onClick: () => setVisible(v => !v),
    style: {
      position: 'absolute',
      top: '50%',
      right: 8,
      transform: 'translateY(-50%)',
      border: 'none',
      background: 'none',
      padding: 2,
      cursor: 'pointer',
      color: 'var(--fg-faint)',
      display: 'flex'
    }
  }, visible ? /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M9.88 9.88a3 3 0 1 0 4.24 4.24"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "2",
    x2: "22",
    y1: "2",
    y2: "22"
  })) : /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  })))), /*#__PURE__*/React.createElement(ValidateButton, {
    disabled: !String(cur).trim() || disabled || loading,
    loading: loading,
    isStored: isStored,
    onClick: onSave,
    label: isStored ? savedLabel : saveLabel
  }), isStored && onDelete ? /*#__PURE__*/React.createElement(DeleteButton, {
    onClick: onDelete,
    disabled: disabled
  }) : null);
}
function ValidateButton({
  disabled,
  loading,
  isStored,
  onClick,
  label
}) {
  const [hover, setHover] = React.useState(false);
  const bg = isStored ? 'color-mix(in srgb, var(--success) 12%, transparent)' : hover && !disabled ? 'color-mix(in srgb, var(--primary) 85%, var(--bg-app))' : 'var(--primary)';
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    disabled: disabled,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      height: 'var(--h-control, 32px)',
      padding: '0 12px',
      borderRadius: 'var(--radius-lg, 8px)',
      border: isStored ? '1px solid color-mix(in srgb, var(--success) 35%, transparent)' : '1px solid transparent',
      background: bg,
      color: isStored ? 'var(--success)' : 'var(--primary-foreground)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 500,
      lineHeight: 1,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled && !isStored ? 0.5 : 1,
      whiteSpace: 'nowrap',
      flexShrink: 0,
      transition: 'background var(--dur-default, 180ms)'
    }
  }, loading ? /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    style: {
      animation: 'opencompany-spin 0.9s linear infinite'
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "M21 12a9 9 0 0 0-9-9",
    stroke: "currentColor",
    strokeWidth: "3",
    strokeLinecap: "round"
  })) : isStored ? /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M22 11.08V12a10 10 0 1 1-5.93-9.14"
  }), /*#__PURE__*/React.createElement("path", {
    d: "m9 11 3 3L22 4"
  })) : /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "17 21 17 13 7 13 7 21"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: "7 3 7 8 15 8"
  })), label, /*#__PURE__*/React.createElement("style", null, '@keyframes opencompany-spin { to { transform: rotate(360deg); } }'));
}
function DeleteButton({
  onClick,
  disabled
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    title: "Delete stored key",
    disabled: disabled,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      width: 'var(--h-control, 32px)',
      height: 'var(--h-control, 32px)',
      flexShrink: 0,
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid transparent',
      background: hover ? 'color-mix(in srgb, var(--destructive) 20%, transparent)' : 'color-mix(in srgb, var(--destructive) 10%, transparent)',
      color: 'var(--destructive)',
      cursor: disabled ? 'not-allowed' : 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'background var(--dur-default, 180ms)'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M3 6h18"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"
  })));
}
Object.assign(__ds_scope, { ApiKeyInput });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/ApiKeyInput.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
/**
 * Checkbox — 16px square, primary blue when checked, with label support.
 */
function Checkbox({
  checked,
  defaultChecked = false,
  onChange,
  disabled = false,
  label,
  style
}) {
  const [internal, setInternal] = React.useState(defaultChecked);
  const isOn = checked !== undefined ? checked : internal;
  const toggle = () => {
    if (disabled) return;
    const next = !isOn;
    if (checked === undefined) setInternal(next);
    onChange && onChange(next);
  };
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 8,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      userSelect: 'none',
      ...style
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    role: "checkbox",
    "aria-checked": isOn,
    onClick: toggle,
    disabled: disabled,
    style: {
      width: 16,
      height: 16,
      borderRadius: 'var(--radius-sm, 4px)',
      border: '1px solid ' + (isOn ? 'var(--primary)' : 'var(--border-strong)'),
      background: isOn ? 'var(--primary)' : 'var(--bg-input)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 0,
      cursor: 'inherit',
      transition: 'background var(--dur-fast, 90ms), border-color var(--dur-fast, 90ms)',
      flexShrink: 0
    }
  }, isOn ? /*#__PURE__*/React.createElement("svg", {
    width: "10",
    height: "10",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--primary-foreground)",
    strokeWidth: "3.5",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M20 6 9 17l-5-5"
  })) : null), label ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      color: 'var(--fg-default)'
    }
  }, label) : null);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Input — 32px text field. Optional leading icon (pass a ReactNode).
 * Focus ring is the solarized blue --border-focus.
 */
function Input({
  icon,
  style,
  wrapperStyle,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      display: 'flex',
      alignItems: 'center',
      width: '100%',
      ...wrapperStyle
    }
  }, icon ? /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      left: 10,
      display: 'flex',
      alignItems: 'center',
      color: 'var(--fg-faint)',
      pointerEvents: 'none'
    }
  }, icon) : null, /*#__PURE__*/React.createElement("input", _extends({
    onFocus: e => {
      setFocus(true);
      rest.onFocus && rest.onFocus(e);
    },
    onBlur: e => {
      setFocus(false);
      rest.onBlur && rest.onBlur(e);
    },
    style: {
      height: 'var(--h-control, 32px)',
      width: '100%',
      borderRadius: 'var(--radius-lg, 8px)',
      border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
      boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
      background: 'var(--bg-input)',
      color: 'var(--fg-default)',
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      padding: icon ? '0 12px 0 32px' : '0 12px',
      outline: 'none',
      transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
      ...style
    }
  }, rest)));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/RadioGroup.jsx
try { (() => {
/**
 * RadioGroup — vertical or horizontal radio list. 16px circles,
 * primary blue dot when selected.
 */
function RadioGroup({
  options = [],
  value,
  defaultValue,
  onChange,
  direction = 'column',
  disabled = false,
  style
}) {
  const [internal, setInternal] = React.useState(defaultValue);
  const current = value !== undefined ? value : internal;
  const norm = options.map(o => typeof o === 'string' ? {
    value: o,
    label: o
  } : o);
  const pick = v => {
    if (disabled) return;
    if (value === undefined) setInternal(v);
    onChange && onChange(v);
  };
  return /*#__PURE__*/React.createElement("div", {
    role: "radiogroup",
    style: {
      display: 'flex',
      flexDirection: direction,
      gap: direction === 'column' ? 8 : 16,
      opacity: disabled ? 0.5 : 1,
      ...style
    }
  }, norm.map(o => {
    const on = o.value === current;
    return /*#__PURE__*/React.createElement("label", {
      key: o.value,
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        cursor: disabled ? 'not-allowed' : 'pointer',
        userSelect: 'none'
      }
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      role: "radio",
      "aria-checked": on,
      disabled: disabled,
      onClick: () => pick(o.value),
      style: {
        width: 16,
        height: 16,
        borderRadius: '50%',
        padding: 0,
        flexShrink: 0,
        border: `1px solid ${on ? 'var(--primary)' : 'var(--border-strong)'}`,
        background: 'var(--bg-input)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'inherit',
        transition: 'border-color var(--dur-fast, 90ms)'
      }
    }, on ? /*#__PURE__*/React.createElement("span", {
      style: {
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: 'var(--primary)'
      }
    }) : null), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: 'var(--font-sans)',
        fontSize: 14,
        color: 'var(--fg-default)'
      }
    }, o.label));
  }));
}
Object.assign(__ds_scope, { RadioGroup });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/RadioGroup.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
/**
 * Select — 32px dropdown matching Input. Custom menu card with
 * check on the selected option. Placeholder copy ends with "...".
 */
function Select({
  options = [],
  value,
  defaultValue,
  onChange,
  placeholder = 'Select...',
  disabled = false,
  style,
  wrapperStyle
}) {
  const [internal, setInternal] = React.useState(defaultValue);
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  const current = value !== undefined ? value : internal;
  const norm = options.map(o => typeof o === 'string' ? {
    value: o,
    label: o
  } : o);
  const sel = norm.find(o => o.value === current);
  React.useEffect(() => {
    if (!open) return;
    const close = e => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const pick = v => {
    if (value === undefined) setInternal(v);
    onChange && onChange(v);
    setOpen(false);
  };
  return /*#__PURE__*/React.createElement("div", {
    ref: ref,
    style: {
      position: 'relative',
      width: '100%',
      ...wrapperStyle
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    disabled: disabled,
    onClick: () => setOpen(o => !o),
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      height: 'var(--h-control, 32px)',
      width: '100%',
      borderRadius: 'var(--radius-lg, 8px)',
      border: `1px solid ${open ? 'var(--border-focus)' : 'var(--border-default)'}`,
      boxShadow: open ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
      background: 'var(--bg-input)',
      color: sel ? 'var(--fg-default)' : 'var(--fg-faint)',
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      padding: '0 10px 0 12px',
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
      textAlign: 'left',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, sel ? sel.label : placeholder), /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--fg-muted)",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flexShrink: 0,
      transform: open ? 'rotate(180deg)' : 'none',
      transition: 'transform var(--dur-default, 180ms) var(--ease-default)'
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "m6 9 6 6 6-6"
  }))), open ? /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'absolute',
      top: 'calc(100% + 4px)',
      left: 0,
      right: 0,
      zIndex: 50,
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-elevated)',
      boxShadow: 'var(--shadow-modal)',
      padding: 4,
      maxHeight: 220,
      overflowY: 'auto'
    }
  }, norm.map(o => /*#__PURE__*/React.createElement(SelectOption, {
    key: o.value,
    option: o,
    selected: o.value === current,
    onPick: pick
  }))) : null);
}
function SelectOption({
  option,
  selected,
  onPick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: () => onPick(option.value),
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      width: '100%',
      border: 'none',
      textAlign: 'left',
      borderRadius: 'var(--radius-md, 6px)',
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: 'var(--fg-default)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13.5,
      padding: '7px 10px',
      cursor: 'pointer'
    }
  }, option.label, selected ? /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--primary)",
    strokeWidth: "2.5",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M20 6 9 17l-5-5"
  })) : null);
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/Slider.jsx
try { (() => {
/**
 * Slider — settings-panel range control: 6px track, accent fill,
 * 16px draggable thumb. Used with a % readout in the row label.
 */
function Slider({
  value,
  defaultValue = 50,
  min = 0,
  max = 100,
  step = 1,
  onChange,
  color = 'var(--primary)',
  disabled = false,
  style
}) {
  const [internal, setInternal] = React.useState(defaultValue);
  const cur = value !== undefined ? value : internal;
  const trackRef = React.useRef(null);
  const draggingRef = React.useRef(false);
  const pct = (cur - min) / (max - min) * 100;
  const setFromClientX = React.useCallback(clientX => {
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
    const move = e => {
      if (draggingRef.current) setFromClientX(e.clientX);
    };
    const up = () => {
      draggingRef.current = false;
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [setFromClientX]);
  return /*#__PURE__*/React.createElement("div", {
    ref: trackRef,
    role: "slider",
    "aria-valuemin": min,
    "aria-valuemax": max,
    "aria-valuenow": cur,
    onPointerDown: e => {
      if (disabled) return;
      draggingRef.current = true;
      setFromClientX(e.clientX);
    },
    style: {
      position: 'relative',
      height: 20,
      display: 'flex',
      alignItems: 'center',
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      touchAction: 'none',
      userSelect: 'none',
      width: '100%',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'absolute',
      left: 0,
      right: 0,
      height: 6,
      borderRadius: 'var(--radius-pill, 999px)',
      background: 'var(--bg-hover)',
      border: '1px solid var(--border-default)'
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'absolute',
      left: 0,
      width: pct + '%',
      height: 6,
      borderRadius: 'var(--radius-pill, 999px)',
      background: color
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'absolute',
      left: `calc(${pct}% - 8px)`,
      width: 16,
      height: 16,
      borderRadius: '50%',
      background: 'var(--bg-elevated)',
      border: `2px solid ${color}`,
      boxShadow: 'var(--shadow-card)'
    }
  }));
}
Object.assign(__ds_scope, { Slider });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Slider.jsx", error: String((e && e.message) || e) }); }

// components/forms/Switch.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Switch — 36×20 toggle. On = primary blue track.
 */
function Switch({
  checked,
  defaultChecked = false,
  onChange,
  disabled = false,
  style,
  ...rest
}) {
  const [internal, setInternal] = React.useState(defaultChecked);
  const isOn = checked !== undefined ? checked : internal;
  const toggle = () => {
    if (disabled) return;
    const next = !isOn;
    if (checked === undefined) setInternal(next);
    onChange && onChange(next);
  };
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    role: "switch",
    "aria-checked": isOn,
    onClick: toggle,
    disabled: disabled,
    style: {
      position: 'relative',
      width: 36,
      height: 20,
      borderRadius: 'var(--radius-pill, 999px)',
      border: '1px solid ' + (isOn ? 'transparent' : 'var(--border-default)'),
      background: isOn ? 'var(--primary)' : 'var(--bg-hover)',
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
      padding: 0,
      transition: 'background var(--dur-default, 180ms) var(--ease-default)',
      flexShrink: 0,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 2,
      left: isOn ? 17 : 2,
      width: 14,
      height: 14,
      borderRadius: '50%',
      background: isOn ? 'var(--primary-foreground)' : 'var(--fg-faint)',
      transition: 'left var(--dur-default, 180ms) var(--ease-default), background var(--dur-default, 180ms)'
    }
  }));
}
Object.assign(__ds_scope, { Switch });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Switch.jsx", error: String((e && e.message) || e) }); }

// components/forms/Textarea.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Textarea — multi-line Input. Mono option for prompts/code.
 */
function Textarea({
  mono = false,
  rows = 4,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  return /*#__PURE__*/React.createElement("textarea", _extends({
    rows: rows,
    onFocus: e => {
      setFocus(true);
      rest.onFocus && rest.onFocus(e);
    },
    onBlur: e => {
      setFocus(false);
      rest.onBlur && rest.onBlur(e);
    },
    style: {
      width: '100%',
      borderRadius: 'var(--radius-lg, 8px)',
      border: `1px solid ${focus ? 'var(--border-focus)' : 'var(--border-default)'}`,
      boxShadow: focus ? '0 0 0 3px color-mix(in srgb, var(--border-focus) 25%, transparent)' : 'none',
      background: 'var(--bg-input)',
      color: 'var(--fg-default)',
      fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
      fontSize: mono ? 13 : 14,
      lineHeight: 1.5,
      padding: '8px 12px',
      outline: 'none',
      resize: 'vertical',
      transition: 'border-color var(--dur-default, 180ms), box-shadow var(--dur-default, 180ms)',
      ...style
    }
  }, rest));
}
Object.assign(__ds_scope, { Textarea });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Textarea.jsx", error: String((e && e.message) || e) }); }

// components/icons/Icon.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Icon — Lucide bridge. OpenCompany uses lucide-react; in this design system
 * icons render from the lucide UMD bundle. Load it once per page:
 *   <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
 * Then <Icon name="Play" size={14} /> — colored via currentColor.
 */
function Icon({
  name,
  size = 16,
  strokeWidth = 2,
  style,
  ...rest
}) {
  const lib = typeof window !== 'undefined' ? window.lucide : null;
  let node = null;
  if (lib && lib.icons) {
    node = lib.icons[name] || lib.icons[toPascal(name)] || null;
  }
  // lucide UMD icon shapes seen across versions:
  //   [[tag, attrs], ...]              — children list
  //   ["svg", attrs, [[tag,attrs],..]] — full element triple
  let children = null;
  if (Array.isArray(node)) {
    if (node.length && Array.isArray(node[0])) children = node;else if (node[0] === 'svg' && Array.isArray(node[2])) children = node[2];
  }
  return /*#__PURE__*/React.createElement("svg", _extends({
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flexShrink: 0,
      display: 'inline-block',
      verticalAlign: 'middle',
      ...style
    },
    "aria-hidden": "true"
  }, rest), children ? children.map((child, i) => {
    const tag = child[0];
    const attrs = child[1] || {};
    return React.createElement(tag, {
      key: i,
      ...attrs
    });
  }) : null);
}
function toPascal(name) {
  if (!name) return '';
  return String(name).split(/[-_ ]/).map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('');
}
Object.assign(__ds_scope, { Icon });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/icons/Icon.jsx", error: String((e && e.message) || e) }); }

// components/panels/CollapsibleSection.jsx
try { (() => {
/**
 * CollapsibleSection — accordion card: elevated trigger head with
 * display-font title + rotating chevron over an app-surface body.
 * Source: client/src/components/ui/CollapsibleSection.tsx.
 */
function CollapsibleSection({
  title,
  defaultCollapsed = false,
  collapsed,
  onToggle,
  children,
  style
}) {
  const [internal, setInternal] = React.useState(defaultCollapsed);
  const isCollapsed = collapsed !== undefined ? collapsed : internal;
  const [hover, setHover] = React.useState(false);
  const toggle = () => {
    if (collapsed === undefined) setInternal(c => !c);
    onToggle && onToggle(!isCollapsed);
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      overflow: 'hidden',
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-app)',
      ...style
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: toggle,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'flex',
      width: '100%',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      border: 'none',
      cursor: 'pointer',
      background: hover ? 'var(--bg-hover)' : 'var(--bg-elevated)',
      padding: '12px 16px',
      color: 'var(--fg-default)',
      transition: 'background var(--dur-fast, 90ms)',
      textAlign: 'left'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-display, var(--font-sans))',
      fontSize: 14.5,
      fontWeight: 500,
      flex: 1,
      minWidth: 0,
      letterSpacing: 'var(--type-tracking-display, 0)',
      textTransform: 'var(--type-uppercase, none)',
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, title), /*#__PURE__*/React.createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--fg-muted)",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flexShrink: 0,
      transform: isCollapsed ? 'rotate(-90deg)' : 'none',
      transition: 'transform var(--dur-default, 180ms) var(--ease-default)'
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "m6 9 6 6 6-6"
  }))), !isCollapsed ? /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 12
    }
  }, children) : null);
}
Object.assign(__ds_scope, { CollapsibleSection });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/panels/CollapsibleSection.jsx", error: String((e && e.message) || e) }); }

// components/panels/DataCard.jsx
try { (() => {
/**
 * DataCard — execution-data card from the parameter panel's Input/Output
 * columns: status left-edge, icon + title + source badge header, Copy
 * action, labeled mono JSON block.
 * Source: client/src/components/ui/InputNodesPanel.tsx / OutputDisplayPanel.tsx.
 */
function DataCard({
  title,
  badge,
  tone = 'success',
  blockLabel = 'Received Data',
  data,
  onCopy,
  style
}) {
  const c = tone === 'error' ? 'var(--destructive)' : tone === 'warning' ? 'var(--warning)' : 'var(--success)';
  const json = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)',
      borderLeft: `4px solid ${c}`,
      background: 'var(--surface-card)',
      padding: 12,
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: c,
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("ellipse", {
    cx: "12",
    cy: "5",
    rx: "9",
    ry: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M3 5V19A9 3 0 0 0 21 19V5"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M3 12A9 3 0 0 0 21 12"
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--fg-default)',
      whiteSpace: 'nowrap'
    }
  }, title), badge ? /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      height: 18,
      padding: '0 6px',
      borderRadius: 'var(--radius-md, 6px)',
      fontSize: 11,
      fontWeight: 500,
      whiteSpace: 'nowrap',
      background: `color-mix(in srgb, ${c} 12%, transparent)`,
      border: `1px solid color-mix(in srgb, ${c} 30%, transparent)`,
      color: c,
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, badge) : null), /*#__PURE__*/React.createElement(CopyButton, {
    onCopy: onCopy,
    json: json
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      overflow: 'hidden',
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-hover)',
      padding: '6px 12px',
      fontFamily: 'var(--font-sans)',
      fontSize: 11.5,
      fontWeight: 600,
      color: 'var(--fg-muted)'
    }
  }, blockLabel), /*#__PURE__*/React.createElement("pre", {
    style: {
      margin: 0,
      maxHeight: 300,
      overflow: 'auto',
      whiteSpace: 'pre-wrap',
      overflowWrap: 'break-word',
      background: 'color-mix(in srgb, var(--bg-hover) 40%, transparent)',
      padding: 12,
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      lineHeight: 1.4,
      color: 'var(--fg-default)'
    }
  }, json)));
}
function CopyButton({
  onCopy,
  json
}) {
  const [hover, setHover] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const handle = () => {
    if (onCopy) onCopy(json);else if (navigator.clipboard) navigator.clipboard.writeText(json).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: handle,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      border: 'none',
      borderRadius: 'var(--radius-md, 6px)',
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: 'var(--fg-muted)',
      fontFamily: 'var(--font-sans)',
      fontSize: 12,
      fontWeight: 500,
      padding: '5px 8px',
      cursor: 'pointer',
      flexShrink: 0,
      transition: 'background var(--dur-fast, 90ms)'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("rect", {
    width: "14",
    height: "14",
    x: "8",
    y: "8",
    rx: "2",
    ry: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"
  })), copied ? 'Copied' : 'Copy');
}
Object.assign(__ds_scope, { DataCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/panels/DataCard.jsx", error: String((e && e.message) || e) }); }

// components/panels/PanelModal.jsx
try { (() => {
/**
 * PanelModal — the product's workspace modal: header bar with title
 * absolute-left (icon + display font), centered headerActions (the
 * Run/Save/Cancel ActionButton cluster), X absolute-right. Header sits
 * on --bg-panel one step above the --bg-app body. Source:
 * client/src/components/ui/Modal.tsx.
 */
function PanelModal({
  open = true,
  title,
  titleIcon,
  headerActions,
  children,
  onClose,
  maxWidth = '90%',
  maxHeight = '88%',
  inline = false,
  style
}) {
  if (!open) return null;
  const card = /*#__PURE__*/React.createElement("div", {
    role: "dialog",
    "aria-modal": !inline,
    className: "modal modal-frame",
    style: {
      width: '100%',
      maxWidth,
      height: inline ? 'auto' : '100%',
      maxHeight,
      borderRadius: 'var(--radius-lg, 8px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-app)',
      boxShadow: 'var(--shadow-modal)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      display: 'flex',
      alignItems: 'center',
      width: '100%',
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      padding: '10px 20px',
      minHeight: 48,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      left: 20,
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontFamily: 'var(--font-display, var(--font-sans))',
      fontSize: 15,
      fontWeight: 600,
      letterSpacing: 'var(--type-tracking-display, 0)',
      textTransform: 'var(--type-uppercase, none)',
      color: 'var(--fg-default)'
    }
  }, titleIcon ? /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.7,
      display: 'flex'
    }
  }, titleIcon) : null, title), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }
  }, headerActions), /*#__PURE__*/React.createElement(PanelClose, {
    onClick: onClose
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden'
    }
  }, children));
  if (inline) return card;
  return /*#__PURE__*/React.createElement("div", {
    onClick: e => {
      if (e.target === e.currentTarget && onClose) onClose();
    },
    style: {
      position: 'fixed',
      inset: 0,
      zIndex: 100,
      background: 'var(--bg-overlay)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24
    }
  }, card);
}
function PanelClose({
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": "Close",
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      position: 'absolute',
      right: 20,
      width: 32,
      height: 32,
      padding: 0,
      borderRadius: 'var(--radius-md, 6px)',
      border: 'none',
      background: hover ? 'var(--bg-hover)' : 'transparent',
      color: hover ? 'var(--fg-default)' : 'var(--fg-muted)',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'background var(--dur-fast, 90ms), color var(--dur-fast, 90ms)'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "18",
    height: "18",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M18 6 6 18"
  }), /*#__PURE__*/React.createElement("path", {
    d: "m6 6 12 12"
  })));
}
Object.assign(__ds_scope, { PanelModal });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/panels/PanelModal.jsx", error: String((e && e.message) || e) }); }

// components/panels/SettingsSection.jsx
try { (() => {
/**
 * SettingsSection + SettingsRow — the Settings panel's building blocks.
 * Section = elevated card with a toned 32px icon tile + display-font
 * title over a divider. Row = label + description left, control right.
 * Source: client/src/components/ui/SettingsPanel.tsx.
 */
const SECTION_TONES = {
  agent: 'var(--node-agent)',
  model: 'var(--node-model)',
  workflow: 'var(--node-workflow)',
  tool: 'var(--node-tool)',
  trigger: 'var(--node-trigger)'
};
function SettingsSection({
  title,
  icon,
  tone = 'agent',
  children,
  style
}) {
  const c = SECTION_TONES[tone] || tone;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16,
      borderRadius: 'var(--radius-md, 6px)',
      border: '1px solid var(--border-default)',
      background: 'var(--bg-elevated)',
      padding: 16,
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      borderBottom: '1px solid var(--border-default)',
      paddingBottom: 12,
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      width: 32,
      height: 32,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: 'var(--radius-md, 6px)',
      background: `color-mix(in srgb, ${c} 8%, transparent)`,
      color: c,
      flexShrink: 0
    }
  }, icon), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-display, var(--font-sans))',
      fontSize: 16,
      fontWeight: 600,
      letterSpacing: 'var(--type-tracking-display, 0)',
      textTransform: 'var(--type-uppercase, none)',
      color: 'var(--fg-default)'
    }
  }, title)), children);
}
function SettingsRow({
  label,
  description,
  children,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 16,
      padding: '8px 0',
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      fontWeight: 500,
      color: 'var(--fg-default)'
    }
  }, label), description ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-sans)',
      fontSize: 12,
      color: 'var(--fg-muted)',
      marginTop: 2,
      lineHeight: 1.4
    }
  }, description) : null), /*#__PURE__*/React.createElement("div", {
    style: {
      flexShrink: 0
    }
  }, children));
}
Object.assign(__ds_scope, { SettingsSection, SettingsRow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/panels/SettingsSection.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/App.jsx
try { (() => {
// OpenCompany UI kit — app shell wiring all panels together.
const DS_APP = window.OpenCompanyDesignSystem_2559cf;
const KIT = window.OpenCompanyKit;
const WORKFLOWS = [{
  id: 'wa',
  name: 'WhatsApp Assistant',
  nodeCount: 6,
  modified: 'Jun 11, 09:14'
}, {
  id: 'digest',
  name: 'Daily Email Digest',
  nodeCount: 5,
  modified: 'Jun 9, 18:30'
}, {
  id: 'support',
  name: 'Customer Support Bot',
  nodeCount: 9,
  modified: 'Jun 5, 11:02'
}];
function App() {
  const {
    StatusBar
  } = DS_APP;
  const {
    Toolbar,
    SidebarPanel,
    PalettePanel,
    CanvasView,
    ConsoleDock,
    SettingsModal,
    NodeConfigModal,
    CredentialsModal
  } = KIT;
  const [dark, setDark] = React.useState(true);
  const [sidebarVisible, setSidebarVisible] = React.useState(true);
  const [paletteVisible, setPaletteVisible] = React.useState(true);
  const [consoleOpen, setConsoleOpen] = React.useState(true);
  const [mode, setMode] = React.useState('normal');
  const [running, setRunning] = React.useState(false);
  const [saved, setSaved] = React.useState(true);
  const [currentWf, setCurrentWf] = React.useState('wa');
  const [selectedId, setSelectedId] = React.useState(null);
  const [extraNodes, setExtraNodes] = React.useState([]);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [credsOpen, setCredsOpen] = React.useState(false);
  const [configNode, setConfigNode] = React.useState(null);
  React.useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
  }, [dark]);
  const wf = WORKFLOWS.find(w => w.id === currentWf);
  const handleDropNode = (item, section) => {
    const id = 'extra-' + Date.now();
    setExtraNodes(ns => [...ns, {
      id,
      icon: item.icon,
      label: item.name,
      color: section.color,
      x: 620 + ns.length * 60 % 180,
      y: 60 + ns.length * 50 % 120
    }]);
    setSaved(false);
    setSelectedId(id);
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg-app)'
    }
  }, /*#__PURE__*/React.createElement(Toolbar, {
    workflowName: wf ? wf.name : 'Untitled Workflow',
    sidebarVisible: sidebarVisible,
    paletteVisible: paletteVisible,
    mode: mode,
    dark: dark,
    running: running,
    saved: saved,
    onToggleSidebar: () => setSidebarVisible(v => !v),
    onTogglePalette: () => setPaletteVisible(v => !v),
    onModeChange: setMode,
    onToggleTheme: () => setDark(d => !d),
    onRun: () => setRunning(r => !r),
    onSave: () => setSaved(true),
    onSettings: () => setSettingsOpen(true),
    onCredentials: () => setCredsOpen(true)
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      minHeight: 0
    }
  }, sidebarVisible ? /*#__PURE__*/React.createElement(SidebarPanel, {
    workflows: WORKFLOWS,
    currentId: currentWf,
    onSelect: id => {
      setCurrentWf(id);
      setSelectedId(null);
    }
  }) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement(CanvasView, {
    running: running,
    extraNodes: extraNodes,
    selectedId: selectedId,
    onSelect: id => setSelectedId(s => s === id ? null : id),
    onConfigure: n => setConfigNode(n)
  }), /*#__PURE__*/React.createElement(ConsoleDock, {
    open: consoleOpen,
    onToggle: () => setConsoleOpen(o => !o)
  })), paletteVisible ? /*#__PURE__*/React.createElement(PalettePanel, {
    mode: mode,
    onDropNode: handleDropNode
  }) : null), /*#__PURE__*/React.createElement(StatusBar, {
    connection: "online",
    workflowName: wf ? wf.name : '—',
    nodeCount: 6 + extraNodes.length,
    themeName: dark ? 'DARK' : 'LIGHT'
  }), /*#__PURE__*/React.createElement(SettingsModal, {
    open: settingsOpen,
    onClose: () => setSettingsOpen(false)
  }), /*#__PURE__*/React.createElement(CredentialsModal, {
    open: credsOpen,
    onClose: () => setCredsOpen(false)
  }), /*#__PURE__*/React.createElement(NodeConfigModal, {
    node: configNode,
    onClose: () => setConfigNode(null)
  }));
}
ReactDOM.createRoot(document.getElementById('root')).render(/*#__PURE__*/React.createElement(App, null));
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/App.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/CanvasView.jsx
try { (() => {
// OpenCompany UI kit — node canvas: dot grid, dashed edges, square nodes,
// rectangular AI Agent node. Click nodes to select; Start runs the flow.
const DS_CV = window.OpenCompanyDesignSystem_2559cf;

// Rectangular agent node (the larger card-style node from the product).
function AgentNode({
  x,
  y,
  status,
  selected,
  executing,
  onClick,
  onGearClick
}) {
  const color = 'var(--node-agent)';
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClick,
    className: executing ? 'opencompany-pulse' : undefined,
    style: {
      '--node-pulse-color': color,
      position: 'absolute',
      left: x,
      top: y,
      width: 190,
      borderRadius: 12,
      border: `2px solid ${selected ? color : `color-mix(in srgb, ${color} 60%, transparent)`}`,
      background: `linear-gradient(135deg, color-mix(in srgb, ${color} 14%, var(--surface-card)) 0%, var(--surface-card) 100%)`,
      boxShadow: selected ? `0 0 0 1px ${color}, 0 4px 14px color-mix(in srgb, ${color} 32%, transparent)` : `0 2px 8px color-mix(in srgb, ${color} 18%, transparent)`,
      cursor: 'pointer',
      userSelect: 'none',
      zIndex: 10,
      padding: '14px 12px 10px',
      textAlign: 'center',
      transition: 'border-color 150ms ease, box-shadow 150ms ease'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: -4,
      left: -4,
      width: 10,
      height: 10,
      borderRadius: '50%',
      zIndex: 30,
      background: status === 'success' ? 'var(--success)' : status === 'executing' ? color : 'var(--fg-faint)',
      boxShadow: status !== 'idle' ? `0 0 6px color-mix(in srgb, ${status === 'executing' ? color : 'var(--success)'} 60%, transparent)` : 'none'
    }
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    title: "Edit parameters",
    onClick: e => {
      e.stopPropagation();
      onGearClick && onGearClick();
    },
    style: {
      position: 'absolute',
      top: -8,
      right: -8,
      width: 20,
      height: 20,
      borderRadius: '50%',
      background: 'var(--surface-card)',
      border: '1px solid var(--border-default)',
      fontSize: 10,
      cursor: 'pointer',
      zIndex: 30,
      padding: 0
    }
  }, "\u2699\uFE0F"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 26,
      lineHeight: 1
    }
  }, "\uD83E\uDD16"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 600,
      color: 'var(--fg-default)',
      marginTop: 6
    }
  }, "AI Agent"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: color,
      marginTop: 2
    }
  }, "LangGraph Agent"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginTop: 8,
      fontSize: 10.5,
      color: 'var(--fg-muted)'
    }
  }, /*#__PURE__*/React.createElement("span", null, "Memory"), /*#__PURE__*/React.createElement("span", null, "Tool")), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      left: -6,
      top: '50%',
      transform: 'translateY(-50%)',
      width: 12,
      height: 12,
      borderRadius: '50%',
      background: 'var(--bg-app)',
      border: '2px solid var(--fg-faint)',
      zIndex: 20
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      right: -6,
      top: '50%',
      transform: 'translateY(-50%)',
      width: 12,
      height: 12,
      borderRadius: '50%',
      background: color,
      border: `2px solid ${color}`,
      zIndex: 20
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      left: 28,
      bottom: -6,
      width: 11,
      height: 11,
      borderRadius: 3,
      transform: 'rotate(45deg)',
      background: 'var(--bg-app)',
      border: '2px solid var(--fg-faint)',
      zIndex: 20
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      right: 28,
      bottom: -6,
      width: 11,
      height: 11,
      borderRadius: 3,
      transform: 'rotate(45deg)',
      background: 'var(--bg-app)',
      border: '2px solid var(--fg-faint)',
      zIndex: 20
    }
  }));
}
const BASE_NODES = [{
  id: 'receive',
  type: 'square',
  icon: '💬',
  label: 'WhatsApp Receive',
  color: 'var(--node-trigger)',
  x: 70,
  y: 210,
  showInput: false,
  trigger: true
}, {
  id: 'agent',
  type: 'agent',
  x: 330,
  y: 150
}, {
  id: 'send',
  type: 'square',
  icon: '📤',
  label: 'WhatsApp Send',
  color: 'var(--node-trigger)',
  x: 670,
  y: 210,
  showOutput: false
}, {
  id: 'memory',
  type: 'square',
  icon: '🧠',
  label: 'Simple Memory',
  color: 'var(--node-agent)',
  x: 290,
  y: 400,
  showInput: false,
  showToolOutput: true
}, {
  id: 'toolkit',
  type: 'square',
  icon: '📱',
  label: 'Android Toolkit',
  color: 'var(--node-tool)',
  x: 480,
  y: 400,
  showInput: false,
  showToolOutput: true
}, {
  id: 'search',
  type: 'square',
  icon: '🔍',
  label: 'Web Search Tool',
  color: 'var(--node-model)',
  x: 130,
  y: 420,
  showInput: false,
  showToolOutput: true
}];

// Edge endpoints (hand-tuned against the node geometry above).
const EDGES = [{
  id: 'e1',
  from: [134, 242],
  to: [330, 215],
  color: 'var(--dracula-pink)'
}, {
  id: 'e2',
  from: [520, 215],
  to: [670, 242],
  color: 'var(--dracula-purple)'
}, {
  id: 'e3',
  from: [358, 286],
  to: [322, 400],
  color: 'var(--dracula-purple)'
}, {
  id: 'e4',
  from: [492, 286],
  to: [512, 400],
  color: 'var(--dracula-green)'
}, {
  id: 'e5',
  from: [358, 286],
  to: [162, 420],
  color: 'var(--dracula-cyan)'
}];
const RUN_ORDER = ['receive', 'agent', 'memory', 'search', 'toolkit', 'send'];
function CanvasView({
  running,
  extraNodes,
  selectedId,
  onSelect,
  onConfigure
}) {
  const {
    SquareNode
  } = DS_CV;
  const [step, setStep] = React.useState(-1);
  React.useEffect(() => {
    if (!running) {
      setStep(-1);
      return;
    }
    setStep(0);
    const id = window.setInterval(() => {
      setStep(s => (s + 1) % (RUN_ORDER.length + 2));
    }, 900);
    return () => window.clearInterval(id);
  }, [running]);
  const statusOf = nodeId => {
    if (!running || step < 0) return 'idle';
    const idx = RUN_ORDER.indexOf(nodeId);
    if (idx === -1) return 'idle';
    if (idx === step) return 'executing';
    if (idx < step) return 'success';
    return 'idle';
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      flex: 1,
      overflow: 'hidden',
      background: 'var(--bg-canvas)',
      backgroundImage: 'radial-gradient(color-mix(in srgb, var(--fg-muted) 30%, transparent) 1px, transparent 1px)',
      backgroundSize: '20px 20px'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    style: {
      position: 'absolute',
      inset: 0,
      width: '100%',
      height: '100%',
      pointerEvents: 'none'
    }
  }, EDGES.map(e => {
    const [x1, y1] = e.from;
    const [x2, y2] = e.to;
    const mx = (x1 + x2) / 2;
    return /*#__PURE__*/React.createElement("path", {
      key: e.id,
      d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`,
      fill: "none",
      stroke: e.color,
      strokeWidth: "2",
      strokeDasharray: "6 6",
      opacity: "0.8"
    });
  })), BASE_NODES.map(n => n.type === 'agent' ? /*#__PURE__*/React.createElement(AgentNode, {
    key: n.id,
    x: n.x,
    y: n.y,
    status: statusOf(n.id),
    executing: statusOf(n.id) === 'executing',
    selected: selectedId === n.id,
    onClick: () => onSelect(n.id),
    onGearClick: () => onConfigure && onConfigure({
      id: n.id,
      icon: '🤖',
      label: 'AI Agent'
    })
  }) : /*#__PURE__*/React.createElement("div", {
    key: n.id,
    style: {
      position: 'absolute',
      left: n.x,
      top: n.y,
      zIndex: 10
    }
  }, /*#__PURE__*/React.createElement(SquareNode, {
    icon: n.icon,
    label: n.label,
    color: n.color,
    status: n.trigger && statusOf(n.id) === 'idle' ? 'listening' : statusOf(n.id),
    executing: statusOf(n.id) === 'executing',
    trigger: !!n.trigger,
    pulseColor: n.trigger ? 'var(--node-trigger)' : undefined,
    selected: selectedId === n.id,
    showInput: n.showInput !== false,
    showOutput: n.showOutput !== false,
    showToolOutput: !!n.showToolOutput,
    onClick: () => onSelect(n.id),
    onGearClick: () => onConfigure && onConfigure({
      id: n.id,
      icon: n.icon,
      label: n.label
    })
  }))), extraNodes.map(n => /*#__PURE__*/React.createElement("div", {
    key: n.id,
    style: {
      position: 'absolute',
      left: n.x,
      top: n.y,
      zIndex: 10
    }
  }, /*#__PURE__*/React.createElement(SquareNode, {
    icon: n.icon,
    label: n.label,
    color: n.color,
    selected: selectedId === n.id,
    onClick: () => onSelect(n.id),
    onGearClick: () => onConfigure && onConfigure({
      id: n.id,
      icon: n.icon,
      label: n.label
    })
  }))));
}
window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, {
  CanvasView
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/CanvasView.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/ConsoleDock.jsx
try { (() => {
// OpenCompany UI kit — multi-tab console dock (Chat / Console / Terminal).
const DS_CD = window.OpenCompanyDesignSystem_2559cf;
const CONSOLE_LINES = [{
  t: '12:01:31',
  tone: 'var(--fg-muted)',
  text: 'Workflow "WhatsApp Assistant" loaded · 6 nodes'
}, {
  t: '12:01:33',
  tone: 'var(--success)',
  text: 'Deployed — listening for incoming messages'
}, {
  t: '12:04:02',
  tone: 'var(--dracula-cyan)',
  text: 'WhatsApp Receive → message from +1 (555) 014-2236'
}, {
  t: '12:04:03',
  tone: 'var(--dracula-purple)',
  text: 'AI Agent → delegating to Web Search Tool'
}, {
  t: '12:04:05',
  tone: 'var(--success)',
  text: 'WhatsApp Send → reply delivered (1.9s)'
}];
function ConsoleDock({
  open,
  onToggle
}) {
  const {
    Tabs,
    Icon,
    Input
  } = DS_CD;
  const [tab, setTab] = React.useState('console');
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderTop: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement(Tabs, {
    tabs: [{
      id: 'chat',
      label: 'Chat'
    }, {
      id: 'console',
      label: 'Console'
    }, {
      id: 'terminal',
      label: 'Terminal'
    }],
    active: tab,
    onChange: id => {
      setTab(id);
      if (!open) onToggle();
    },
    style: {
      flex: 1,
      borderBottom: open ? '1px solid var(--border-default)' : 'none'
    }
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onToggle,
    title: open ? 'Collapse console' : 'Expand console',
    style: {
      background: 'none',
      border: 'none',
      color: 'var(--fg-muted)',
      cursor: 'pointer',
      padding: '6px 12px'
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: open ? 'ChevronDown' : 'ChevronUp',
    size: 14
  }))), open ? /*#__PURE__*/React.createElement("div", {
    style: {
      height: 150,
      overflowY: 'auto',
      padding: '10px 14px',
      background: 'var(--bg-app)'
    }
  }, tab === 'console' ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4
    }
  }, CONSOLE_LINES.map((l, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      display: 'flex',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--fg-faint)'
    }
  }, "[", l.t, "]"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: l.tone
    }
  }, l.text)))) : tab === 'chat' ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      height: '100%'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      alignSelf: 'flex-end',
      maxWidth: 420,
      background: 'color-mix(in srgb, var(--primary) 18%, transparent)',
      border: '1px solid color-mix(in srgb, var(--primary) 35%, transparent)',
      borderRadius: '10px 10px 2px 10px',
      padding: '8px 12px',
      fontSize: 13,
      color: 'var(--fg-default)'
    }
  }, "Summarize my unread emails every weekday at 9 AM."), /*#__PURE__*/React.createElement("div", {
    style: {
      alignSelf: 'flex-start',
      maxWidth: 420,
      background: 'var(--surface-card)',
      border: '1px solid var(--border-default)',
      borderRadius: '10px 10px 10px 2px',
      padding: '8px 12px',
      fontSize: 13,
      color: 'var(--fg-default)'
    }
  }, "Done \u2014 I scheduled a daily digest. Want it on WhatsApp too?"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 'auto'
    }
  }, /*#__PURE__*/React.createElement(Input, {
    placeholder: "Message your agent..."
  }))) : /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      color: 'var(--fg-default)'
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--success)'
    }
  }, "$"), " company start"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--fg-muted)',
      marginTop: 4
    }
  }, "OpenCompany running at http://localhost:3000"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--fg-muted)'
    }
  }, "Temporal \xB7 Python backend \xB7 WhatsApp service \u2014 all up"))) : null);
}
window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, {
  ConsoleDock
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/ConsoleDock.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/Panels.jsx
try { (() => {
// OpenCompany UI kit — workflow sidebar (280px) + component palette (320px).
const DS_PN = window.OpenCompanyDesignSystem_2559cf;
function SidebarPanel({
  workflows,
  currentId,
  onSelect
}) {
  const {
    WorkflowCard,
    Icon
  } = DS_PN;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      width: 'var(--w-sidebar, 280px)',
      borderRight: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      overflow: 'hidden',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-app)',
      padding: '18px 16px'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 36,
      height: 36,
      borderRadius: 'var(--radius-md)',
      background: 'color-mix(in srgb, var(--accent) 20%, transparent)',
      color: 'var(--accent)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "FolderOpen",
    size: 16
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 16,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, "Workflows"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      color: 'var(--fg-muted)'
    }
  }, workflows.length, " saved"))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 12,
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, workflows.map(wf => /*#__PURE__*/React.createElement(WorkflowCard, {
    key: wf.id,
    name: wf.name,
    nodeCount: wf.nodeCount,
    modified: wf.modified,
    selected: wf.id === currentId,
    onClick: () => onSelect(wf.id)
  }))));
}
const PALETTE_SECTIONS = [{
  id: 'ai',
  label: 'AI',
  icon: '🤖',
  color: 'var(--node-agent)',
  visibility: 'normal',
  items: [{
    icon: '🤖',
    name: 'AI Agent',
    description: 'LangGraph agent with memory and tools'
  }, {
    icon: '🧠',
    name: 'Simple Memory',
    description: 'Conversation memory store'
  }, {
    icon: '📚',
    name: 'Skill',
    description: 'Teach your agent a capability'
  }]
}, {
  id: 'messaging',
  label: 'Messaging',
  icon: '💬',
  color: 'var(--node-trigger)',
  visibility: 'normal',
  items: [{
    icon: '💬',
    name: 'WhatsApp Receive',
    description: 'Trigger on incoming message'
  }, {
    icon: '📤',
    name: 'WhatsApp Send',
    description: 'Send a WhatsApp message'
  }, {
    icon: '✈️',
    name: 'Telegram',
    description: 'Bot send and receive'
  }]
}, {
  id: 'android',
  label: 'Android',
  icon: '📱',
  color: 'var(--node-tool)',
  visibility: 'normal',
  items: [{
    icon: '📱',
    name: 'Android Toolkit',
    description: '16 device services'
  }, {
    icon: '🔋',
    name: 'Battery Monitor',
    description: 'Read battery status'
  }, {
    icon: '🚀',
    name: 'App Launcher',
    description: 'Launch apps on device'
  }]
}, {
  id: 'web',
  label: 'Web',
  icon: '🔍',
  color: 'var(--node-model)',
  visibility: 'normal',
  items: [{
    icon: '🔍',
    name: 'Web Search Tool',
    description: 'DuckDuckGo, Brave, Serper'
  }, {
    icon: '🌐',
    name: 'Browser',
    description: 'Accessibility-tree navigation'
  }]
}, {
  id: 'code',
  label: 'Code',
  icon: '⚙️',
  color: 'var(--node-workflow)',
  visibility: 'dev',
  items: [{
    icon: '🐍',
    name: 'Python',
    description: 'Run sandboxed Python'
  }, {
    icon: '📜',
    name: 'JavaScript',
    description: 'Run sandboxed JS/TS'
  }, {
    icon: '🖥️',
    name: 'Process Manager',
    description: 'Own long-running tasks'
  }]
}];
function PalettePanel({
  mode,
  onDropNode
}) {
  const {
    Input,
    Badge,
    ComponentItem,
    Icon
  } = DS_PN;
  const [query, setQuery] = React.useState('');
  const [collapsed, setCollapsed] = React.useState({});
  const sections = PALETTE_SECTIONS.filter(s => mode === 'dev' || s.visibility === 'normal').map(s => ({
    ...s,
    items: s.items.filter(it => !query.trim() || it.name.toLowerCase().includes(query.toLowerCase()) || it.description.toLowerCase().includes(query.toLowerCase()))
  })).filter(s => s.items.length > 0);
  const total = sections.reduce((acc, s) => acc + s.items.length, 0);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      width: 'var(--w-palette, 320px)',
      borderLeft: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      overflow: 'hidden',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-app)',
      padding: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 16,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, "Components"), /*#__PURE__*/React.createElement(Badge, {
    mono: true
  }, total)), /*#__PURE__*/React.createElement(Input, {
    placeholder: "Search...",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Search",
      size: 14
    }),
    value: query,
    onChange: e => setQuery(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 12
    }
  }, sections.length === 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'center',
      padding: '48px 24px',
      color: 'var(--fg-muted)'
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "Search",
    size: 40,
    style: {
      opacity: 0.5
    }
  }), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 13,
      marginTop: 12
    }
  }, "No components found matching \"", query, "\"")) : sections.map(section => /*#__PURE__*/React.createElement("div", {
    key: section.id,
    style: {
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: () => setCollapsed(c => ({
      ...c,
      [section.id]: !c[section.id]
    })),
    style: {
      display: 'flex',
      width: '100%',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: 'none',
      border: 'none',
      padding: '6px 4px',
      cursor: 'pointer'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      width: 28,
      height: 28,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: 'var(--radius-md)',
      fontSize: 14,
      background: `color-mix(in srgb, ${section.color} 8%, transparent)`
    }
  }, section.icon), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--fg-default)'
    }
  }, section.label)), /*#__PURE__*/React.createElement(Badge, {
    mono: true
  }, section.items.length)), !collapsed[section.id] ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gap: 8,
      paddingTop: 8
    }
  }, section.items.map(it => /*#__PURE__*/React.createElement(ComponentItem, {
    key: it.name,
    icon: it.icon,
    name: it.name,
    description: it.description,
    onClick: () => onDropNode && onDropNode(it, section)
  }))) : null))));
}
window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, {
  SidebarPanel,
  PalettePanel
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/Panels.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/PanelsModals.jsx
try { (() => {
// OpenCompany UI kit — Settings, Node Configuration, and Credentials modals.
// Faithful recreations of SettingsPanel.tsx / ParameterPanel.tsx, composed
// from the DS panel components.
const DS_PM = window.OpenCompanyDesignSystem_2559cf;
function SettingsModal({
  open,
  onClose
}) {
  const {
    PanelModal,
    SettingsSection,
    SettingsRow,
    Switch,
    Input,
    Slider,
    Button,
    ActionButton,
    Icon
  } = DS_PM;
  const [ratio, setRatio] = React.useState(80);
  if (!open) return null;
  return /*#__PURE__*/React.createElement(PanelModal, {
    title: "Settings",
    titleIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "Settings",
      size: 14
    }),
    maxWidth: "720px",
    maxHeight: "88%",
    onClose: onClose,
    headerActions: /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 8
      }
    }, /*#__PURE__*/React.createElement(ActionButton, {
      intent: "config",
      title: "Reset to default settings"
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "RotateCcw",
      size: 12
    }), " Reset"), /*#__PURE__*/React.createElement(ActionButton, {
      intent: "run",
      title: "Save settings"
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "Save",
      size: 12
    }), " Save"), /*#__PURE__*/React.createElement(ActionButton, {
      intent: "stop",
      onClick: onClose,
      title: "Close settings"
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "X",
      size: 12
    }), " Close"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 16
    }
  }, /*#__PURE__*/React.createElement(SettingsSection, {
    title: "UI Defaults",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Monitor",
      size: 16
    }),
    tone: "agent"
  }, /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Sidebar Open by Default",
    description: "Show the sidebar panel when the application starts"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  })), /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Component Palette Open by Default",
    description: "Show the component palette when the application starts"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  })), /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Console Panel Open by Default",
    description: "Show the console/chat panel at the bottom when the application starts"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  })), /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Auto-add Skill for Connected Tools",
    description: "When a tool node is connected to an AI agent, automatically enable the matching skill in the agent's Master Skill"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  }))), /*#__PURE__*/React.createElement(SettingsSection, {
    title: "Auto-save",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Save",
      size: 16
    }),
    tone: "model"
  }, /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Enable Auto-save",
    description: "Automatically save the workflow at regular intervals"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  })), /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Auto-save Interval",
    description: "How often to auto-save (10-300 seconds)"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      width: 96
    }
  }, /*#__PURE__*/React.createElement(Input, {
    type: "number",
    defaultValue: 30,
    min: 10,
    max: 300,
    step: 5,
    style: {
      paddingRight: 24
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: '50%',
      right: 8,
      transform: 'translateY(-50%)',
      fontSize: 12,
      color: 'var(--fg-muted)',
      pointerEvents: 'none'
    }
  }, "s")))), /*#__PURE__*/React.createElement(SettingsSection, {
    title: "Memory & Compaction",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Brain",
      size: 16
    }),
    tone: "agent"
  }, /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Default Window Size",
    description: "Number of message pairs to keep in short-term memory (1-100)"
  }, /*#__PURE__*/React.createElement(Input, {
    type: "number",
    defaultValue: 100,
    min: 1,
    max: 100,
    style: {
      width: 80
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--border-default)',
      margin: '4px 0'
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '8px 0'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      fontWeight: 500,
      color: 'var(--fg-default)'
    }
  }, "Compaction Ratio"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--fg-muted)',
      marginTop: 2
    }
  }, "Fraction of context window that triggers memory compaction")), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 600,
      color: 'var(--node-model)',
      minWidth: 42,
      textAlign: 'right'
    }
  }, ratio, "%")), /*#__PURE__*/React.createElement(Slider, {
    min: 5,
    max: 95,
    step: 5,
    value: ratio,
    onChange: setRatio,
    color: "var(--node-model)",
    style: {
      margin: '12px 0 6px'
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      fontSize: 10,
      color: 'var(--fg-muted)'
    }
  }, /*#__PURE__*/React.createElement("span", null, "5%"), /*#__PURE__*/React.createElement("span", null, "50%"), /*#__PURE__*/React.createElement("span", null, "95%")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--fg-muted)',
      marginTop: 6,
      lineHeight: 1.45
    }
  }, "Lower = compact sooner (saves tokens, loses detail). Higher = compact later (preserves context, uses more tokens)."))), /*#__PURE__*/React.createElement(SettingsSection, {
    title: "Audio",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Volume2",
      size: 16
    }),
    tone: "tool"
  }, /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Sound Effects",
    description: "Play per-theme click / hover / save / error sounds. Each theme ships a different pack (parchment, terminal, marble, ink, clockwork, ...)"
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true
  }))), /*#__PURE__*/React.createElement(SettingsSection, {
    title: "Help",
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "HelpCircle",
      size: 16
    }),
    tone: "model",
    style: {
      marginBottom: 0
    }
  }, /*#__PURE__*/React.createElement(SettingsRow, {
    label: "Replay Welcome Guide",
    description: "Show the onboarding wizard again to review platform features"
  }, /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "HelpCircle",
    size: 13
  }), " Replay")))));
}
function NodeConfigModal({
  node,
  onClose
}) {
  const {
    PanelModal,
    DataCard,
    CollapsibleSection,
    Select,
    Textarea,
    Slider,
    Checkbox,
    ActionButton,
    EmptyState,
    Icon
  } = DS_PM;
  const [temp, setTemp] = React.useState(70);
  const [ran, setRan] = React.useState(false);
  if (!node) return null;
  const colHead = (icon, text) => /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '10px 14px',
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--fg-default)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: icon,
    size: 14
  }), " ", text);
  return /*#__PURE__*/React.createElement(PanelModal, {
    title: "Node Configuration",
    titleIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "Settings",
      size: 14
    }),
    maxWidth: "95%",
    maxHeight: "92%",
    onClose: onClose,
    headerActions: /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 15,
        fontWeight: 600,
        color: 'var(--fg-default)'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 18
      }
    }, node.icon), " ", node.label, /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--warning)'
      }
    }, "*")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 8
      }
    }, /*#__PURE__*/React.createElement(ActionButton, {
      intent: "run",
      title: "Execute this node",
      onClick: () => setRan(true)
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "Play",
      size: 12
    }), " Run"), /*#__PURE__*/React.createElement(ActionButton, {
      intent: "tools",
      title: "Save parameters"
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "Save",
      size: 12
    }), " Save"), /*#__PURE__*/React.createElement(ActionButton, {
      intent: "stop",
      onClick: onClose
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "X",
      size: 12
    }), " Cancel")))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 0.7,
      minWidth: 0,
      display: 'flex',
      flexDirection: 'column',
      borderRight: '1px solid var(--border-default)'
    }
  }, colHead('Link2', 'Input Data (1 item)'), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 12
    }
  }, /*#__PURE__*/React.createElement(DataCard, {
    title: "Item 1",
    badge: "from WhatsApp Receive",
    data: {
      message: "What's on my calendar today?",
      from: '+1 555 014 2236',
      timestamp: '2026-06-12T09:14:02Z'
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '8px 14px',
      borderTop: '1px solid var(--border-default)',
      fontSize: 11,
      color: 'var(--fg-muted)',
      flexShrink: 0
    }
  }, "Shows actual data received by this node during execution")), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1.6,
      minWidth: 0,
      display: 'flex',
      flexDirection: 'column'
    }
  }, colHead('SlidersHorizontal', 'Parameters'), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 16,
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--fg-default)'
    }
  }, "Model"), /*#__PURE__*/React.createElement(Select, {
    defaultValue: "claude-sonnet-4-5",
    options: ['claude-sonnet-4-5', 'gpt-4o', 'llama3.1:8b', 'gemini-2.0-flash']
  })), /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--fg-default)'
    }
  }, "System Prompt"), /*#__PURE__*/React.createElement(Textarea, {
    rows: 4,
    defaultValue: "You are a personal assistant with access to my calendar, email, and messages. Answer briefly and take action when asked."
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--fg-default)'
    }
  }, "Temperature"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      color: 'var(--node-model)'
    }
  }, (temp / 100).toFixed(2))), /*#__PURE__*/React.createElement(Slider, {
    min: 0,
    max: 100,
    step: 5,
    value: temp,
    onChange: setTemp,
    color: "var(--node-model)"
  })), /*#__PURE__*/React.createElement(CollapsibleSection, {
    title: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Icon, {
      name: "Wrench",
      size: 13
    }), " Connected Skills")
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Checkbox, {
    defaultChecked: true,
    label: "Web Search \u2014 search the web via DuckDuckGo"
  }), /*#__PURE__*/React.createElement(Checkbox, {
    defaultChecked: true,
    label: "Android Toolkit \u2014 16 device services"
  }), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Code Execution \u2014 sandboxed Python"
  }))))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 0.7,
      minWidth: 0,
      display: 'flex',
      flexDirection: 'column',
      borderLeft: '1px solid var(--border-default)'
    }
  }, colHead('ArrowRightFromLine', 'Output'), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 12
    }
  }, ran ? /*#__PURE__*/React.createElement(DataCard, {
    title: "Execution Result",
    badge: "Success \xB7 1.9s",
    blockLabel: "Response",
    data: {
      response: 'You have 2 events today: standup at 10:00 and a dentist appointment at 15:30.'
    }
  }) : /*#__PURE__*/React.createElement(EmptyState, {
    icon: /*#__PURE__*/React.createElement(Icon, {
      name: "Play",
      size: 26,
      strokeWidth: 1.5
    }),
    title: "No output yet",
    hint: "Run this node to see its output",
    style: {
      padding: '24px 16px'
    }
  })))));
}
function CredentialsModal({
  open,
  onClose
}) {
  const {
    PanelModal,
    ApiKeyInput,
    Avatar,
    ActionButton,
    Icon
  } = DS_PM;
  if (!open) return null;
  const providers = [{
    name: 'Anthropic',
    color: 'var(--dracula-orange)',
    stored: true,
    val: 'sk-ant-api03-xxxxxxxxxxxxxxxx'
  }, {
    name: 'Open AI',
    color: 'var(--dracula-cyan)',
    stored: false,
    val: ''
  }, {
    name: 'Groq',
    color: 'var(--dracula-pink)',
    stored: false,
    val: ''
  }, {
    name: 'Google',
    color: 'var(--dracula-green)',
    stored: true,
    val: 'AIzaSyxxxxxxxxxxxxxxxx'
  }];
  return /*#__PURE__*/React.createElement(PanelModal, {
    title: "API Credentials",
    titleIcon: /*#__PURE__*/React.createElement(Icon, {
      name: "KeyRound",
      size: 14
    }),
    maxWidth: "640px",
    maxHeight: "80%",
    onClose: onClose,
    headerActions: /*#__PURE__*/React.createElement(ActionButton, {
      intent: "stop",
      onClick: onClose
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "X",
      size: 12
    }), " Close")
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 16,
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12.5,
      color: 'var(--fg-muted)',
      lineHeight: 1.5
    }
  }, "Bring your own keys \u2014 they're stored locally and never leave your machine."), providers.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.name,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Avatar, {
    name: p.name,
    color: p.color,
    square: true
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      width: 86,
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--fg-default)',
      flexShrink: 0
    }
  }, p.name), /*#__PURE__*/React.createElement(ApiKeyInput, {
    defaultValue: p.val,
    isStored: p.stored,
    onSave: () => {},
    onDelete: p.stored ? () => {} : undefined,
    placeholder: "Enter API key..."
  })))));
}
window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, {
  SettingsModal,
  NodeConfigModal,
  CredentialsModal
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/PanelsModals.jsx", error: String((e && e.message) || e) }); }

// ui_kits/opencompany/Toolbar.jsx
try { (() => {
// OpenCompany UI kit — top toolbar (48px, bg-panel).
// Composes ActionButton / Button / ModeToggle / Icon from the DS bundle.
const DS_TB = window.OpenCompanyDesignSystem_2559cf;
function Divider() {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      width: 1,
      height: 24,
      background: 'var(--border-default)',
      margin: '0 4px',
      flexShrink: 0
    }
  });
}
function Toolbar({
  workflowName,
  sidebarVisible,
  paletteVisible,
  mode,
  dark,
  running,
  saved,
  onToggleSidebar,
  onTogglePalette,
  onModeChange,
  onToggleTheme,
  onRun,
  onSave,
  onSettings,
  onCredentials
}) {
  const {
    ActionButton,
    Icon,
    ModeToggle
  } = DS_TB;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      height: 'var(--h-toolbar, 48px)',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      borderBottom: '1px solid var(--border-default)',
      background: 'var(--bg-panel)',
      padding: '0 12px',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(ActionButton, {
    intent: "save",
    iconOnly: true,
    title: sidebarVisible ? 'Hide sidebar' : 'Show sidebar',
    onClick: onToggleSidebar
  }, /*#__PURE__*/React.createElement(Icon, {
    name: sidebarVisible ? 'PanelLeftClose' : 'PanelLeftOpen',
    size: 14
  })), /*#__PURE__*/React.createElement(Divider, null), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "save"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "FileText",
    size: 13
  }), " File ", /*#__PURE__*/React.createElement(Icon, {
    name: "ChevronDown",
    size: 12
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      flex: 1,
      justifyContent: 'center',
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 500,
      color: 'var(--fg-default)',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, workflowName), /*#__PURE__*/React.createElement(Icon, {
    name: "Pencil",
    size: 11,
    style: {
      color: 'var(--fg-muted)'
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--node-model)',
      whiteSpace: 'nowrap'
    }
  }, "Mode:"), /*#__PURE__*/React.createElement(ModeToggle, {
    mode: mode,
    onChange: onModeChange
  }), /*#__PURE__*/React.createElement(Divider, null), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "config",
    iconOnly: true,
    title: "Settings",
    onClick: onSettings
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "Settings",
    size: 14
  })), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "secret",
    iconOnly: true,
    title: "API Credentials",
    onClick: onCredentials
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "KeyRound",
    size: 14
  })), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "tools",
    iconOnly: true,
    title: "Toggle theme",
    onClick: onToggleTheme
  }, /*#__PURE__*/React.createElement(Icon, {
    name: dark ? 'Sun' : 'Moon',
    size: 14
  })), /*#__PURE__*/React.createElement(Divider, null), !running ? /*#__PURE__*/React.createElement(ActionButton, {
    intent: "run",
    title: "Start workflow",
    onClick: onRun
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "Play",
    size: 12
  }), " Start") : /*#__PURE__*/React.createElement(ActionButton, {
    intent: "stop",
    title: "Stop workflow",
    onClick: onRun
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "Square",
    size: 12
  }), " Stop"), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "save",
    disabled: saved,
    title: saved ? 'No changes to save' : 'Save changes',
    onClick: onSave
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "Save",
    size: 12
  }), " Save"), /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '4px 10px',
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      color: saved ? 'var(--success)' : 'var(--warning)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: '50%',
      background: saved ? 'var(--success)' : 'var(--warning)'
    }
  }), saved ? 'Saved' : 'Modified'), /*#__PURE__*/React.createElement(Divider, null), /*#__PURE__*/React.createElement(ActionButton, {
    intent: "tools",
    iconOnly: true,
    title: paletteVisible ? 'Hide components' : 'Show components',
    onClick: onTogglePalette
  }, /*#__PURE__*/React.createElement(Icon, {
    name: paletteVisible ? 'PanelRightClose' : 'PanelRightOpen',
    size: 14
  }))));
}
window.OpenCompanyKit = Object.assign(window.OpenCompanyKit || {}, {
  Toolbar
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/opencompany/Toolbar.jsx", error: String((e && e.message) || e) }); }

__ds_ns.ActionButton = __ds_scope.ActionButton;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.ComponentItem = __ds_scope.ComponentItem;

__ds_ns.ModeToggle = __ds_scope.ModeToggle;

__ds_ns.SquareNode = __ds_scope.SquareNode;

__ds_ns.StatusBar = __ds_scope.StatusBar;

__ds_ns.WorkflowCard = __ds_scope.WorkflowCard;

__ds_ns.Avatar = __ds_scope.Avatar;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.ChatBubble = __ds_scope.ChatBubble;

__ds_ns.Kbd = __ds_scope.Kbd;

__ds_ns.LogLine = __ds_scope.LogLine;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.EmptyState = __ds_scope.EmptyState;

__ds_ns.Modal = __ds_scope.Modal;

__ds_ns.Progress = __ds_scope.Progress;

__ds_ns.Spinner = __ds_scope.Spinner;

__ds_ns.Toast = __ds_scope.Toast;

__ds_ns.Tooltip = __ds_scope.Tooltip;

__ds_ns.ApiKeyInput = __ds_scope.ApiKeyInput;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.RadioGroup = __ds_scope.RadioGroup;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.Slider = __ds_scope.Slider;

__ds_ns.Switch = __ds_scope.Switch;

__ds_ns.Textarea = __ds_scope.Textarea;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.CollapsibleSection = __ds_scope.CollapsibleSection;

__ds_ns.DataCard = __ds_scope.DataCard;

__ds_ns.PanelModal = __ds_scope.PanelModal;

__ds_ns.SettingsSection = __ds_scope.SettingsSection;

__ds_ns.SettingsRow = __ds_scope.SettingsRow;

})();
