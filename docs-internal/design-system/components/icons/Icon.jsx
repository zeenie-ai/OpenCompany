import * as React from 'react';

/**
 * Icon — Lucide bridge. OpenCompany uses lucide-react; in this design system
 * icons render from the lucide UMD bundle. Load it once per page:
 *   <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
 * Then <Icon name="Play" size={14} /> — colored via currentColor.
 */
export function Icon({ name, size = 16, strokeWidth = 2, style, ...rest }) {
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
    if (node.length && Array.isArray(node[0])) children = node;
    else if (node[0] === 'svg' && Array.isArray(node[2])) children = node[2];
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0, display: 'inline-block', verticalAlign: 'middle', ...style }}
      aria-hidden="true"
      {...rest}
    >
      {children
        ? children.map((child, i) => {
            const tag = child[0];
            const attrs = child[1] || {};
            return React.createElement(tag, { key: i, ...attrs });
          })
        : null}
    </svg>
  );
}

function toPascal(name) {
  if (!name) return '';
  return String(name)
    .split(/[-_ ]/)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join('');
}
